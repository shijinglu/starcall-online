# AI Conversation & Digital Agent System — High-Level Test Design

## Overview

This document covers integration test design across three granularity levels. Tests focus on **data and user flow coverage** rather than code coverage. Level 3 (component) tests are preferred; Level 1 (full-stack) tests are reserved for flows that require end-to-end verification through the iOS UI.

**External dependencies assumed available at all levels:** Gemini Live API credentials, Claude Agent SDK credentials (`ANTHROPIC_API_KEY`), Google Cloud TTS credentials, Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`).

---

## Level 1: Full-Stack Tests (iOS App + Backend)

*Setup: iOS app running (emulator or device), backend service up. Tooling: `mobile-mcp` for UI automation, `curl` scripts for backend REST, log inspection.*

### L1-01 — Simple Voice Query End-to-End

**What it covers:** Full audio capture → STT → Gemini response → TTS → playback path; session lifecycle (start/stop).

**Setup:** App launched, backend healthy (`GET /health` returns 200).

**Operation:**
1. `mobile-mcp`: tap "Start" button.
2. Verify via log that `POST /sessions` was called and WS connected.
3. `mobile-mcp`: speak (or inject audio) a simple factual question (e.g., "What is 2 + 2?").
4. Wait up to 5 s for audio playback to complete.
5. `mobile-mcp`: tap "Stop".

**Verify:**
- App shows transcript of the spoken question.
- App plays back a voice response (playback engine receives at least one `audio_response` binary frame).
- No `agent_status` events appear (no agent was dispatched).
- `DELETE /sessions/{id}` is called on stop; subsequent WS send returns closed-connection error.

---

### L1-02 — Complex Query Triggers Agent Dispatch

**What it covers:** `dispatch_agent` tool call path; `agent_status{dispatched}` → `agent_status{thinking}` → `agent_audio` playback; distinct agent voice heard.

**Setup:** Same as L1-01.

**Operation:**
1. Start session.
2. Ask a question phrased to require deep analysis (e.g., "Ellen, analyze my spending patterns for this month and give me a risk summary.").
3. Wait up to 40 s for agent audio to complete.
4. Stop session.

**Verify:**
- UI shows a "thinking" spinner for the dispatched agent within 2 s of the voice query.
- Gemini's acknowledgment phrase (e.g., "Ellen is on it!") is heard before agent audio.
- Agent audio plays in a distinct voice from the moderator.
- `agent_audio` binary frames are received (whole-message TTS confirmed).
- `agent_status{done}` event appears in logs after playback ends.
- No moderator audio overlaps with agent audio during playback.

---

### L1-03 — Barge-In During Agent Audio Playback

**What it covers:** Client-side RMS barge-in detection; `interrupt` message; `gen_id` advance; stale audio discard; backend task cancellation.

**Setup:** Same as L1-01.

**Operation:**
1. Start session; issue complex query to trigger agent (same as L1-02).
2. While agent audio is playing (within first 5 s), `mobile-mcp` injects a loud audio chunk (or speaks a new question).
3. Wait for new Gemini response.

**Verify:**
- Agent audio stops within ~200 ms of the injected speech.
- An `interrupt` JSON frame is sent to backend (confirm in WS logs).
- Backend responds with `interruption` JSON carrying a new `gen_id`.
- No further agent audio frames from the old generation play.
- A new Gemini response audio begins promptly.
- Backend log shows Claude tasks were cancelled.

---

### L1-04 — Meeting Mode (Multiple Agents in Parallel)

**What it covers:** Multiple `dispatch_agent` calls in one turn; `meeting_status` progress events; sequential audio delivery; all agents heard in order.

**Setup:** Same as L1-01.

**Operation:**
1. Start session.
2. Issue a query that should trigger all 4 agents (e.g., "Everyone, what's my risk profile?").
3. Wait up to 90 s for all agents to finish.
4. Stop.

**Verify:**
- `meeting_status` JSON events appear showing `{total_agents: 4, completed: N, pending: [...]}` progressing over time.
- Agents' audio plays **sequentially**, not concurrently (verify by timestamps: next agent starts only after previous finishes).
- All 4 agent voices are heard.
- `meeting_status{completed: 4, pending: [], failed: []}` appears at the end.

---

### L1-05 — Session Expiry / Network Drop Recovery

**What it covers:** WS reconnect with exponential backoff; session invalidation after explicit stop.

**Operation:**
1. Start session, confirm WS connected.
2. Kill backend process briefly (5 s), then restart.
3. Observe app behavior.
4. Restart backend; wait for reconnect.

**Verify:**
- App does not crash; UI shows a "reconnecting" state.
- After backend restarts, app attempts to reconnect (WS logs show reconnect attempt).
- On reconnect with a consumed/expired token, app re-authenticates (`POST /sessions`) or shows appropriate error.

---

## Level 2: Backend-Only Integration Tests

*Setup: Backend service running, no iOS app. Tooling: `curl`/`httpx` scripts, Python WS client, log inspection. Audio input is injected as raw PCM files.*

### L2-01 — Session Lifecycle REST API

**What it covers:** `POST /sessions`, `GET /health`, `DELETE /sessions/{id}`; token issuance and invalidation.

**Operation:**
```
POST /api/v1/sessions → expect {session_id, auth_token}
GET  /api/v1/health   → expect 200 OK
DELETE /api/v1/sessions/{session_id} → expect 200
POST /api/v1/sessions/{same_id} again → already deleted, irrelevant
```
Re-use the consumed token to open a WS → expect HTTP 401.

**Verify:**
- `auth_token` is a UUID, `session_id` is non-empty.
- WS with consumed/expired token returns 401.
- After DELETE, a second DELETE returns 404.

---

### L2-02 — WebSocket Auth Token Validation

**What it covers:** Token binding; rejection paths.

**Cases:**
1. WS with valid unused token → accept (101 Upgrade).
2. WS with no token → reject 401.
3. WS with malformed token → reject 401.
4. WS with already-consumed token (same session opened twice) → reject 401.

**Verify:** HTTP status codes match expectations in all four cases.

---

### L2-03 — Binary Frame Routing: Audio Chunk to Gemini

**What it covers:** Binary frame parsing (4-byte header); PCM proxying to Gemini Live; transcript JSON response.

**Operation:**
1. Open WS with valid token.
2. Send binary frames: `[0x01, 0x00, seq] + 100ms PCM` (pre-recorded audio of a simple question) in a loop.
3. Wait for response frames.

**Verify:**
- Server emits `transcript` JSON frames with recognized text.
- Server emits `audio_response` binary frames (moderator TTS).
- All outbound binary frames carry consistent `gen_id` in their header.

---

### L2-04 — Agent Dispatch via Simulated Tool Call

**What it covers:** `dispatch_agent` flow; `agent_status{dispatched}` and `agent_status{thinking}` heartbeat; `agent_audio` binary frames.

**Operation:**
1. Open WS.
2. Inject audio of a complex query designed to trigger agent dispatch (pre-recorded PCM).
3. Alternatively: directly POST a JSON tool-call event to a test endpoint if one exists, or verify by sending audio and observing Gemini's tool call behavior.

**Verify:**
- Within 2 s of dispatch: `agent_status{dispatched, agent_name, agent_session_id}` JSON received.
- Within 12 s: at least one `agent_status{thinking, elapsed_ms}` heartbeat received.
- `agent_audio` binary frames arrive; `speaker_id` matches the dispatched agent index.
- `agent_status{done}` received after all audio frames.

---

### L2-05 — Agent Timeout (30 s Hard Limit)

**What it covers:** `asyncio.wait_for` timeout; `agent_status{timeout}`; fallback TTS phrase.

**Operation:**
Dispatch an agent with a task designed to take >30 s (or mock Claude to hang). Wait 35 s.

**Verify:**
- `agent_status{timeout}` JSON received at approximately 30 s.
- A fallback `agent_audio` frame is received (synthesized fallback phrase).
- No further agent audio frames arrive after timeout.
- Backend log confirms the asyncio task was cancelled.

---

### L2-06 — Resume Agent (Idle → Continues with History)

**What it covers:** `resume_agent` tool call on an idle session; history preservation.

**Operation:**
1. Dispatch an agent; wait for `status{done}`.
2. Send a follow-up audio that triggers `resume_agent(agent_session_id, follow_up)`.
3. Wait for second response.

**Verify:**
- Second response arrives without re-dispatching a new session (same `agent_session_id` in status events).
- Response time is faster than the first turn (~5 s target).
- `agent_audio` frames from the second turn are received and marked with the same `speaker_id`.

---

### L2-07 — Resume Agent (Busy → Error Response)

**What it covers:** `agent_busy` error path; deterministic behavior when Claude is still active.

**Operation:**
1. Dispatch agent with a slow task.
2. While `status == active` (within first 5 s), send `resume_agent` for the same session.

**Verify:**
- Backend returns `ToolResponse{error: "agent_busy"}` immediately (no queuing).
- Gemini voices the busy message to the client.
- Original agent task continues uninterrupted.

---

### L2-08 — Interrupt Modes: cancel_all and skip_speaker

**What it covers:** `interrupt` JSON message with `mode` field; `gen_id` increment; Claude task cancellation; Meeting Mode queue preservation; stale frame rejection policy.

**Operation — Part A (`cancel_all`):**
1. Open WS; dispatch an agent in Meeting Mode (all 4 agents).
2. While agents are running (`status{thinking}`), send `{"type":"interrupt","mode":"cancel_all"}`.
3. Continue sending new audio chunks.

**Verify (cancel_all):**
- Backend emits `interruption` JSON with a new (incremented) `gen_id`.
- All running Claude tasks are cancelled (confirmed in backend logs).
- `meeting_queue` is cleared — no further `agent_audio` frames arrive.
- New Gemini-handled audio response arrives promptly.

**Operation — Part B (`skip_speaker`):**
1. Open WS; trigger Meeting Mode (all 4 agents). Wait for agents to finish computing (all `status{done}` received). First agent's audio begins playing.
2. While first agent's audio is playing, send `{"type":"interrupt","mode":"skip_speaker"}`.

**Verify (skip_speaker):**
- First agent's audio stream stops immediately.
- `gen_id` is incremented; no further frames from the first agent play.
- Second agent's audio begins promptly (pre-computed result preserved).
- No Claude tasks are cancelled (confirmed in backend logs — agents already done).
- `meeting_status` continues to progress normally.

---

### L2-09 — gen_id Stamping Consistency

**What it covers:** `gen_id` on all outbound binary frames; increment on each interrupt.

**Operation:**
1. Open WS; note initial `gen_id` from first audio frame (byte index 2 of the 4-byte header: `frame[2]`).
2. Send two interrupt messages in sequence.
3. Observe `gen_id` on subsequent frames.

**Verify:**
- `gen_id` on binary audio frames increments exactly once per interrupt message.
- All frames within a generation carry the same `gen_id`.
- `interruption` JSON events report the new `gen_id` matching subsequent frame headers.

---

### L2-10 — Meeting Mode Sequential Audio Delivery

**What it covers:** `meeting_queue` FIFO discipline; `meeting_status` progress; per-agent `speaker_id` labeling.

**Operation:**
1. Trigger a multi-agent query (all 4 agents).
2. Record timestamps of all received `agent_audio` frames and `meeting_status` events.

**Verify:**
- Agent audio streams do not overlap: agent N+1 first frame timestamp > agent N last frame timestamp.
- `meeting_status` `completed` count increments monotonically.
- Each agent's audio frames carry the correct `speaker_id` (0x01–0x04).
- Final `meeting_status` shows `{total_agents: 4, completed: 4, pending: [], failed: []}`.

---

### L2-11 — Session TTL / Cleanup

**What it covers:** 2-hour TTL auto-cancellation; orphaned asyncio task cleanup.

**Operation (accelerated):** Configure TTL to a short value (e.g., 10 s) in test mode. Open session, dispatch agent, then wait for TTL to expire without sending any messages.

**Verify:**
- After TTL: WS is closed by the server.
- Backend log shows all asyncio tasks for the session were cancelled.
- `GET /api/v1/sessions/{id}` (if exists) returns 404.
- No resource leaks (task count in logs returns to zero).

---

### L2-12 — Agent List Endpoint

**What it covers:** `GET /api/v1/agents`; Agent Registry content.

**Operation:** `curl GET /api/v1/agents`

**Verify:**
- Response includes all 4 agents: `ellen`, `shijing`, `eva`, `ming`.
- Each entry includes `voice_id` and a non-empty `tool_set` or capability description.

---

## Level 3: Component Tests (No iOS/Backend, Real External APIs)

*Setup: No running services. Tests call external APIs directly using SDK/HTTP clients. Credentials injected via environment variables.*

### L3-01 — Gemini Live API: STT + VAD + TTS Round-Trip

**What it covers:** Gemini Live connectivity; speech-to-text; text-to-speech; VAD end-of-utterance detection.

**Operation:**
Send a pre-recorded 16kHz int16 PCM file of a short question directly to the Gemini Live API using the SDK. Collect all response events.

**Verify:**
- API returns a transcript matching the spoken question (fuzzy match acceptable).
- API returns a TTS audio blob in the expected format.
- VAD end-of-utterance event fires within 2 s of silence.
- No API authentication errors.

---

### L3-02 — Gemini Live API: Tool Call Emission

**What it covers:** `dispatch_agent` tool call schema; Gemini produces correct function call when prompted with a complex query; tool definition injection works.

**Operation:**
Start a Gemini Live session with the agent roster injected in the system prompt (same format as Section 8 of the design). Send a text turn (or audio) asking a complex analytical question. Inspect the response for tool calls.

**Verify:**
- Gemini emits a `dispatch_agent` function call with `name` and `task` fields.
- `name` is one of: `ellen`, `shijing`, `eva`, `ming`.
- `task` is a non-empty string describing the work.
- No hallucinated tool names are used.

---

### L3-03 — Gemini Live API: resume_agent Tool Call

**What it covers:** `resume_agent` schema; Gemini uses the correct tool for follow-up turns.

**Operation:**
1. Same setup as L3-02; complete a first agent dispatch turn.
2. Send a follow-up text turn ("What about last month?" directed at same agent).

**Verify:**
- Gemini emits `resume_agent` with `agent_session_id` matching the one returned from the first turn and a non-empty `follow_up`.
- Gemini does NOT emit a second `dispatch_agent` for the same agent.

---

### L3-04 — Agent SDK: Full Response via query()

**What it covers:** Agent SDK `query()` function; `ResultMessage` with full response; MCP tool integration.

**Operation:**
Invoke `query()` with Ellen's system prompt, an MCP server wrapping Ellen's tools, and a sample analytical task. Iterate messages until `ResultMessage` is received.

**Verify:**
- `SystemMessage` with `subtype == "init"` is received first, containing a valid `session_id`.
- `ResultMessage` with `subtype == "success"` is received at completion.
- `ResultMessage.result` contains a non-empty, coherent response.
- If the task requires tool calls, `AssistantMessage` blocks with `ToolUseBlock` content are observed before the `ResultMessage`.
- Total elapsed time is reasonable (< 30 s for a simple task).

---

### L3-05 — Agent SDK: Session Resume Continuity

**What it covers:** Multi-turn context preservation via `resume=session_id`.

**Operation:**
1. Invoke `query()` with Shijing's persona + turn 1 ("What's the user's risk score?"). Capture `session_id` from `SystemMessage`.
2. Invoke `query()` again with `resume=session_id` + turn 2 ("Why is it that value?").

**Verify:**
- Turn 2 response references context from turn 1 without re-asking for the same information.
- Response coherence indicates session state was correctly preserved by the SDK.
- No manual conversation history management is needed.

---

### L3-06 — Agent SDK: 30-Second Timeout and Subprocess Cleanup

**What it covers:** Timeout propagation to Agent SDK subprocess; graceful cleanup; `sdk_session_id` invalidation.

**Operation:**
Wrap `query()` in `asyncio.wait_for(timeout=30)`. Use a prompt that produces a very long response (or triggers many tool calls). Cancel after timeout. Then attempt a new `query()` with the same `resume=session_id`.

**Verify:**
- `asyncio.TimeoutError` is raised at approximately 30 s.
- The `query()` async generator is properly closed (`aclose()`) and no orphaned subprocess remains.
- A subsequent `query()` without `resume` (fresh session) works correctly.
- A subsequent `query()` with `resume=old_session_id` either works or fails gracefully (no crash).

---

### L3-07 — Google Cloud TTS: Whole-Message Synthesis

**What it covers:** TTS API call with `LINEAR16` encoding at 16 kHz; whole-message synthesis; audio format correctness.

**Operation:**
Call Google Cloud TTS with 3 sample texts of varying length (short sentence, medium paragraph, long multi-paragraph response), using the voice IDs for all 4 agents (`en-US-Journey-F`, `en-US-Journey-D`, `en-US-Journey-O`, `en-US-Neural2-D`).

**Verify:**
- Each API call returns raw PCM bytes with correct format (16 kHz int16 LE).
- Whole-message synthesis completes within reasonable time (< 5 s for typical agent responses).
- Audio duration is proportional to text length (sanity check).
- All 4 voice IDs produce distinct non-empty audio.

---

### L3-08 — Agent Persona System Prompt and MCP Tool Isolation

**What it covers:** Per-agent system prompt injection; MCP tool scoping (each agent only has access to its own MCP server's tools).

**Operation:**
Invoke `query()` with each agent's system prompt and MCP tool server (ellen / shijing / eva / ming). Ask each agent to perform a task belonging to a *different* agent's domain (e.g., ask Ellen to run a fraud ID check).

**Verify:**
- Agent declines or expresses inability to perform out-of-scope tasks.
- Agent does not hallucinate tools it does not have (only `mcp__<agent>_tools__*` tools are available).
- Each agent's response tone/persona matches its declared description.

---

### L3-09 — Binary Frame Header Encode/Decode

**What it covers:** 4-byte binary header format; `msg_type`, `speaker_id`, `seq_num` fields; `gen_id` encoding.

**Operation:**
Unit test (pure Python, no external services): encode a set of frames with known header values using the 4-byte layout `[msg_type][speaker_id][gen_id][frame_seq]`, then decode and verify round-trip. Test edge cases: `frame_seq` wrap at 255, `gen_id` values 0x00–0xFF, `speaker_id` values 0x00–0x04, `msg_type` 0x01/0x02/0x03.

**Verify:**
- All field values are preserved exactly after encode → decode.
- `frame_seq` wraps from 255 to 0 without error.
- Frames with unknown `msg_type` are rejected or flagged.

---

### L3-10 — MCP Tool Wrapper Correctness

**What it covers:** `@tool` decorator wrappers correctly delegate to underlying tool functions; MCP server creation.

**Operation:**
Pure unit test: call each `@tool`-decorated function in `tools/mcp_servers.py` directly with sample arguments. Verify the wrapper delegates to the underlying tool function and returns correctly formatted MCP content.

**Test cases:**
- `user_profile_read({"user_id": "test"})` → returns `{"content": [{"type": "text", "text": "<json>"}]}`.
- `calendar_read({"date": "today"})` → returns valid MCP content format.
- Each of the 12 tool wrappers produces well-formed `content` array.
- `AGENT_MCP_SERVERS` dict contains entries for all 4 agents.

**Verify:** All wrappers return MCP-compliant content blocks; no exceptions raised.

---

## Coverage Summary

| Flow from Design | L1 | L2 | L3 |
|---|---|---|---|
| Session create / auth / destroy | L1-01 | L2-01, L2-02 | — |
| Simple query (Gemini handles) | L1-01 | L2-03 | L3-01 |
| Agent dispatch (complex query) | L1-02 | L2-04 | L3-02, L3-04 |
| Agent SDK query() + ResultMessage | L1-02 | L2-04 | L3-04 |
| Agent timeout (30 s) + subprocess cleanup | — | L2-05 | L3-06 |
| Multi-turn resume (SDK session) | — | L2-06 | L3-05 |
| Resume busy → error | — | L2-07 | — |
| Barge-in / interrupt | L1-03 | L2-08 | — |
| gen_id zombie audio prevention | L1-03 | L2-09 | L3-09 |
| Meeting mode parallel + sequential | L1-04 | L2-10 | — |
| Session TTL cleanup | — | L2-11 | — |
| Agent roster / personas + MCP tool isolation | — | L2-12 | L3-08 |
| Gemini tool call schema | — | — | L3-02, L3-03 |
| TTS whole-message / per-voice | L1-02 | L2-04 | L3-07 |
| MCP tool wrapper correctness | — | — | L3-10 |
| WS reconnect / network drop | L1-05 | — | — |
