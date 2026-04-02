# Listener Mode Design

## Context

StarCl is a voice-first AI assistant where users speak with a fast Gemini moderator that delegates complex tasks to Claude agents. Currently, Gemini always responds with voice audio and agent results are TTS'd back.

**Problem**: In meetings, the user needs a silent assistant that listens to conversation, smartly dispatches agents, and shows results as text — no audio output whatsoever.

**Solution**: Add a "Listener" mode toggle. When active, Gemini switches from `AUDIO` to `TEXT` response modality. Agent results skip TTS and are delivered as full text transcripts. The iOS app suppresses all audio playback.

## Architecture

The existing pipeline remains intact. Listener mode is a **configuration flag** (`listener_mode: bool`) that suppresses audio output at every layer:

```
iOS ──mic──> Backend ──> Gemini Live API (TEXT modality)
                            │
                       (no audio out)
                            │
                       tool calls ──> Claude agents
                            │
                       text results ──> iOS (display only)
```

The flag flows: iOS UI selection → `POST /sessions` body → `SessionManager.create_session(listener_mode)` → `ConversationSession(listener_mode=True)` → Gemini config + AgentTaskManager behavior.

## Changes by Component

### 1. Data Model & Protocol

**`backend/app/models.py` — `ConversationSession`**:
- Add field: `listener_mode: bool = False`

**`backend/app/session_manager.py` — `SessionManager.create_session()`**:
- Add `listener_mode: bool = False` parameter
- Pass to `ConversationSession(listener_mode=listener_mode, ...)`

**`backend/app/routers/sessions.py` — `POST /api/v1/sessions`**:
- Define Pydantic request model: `class CreateSessionRequest(BaseModel): listener_mode: bool = False`
- Accept optional JSON body: `{"listener_mode": true}`
- Pass `request.listener_mode` to `session_manager.create_session(listener_mode=...)`

**iOS `HTTPClient.createSession()`**:
- Add `listenerMode: Bool = false` parameter
- Include in POST body as `{"listener_mode": true/false}`
- `CreateSessionResponse` unchanged (same response shape)

**No new WebSocket message types.** Agent results already flow as `transcript` JSON. Moderator text responses already have a text fallback path.

### 2. Backend — Gemini Configuration

**`backend/app/gemini_proxy.py` — `start_session()` (line ~86)**:

When `conv_session.listener_mode`:
- `response_modalities=["TEXT"]` instead of `["AUDIO"]`
- Keep `input_audio_transcription` (still need user speech-to-text)
- Remove `output_audio_transcription` (no audio output)
- Append to system prompt:

```
LISTENER MODE ACTIVE: You are in a meeting as a silent assistant. Do NOT address
the user directly or respond conversationally. Listen to the meeting conversation
and dispatch agents when you detect questions or topics that need analysis.
When you dispatch an agent, respond with a brief text acknowledgment only
(e.g., "Dispatching Eva to check risk exposure."). Do not attempt to answer
questions yourself unless they are trivially simple.
```

When not listener mode: no changes (existing `["AUDIO"]` behavior).

**Note on Gemini TEXT mode**: Tool calls (`dispatch_agent`, `resume_agent`) and `input_audio_transcription` work identically in TEXT mode. The only difference is response delivery: text instead of audio. Turn completion semantics are the same. Mid-session modality switching is NOT supported by Gemini Live API — this is why the mode is locked at session creation.

**`_route_response()` (line ~496)**:
- Audio data path (`response.data`) won't fire in TEXT mode — no changes needed
- Text path (`response.text`, line ~536) already sends `{"type": "transcript", "speaker": "moderator", ...}` — works as-is
- Tool calls work identically in both modes

### 3. Backend — Agent Result Delivery

**`backend/app/agent_task_manager.py` — `_run_agent()` success path (line ~271)**:

When `conv_session.listener_mode`:
- **Skip** `rephrase_for_tts()` — send the full, unabridged agent result
- **Skip** `output_controller.enqueue_response()` — no TTS/audio delivery
- **Instead**, send a `transcript` JSON directly via WebSocket:
  ```json
  {"type": "transcript", "speaker": "eva", "text": "<full agent result>", "is_final": true}
  ```
- **Update** `transcript_history.append()` to store `full_text` (not `spoken_text`, which is undefined when rephrase is skipped)
- This gives the user the complete analytical response (not the shortened TTS-friendly version)

**`_handle_timeout()` (line ~367)**:

When `conv_session.listener_mode`:
- **Skip** TTS synthesis of fallback phrase
- **Instead**, send the fallback text as a `transcript` JSON:
  ```json
  {"type": "transcript", "speaker": "eva", "text": "Request timed out.", "is_final": true}
  ```

When not listener mode: no changes to either path.

**`backend/app/output_controller.py`**: No modifications. It simply receives no items in listener mode.

### 4. iOS — Mode Selection UI

**`ios/StarCall/Models/AgentState.swift` — New enum** (co-located with `SessionState`):
```swift
enum SessionMode: String {
    case talk
    case listen
}
```

**`ios/StarCall/ViewModel/ConversationViewModel.swift`**:
- Add `@Published var sessionMode: SessionMode = .talk`
- In `tapStart()`: pass `sessionMode` to `ConversationSession.start(listenerMode:)`

**`ios/StarCall/Views/ContentView.swift` — Idle screen**:
- Add a segmented picker (`Talk` / `Listen`) between the greeting text and "TAP TO START" prompt
- Styled with `StarClTheme` dark palette
- Picker disabled when session is active (mode is locked at session start)

### 5. iOS — Session Behavior in Listener Mode

**`ios/StarCall/Session/ConversationSession.swift`**:

Add a stored property: `var listenerMode: Bool = false`, set from `start(listenerMode:)`.

When `listenerMode`:
- Audio capture: works normally (mic → server)
- `AudioPlaybackEngine`: still call `playbackEngine.start()` (required for shared `AVAudioEngine` setup — capture and playback share the same engine for AEC). The playback engine initializes but incoming audio frames are **silently discarded** in the WebSocket receive handler (check `listenerMode` before routing binary frames to playback).
- Barge-in detection: disabled (no playback = no echo to detect)
- Audio gate (`gateEndTime`): disabled (no playback timing to track)
- Binary audio frames from server: silently discard (shouldn't arrive in TEXT mode, but defensive)
- Gen_id/interrupt protocol: still functional for cancelling agents via JSON

**Reauthentication**: `transportRequiresReauthentication()` creates a fresh session on reconnect. Since `listenerMode` is stored as a property on `ConversationSession`, it must be re-sent in the `httpClient.createSession(listenerMode: self.listenerMode)` call.

**iOS call chain**: `ConversationViewModel.tapStart()` → `session.start(listenerMode: sessionMode == .listen)` → `httpClient.createSession(listenerMode:)` → POST body includes `listener_mode`.

**`ios/StarCall/Views/ContentView.swift` — Active state**:
- Header tag: `"LISTENER"` or `"LISTENER · N AGENTS"` instead of `"LIVE"` / `"LIVE · N AGENTS"`
- Bottom bar: keep mute button (user may want to stop mic streaming temporarily during private sidebar conversations), mic button shows ear/waveform icon
- Agent strip: still visible with status cards (thinking/done animations)
- Transcript feed: agent results appear as regular `TranscriptLine` entries with speaker labels and full text — handled by existing `messageListView`

### 6. iOS — Transcript Display Enhancement

Agent results in listener mode can be lengthy (full analytical text vs short TTS phrases). The existing transcript feed handles this, but:
- Agent result entries get a subtle visual distinction (e.g., card background using the agent's theme color at low opacity) to differentiate from user speech transcripts
- Text is fully scrollable within the feed
- No truncation — the full response is shown

## Files Modified

### Backend (5 files)
| File | Change |
|------|--------|
| `backend/app/models.py` | Add `listener_mode: bool` to `ConversationSession` |
| `backend/app/session_manager.py` | Add `listener_mode` param to `create_session()` |
| `backend/app/routers/sessions.py` | Pydantic request model, accept `listener_mode` in POST body |
| `backend/app/gemini_proxy.py` | Conditional `TEXT` modality + listener system prompt |
| `backend/app/agent_task_manager.py` | Skip TTS, send full text transcript in listener mode (both success + timeout paths) |

### iOS (5 files)
| File | Change |
|------|--------|
| `ios/StarCall/Models/AgentState.swift` | Add `SessionMode` enum |
| `ios/StarCall/ViewModel/ConversationViewModel.swift` | Add `sessionMode` property, thread through to session |
| `ios/StarCall/Session/ConversationSession.swift` | `listenerMode` property, conditional barge-in/gate disable, discard audio frames, reauthentication support |
| `ios/StarCall/Views/ContentView.swift` | Mode picker on idle screen, listener header/UI adjustments |
| `ios/StarCall/Network/HTTPClient.swift` | Pass `listener_mode` in POST body |

## What Does NOT Change

- Audio capture pipeline (mic → server)
- WebSocket transport and frame format
- Agent dispatch (AgentTaskManager, SDKAgentRunner, A2A)
- Agent registry
- Session lifecycle management
- Output controller internals
- Codec / frame header format
- Shared AVAudioEngine startup sequence (playback engine still initializes)

## Verification

1. **Backend unit test**: Create session with `listener_mode=true`, assert Gemini config uses `TEXT` modality
2. **Backend integration test**: Listener session → send audio frames → assert no audio frames returned, only transcript JSON messages
3. **Backend test**: Agent timeout in listener mode sends text fallback (no TTS call)
4. **iOS manual test**: Toggle to "Listen" → start session → speak into mic → verify:
   - No audio plays from device
   - Moderator text appears in transcript feed
   - Agent results appear as full text in transcript feed
   - Agent status cards show thinking/done animations
5. **Edge case**: Auto-start countdown respects pre-selected listener mode
6. **Edge case**: Verify interrupt/cancel-agents still works (JSON-based, not audio-dependent)
7. **Edge case**: WebSocket reauthentication preserves listener mode
8. **Edge case**: Mute button still works in listener mode (stops mic streaming)
