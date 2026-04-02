# Execution Plan: Barge-In -- Response Queue + iOS Polish

**Date:** 2026-04-02
**Status:** Draft
**Scope:** Backend Response Queue, interrupt plumbing updates, iOS haptic feedback and AEC verification

---

## Problem Statement

The barge-in design (`docs/barge-in-design.md`) specifies a **Response Queue** in the backend that holds agent TEXT results and only TTS/injects them when the audio pipeline is idle. Currently, `AgentTaskManager._run_agent()` immediately TTS-synthesizes agent output and enqueues the PCM into `OutputController` -- even if the moderator (Gemini) is actively speaking. This leads to agent audio colliding with moderator audio and makes barge-in flush incomplete because text-stage results (pre-TTS) are invisible to `OutputController.flush()`.

### What exists today

| Component | State |
|---|---|
| `OutputController` (drain loop, flush, gen_id zombie filter) | Complete |
| `AgentTaskManager` (dispatch, timeout, heartbeat, TTS delivery) | Complete -- but TTS is immediate, no queuing |
| `GeminiLiveProxy` (VAD interruption, `{"type":"interruption"}` to iOS) | Complete |
| iOS dual-trigger barge-in (local RMS + server interrupt) | Complete |
| iOS `AudioPlaybackEngine.flushAllAndStop()` | Complete |
| iOS time-based echo gate | Complete |
| **Response Queue** (hold agent text, drain when idle, flush on barge-in) | **NOT IMPLEMENTED** |
| iOS haptic on barge-in | **NOT IMPLEMENTED** |

---

## Phase 1: Backend Response Queue

**Goal:** Agent text results queue up. They TTS and play only when `OutputController.state == LISTENING`. Barge-in flushes unspoken text results.

### Task 1.1 -- Add `ResponseQueue` to `OutputController`

**File:** `backend/app/output_controller.py`

Add a new text-level queue alongside the existing audio queues:

```python
@dataclass
class PendingResponse:
    agent_name: str
    spoken_text: str      # post-rephrase text ready for TTS
    raw_text: str         # original agent output (for transcript logging)
    gen_id: int
    enqueued_at: float
    pcm: bytes | None = None       # eager TTS result (populated by background task)
    tts_task: asyncio.Task | None = None  # background TTS task handle

class OutputController:
    def __init__(self):
        # ... existing fields ...
        self._response_queue: list[PendingResponse] = []
        self._response_drain_event = asyncio.Event()
        self._response_drain_task: asyncio.Task | None = None
```

Key behaviors:

- `enqueue_response(agent_name, spoken_text, raw_text, gen_id)` -- appends to `_response_queue`, kicks off **eager background TTS** (`asyncio.create_task` that populates `pending.pcm`), and signals the response drain event. This eliminates the 1-2s dead-air gap between moderator finishing and agent audio starting.
- `_response_drain_loop()` -- waits for `_response_drain_event`, checks `self.state == LISTENING`. When idle, pops the first `PendingResponse`, awaits its `tts_task` if PCM not yet ready, **re-checks state after TTS completes** (critical: state may have changed during TTS -- see Race Condition Guard below), then calls `enqueue_agent_audio(...)`. Loops until queue is empty or state changes.
- `flush()` (existing) -- also clears `_response_queue`, cancels any in-flight `tts_task` handles. Log flushed items to transcript with a `"flushed": true` flag.

**Race Condition Guard (Critical):** After TTS completes in the drain loop, re-check `self.state` before calling `enqueue_agent_audio()`. If state is no longer `LISTENING` (e.g., new moderator audio arrived during TTS), push the response (now with PCM populated) back to the front of `_response_queue` and continue waiting:

```python
async def _response_drain_loop(self):
    while True:
        await self._response_drain_event.wait()
        self._response_drain_event.clear()
        while self._response_queue:
            if self.state != OutputState.LISTENING:
                break  # wait for next signal
            resp = self._response_queue.pop(0)
            # Await eager TTS if not ready
            if resp.pcm is None and resp.tts_task:
                resp.pcm = await resp.tts_task
            # RE-CHECK state after TTS (may have changed)
            if self.state != OutputState.LISTENING:
                self._response_queue.insert(0, resp)  # put back
                break
            if resp.pcm:
                self.enqueue_agent_audio(resp.agent_name, resp.pcm, resp.gen_id)
```

**Design decision:** Place the response queue in `OutputController` rather than `AgentTaskManager` because `OutputController` already owns the `state` machine and the audio queues. It is the single point that knows whether the pipeline is idle.

**Dependency:** `OutputController` needs a TTS callback. Pass it during `start()`:

```python
def start(self, ws, tts_fn, gen_id_fn):
    self._ws = ws
    self._tts_fn = tts_fn        # async (text, agent_name) -> bytes
    self._gen_id_fn = gen_id_fn  # () -> int, returns current session gen_id
    self._drain_task = asyncio.create_task(self._drain_loop())
    self._response_drain_task = asyncio.create_task(self._response_drain_loop())
```

**Acceptance criteria:**
- [ ] `PendingResponse` items only TTS when `self.state == LISTENING` and both audio queues are empty
- [ ] `flush()` clears `_response_queue` and logs flushed items
- [ ] Response drain loop does not race with the audio drain loop (both run but response drain only feeds audio drain, never writes to WS directly)
- [ ] Unit test: enqueue 3 responses while state is AGENT_SPEAKING, verify none are TTS'd until state returns to LISTENING

### Task 1.2 -- Update `AgentTaskManager._run_agent()` to use Response Queue

**File:** `backend/app/agent_task_manager.py`

Change the post-agent-completion flow (lines 271-318). Instead of:

```
rephrase -> TTS -> output_controller.enqueue_agent_audio()
```

Do:

```
rephrase -> output_controller.enqueue_response(agent_name, spoken_text, raw_text, gen_id)
```

The TTS call moves into `OutputController._response_drain_loop()`.

This means `AgentTaskManager` no longer needs a direct `TTSService` dependency for the normal path. Keep it for the timeout fallback path (`_handle_timeout`) which should still TTS immediately (fallback phrases are short and important).

**Acceptance criteria:**
- [ ] `_run_agent()` calls `output_controller.enqueue_response()` instead of synthesizing TTS
- [ ] Timeout fallback still TTS's immediately via `enqueue_agent_audio()`
- [ ] Transcript history append happens at enqueue time (before TTS), not after -- so even flushed responses are in context

### Task 1.3 -- Wire TTS callback into OutputController startup

**File:** `backend/app/ws/handler.py`

Update the `OutputController` initialization in `websocket_endpoint()`:

```python
oc = OutputController()
session.output_controller = oc
oc.start(
    ws=ws,
    tts_fn=tts_service.synthesize,
    gen_id_fn=lambda: session.gen_id,
)
```

**File:** `backend/app/output_controller.py`

Update `stop()` to cancel both tasks (currently it only cancels `_drain_task`):

```python
async def stop(self) -> None:
    for task_attr in ("_drain_task", "_response_drain_task"):
        task = getattr(self, task_attr, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        setattr(self, task_attr, None)
    self._ws = None
```

**Acceptance criteria:**
- [ ] `OutputController` receives a working `tts_fn` at startup
- [ ] `stop()` cancels both `_drain_task` and `_response_drain_task`
- [ ] Any in-flight `tts_task` handles on queued `PendingResponse` items are also cancelled during `stop()`

### Task 1.4 -- Flush Response Queue on interrupt

**Files:** `backend/app/output_controller.py`, `backend/app/ws/handler.py`

`OutputController.flush()` already clears the audio queues. Extend it to also clear `_response_queue`:

```python
def flush(self, gen_id=None):
    # ... existing audio queue clearing ...
    flushed_responses = list(self._response_queue)
    self._response_queue.clear()
    for resp in flushed_responses:
        logger.info("INTERRUPT: flushed unspoken response from %s (gen=%d): %.100s",
                     resp.agent_name, resp.gen_id, resp.spoken_text)
    return flushed_responses  # caller can log to transcript
```

In `_handle_interrupt` in `handler.py`, capture and log flushed responses to `session.transcript_history`:

```python
flushed = session.output_controller.flush(gen_id=new_gen)
for resp in (flushed or []):
    session.transcript_history.append({
        "speaker": resp.agent_name,
        "text": resp.spoken_text,
        "flushed": True,  # never spoken aloud
    })
```

**Acceptance criteria:**
- [ ] Barge-in flushes both audio queues AND the response queue
- [ ] Flushed text responses are recorded in transcript with `flushed: True`
- [ ] Context builders include flushed entries (agents have full context even if user interrupted)

---

## Phase 2: Gemini Speaking-State Awareness

**Goal:** The response drain loop knows when Gemini is speaking so it waits for the moderator to finish before injecting agent audio.

### Task 2.1 -- Track moderator speaking state in OutputController

**File:** `backend/app/output_controller.py`

This is already implemented. `OutputController.state` transitions to `MODERATOR_SPEAKING` when moderator audio is being drained and back to `LISTENING` when both queues are empty. The response drain loop simply checks `self.state == LISTENING` before popping a response.

**Acceptance criteria:**
- [ ] Response drain loop does not pop a response while `self.state == MODERATOR_SPEAKING`
- [ ] Response drain loop does not pop while `self.state == AGENT_SPEAKING` (previous agent still playing)
- [ ] After moderator finishes and state returns to LISTENING, response drain wakes up and processes

### Task 2.2 -- Signal response drain on state transitions

**File:** `backend/app/output_controller.py`

In the audio `_drain_loop`, when the inner loop breaks and state changes to LISTENING, signal the response drain event:

```python
if self.state != OutputState.LISTENING:
    self.state = OutputState.LISTENING
    await self._emit_playback_state("listening")
    self._response_drain_event.set()  # wake response drain
```

**Acceptance criteria:**
- [ ] Response drain loop wakes up within one event-loop tick of state -> LISTENING
- [ ] No busy-spin if response queue is empty

---

## Phase 3: iOS Haptic Feedback

**Goal:** Subtle haptic tap acknowledges barge-in to the user.

### Task 3.1 -- Add haptic feedback on barge-in

**File:** `ios/StarCall/Session/ConversationSession.swift`

```swift
import UIKit

// At class level:
private let hapticGenerator = UIImpactFeedbackGenerator(style: .light)

// In handleBargein() / handleServerInterruption():
hapticGenerator.impactOccurred()
```

Use `.light` style -- the design doc says "subtle, barely audible haptic tap."

### Task 3.2 -- Deduplicate haptic between local and server triggers

**File:** `ios/StarCall/Session/ConversationSession.swift`

```swift
private var lastHapticTime: CFAbsoluteTime = 0
private let hapticDedupeInterval: Double = 0.5

private func fireHaptic() {
    let now = CFAbsoluteTimeGetCurrent()
    guard now - lastHapticTime >= hapticDedupeInterval else { return }
    lastHapticTime = now
    hapticGenerator.impactOccurred()
}
```

**Acceptance criteria:**
- [ ] User feels a light tap when barge-in fires
- [ ] At most one haptic per 500ms window regardless of trigger source

---

## Phase 4: Edge Cases and Hardening

### Task 4.1 -- Agent finishes during active barge-in

**Scenario:** User is speaking (barge-in in progress), agent completes in background, `_run_agent` calls `enqueue_response()`.

**Expected:** Response sits in `_response_queue`. Response drain loop sees `state != LISTENING` and waits. When moderator finishes responding, drain loop picks up the agent response. If user's new command supersedes, next barge-in flushes the queue.

**Action:** Write integration test confirming `enqueue_response()` during non-LISTENING state does not TTS or send audio.

### Task 4.2 -- Multiple agents queue up simultaneously

**Scenario:** Ellen, Eva, and Ming all finish within milliseconds.

**Expected:** Response drain loop pops them one at a time. After TTS + audio delivery of Ellen's response, state goes AGENT_SPEAKING, response drain waits. When Ellen's audio finishes, state returns to LISTENING, drain pops Eva's. Repeat for Ming.

**Action:** Write integration test with 3 enqueued responses confirming sequential delivery.

### Task 4.3 -- Gen_id staleness in response queue

**Scenario:** Agent dispatched at gen_id=5. User barge-ins, gen_id becomes 6. Agent finishes and tries to enqueue response with gen_id=5.

**Action:** In `enqueue_response()`, check gen_id staleness using RFC 1982 modular arithmetic (consistent with iOS `AudioPlaybackEngine` zombie filter). Do NOT use simple `!=` comparison -- it breaks on wrap-around:

```python
def _is_stale_gen(self, gen_id: int) -> bool:
    """RFC 1982 staleness check for 8-bit gen_id (wraps at 255)."""
    current = self._gen_id_fn() & 0xFF
    diff = (current - gen_id) & 0xFF
    return 0 < diff < 128  # diff > 0 means behind, < 128 means not wrapped past

def enqueue_response(self, agent_name, spoken_text, raw_text, gen_id):
    if self._is_stale_gen(gen_id):
        logger.info("Discarding stale response from %s (gen=%d, current=%d)",
                     agent_name, gen_id, self._gen_id_fn())
        return
    resp = PendingResponse(agent_name, spoken_text, raw_text, gen_id, time.monotonic())
    resp.tts_task = asyncio.create_task(self._tts_fn(spoken_text, agent_name))
    self._response_queue.append(resp)
    self._response_drain_event.set()
```

This uses the same modular comparison as iOS, ensuring consistent behavior across the stack.

**Acceptance criteria:**
- [ ] Stale agent responses are discarded at enqueue time
- [ ] Non-stale responses are enqueued normally

---

## Phase 5: Integration Testing

### Task 5.1 -- End-to-end barge-in flow verification

1. Start session, let moderator greet
2. Ask moderator to dispatch Ellen for a long task
3. While Ellen's TTS is playing, say "stop" mid-sentence
4. Verify: playback stops immediately, haptic fires, moderator acknowledges new command
5. Verify: unspoken agent responses are flushed (check backend logs for `flushed unspoken response`)

### Task 5.2 -- Meeting mode barge-in

1. Ask for a meeting with all agents
2. While first agent is speaking, barge-in
3. Verify: all queued agent responses in response queue are flushed
4. Verify: playback stops, moderator takes floor

### Task 5.3 -- AEC echo gate verification

1. Let moderator speak a long response
2. Verify: no false barge-in fires during playback (check DIAG-ECHO logs)
3. During playback, speak loudly -- verify barge-in fires correctly

---

## Sequencing

```
Phase 1 (Backend Response Queue)
  Task 1.1 --+
  Task 1.3 --+-- Task 1.2 -- Task 1.4

Phase 2 (State Awareness)
  Task 2.1 -- Task 2.2  (can parallel with Phase 1)

Phase 3 (iOS Haptic)
  Task 3.1 -- Task 3.2  (independent of Phase 1-2)

Phase 4 (Edge Cases)
  Task 4.1, 4.2, 4.3  (depends on Phase 1 complete)

Phase 5 (Integration)
  All of Phase 1-4 complete
```

---

## Files Changed (Summary)

| File | Changes |
|---|---|
| `backend/app/output_controller.py` | Add `PendingResponse`, `_response_queue`, `_response_drain_loop()`, update `flush()`, add `enqueue_response()` |
| `backend/app/agent_task_manager.py` | Change `_run_agent()` to call `enqueue_response()` instead of direct TTS+enqueue |
| `backend/app/ws/handler.py` | Pass `tts_fn` to `OutputController.start()`, log flushed responses to transcript |
| `ios/StarCall/Session/ConversationSession.swift` | Add haptic generator, `fireHaptic()`, call from barge-in handlers |

---

## Risks

1. **TTS latency in response drain loop:** ~~Solved~~ by eager TTS (Task 1.1). TTS starts immediately on `enqueue_response()` as a background task. The drain loop awaits the result only when it's time to play, so the PCM is typically already ready. Residual risk: if TTS is slower than expected, the drain loop re-checks state after awaiting, and pushes the response back if state changed (Race Condition Guard).

2. **Response queue unbounded growth:** If agents keep finishing but the pipeline is never idle, responses pile up. **Mitigation:** Cap response queue at 5 items. Drop oldest on overflow (with logging). Cancel the dropped item's `tts_task`.

3. **Gen_id wrap-around:** The 8-bit gen_id wraps at 255. Handled consistently via RFC 1982 modular arithmetic in `_is_stale_gen()`, matching the iOS zombie filter implementation.
