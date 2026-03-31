# Audio Output Controller Refactor

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the multi-writer audio delivery system with a single-writer OutputController that serializes all audio output and feeds agent results back to Gemini for coherent follow-ups.

**Architecture:** A new `OutputController` owns all WebSocket audio writes via a priority queue. Gemini moderator audio, agent TTS audio, and meeting-mode queued audio all flow through a single async drain task. An `OutputState` enum (`LISTENING`, `MODERATOR_SPEAKING`, `AGENT_SPEAKING`) gates transitions and prevents races. Agent results are injected back to Gemini context after TTS delivery.

**Tech Stack:** Python 3.11+, asyncio, FastAPI/Starlette WebSocket, existing codec module

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `app/output_controller.py` | OutputState enum, OutputController class (single-writer, priority queue, state machine) |
| Create | `tests/unit/test_output_controller.py` | Unit tests for OutputController state transitions, queue ordering, chunking |
| Modify | `app/models.py` | Add `output_controller` field to ConversationSession |
| Modify | `app/agent_task_manager.py` | Replace direct WS writes and meeting-mode sender with OutputController.enqueue_agent_audio() |
| Modify | `app/gemini_proxy.py` | Replace direct send_audio_response with OutputController.enqueue_moderator_audio(); add agent result injection |
| Modify | `app/ws/handler.py` | Remove send_agent_audio/send_audio_response; add OutputController init; keep send_json_msg |
| Modify | `app/main.py` | Wire OutputController into startup |

---

## Chunk 1: OutputController Core

### Task 1: OutputState enum and OutputController skeleton

**Files:**
- Create: `app/output_controller.py`
- Create: `tests/unit/test_output_controller.py`

- [ ] **Step 1: Write failing tests for OutputState and basic queue operations**

```python
# tests/unit/test_output_controller.py
"""Unit tests for the audio OutputController."""

import asyncio
import pytest
from app.output_controller import OutputController, OutputState, AudioItem


class TestOutputState:
    def test_initial_state_is_listening(self):
        oc = OutputController()
        assert oc.state == OutputState.LISTENING

    def test_states_are_distinct(self):
        assert OutputState.LISTENING != OutputState.MODERATOR_SPEAKING
        assert OutputState.MODERATOR_SPEAKING != OutputState.AGENT_SPEAKING


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_moderator_audio(self):
        oc = OutputController()
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        assert oc.pending_count() == 1

    @pytest.mark.asyncio
    async def test_enqueue_agent_audio(self):
        oc = OutputController()
        oc.enqueue_agent_audio("ellen", b"\x00" * 6400, gen_id=0)
        assert oc.pending_count() == 1

    @pytest.mark.asyncio
    async def test_moderator_has_priority_over_agent(self):
        """Moderator audio should drain before agent audio."""
        oc = OutputController()
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=0)
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        item = oc._get_next()
        assert item is not None
        assert item.speaker == "moderator"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/unit/test_output_controller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.output_controller'`

- [ ] **Step 3: Implement OutputController skeleton**

```python
# app/output_controller.py
"""Single-writer audio output controller.

All audio destined for the iOS client (moderator TTS, agent TTS) flows
through this controller.  A single drain task serializes writes to the
WebSocket, preventing races and interleaved audio.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from dataclasses import dataclass, field
from typing import Any

from app.codec import AGENT_SPEAKER_IDS, MsgType, SpeakerId, encode_frame

logger = logging.getLogger(__name__)

_AUDIO_CHUNK_SIZE = 3200  # 100ms of 16 kHz 16-bit PCM


class OutputState(enum.Enum):
    LISTENING = "listening"
    MODERATOR_SPEAKING = "moderator_speaking"
    AGENT_SPEAKING = "agent_speaking"


@dataclass
class AudioItem:
    """A unit of audio to be written to the WebSocket."""
    speaker: str          # "moderator" or agent name (e.g., "ellen")
    pcm: bytes            # raw PCM audio
    gen_id: int           # generation counter for zombie prevention

    @property
    def priority(self) -> int:
        """Lower number = higher priority. Moderator always wins."""
        return 0 if self.speaker == "moderator" else 1


class OutputController:
    """Serializes all audio output through a single writer task."""

    def __init__(self) -> None:
        self.state: OutputState = OutputState.LISTENING
        self._moderator_queue: list[AudioItem] = []
        self._agent_queue: list[AudioItem] = []
        self._drain_event = asyncio.Event()
        self._drain_task: asyncio.Task | None = None
        self._ws: Any = None  # WebSocket connection, set via start()
        self._current_gen_id: int = 0
        self._frame_seq: int = 0
        # Callback to send JSON messages (status updates, etc.)
        self._send_json_fn: Any = None
        # Callback to inject agent results back to Gemini
        self._inject_agent_result_fn: Any = None

    def start(
        self,
        ws: Any,
        send_json_fn: Any = None,
        inject_agent_result_fn: Any = None,
    ) -> None:
        """Attach to a WebSocket and start the drain loop."""
        self._ws = ws
        self._send_json_fn = send_json_fn
        self._inject_agent_result_fn = inject_agent_result_fn
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        """Stop the drain loop."""
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        self._drain_task = None
        self._ws = None

    def enqueue_moderator_audio(self, pcm: bytes, gen_id: int) -> None:
        """Enqueue moderator (Gemini) audio for output."""
        self._moderator_queue.append(AudioItem("moderator", pcm, gen_id))
        self._drain_event.set()

    def enqueue_agent_audio(self, agent_name: str, pcm: bytes, gen_id: int) -> None:
        """Enqueue agent TTS audio for output."""
        self._agent_queue.append(AudioItem(agent_name, pcm, gen_id))
        self._drain_event.set()

    def enqueue_agent_result(
        self, agent_name: str, pcm: bytes, result_text: str, gen_id: int
    ) -> None:
        """Enqueue agent audio AND schedule Gemini result injection after playback."""
        item = AudioItem(agent_name, pcm, gen_id)
        item._result_text = result_text  # type: ignore[attr-defined]
        self._agent_queue.append(item)
        self._drain_event.set()

    def pending_count(self) -> int:
        return len(self._moderator_queue) + len(self._agent_queue)

    def _get_next(self) -> AudioItem | None:
        """Return the next item to drain.  Moderator always takes priority."""
        if self._moderator_queue:
            return self._moderator_queue.pop(0)
        if self._agent_queue:
            return self._agent_queue.pop(0)
        return None

    def flush(self, gen_id: int | None = None) -> None:
        """Flush queued audio (barge-in).  If gen_id given, only flush stale items."""
        if gen_id is None:
            self._moderator_queue.clear()
            self._agent_queue.clear()
        else:
            self._moderator_queue = [
                i for i in self._moderator_queue if i.gen_id >= gen_id
            ]
            self._agent_queue = [
                i for i in self._agent_queue if i.gen_id >= gen_id
            ]
        self.state = OutputState.LISTENING

    # ------------------------------------------------------------------
    # Drain loop — the single writer
    # ------------------------------------------------------------------

    async def _drain_loop(self) -> None:
        """Single async task that owns all WS audio writes."""
        try:
            while True:
                await self._drain_event.wait()
                self._drain_event.clear()

                while True:
                    item = self._get_next()
                    if item is None:
                        self.state = OutputState.LISTENING
                        break

                    # Update state
                    if item.speaker == "moderator":
                        self.state = OutputState.MODERATOR_SPEAKING
                    else:
                        self.state = OutputState.AGENT_SPEAKING

                    # Chunk and send
                    await self._send_audio(item)

                    # If this item carried an agent result, inject it to Gemini
                    result_text = getattr(item, "_result_text", None)
                    if result_text and self._inject_agent_result_fn:
                        try:
                            await self._inject_agent_result_fn(
                                item.speaker, result_text
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to inject agent result to Gemini: %s", exc
                            )

                self.state = OutputState.LISTENING
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("OutputController drain loop crashed: %s", exc, exc_info=True)

    async def _send_audio(self, item: AudioItem) -> None:
        """Chunk PCM and write frames to the WebSocket."""
        if self._ws is None:
            return

        if item.speaker == "moderator":
            msg_type = MsgType.AUDIO_RESPONSE
            speaker_id = SpeakerId.MODERATOR
        else:
            msg_type = MsgType.AGENT_AUDIO
            speaker_id = AGENT_SPEAKER_IDS.get(item.speaker, 0)

        try:
            for offset in range(0, len(item.pcm), _AUDIO_CHUNK_SIZE):
                chunk = item.pcm[offset : offset + _AUDIO_CHUNK_SIZE]
                frame = encode_frame(
                    msg_type,
                    speaker_id,
                    item.gen_id & 0xFF,
                    self._frame_seq & 0xFF,
                    chunk,
                )
                await self._ws.send_bytes(frame)
                self._frame_seq += 1
        except Exception:
            logger.warning("OutputController: WS write failed (closed?)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/unit/test_output_controller.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/output_controller.py tests/unit/test_output_controller.py
git commit -m "feat: add OutputController with single-writer drain loop and priority queue"
```

### Task 2: Tests for flush (barge-in) and state transitions

**Files:**
- Modify: `tests/unit/test_output_controller.py`

- [ ] **Step 1: Write failing tests for flush and state machine**

Add to `tests/unit/test_output_controller.py`:

```python
class TestFlush:
    def test_flush_clears_all_queues(self):
        oc = OutputController()
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=0)
        oc.flush()
        assert oc.pending_count() == 0
        assert oc.state == OutputState.LISTENING

    def test_flush_with_gen_id_keeps_current(self):
        oc = OutputController()
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=1)
        oc.enqueue_agent_audio("shijing", b"\x00" * 3200, gen_id=3)
        oc.flush(gen_id=2)
        assert oc.pending_count() == 1  # only shijing (gen_id=3) remains

    def test_flush_resets_state_to_listening(self):
        oc = OutputController()
        oc.state = OutputState.AGENT_SPEAKING
        oc.flush()
        assert oc.state == OutputState.LISTENING


class TestDrainLoop:
    @pytest.mark.asyncio
    async def test_drain_sends_chunks_to_ws(self):
        """Verify the drain loop writes chunked frames to the WS mock."""
        sent_frames: list[bytes] = []

        class MockWS:
            async def send_bytes(self, data: bytes) -> None:
                sent_frames.append(data)

        oc = OutputController()
        oc.start(ws=MockWS())
        # 6400 bytes = 2 chunks of 3200
        oc.enqueue_moderator_audio(b"\x00" * 6400, gen_id=0)
        await asyncio.sleep(0.05)  # let drain loop run
        await oc.stop()
        assert len(sent_frames) == 2

    @pytest.mark.asyncio
    async def test_drain_moderator_before_agent(self):
        """Moderator audio drains before agent audio regardless of enqueue order."""
        speakers: list[int] = []

        class MockWS:
            async def send_bytes(self, data: bytes) -> None:
                # byte 0 = msg_type: 0x02=moderator, 0x03=agent
                speakers.append(data[0])

        oc = OutputController()
        oc.start(ws=MockWS())
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=0)
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        await asyncio.sleep(0.05)
        await oc.stop()
        # Moderator (0x02) should come first, then agent (0x03)
        assert speakers[0] == 0x02
        assert speakers[-1] == 0x03

    @pytest.mark.asyncio
    async def test_state_returns_to_listening_after_drain(self):
        class MockWS:
            async def send_bytes(self, data: bytes) -> None:
                pass

        oc = OutputController()
        oc.start(ws=MockWS())
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        await asyncio.sleep(0.05)
        assert oc.state == OutputState.LISTENING
        await oc.stop()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/unit/test_output_controller.py -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_output_controller.py
git commit -m "test: add flush, drain loop, and priority tests for OutputController"
```

---

## Chunk 2: Wire OutputController into the Session and Handler

### Task 3: Add OutputController to ConversationSession

**Files:**
- Modify: `app/models.py`

- [ ] **Step 1: Add output_controller field to ConversationSession**

In `app/models.py`, add import at top and field to `ConversationSession`:

```python
# At top of file, add:
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.output_controller import OutputController

# In ConversationSession, add field after meeting_sender_task:
    output_controller: Any = None  # OutputController, set during WS connect
```

Note: Use `Any` to avoid circular import issues. The field is set during WebSocket connection setup.

- [ ] **Step 2: Verify imports still work**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/python -c "from app.models import ConversationSession; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add app/models.py
git commit -m "feat: add output_controller field to ConversationSession"
```

### Task 4: Wire OutputController in ws/handler.py and main.py

**Files:**
- Modify: `app/ws/handler.py`
- Modify: `app/main.py`

- [ ] **Step 1: Update handler to create and start OutputController per session**

In `app/ws/handler.py`:

1. Add import at top:
```python
from app.output_controller import OutputController
```

2. Replace the `send_audio_response` function body — it now delegates to OutputController:
```python
async def send_audio_response(
    session: "ConversationSession", pcm: bytes, frame_seq: int
) -> None:
    """Enqueue moderator audio into the OutputController."""
    if session.output_controller is not None:
        session.output_controller.enqueue_moderator_audio(pcm, session.gen_id)
```

3. Replace the `send_agent_audio` function body:
```python
async def send_agent_audio(
    session: "ConversationSession",
    agent_name: str,
    pcm: bytes,
    frame_seq: int,
) -> None:
    """Enqueue agent audio into the OutputController."""
    if session.output_controller is not None:
        session.output_controller.enqueue_agent_audio(agent_name, pcm, session.gen_id)
```

4. In `websocket_endpoint`, after `await ws.accept()` and `session.ws_connection = ws`, add OutputController init:
```python
    # Initialize the output controller for this session
    oc = OutputController()
    session.output_controller = oc
    oc.start(ws=ws, send_json_fn=lambda s, p: send_json_msg(s, p))
```

5. In the `finally` block of `websocket_endpoint`, before `await _gemini_proxy.close_session(session)`, add:
```python
        if session.output_controller:
            await session.output_controller.stop()
```

6. In `_handle_interrupt`, after `new_gen = _session_manager.increment_gen_id(...)`, add:
```python
    if session.output_controller:
        session.output_controller.flush(gen_id=new_gen)
```

- [ ] **Step 2: Verify imports compile**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/python -c "import app.ws.handler; import app.main; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run existing tests to check nothing is broken**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/unit/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/ws/handler.py app/main.py
git commit -m "feat: wire OutputController into WS handler, moderator and agent audio flow through single writer"
```

---

## Chunk 3: Simplify AgentTaskManager (Remove Meeting-Mode Sender)

### Task 5: Remove meeting-mode queue and direct WS writes from AgentTaskManager

**Files:**
- Modify: `app/agent_task_manager.py`

The OutputController now handles all audio serialization, so the meeting queue, `_deliver_or_queue`, and `_meeting_mode_audio_sender` are no longer needed. Agent audio goes straight to `OutputController.enqueue_agent_audio()`.

- [ ] **Step 1: Simplify `_run_agent` delivery — replace `_deliver_or_queue` with OutputController enqueue**

In `_run_agent`, replace the block after TTS synthesis:

```python
                if pcm:
                    # Deliver via OutputController (serialized, chunked)
                    if conv_session.output_controller:
                        conv_session.output_controller.enqueue_agent_audio(
                            agent_session.agent_name, pcm, conv_session.gen_id
                        )
                    elif self.send_agent_audio:
                        # Fallback for tests without OutputController
                        await self.send_agent_audio(
                            conv_session, agent_session.agent_name, pcm,
                            agent_session.next_frame_seq()
                        )
```

- [ ] **Step 2: Remove `_deliver_or_queue` method entirely**

Delete the `_deliver_or_queue` method (and its DIAG logs).

- [ ] **Step 3: Remove `_meeting_mode_audio_sender` method entirely**

Delete the `_meeting_mode_audio_sender` method.

- [ ] **Step 4: Remove meeting-mode logic from `dispatch`**

In `dispatch()`, remove the block that checks `entering_meeting_mode` / `already_in_meeting_mode` and launches `_meeting_mode_audio_sender`. Keep only the agent session creation, "thinking" status emit, and task spawn:

```python
    async def dispatch(self, conv_session, agent_name, task) -> str:
        if agent_name not in self._registry:
            raise ValueError(f"Unknown agent: {agent_name}")

        agent_session = AgentSession(
            agent_name=agent_name,
            parent_session_id=conv_session.session_id,
        )
        conv_session.agent_sessions[agent_session.agent_session_id] = agent_session

        if self.send_json:
            await self.send_json(
                conv_session,
                {
                    "type": "agent_status",
                    "agent_name": agent_name,
                    "agent_session_id": agent_session.agent_session_id,
                    "status": "thinking",
                    "elapsed_ms": 0,
                    "gen_id": conv_session.gen_id,
                },
            )

        agent_session.claude_task = asyncio.create_task(
            self._run_agent(conv_session, agent_session, task)
        )
        return agent_session.agent_session_id
```

- [ ] **Step 5: Simplify `handle_interrupt` — remove meeting queue references**

```python
    async def handle_interrupt(self, conv_session, mode) -> None:
        if mode == "cancel_all":
            # Flush is handled by OutputController; just reset agent frame seqs
            for agent_session in conv_session.agent_sessions.values():
                agent_session.current_frame_seq = 0
        elif mode == "cancel_agents":
            for agent_session in conv_session.agent_sessions.values():
                if agent_session.claude_task and not agent_session.claude_task.done():
                    logger.info(
                        "handle_interrupt: cancelling agent=%s (%s)",
                        agent_session.agent_name,
                        agent_session.agent_session_id,
                    )
                    agent_session.claude_task.cancel()
```

- [ ] **Step 6: Remove unused imports and constants**

Remove `_AUDIO_CHUNK_SIZE`, the `encode_frame`/`MsgType`/`AGENT_SPEAKER_IDS` imports (if no longer used), and meeting-mode related fields that are no longer referenced.

- [ ] **Step 7: Verify imports compile**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/python -c "import app.agent_task_manager; print('OK')"`
Expected: `OK`

- [ ] **Step 8: Run all unit tests**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/unit/ -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/agent_task_manager.py
git commit -m "refactor: remove meeting-mode sender and direct WS writes from AgentTaskManager

Audio delivery now flows through OutputController which serializes all
writes to the WebSocket, eliminating interleaved audio races."
```

---

## Chunk 4: Agent Result Injection to Gemini

### Task 6: Feed agent results back to Gemini context

**Files:**
- Modify: `app/gemini_proxy.py`
- Modify: `app/agent_task_manager.py`
- Modify: `app/ws/handler.py`

This is the fix for Issue 3 (follow-ups get no response). Currently, when Gemini calls `dispatch_agent`, it immediately gets back `{"status": "dispatched"}` but never learns what the agent found. We need to send the agent's result text back to Gemini after TTS delivery.

- [ ] **Step 1: Add `inject_agent_result` method to GeminiLiveProxy**

In `app/gemini_proxy.py`, add a new public method:

```python
    async def inject_agent_result(
        self,
        conv_session: "ConversationSession",
        agent_name: str,
        result_text: str,
    ) -> None:
        """Send an agent's result back to Gemini as context.

        Uses send_tool_response with a synthetic function response so Gemini
        can reference the agent's findings in subsequent turns.
        """
        if conv_session.gemini_session is None:
            logger.warning("Cannot inject agent result -- Gemini session is None")
            return

        from google.genai import types

        # Send as a client-side content turn so Gemini sees it as context
        try:
            await conv_session.gemini_session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            text=f"[Agent {agent_name} reported]: {result_text}"
                        )
                    ],
                ),
                turn_complete=False,  # don't end the user's turn
            )
            logger.info(
                "[session=%s] Injected %s result to Gemini (%d chars)",
                conv_session.session_id,
                agent_name,
                len(result_text),
            )
        except Exception as exc:
            logger.warning(
                "[session=%s] Failed to inject agent result: %s",
                conv_session.session_id,
                exc,
            )
```

- [ ] **Step 2: Call inject_agent_result from AgentTaskManager after TTS delivery**

In `app/agent_task_manager.py`, in `_run_agent`, after the audio enqueue block, add result injection:

```python
                # Inject agent result text back to Gemini for follow-up context
                if self._inject_agent_result_fn:
                    try:
                        await self._inject_agent_result_fn(
                            conv_session, agent_session.agent_name, full_text
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to inject %s result to Gemini: %s",
                            agent_session.agent_name, exc,
                        )
```

- [ ] **Step 3: Add inject callback to AgentTaskManager constructor**

In `AgentTaskManager.__init__`, add a new parameter:

```python
    def __init__(
        self,
        agent_registry,
        agent_runner,
        tts_service,
        send_json_fn=None,
        send_agent_audio_fn=None,
        inject_agent_result_fn=None,
    ) -> None:
        ...
        self._inject_agent_result_fn = inject_agent_result_fn
```

- [ ] **Step 4: Wire the callback in main.py**

In `app/main.py`, update the AgentTaskManager instantiation. Since `gemini_proxy` is created after `agent_task_manager`, use a late-binding lambda:

```python
    agent_task_manager = AgentTaskManager(
        agent_registry=agent_registry,
        agent_runner=sdk_agent_runner,
        tts_service=tts_service,
        send_json_fn=send_json_msg,
        send_agent_audio_fn=send_agent_audio,
    )
    gemini_proxy = GeminiLiveProxy(
        agent_registry=agent_registry,
        agent_task_manager=agent_task_manager,
        session_manager=session_manager,
        send_audio_response_fn=send_audio_response,
        send_json_fn=send_json_msg,
    )

    # Late-bind: inject_agent_result needs gemini_proxy which is created after ATM
    agent_task_manager._inject_agent_result_fn = gemini_proxy.inject_agent_result
```

- [ ] **Step 5: Verify imports compile**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/python -c "import app.main; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Run all unit tests**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/unit/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/gemini_proxy.py app/agent_task_manager.py app/main.py
git commit -m "feat: inject agent results back to Gemini context for coherent follow-ups

After an agent completes and its TTS is delivered, the result text is
sent to Gemini as client content so follow-up questions have context."
```

---

## Chunk 5: Cleanup and Integration Verification

### Task 7: Remove obsolete meeting-mode fields from models

**Files:**
- Modify: `app/models.py`

- [ ] **Step 1: Remove meeting_queue and meeting_sender_task from ConversationSession**

In `app/models.py`, remove these fields from `ConversationSession`:

```python
    # DELETE these lines:
    meeting_queue: list[str] = field(default_factory=list)
    meeting_sender_task: Optional[asyncio.Task] = None
```

Also remove `audio_buffer` and `completion_event` from `AgentSession` since the OutputController handles buffering:

```python
    # DELETE these lines from AgentSession:
    audio_buffer: list[bytes] = field(default_factory=list)
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
```

- [ ] **Step 2: Grep for any remaining references to removed fields**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && grep -rn "meeting_queue\|meeting_sender_task\|audio_buffer\|completion_event" app/`

Fix any remaining references. If `handle_interrupt` still references `audio_buffer`, remove those lines.

- [ ] **Step 3: Verify imports compile**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/python -c "import app.main; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Run all tests**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && .venv/bin/pytest tests/ -v --ignore=tests/component --ignore=tests/integration`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py
git commit -m "refactor: remove obsolete meeting-mode fields from session models"
```

### Task 8: Manual smoke test

- [ ] **Step 1: Start the backend**

Run: `cd /Users/shijinglu/Workspace/hackthon/backend && make run`

- [ ] **Step 2: Test single agent dispatch**

Speak: "Ask Ellen for my TODOs"
Verify: Ellen responds with full multi-sentence answer (not truncated), audio plays completely.

- [ ] **Step 3: Test multi-agent (previously "meeting mode")**

Speak: "Ask Ellen for my TODOs and ask Shijing for today's metrics"
Verify: Both agents respond sequentially (no overlap), each plays full audio.

- [ ] **Step 4: Test follow-up**

After Ellen responds, speak: "Cancel the first meeting"
Verify: Gemini routes follow-up to Ellen (via resume_agent), Ellen responds with context from her previous answer.

- [ ] **Step 5: Test barge-in**

While an agent is speaking, interrupt with speech.
Verify: Agent audio stops, Gemini responds to the new input.
