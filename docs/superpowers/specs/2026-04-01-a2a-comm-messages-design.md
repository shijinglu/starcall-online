# Surface A2A Communication Messages to iOS

## Problem

When agents collaborate via A2A, they produce intermediate reasoning text (e.g., "I need to investigate this wire reversal. Let me consult Eva..."). Today these are only logged on the backend. The iOS app has no visibility into inter-agent conversation, making the system feel like a black box during multi-agent collaboration.

## Goals

- Surface A2A "thinking aloud" messages to iOS in real-time
- **No TTS** — these are display-only text messages
- Visually distinguish them from final agent responses

## Non-Goals

- Exposing internal `ThinkingBlock` content (model chain-of-thought)
- Making A2A comm messages interactive or actionable
- Persisting comm messages beyond the current session
- Streaming intermediate text from nested A2A callee agents (v1 limitation — see Scope section)

---

## Design

### Scope (v1)

Only the **top-level agent's** intermediate `TextBlock`s are surfaced. When Agent A calls Agent B via A2A (`send_task_to_agent`), the A2A protocol is HTTP request/response — intermediate text from Agent B is not streamed back. This is acceptable for v1 because the interesting "I'm going to consult Eva about..." messages come from the top-level agent's reasoning, not the callee.

### New JSON Message Type: `agent_comm`

A new WebSocket JSON message type sent from backend to iOS:

```json
{
  "type": "agent_comm",
  "from_agent": "shijing",
  "to_agent": "eva",
  "text": "Let me check recent user activity for anomalies and consult Eva about wire reversals.",
  "gen_id": 3
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"agent_comm"` |
| `from_agent` | string | Agent producing the text |
| `to_agent` | string? | Agent being consulted; `null` for self-directed reasoning |
| `text` | string | Intermediate reasoning/communication text |
| `gen_id` | int | Generation ID for barge-in zombie filtering |

### Backend Plumbing: Callback Registry

The core challenge is that `SDKAgentRunner.run()` executes inside `ClaudeA2AExecutor` (called via HTTP from `AgentTaskManager`), but has no reference to the WebSocket or `ConversationSession`. The solution is a **process-level callback registry** + an `on_text` callback parameter.

#### Flow

```
AgentTaskManager._run_agent()
  1. Register callback in _comm_callbacks[agent_name] = (send_json, conv_session)
  2. Call send_task_to_agent() (HTTP to A2A server, same process)
     → ClaudeA2AExecutor.execute()
       → Looks up callback from _comm_callbacks[agent_name]
       → Calls sdk_runner.run(agent_session, task, on_text=callback)
         → On each non-final TextBlock, invokes on_text(agent_name, text)
           → callback sends {"type": "agent_comm", ...} via WebSocket
  3. Unregister callback from _comm_callbacks[agent_name]
```

### Backend Changes

#### `backend/app/sdk_agent_runner.py`

Add optional `on_text` callback parameter to `run()`:

```python
async def run(
    self,
    agent_session: "AgentSession",
    task: str,
    on_text: Callable[[str, str], Awaitable[None]] | None = None,
) -> str:
```

In the message processing loop, when a `TextBlock` is encountered in an `AssistantMessage` (not the final `ResultMessage`), call `on_text(agent_name, text)` if provided. **All** non-result `TextBlock`s are emitted — no need to detect A2A-specific tool calls, since these intermediate messages are useful regardless of whether the agent is about to delegate.

#### `backend/app/agent_task_manager.py`

1. Add a module-level callback registry:

```python
_comm_callbacks: dict[str, tuple[Callable, "ConversationSession"]] = {}
```

2. In `_run_agent()`, before calling `send_task_to_agent()`:
   - Register a callback that captures `send_json` and `conv_session`
   - The callback constructs and sends `{"type": "agent_comm", ...}` with `gen_id` from `conv_session.gen_id`

3. After `send_task_to_agent()` returns (or on exception), unregister the callback.

4. The `to_agent` field is set to `null` in v1 (determining the target from TextBlock content would require NLP; the tool call hasn't happened yet when the text is emitted).

#### `backend/app/a2a/executor.py`

In `execute()`, look up `_comm_callbacks[agent_name]` and construct an `on_text` callable from it. Pass this to `sdk_runner.run()`. If no callback is registered (e.g., standalone A2A testing), `on_text` is `None` and behavior is unchanged.

#### Rate Limiting

Backend-side: debounce `agent_comm` messages to max **1 per agent per 500ms**. If multiple `TextBlock`s arrive within the window, only the last one is sent. This prevents chatty agents from flooding the WebSocket.

### iOS Changes

#### `ios/VoiceAgent/Models/AgentState.swift`

Add `AgentCommEvent` alongside existing `AgentStatusEvent`:

```swift
struct AgentCommEvent {
    let fromAgent: String
    let toAgent: String?
    let text: String
    let genId: Int
}
```

#### `ios/VoiceAgent/Session/ConversationSession.swift`

Add `case "agent_comm"` to the `transportDidReceiveTextFrame` switch. Parse into `AgentCommEvent` (dictionary-based, matching existing pattern) and call `delegate?.sessionDidReceiveAgentComm(_:)`.

Add `sessionDidReceiveAgentComm(_:)` to the `ConversationSessionDelegate` protocol.

#### `ios/VoiceAgent/ViewModel/ConversationViewModel.swift`

- Add `@Published var agentCommTexts: [String: (text: String, genId: Int)]` — maps agent name → latest comm text + its gen_id.
- Implement `sessionDidReceiveAgentComm(_:)` delegate callback.
- On barge-in (gen_id change): clear only entries whose stored `genId` is stale, not all comms (agents survive barge-in per existing `handle_interrupt` logic).

#### `ios/VoiceAgent/Views/AgentStatusCard.swift`

Display the latest comm text for the agent beneath the existing status card:

- Smaller font (`.caption`), italicized
- Muted color (`NexusTheme.mutedText`)
- Fade-in animation on arrival
- Auto-fade after ~5 seconds using local timer; receipt time tracked locally (no server timestamp needed for v1)
- Max 2 lines with truncation

---

## Data Flow

```
Agent (TextBlock in AssistantMessage)
  → sdk_runner.run() calls on_text(agent_name, text)
  → callback (registered by AgentTaskManager) sends via WebSocket:
      {"type": "agent_comm", "from_agent": "shijing", "to_agent": null, "text": "...", "gen_id": 3}
  → iOS ConversationSession parses → delegate
  → ViewModel updates agentCommTexts[agentName]
  → AgentStatusCard renders text beneath card (no TTS triggered)
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| New message type vs. reusing `agent_status` | Cleaner separation; `agent_status` is a state machine, `agent_comm` is a stream of text |
| No TTS | Intermediate reasoning, not user-facing answers |
| Gen-ID filtering | Prevents stale A2A messages after barge-in |
| Process-level callback registry | Bridges the HTTP boundary between `AgentTaskManager` and `ClaudeA2AExecutor` without changing the A2A protocol |
| v1: top-level agent only | The A2A protocol is request/response; streaming from nested agents requires protocol changes |
| All non-result TextBlocks emitted | Simpler than detecting A2A-specific tool calls; intermediate reasoning is useful regardless |
| `to_agent` is null in v1 | Determining the target before the tool call happens would require heuristics; not worth the complexity |
| Backend-side rate limiting (500ms debounce) | Prevents WebSocket flood from chatty agents |
| Clear stale gen_id comms only | Agents survive barge-in; clearing all comms would remove valid in-progress agent comms |
| `agentCommTexts` dict with gen_id (latest per agent) | Simpler than a capped array; UI only shows one line per agent card; gen_id enables stale-only clearing on barge-in |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Chatty agents flood UI | Backend 500ms debounce + UI shows only latest per agent |
| Sensitive reasoning exposed | Only `TextBlock` content (public reasoning) is surfaced, never `ThinkingBlock` |
| Message ordering across agents | Gen-ID + per-agent latest-wins provides sufficient ordering |
| Callback registry leak on crash | `finally` block in `_run_agent()` ensures cleanup |
| Concurrent runs for same agent name | Not a concern in practice — dispatch creates one task per agent per user utterance; the semaphore serializes further. Registry keyed by `agent_name` is sufficient. |

## Files to Change

| File | Change |
|------|--------|
| `backend/app/sdk_agent_runner.py` | Add `on_text` callback param; call it on non-final `TextBlock` |
| `backend/app/agent_task_manager.py` | Add callback registry + `agent_comm` sender; register/unregister around `send_task_to_agent()` |
| `backend/app/a2a/executor.py` | Look up callback from registry; pass `on_text` to `sdk_runner.run()` |
| `ios/VoiceAgent/Models/AgentState.swift` | Add `AgentCommEvent` struct |
| `ios/VoiceAgent/Session/ConversationSession.swift` | Parse `agent_comm` messages; add delegate method |
| `ios/VoiceAgent/ViewModel/ConversationViewModel.swift` | Store latest comm text per agent; handle barge-in cleanup |
| `ios/VoiceAgent/Views/AgentStatusCard.swift` | Render comm text below card with fade animation |
