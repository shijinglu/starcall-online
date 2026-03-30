# iOS UI — Backend Gaps

Features present in the NEXUS UI design that are **not currently supported** by backend endpoints.

## 1. Session History / Recent Sessions

**UI**: Idle screen shows a "RECENT SESSIONS" list with title, timestamp, and preview text for past conversations.

**Gap**: No endpoint exists to list or retrieve past sessions. The backend only supports:
- `POST /api/v1/sessions` — create a new session
- `DELETE /api/v1/sessions/{id}` — terminate a session
- `GET /api/v1/health` — health check

**Needed**: A `GET /api/v1/sessions/history` endpoint returning a list of past sessions with metadata (title/summary, timestamp, last message preview). Requires server-side session persistence beyond the current in-memory lifecycle.

## 2. History Button

**UI**: Bottom bar has a history button (counterclockwise arrow icon) for accessing past sessions.

**Gap**: Same as above — no session history retrieval endpoint. The button renders but is non-functional.

**Needed**: Same history endpoint, plus potentially a `GET /api/v1/sessions/{id}/transcript` for replaying a past session's conversation.

## 3. Mute (Partial)

**UI**: Mute button silences mic input without ending the session. Icon turns red when active.

**Current support**: Implemented client-side only — when muted, audio chunks are not sent to the server. The server is unaware of the mute state.

**Gap**: No server-side "pause" or "mute" state tracking. The existing `{"type": "control", "action": "pause"}` message type is defined in the protocol but its server-side behavior is undocumented/unverified.

**Needed**: Verify that the server handles the `pause` control action gracefully (e.g., suppresses VAD timeouts while muted). Optionally add a `resume` action.

## 4. Session Title / Auto-Summary

**UI**: Recent sessions show descriptive titles like "ACH return investigation" which imply auto-generated summaries.

**Gap**: No session summarization or title generation exists. Sessions are identified only by UUID.

**Needed**: Post-session summarization (could use Claude agent) to generate a title and preview text, stored alongside session history.
