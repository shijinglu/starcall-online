# A2A Communication Messages Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface agent-to-agent intermediate reasoning text to the iOS app as display-only messages (no TTS).

**Architecture:** Add `on_text` callback to `SDKAgentRunner.run()`, wire it through a process-level callback registry in `AgentTaskManager` that bridges the HTTP boundary to `ClaudeA2AExecutor`. Backend emits `{"type": "agent_comm"}` JSON messages over WebSocket. iOS parses, stores latest-per-agent, and renders beneath agent avatars with auto-fade.

**Tech Stack:** Python (FastAPI, asyncio), Swift (SwiftUI, Foundation)

**Spec:** `docs/superpowers/specs/2026-04-01-a2a-comm-messages-design.md`

---

## Chunk 1: Backend — SDKAgentRunner on_text callback

### Task 1: Add on_text callback to SDKAgentRunner.run()

**Files:**
- Modify: `backend/app/sdk_agent_runner.py:44-53` (run signature)
- Modify: `backend/app/sdk_agent_runner.py:102-115` (AssistantMessage TextBlock handling)
- Test: `backend/tests/component/test_sdk_agent_runner.py`

- [ ] **Step 1: Write failing test — on_text callback is invoked for non-final TextBlocks**

Add to `backend/tests/component/test_sdk_agent_runner.py`:

```python
from claude_agent_sdk import AssistantMessage, TextBlock


def _make_assistant_msg(text: str) -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)])


INTERMEDIATE_TEXT = "Let me investigate the wire reversal and consult Eva."


async def fake_query_gen_with_text(**kwargs):
    """Fake async generator with intermediate TextBlock before result."""
    yield _make_system_msg()
    yield _make_assistant_msg(INTERMEDIATE_TEXT)
    yield _make_result_msg()


@pytest.mark.asyncio
async def test_run_invokes_on_text_for_intermediate_textblocks(registry, tts_service):
    """on_text callback should be called for TextBlocks in AssistantMessages."""
    from app.sdk_agent_runner import SDKAgentRunner

    runner = SDKAgentRunner(registry, tts_service)
    agent_session = AgentSession(agent_name="shijing")

    collected = []

    async def on_text(agent_name: str, text: str):
        collected.append((agent_name, text))

    with patch("app.sdk_agent_runner.query") as mock_query:
        mock_query.return_value = fake_query_gen_with_text()
        await runner.run(agent_session, "Check the wire", on_text=on_text)

    assert len(collected) == 1
    assert collected[0] == ("shijing", INTERMEDIATE_TEXT)


@pytest.mark.asyncio
async def test_run_does_not_invoke_on_text_when_none(registry, tts_service):
    """When on_text is None (default), no error occurs."""
    from app.sdk_agent_runner import SDKAgentRunner

    runner = SDKAgentRunner(registry, tts_service)
    agent_session = AgentSession(agent_name="shijing")

    with patch("app.sdk_agent_runner.query") as mock_query:
        mock_query.return_value = fake_query_gen_with_text()
        result = await runner.run(agent_session, "Check the wire")

    assert result == RESULT_TEXT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/component/test_sdk_agent_runner.py -v`
Expected: FAIL — `run()` does not accept `on_text` parameter

- [ ] **Step 3: Implement on_text callback in SDKAgentRunner.run()**

In `backend/app/sdk_agent_runner.py`, modify:

1. Add import at top:
```python
from typing import TYPE_CHECKING, Any, Awaitable, Callable
```

2. Update `run()` signature (line 44):
```python
async def run(
    self,
    agent_session: "AgentSession",
    task: str,
    on_text: Callable[[str, str], Awaitable[None]] | None = None,
) -> str:
```

3. In the `TextBlock` handler (around line 110), after the existing `logger.info`, add:
```python
elif isinstance(block, TextBlock):
    logger.info(
        "[%s] text: %s",
        agent,
        block.text[:300],
    )
    if on_text:
        await on_text(agent, block.text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/component/test_sdk_agent_runner.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/sdk_agent_runner.py backend/tests/component/test_sdk_agent_runner.py
git commit -m "feat: add on_text callback to SDKAgentRunner.run()"
```

---

## Chunk 2: Backend — Callback registry and agent_comm emission

### Task 2: Add callback registry and agent_comm sender to AgentTaskManager

**Files:**
- Modify: `backend/app/agent_task_manager.py:1-10` (imports)
- Modify: `backend/app/agent_task_manager.py:35-52` (class init, add registry)
- Modify: `backend/app/agent_task_manager.py:163-235` (_run_agent, register/unregister + debounce)
- Test: `backend/tests/component/test_agent_comm.py` (new)

- [ ] **Step 1: Write failing test — agent_comm message is sent via WebSocket**

Create `backend/tests/component/test_agent_comm.py`:

```python
"""Tests for agent_comm message emission through callback registry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_task_manager import AgentTaskManager, _comm_callbacks
from app.models import AgentSession, ConversationSession


def _make_conv_session() -> ConversationSession:
    cs = ConversationSession()
    cs.gen_id = 5
    return cs


def _make_agent_session(agent_name: str = "shijing") -> AgentSession:
    return AgentSession(agent_name=agent_name, parent_session_id="parent-1")


@pytest.mark.asyncio
async def test_comm_callback_sends_agent_comm_json():
    """When on_text fires, an agent_comm JSON message should be sent via send_json."""
    sent_messages = []

    async def fake_send_json(conv_session, msg):
        sent_messages.append(msg)

    async def fake_send_task(agent_name, task_text, metadata=None):
        # Simulate the executor calling on_text via the registry
        cb = _comm_callbacks.get(agent_name)
        if cb:
            on_text_fn = cb
            await on_text_fn(agent_name, "Investigating wire reversal")
        return "Final agent result"

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=True)
    runner = AsyncMock()
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"\x00" * 3200)

    mgr = AgentTaskManager(
        agent_registry=registry,
        agent_runner=runner,
        tts_service=tts,
        send_json_fn=fake_send_json,
    )

    conv = _make_conv_session()
    conv.output_controller = MagicMock()

    with patch("app.agent_task_manager.send_task_to_agent", side_effect=fake_send_task), \
         patch("app.agent_task_manager.rephrase_for_tts", return_value="Final result"):
        agent_session = _make_agent_session("shijing")
        conv.agent_sessions[agent_session.agent_session_id] = agent_session
        await mgr._run_agent(conv, agent_session, "Check the wire")

    comm_msgs = [m for m in sent_messages if m.get("type") == "agent_comm"]
    assert len(comm_msgs) >= 1
    assert comm_msgs[0]["from_agent"] == "shijing"
    assert comm_msgs[0]["text"] == "Investigating wire reversal"
    assert comm_msgs[0]["gen_id"] == 5
    assert comm_msgs[0]["to_agent"] is None


@pytest.mark.asyncio
async def test_comm_callback_cleaned_up_after_run():
    """Callback registry should be cleaned up even if send_task raises."""

    async def failing_send_task(agent_name, task_text, metadata=None):
        raise RuntimeError("boom")

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=True)
    runner = AsyncMock()
    tts = AsyncMock()

    mgr = AgentTaskManager(
        agent_registry=registry,
        agent_runner=runner,
        tts_service=tts,
        send_json_fn=AsyncMock(),
    )

    conv = _make_conv_session()
    agent_session = _make_agent_session("eva")
    conv.agent_sessions[agent_session.agent_session_id] = agent_session

    with patch("app.agent_task_manager.send_task_to_agent", side_effect=failing_send_task):
        await mgr._run_agent(conv, agent_session, "Analyze transactions")

    assert "eva" not in _comm_callbacks
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/component/test_agent_comm.py -v`
Expected: FAIL — `_comm_callbacks` does not exist

- [ ] **Step 3: Implement callback registry and agent_comm emission**

In `backend/app/agent_task_manager.py`:

1. Add module-level callback registry after imports:
```python
import time as _time

# Process-level callback registry: agent_name -> on_text callable
# Bridges the HTTP boundary between AgentTaskManager and ClaudeA2AExecutor.
_comm_callbacks: dict[str, Callable] = {}

# Debounce tracking: agent_name -> last send timestamp
_comm_last_sent: dict[str, float] = {}
_COMM_DEBOUNCE_S = 0.5
```

2. Add `Callable` and `Awaitable` to imports from typing.

3. In `_run_agent()`, wrap the `send_task_to_agent()` call with callback registration. Replace the try block inside `async with self._agent_semaphore:` (around lines 190-198):

```python
# Register comm callback so ClaudeA2AExecutor can forward
# intermediate TextBlocks back to the iOS client.
async def _on_text(agent_name: str, text: str):
    now = _time.monotonic()
    last = _comm_last_sent.get(agent_name, 0.0)
    if now - last < _COMM_DEBOUNCE_S:
        return
    _comm_last_sent[agent_name] = now
    if self.send_json:
        await self.send_json(
            conv_session,
            {
                "type": "agent_comm",
                "from_agent": agent_name,
                "to_agent": None,
                "text": text,
                "gen_id": conv_session.gen_id,
            },
        )

_comm_callbacks[agent_session.agent_name] = _on_text
try:
    full_text = await asyncio.wait_for(
        send_task_to_agent(
            agent_name=agent_session.agent_name,
            task_text=task,
            metadata={"agent_name": agent_session.agent_name},
        ),
        timeout=AGENT_TASK_TIMEOUT_SECONDS,
    )
    # ... existing logging ...
except asyncio.TimeoutError:
    # ... existing timeout handling ...
except asyncio.CancelledError:
    # ... existing cancel handling ...
except Exception as exc:
    # ... existing error handling ...
finally:
    heartbeat_task.cancel()
    _comm_callbacks.pop(agent_session.agent_name, None)
    _comm_last_sent.pop(agent_session.agent_name, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/component/test_agent_comm.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent_task_manager.py backend/tests/component/test_agent_comm.py
git commit -m "feat: add agent_comm callback registry and WebSocket emission"
```

### Task 3: Wire callback through ClaudeA2AExecutor

**Files:**
- Modify: `backend/app/a2a/executor.py:29-42` (execute method)
- Test: `backend/tests/unit/test_a2a_executor.py`

- [ ] **Step 1: Write failing test — executor passes on_text from registry**

Add to `backend/tests/unit/test_a2a_executor.py`:

```python
from app.agent_task_manager import _comm_callbacks


@pytest.mark.asyncio
async def test_execute_passes_on_text_from_registry():
    """Executor should look up comm callback and pass it to sdk_runner.run()."""
    runner = AsyncMock()
    runner.run = AsyncMock(return_value="Result")
    registry = MagicMock()

    fake_callback = AsyncMock()
    _comm_callbacks["ming"] = fake_callback

    try:
        executor = ClaudeA2AExecutor(sdk_runner=runner, registry=registry)
        ctx = _make_context("Check fraud signals", "ming")
        eq = _make_event_queue()

        await executor.execute(ctx, eq)

        call_kwargs = runner.run.call_args
        assert call_kwargs[1].get("on_text") is fake_callback or \
               (len(call_kwargs[0]) >= 3 and call_kwargs[0][2] is fake_callback)
    finally:
        _comm_callbacks.pop("ming", None)


@pytest.mark.asyncio
async def test_execute_passes_none_when_no_callback():
    """When no callback is registered, on_text should be None."""
    runner = AsyncMock()
    runner.run = AsyncMock(return_value="Result")
    registry = MagicMock()

    _comm_callbacks.pop("eva", None)  # ensure clean

    executor = ClaudeA2AExecutor(sdk_runner=runner, registry=registry)
    ctx = _make_context("Analyze transactions", "eva")
    eq = _make_event_queue()

    await executor.execute(ctx, eq)

    call_kwargs = runner.run.call_args
    on_text_val = call_kwargs[1].get("on_text") if call_kwargs[1] else None
    assert on_text_val is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/unit/test_a2a_executor.py -v`
Expected: FAIL — executor doesn't pass `on_text`

- [ ] **Step 3: Implement callback lookup in executor**

In `backend/app/a2a/executor.py`:

1. Add import:
```python
from app.agent_task_manager import _comm_callbacks
```

2. Update the `execute()` method to look up and pass the callback:
```python
async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
    agent_name = (context.metadata or {}).get("agent_name", "")
    task_text = context.get_user_input()
    task_id = context.task_id or "unknown"
    context_id = context.context_id or "unknown"

    logger.info("A2A execute: agent=%s, task_id=%s, text=%.100s", agent_name, task_id, task_text)

    updater = TaskUpdater(event_queue, task_id, context_id)
    await updater.start_work()

    try:
        agent_session = AgentSession(agent_name=agent_name)
        on_text = _comm_callbacks.get(agent_name)
        result_text = await self.sdk_runner.run(agent_session, task_text, on_text=on_text)

        await event_queue.enqueue_event(
            new_agent_text_message(result_text or "No result.", context_id, task_id)
        )
        await updater.complete()

    except Exception as exc:
        logger.exception("A2A execute failed for agent=%s", agent_name)
        await updater.failed(
            new_agent_text_message(f"Agent error: {exc}", context_id, task_id)
        )
```

- [ ] **Step 4: Run all backend tests to verify nothing broke**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/a2a/executor.py backend/tests/unit/test_a2a_executor.py
git commit -m "feat: wire on_text callback through A2A executor"
```

---

## Chunk 3: iOS — Parse agent_comm and update ViewModel

### Task 4: Add AgentCommEvent model

**Files:**
- Modify: `ios/VoiceAgent/Models/AgentState.swift:57` (after MeetingStatusEvent)

- [ ] **Step 1: Add AgentCommEvent struct**

Add after `MeetingStatusEvent` in `ios/VoiceAgent/Models/AgentState.swift`:

```swift
/// Parsed agent_comm server event — intermediate agent reasoning text (no TTS).
struct AgentCommEvent {
    let fromAgent: String
    let toAgent: String?
    let text: String
    let genId: Int
}
```

- [ ] **Step 2: Build to verify it compiles**

Run: Xcode build or `cd ios && swift build` (SPM)
Expected: Compiles successfully

- [ ] **Step 3: Commit**

```bash
git add ios/VoiceAgent/Models/AgentState.swift
git commit -m "feat(ios): add AgentCommEvent model"
```

### Task 5: Parse agent_comm in ConversationSession and add delegate method

**Files:**
- Modify: `ios/VoiceAgent/Session/ConversationSession.swift:5-13` (delegate protocol)
- Modify: `ios/VoiceAgent/Session/ConversationSession.swift:254-311` (message handlers section)
- Modify: `ios/VoiceAgent/Session/ConversationSession.swift:363-378` (switch in transportDidReceiveTextFrame)

- [ ] **Step 1: Add delegate method to protocol**

In `ios/VoiceAgent/Session/ConversationSession.swift`, add to the `ConversationSessionDelegate` protocol (after line 9):

```swift
func sessionDidReceiveAgentComm(_ event: AgentCommEvent)
```

- [ ] **Step 2: Add handler method**

After the `handleError` method (around line 311), add:

```swift
/// Parse and route an agent_comm JSON message.
func handleAgentComm(_ json: [String: Any]) {
    guard let fromAgent = json["from_agent"] as? String,
          let text = json["text"] as? String else { return }

    let toAgent = json["to_agent"] as? String
    let genId = json["gen_id"] as? Int ?? 0

    let event = AgentCommEvent(
        fromAgent: fromAgent,
        toAgent: toAgent,
        text: text,
        genId: genId
    )
    delegate?.sessionDidReceiveAgentComm(event)
}
```

- [ ] **Step 3: Add case to message routing switch**

In `transportDidReceiveTextFrame`, add before the `default` case (around line 375):

```swift
case "agent_comm":
    handleAgentComm(json)
```

- [ ] **Step 4: Build to verify it compiles**

Expected: Build will fail because `ConversationViewModel` does not yet conform to the updated delegate. That's expected — Task 6 fixes it.

- [ ] **Step 5: Commit (even if build fails — Task 6 completes conformance)**

```bash
git add ios/VoiceAgent/Session/ConversationSession.swift
git commit -m "feat(ios): parse agent_comm messages in ConversationSession"
```

### Task 6: Handle agent_comm in ConversationViewModel

**Files:**
- Modify: `ios/VoiceAgent/ViewModel/ConversationViewModel.swift:15-21` (published state)
- Modify: `ios/VoiceAgent/ViewModel/ConversationViewModel.swift:129-141` (reset method)
- Modify: `ios/VoiceAgent/ViewModel/ConversationViewModel.swift:146-188` (delegate conformance)

- [ ] **Step 1: Add published state for comm texts**

In `ConversationViewModel`, after line 21 (`@Published var isMuted`), add:

```swift
/// Latest intermediate reasoning text per agent (no TTS).
/// Value is (text, genId) so stale entries can be cleared on barge-in.
@Published var agentCommTexts: [String: (text: String, genId: Int)] = [:]
```

- [ ] **Step 2: Add handler method**

After `handleMeetingStatusEvent` (around line 124), add:

```swift
// MARK: - Agent Comm Handling

/// Handle an agent_comm event — store latest text per agent.
func handleAgentCommEvent(_ event: AgentCommEvent) {
    agentCommTexts[event.fromAgent] = (text: event.text, genId: event.genId)
}

/// Clear agent comm entries with stale gen_id.
func clearStaleAgentComms(currentGenId: Int) {
    agentCommTexts = agentCommTexts.filter { $0.value.genId >= currentGenId }
}
```

- [ ] **Step 3: Clear in reset()**

In the `reset()` method, add `agentCommTexts.removeAll()` alongside the other `.removeAll()` calls.

- [ ] **Step 4: Add delegate conformance**

In the `ConversationSessionDelegate` extension (around line 186), add:

```swift
nonisolated func sessionDidReceiveAgentComm(_ event: AgentCommEvent) {
    Task { @MainActor in
        self.handleAgentCommEvent(event)
    }
}
```

- [ ] **Step 5: Wire clearStaleAgentComms to barge-in**

In `ios/VoiceAgent/Session/ConversationSession.swift`, add to both `handleBargein()` (after the `transport.sendJSON` call, around line 216) and `handleServerInterruption()` (after `audioCaptureEngine.isPlaying = false`, around line 234):

```swift
delegate?.sessionDidReceiveBargeIn(currentGenId: Int(currentGen))
```

Add the delegate method to `ConversationSessionDelegate` protocol:

```swift
func sessionDidReceiveBargeIn(currentGenId: Int)
```

In `ConversationViewModel`, add delegate conformance:

```swift
nonisolated func sessionDidReceiveBargeIn(currentGenId: Int) {
    Task { @MainActor in
        self.clearStaleAgentComms(currentGenId: currentGenId)
    }
}
```

- [ ] **Step 6: Build to verify it compiles**

Run: Xcode build
Expected: PASS — all delegate methods now implemented

- [ ] **Step 7: Commit**

```bash
git add ios/VoiceAgent/ViewModel/ConversationViewModel.swift ios/VoiceAgent/Session/ConversationSession.swift
git commit -m "feat(ios): store and expose agent comm texts in ViewModel"
```

---

## Chunk 4: iOS — Render comm text in AgentStatusCard

### Task 7: Display comm text beneath agent avatar

**Files:**
- Modify: `ios/VoiceAgent/Views/AgentStatusCard.swift:10-49` (AgentAvatarView body)
- Modify: `ios/VoiceAgent/Views/AgentStatusCard.swift:91-128` (AgentStripView)
- Modify: `ios/VoiceAgent/Views/ContentView.swift:99-103` (pass commTexts to strip)

- [ ] **Step 1: Add commText parameter to AgentAvatarView**

In `ios/VoiceAgent/Views/AgentStatusCard.swift`, add to `AgentAvatarView` properties (after line 13):

```swift
let commText: String?
```

- [ ] **Step 2: Render comm text below avatar name**

Replace the `body` of `AgentAvatarView` (lines 17-48) to add comm text display:

```swift
var body: some View {
    VStack(spacing: 7) {
        ZStack(alignment: .bottomTrailing) {
            // Ring + initials
            Text(definition.initials)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(definition.ringColor)
                .frame(width: avatarSize, height: avatarSize)
                .background(Color(hex: 0x111118))
                .clipShape(Circle())
                .overlay(
                    Circle()
                        .stroke(definition.ringColor, lineWidth: 2)
                )
                .opacity(status.showsSpinner ? thinkingOpacity : 1.0)
                .scaleEffect(isSpeaking ? speakingScale : 1.0)
                .animation(
                    status.showsSpinner
                        ? .easeInOut(duration: 1.8).repeatForever(autoreverses: true)
                        : .easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                    value: status.showsSpinner || isSpeaking
                )

            // Status dot
            statusDot
                .offset(x: 1, y: 1)
        }

        Text(definition.name)
            .font(.system(size: 11))
            .foregroundColor(NexusTheme.agentLabel)

        // Agent comm text (intermediate reasoning, no TTS)
        if let commText = commText, !commText.isEmpty {
            Text(commText)
                .font(.system(size: 10).italic())
                .foregroundColor(NexusTheme.mutedText)
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 100)
                .transition(.opacity)
                .animation(.easeInOut(duration: 0.3), value: commText)
        }
    }
}
```

- [ ] **Step 3: Add commTexts parameter to AgentStripView**

Update `AgentStripView` to accept and pass comm texts. Modify the struct properties (line 92-93):

```swift
struct AgentStripView: View {
    let agents: [(definition: AgentDefinition, status: AgentStatusKind)]
    let currentlyPlayingSpeaker: UInt8?
    let commTexts: [String: String]
```

Update the `ForEach` body (around line 107) to pass `commText`:

```swift
AgentAvatarView(
    definition: agent.definition,
    status: agent.status,
    isSpeaking: isSpeaking,
    commText: commTexts[agent.definition.key]
)
```

- [ ] **Step 4: Update ContentView to pass commTexts**

In `ios/VoiceAgent/Views/ContentView.swift`, update the `AgentStripView` call (around line 100):

```swift
AgentStripView(
    agents: activeAgents,
    currentlyPlayingSpeaker: viewModel.currentlyPlayingSpeaker,
    commTexts: viewModel.agentCommTexts.mapValues { $0.text }
)
```

- [ ] **Step 5: Update Preview**

In `AgentStatusCard.swift`, update the preview (around line 136):

```swift
AgentStripView(
    agents: [
        (AgentDefinition.all[0], .thinking),
        (AgentDefinition.all[1], .done),
        (AgentDefinition.all[2], .thinking),
    ],
    currentlyPlayingSpeaker: nil,
    commTexts: ["ellen": "Checking calendar for conflicts..."]
)
```

- [ ] **Step 6: Build to verify it compiles**

Run: Xcode build
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ios/VoiceAgent/Views/AgentStatusCard.swift ios/VoiceAgent/Views/ContentView.swift
git commit -m "feat(ios): render agent comm text beneath avatars"
```

### Task 8: Add auto-fade behavior for comm text

**Files:**
- Modify: `ios/VoiceAgent/ViewModel/ConversationViewModel.swift` (add fade timer)

- [ ] **Step 1: Add fade timer logic**

In `ConversationViewModel`, add a timer that clears comm text 5 seconds after last update. Update `handleAgentCommEvent`:

```swift
/// Timers for auto-fading agent comm text.
private var commFadeTimers: [String: Task<Void, Never>] = [:]

func handleAgentCommEvent(_ event: AgentCommEvent) {
    agentCommTexts[event.fromAgent] = (text: event.text, genId: event.genId)

    // Cancel existing fade timer for this agent.
    commFadeTimers[event.fromAgent]?.cancel()

    // Start new 5-second fade timer.
    let agentName = event.fromAgent
    commFadeTimers[agentName] = Task { @MainActor in
        try? await Task.sleep(nanoseconds: 5_000_000_000)
        guard !Task.isCancelled else { return }
        self.agentCommTexts.removeValue(forKey: agentName)
    }
}
```

- [ ] **Step 2: Clean up timers in reset()**

In the `reset()` method, add:

```swift
commFadeTimers.values.forEach { $0.cancel() }
commFadeTimers.removeAll()
```

- [ ] **Step 3: Build to verify it compiles**

Run: Xcode build
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add ios/VoiceAgent/ViewModel/ConversationViewModel.swift
git commit -m "feat(ios): auto-fade agent comm text after 5 seconds"
```

---

## Chunk 5: Integration verification

### Task 9: Run full backend test suite

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run iOS build**

Build via Xcode or `cd ios && swift build`
Expected: PASS

- [ ] **Step 3: Final commit if any fixups needed**
