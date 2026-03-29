# AI Conversation & Digital Agent System — High-Level Design

## 1. System Overview

A voice-first AI assistant system where users speak naturally with a fast AI moderator that can transparently delegate complex tasks to specialized deep-thinking agents running in parallel. The system supports real-time barge-in (interruption), multi-agent "meeting mode," and distinct agent voices.

**Architecture**: Hybrid synchronous/asynchronous
- **Fast path**: iOS app ↔ FastAPI backend ↔ Gemini Live API (real-time voice loop)
- **Slow path**: Backend ↔ Claude Agent SDK agents (async, non-blocking, inject results back when ready)

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          iOS Client App                             │
│   [Mic capture] ──► [WebSocketTransport] ──► [Audio Playback]       │
│                           (Swift)                                   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │  WebSocket (binary frames: audio | JSON: control)
┌─────────────────────────────▼───────────────────────────────────────┐
│                      FastAPI Backend                                │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │              Session Manager / WS Handler                   │    │
│  │         (token auth + session TTL + gen_id tracking)        │    │
│  └───────────┬──────────────────────────────┬──────────────────┘    │
│              │ audio proxy                  │ tool call dispatch    │
│  ┌───────────▼──────────┐    ┌──────────────▼────────────────────┐  │
│  │   Gemini Live API    │    │     Async Agent Task Manager      │  │
│  │  (STT + VAD + TTS    │    │  (asyncio, in-memory + TTL,       │  │
│  │   + fast moderator)  │    │   timeout, heartbeat, cancel)     │  │
│  └──────────────────────┘    └──────────┬────────────────────────┘  │
│                                         │ Claude SDK calls (stream) │
│                                ┌────────┴────────────────────────┐  │
│                                │   Deep Agents (per persona)     │  │
│                                │  Ellen / Shijing / Eva / Ming   │  │
│                                │  (Claude Agent SDK + MCP tools) │  │
│                                └────────┬────────────────────────┘  │
│                                         │ full response text        │
│                                ┌────────▼────────────────────────┐  │
│                                │   Google Cloud TTS              │  │
│                                │  (whole-message, 16kHz LINEAR16)│  │
│                                └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Backend Endpoints

### WebSocket Endpoints

| Endpoint                                      | Direction     | Purpose      |
|-----------------------------------------------|---------------|--------------|
| `WS /api/v1/conversation/live?token=<token>`  | Bidirectional | Event BUS(*) |
(*) Main real-time voice stream; carries audio frames (binary WS frames) and control/status events (JSON text frames). Requires a short-lived auth token issued by `POST /sessions`. Connection is rejected with HTTP 401 if the token is missing or expired.

**Wire format — two frame types on the same WebSocket:**

| Frame kind | Used for | Format |
|---|---|---|
| **Binary frame** | All audio: `audio_chunk`, `audio_response`, `agent_audio` | 4-byte header + raw PCM (see below) |
| **Text frame** | All control/status: `control`, `interrupt`, `transcript`, `agent_status`, `meeting_status`, `interruption`, `error` | JSON |

**Binary audio frame header (4 bytes):**
```
[1B: msg_type] [1B: speaker_id] [1B: gen_id] [1B: frame_seq] | [raw PCM bytes — 16kHz int16 LE]
```
- `msg_type`: `0x01` = audio_chunk (client→server), `0x02` = audio_response (moderator), `0x03` = agent_audio
- `speaker_id`: `0x00` = moderator/user, `0x01–0x04` = agent index (ellen/shijing/eva/ming)
- `gen_id`: server-authoritative generation counter (1 byte, 0–255); incremented on every barge-in. Client discards any frame where `frame.gen_id < current_gen`. Always `0x00` on client→server frames (client does not set gen_id).
- `frame_seq`: monotonically increasing sequence number within a generation (1 byte, wraps at 255); used for jitter detection within a stream
- Eliminates base64 overhead (~33% bandwidth saving vs. JSON encoding)

**Client → Server message types:**

| Message Type     | Frame  | Description                                                                         |
|------------------|--------|-------------------------------------------------------------------------------------|
| `audio_chunk`    | Binary | Raw PCM (16kHz int16 LE), streamed continuously at 100 ms intervals                 |
| `control`        | JSON   | Session lifecycle: `start`, `stop`, `pause`                                         |
| `interrupt`      | JSON   | User barge-in signal. Optional `mode` field: `cancel_all` (default) — cancels in-flight TTS and all running Claude tasks; `skip_speaker` — cancels only the currently-playing agent's audio and advances the Meeting Mode queue, preserving pre-computed results from other agents    |
| `agent_followup` | JSON   | Route a follow-up text turn to an existing agent session; carries `agent_session_id` and `text` |

**Server → Client message types:**

| Message Type     | Frame  | Description                                                                                              |
|------------------|--------|----------------------------------------------------------------------------------------------------------|
| `audio_response` | Binary | Moderator TTS audio (raw PCM 16kHz int16 from Gemini Live), tagged with `gen_id`                         |
| `agent_audio`    | Binary | Deep agent TTS audio (raw PCM 16kHz int16 from Google Cloud TTS), tagged with `speaker_id` and `gen_id` |
| `transcript`     | JSON   | Recognized speech text with speaker label                                                                |
| `agent_status`   | JSON   | Agent lifecycle events: `dispatched`, `thinking` (heartbeat every 10s), `done`, `timeout`, `cancelled`  |
| `meeting_status` | JSON   | Meeting Mode progress: `{total_agents, completed, pending: [...], failed: [...]}`                        |
| `interruption`   | JSON   | Server-side barge-in confirmation; client discards all frames with `gen_id` below current generation     |
| `error`          | JSON   | Error notification with message                                                                          |

**`gen_id` — zombie audio prevention:**
The backend maintains a per-session monotonically increasing `gen_id` (generation counter). It is incremented on every barge-in. All binary audio frames carry the current `gen_id` as a dedicated byte (byte index 2) in the 4-byte header; JSON status messages carry it as a separate `gen_id` field. The iOS client tracks `current_gen`; any incoming audio frame with `frame.gen_id < current_gen` is silently discarded, preventing stale audio from playing after a flush. The server is the authoritative source of `gen_id`; the client adopts `gen_id = N` from the `interruption{gen_id: N}` confirmation message.

### REST Endpoints

| Endpoint                        | Method | Purpose                                                                   |
|---------------------------------|--------|---------------------------------------------------------------------------|
| `/api/v1/sessions`              | POST   | Create a new conversation session; returns `session_id` + short-lived `auth_token` (UUID, 5-min TTL) |
| `/api/v1/sessions/{session_id}` | DELETE | Terminate a session, cancel all running agent tasks, invalidate token     |
| `/api/v1/agents`                | GET    | List available agents and their personas/capabilities                     |
| `/api/v1/health`                | GET    | Service health check                                                      |

**Auth flow:** `POST /sessions` → `{session_id, auth_token}` → client opens `WS /api/v1/conversation/live?token=<auth_token>` → backend validates token, binds WS to `session_id`, marks token consumed.

---

## 4. Backend Critical Modules

| Module | Responsibility | Interacts With |
|---|---|---|
| **WebSocket Handler** | Accept/manage client WS connections; validate auth token on handshake (reject 401 if missing/expired); dispatch binary audio frames to Gemini; dispatch JSON control events; stamp all outbound audio frames with current `gen_id` | Session Manager, Gemini Live Proxy, Agent Task Manager |
| **Session Manager** | Track active sessions (`session_id` → WS connection + Gemini session + active jobs + `gen_id` counter + `auth_token`); enforce per-session TTL (auto-cancel all agent tasks and close WS after 2 hours of inactivity or explicit stop); provide lookup for agent result delivery | WebSocket Handler, Agent Task Manager |
| **Gemini Live Proxy** | Stream client PCM to Gemini Live; receive TTS audio + tool calls; relay audio back to client as binary frames | WebSocket Handler, Agent Task Manager |
| **Agent Task Manager** | Receive dispatch requests from Gemini tool calls; spawn async Agent SDK tasks; enforce a **30-second hard timeout** per task (`asyncio.wait_for`); emit `agent_status{thinking}` heartbeat every 10 s while the agent is running; emit `agent_status{timeout}` + synthesize fallback phrase on timeout (clears `sdk_session_id` to prevent resuming a broken session); **reject `resume_agent` with `ToolResponse{error: "agent_busy"}` if `AgentSession.status == "active"`**; on `interrupt`, apply cancellation based on `mode`: `cancel_all` (default) calls `asyncio.Task.cancel()` on all running agent tasks for the session and clears the `meeting_queue`; `skip_speaker` cancels only the active TTS stream for the currently-playing agent and advances the `meeting_queue` to the next entry, preserving pre-computed results from agents that have already finished; maintain `meeting_queue` for Meeting Mode serialized audio delivery; apply session TTL cleanup | Gemini Live Proxy, SDK Agent Runner, TTS Service, Session Manager |
| **SDK Agent Runner** | Wrap Claude Agent SDK `query()` with per-agent system prompt + MCP tool server; SDK handles the agent loop (tool dispatch, streaming, retries) autonomously; wait for full response via `ResultMessage`; pass complete response text to TTS Service for whole-message synthesis; maintain `sdk_session_id` for multi-turn continuity via `resume=session_id`; append text summary to `conversation_history` for Gemini context | Agent Task Manager, MCP tool servers |
| **TTS Service** | Accept full response text from SDK Agent Runner; call Google Cloud TTS with `LINEAR16` encoding at **16 kHz**; return raw PCM chunk; pipeline is called once per agent response (whole-message), not per-sentence | Agent Task Manager |
| **Agent Registry** | Static in-memory map of agent name → `{system_prompt, voice_id, tool_set, subagents}`; loaded at startup; injected into Gemini session system prompt so the moderator knows available agent names and capabilities. Each agent's tools are exposed as an in-process MCP server via the Agent SDK. Subagent definitions (optional) allow agents to delegate sub-tasks. | Session Manager, Gemini Live Proxy, SDK Agent Runner |

**TTS Architecture — Two Distinct Paths:**

The system uses two separate TTS engines serving different roles. These are not interchangeable:

| Path | Engine | Used for | Why |
|------|--------|----------|-----|
| **Fast path (Moderator)** | Gemini Live (native) | All moderator speech: acknowledgments, simple answers, "Ellen is on it!" | Gemini Live produces TTS audio natively as part of its streaming response — zero extra latency, no additional API call |
| **Slow path (Deep Agents)** | Google Cloud TTS | All agent speech: Ellen, Shijing, Eva, Ming responses | Gemini Live cannot synthesize audio in a configurable external voice. Agent personas require distinct, assignable voice IDs (e.g., `en-US-Journey-F`). Google Cloud TTS is called once per full agent response by the TTS Service module. |

These paths produce different message types: moderator speech arrives as `audio_response` binary frames; agent speech arrives as `agent_audio` binary frames. Both use `LINEAR16` PCM at 16 kHz.

---

## 5. iOS Client Modules

| Module | Responsibility | Interacts With |
|---|---|---|
| **AudioCaptureEngine** | Configure `AVAudioSession` with `.playAndRecord` category and **`.voiceChat` mode** (enables hardware Acoustic Echo Canceller and AGC) before starting `AVAudioEngine`; downsample 44.1 kHz → 16 kHz int16 PCM; compute RMS per 100 ms chunk against an **adaptive noise floor** (EMA of quiet-period RMS, updated every 500 ms); emit barge-in signal when `RMS > noise_floor + 15 dB`; emit 100 ms audio chunks | ConversationSession |
| **AudioPlaybackEngine** | Queue and play PCM chunks (16kHz int16) via `AVAudioPlayerNode`; maintain a **per-stream FIFO queue** keyed by `speaker_id`; **discard any incoming frame with `gen_id < current_gen`** (zombie audio prevention); in Meeting Mode, play streams strictly in arrival-completion order — start the next agent's audio only after the current one finishes (`meeting_queue` discipline); expose `flushAllAndStop(newGen:)` for barge-in and `cancelStream(speakerId:)` for individual stream cancellation | ConversationSession |
| **WebSocketTransport** | Manage WS connection with exponential-backoff reconnect; send audio as **binary WebSocket frames** (4-byte header + raw PCM); send control messages as **JSON text frames**; parse inbound frame type (binary vs. text) before dispatch; include `?token=<auth_token>` in the WS URL on connect | ConversationSession |
| **ConversationSession** | Central coordinator and state machine (`idle → connecting → active → stopped`); track `current_gen` (incremented on every barge-in); wire audio engines to transport; implement dual-trigger barge-in: on local RMS trigger, increment `current_gen`, call `flushAllAndStop(newGen: current_gen)`, send `interrupt` JSON message | All three engines above, AVAudioSession |
| **ConversationViewModel** | SwiftUI binding layer; expose `@Published` state (transcript, per-agent statuses including `thinking`/`timeout`/`done`, meeting progress `meeting_status`, session state, mic amplitude) to UI | ConversationSession |

---

## 6. Data Flow Diagram

Typical flow: user asks a question that requires a deep agent.

- iOS app obtains `session_id` + `auth_token` from `POST /sessions`, then opens the WebSocket with `?token=<auth_token>`.
- Audio streams as binary WS frames (100 ms, 16kHz int16 PCM); backend proxies to Gemini Live.
- When the query requires deep analysis, Gemini emits a `dispatch_agent` tool call.
- Backend spawns an async Agent SDK task and immediately returns `ToolResponse{dispatched}` so Gemini can voice-acknowledge.
- The Agent SDK runs the agent autonomously (tool calls, reasoning) and returns a full response via `ResultMessage`. The SDK Agent Runner then sends the complete text to Google Cloud TTS for whole-message synthesis.
- The resulting PCM chunk is sent as a binary frame stamped with the current `gen_id`.
- Client plays the agent's audio at 16kHz; user hears a distinct voice per agent.

```
iOS App                  FastAPI Backend               Gemini Live       Agent SDK       Google TTS
   │                           │                            │                  │               │
   │──── POST /sessions ───────►                            │                  │               │
   │◄─── {session_id, token} ──│                            │                  │               │
   │──── WS connect(?token) ───►                            │                  │               │
   │──── [binary] audio_chunk ─►──────── PCM stream ────────►                  │               │
   │                           │◄─────── TTS audio ─────────│                  │               │
   │◄─── [binary] audio_resp ──│                            │                  │               │
   │                           │                            │                  │               │
   │  [user asks complex task] │                            │                  │               │
   │──── [binary] audio_chunk ─►──────── PCM stream ────────►                  │               │
   │                           │◄── ToolCall: dispatch_agent│                  │               │
   │                           │─── query(task, options) ───────────────────────►              │
   │                           │─── ToolResponse{dispatched}►                  │               │
   │◄─── [JSON] agent_status{dispatched} ───────────────────│                  │               │
   │◄─── [binary] audio_resp ──│◄─── TTS "Ellen is on it!" ─│                  │               │
   │                           │                            │  [agent loop     │               │
   │                           │                            │   tool calls +   │               │
   │                           │                            │   reasoning ~10s]│               │
   │                           │                            │◄─ ResultMessage ─│               │
   │                           │─── synthesize(full_text) ─────────────────────────────────────►│
   │                           │◄─── PCM (whole response) ─────────────────────────────────────│
   │◄─── [binary] agent_audio ─│   (audio after full response completes)       │               │
   │  [plays Ellen's voice]    │                            │                  │               │
```

**Barge-in sub-flow** (user speaks while audio is playing):

- `AudioCaptureEngine` detects `RMS > noise_floor + 15 dB` while audio is playing (hardware AEC prevents speaker bleed from triggering this).
- `ConversationSession` increments `current_gen`, calls `flushAllAndStop(newGen: current_gen)` on `AudioPlaybackEngine`.
- Client sends `interrupt` JSON message to the backend.
- Backend increments its session `gen_id`, applies cancellation according to `mode` (`cancel_all`: cancels all running Claude tasks and in-flight TTS, clears meeting_queue; `skip_speaker`: cancels only the current speaker's TTS stream and advances the meeting_queue, preserving results from agents that already finished), and sends `interruption{gen_id: N}` JSON to client. The client adopts the server's `gen_id` value as authoritative (taking `max(serverGen, clientGen)`).
- Any audio frames arriving at the client after the flush are discarded (`frame.gen_id < current_gen`).
- ~200 ms later Gemini Live independently detects speech and emits its own interruption event (backend forwards it; client no-ops since `gen_id` already advanced).

```
iOS App (client trigger ~0ms)           FastAPI Backend                           Gemini Live
   │                                          │                                        │
   │  RMS > noise_floor+15dB (AEC active)     │                                        │
   │  increment current_gen                   │                                        │
   │  flushAllAndStop(newGen)                 │                                        │
   │──── [JSON] interrupt ────────────────────►                                        │
   │                                          │  cancel Claude tasks + TTS             │
   │                                          │  increment session gen_id              │
   │◄─── [JSON] interruption (new gen_id) ────│                                        │
   │  discard frames with old gen_id          │  [Gemini detects speech ~200ms later]  │
   │                                          │◄───────── interruption event───────────│
   │◄─── [JSON] interruption (idempotent) ────│                                        │
   │  gen_id already advanced, no-op          │                                        │
```

---

## 7. User Flow Diagram

Typical end-to-end conversation session:

- User taps Start; the app calls `POST /sessions` to obtain a `session_id` and `auth_token`, then opens the WebSocket with the token.
- Spoken audio streams as binary frames (100 ms, 16kHz int16); Gemini Live transcribes and handles VAD.
- Simple queries are answered directly by Gemini's fast TTS and returned as binary `audio_response` frames.
- Complex tasks trigger a `dispatch_agent` tool call; backend spawns the agent async and Gemini immediately voices an acknowledgment. Backend emits `agent_status{thinking}` heartbeat every 10 s so the UI shows a spinner.
- While the agent thinks, the user can continue speaking and receive real-time Gemini responses.
- When the agent completes, the full response is synthesized via whole-message TTS and sent as `agent_audio` binary frames.
- User taps Stop; app sends `control: stop`, backend closes the session (cancels all tasks, invalidates token).

```
User                  iOS App                 Backend              AI Agents
 │                       │                       │                     │
 │── tap Start ──────────►                       │                     │
 │                       │── POST /sessions ───►►│                     │
 │                       │◄── {session_id,       │                     │
 │                       │     auth_token} ──────│                     │
 │                       │── WS connect          │                     │
 │                       │   (?token=...) ───────►                     │
 │                       │                       │                     │
 │── speak ──────────────►── [bin] audio_chunk ──►── Gemini Live ──────►
 │                       │◄── [JSON] transcript  │  (STT + VAD)        │
 │                       │                       │                     │
 │  [simple query]       │                       │                     │
 │                       │◄── [bin] audio_resp ──│◄── Gemini TTS ──────│
 │◄── hear response ─────│                       │                     │
 │                       │                       │                     │
 │  [complex task]       │                       │                     │
 │── speak ──────────────►── [bin] audio_chunk ►── Gemini Live ──────►
 │                       │◄── [JSON] "dispatched"│  tool_call ───────►── dispatch_agent
 │◄── hear "on it" ──────│◄── [bin] audio_resp ──│                     │
 │                       │◄── [JSON] status{thinking} every 10s       │── agent loop ────►
 │  [spinner: Ellen…]    │                      │                     │
 │  [continue chatting]  │                      │                     │
 │── speak ──────────────►── [bin] audio_chunk ─►── handles directly  │
 │◄── hear response ─────│                      │                     │
 │                       │                      │◄── ResultMessage ───│
 │                       │                      │   synth(full_text)  │
 │◄── hear Ellen ────────│◄── [bin] agent_audio─│   (whole-message)   │
 │                       │                      │                     │
 │── tap Stop ───────────►── [JSON] ctrl:stop ─►── close session ─────│
 │                       │── DELETE /session ──►  cancel tasks        │
```

**Meeting mode** (multiple agents dispatched in parallel): agents compute concurrently, but audio is delivered sequentially. The `Agent Task Manager` places completed agent results in a FIFO `meeting_queue`. The backend starts sending each agent's audio only after the previous one finishes. A `meeting_status` JSON event tracks progress (`{total_agents: 4, completed: 2, pending: ["shijing", "eva"], failed: []}`).

---

## 8. Gemini Moderator Tool Calls

The Gemini moderator has two tools available via function calling. Their definitions are injected into the Gemini session system prompt at session start, along with the full agent roster from the Agent Registry.

| Tool | Parameters | When Used |
|------|-----------|-----------|
| `dispatch_agent` | `name: str`, `task: str` | First turn to a named agent; creates a new agent session, returns `agent_session_id` |
| `resume_agent` | `agent_session_id: str`, `follow_up: str` | Subsequent turns to the same agent; returns error if agent is still active |

**`resume_agent` busy policy:** If `AgentSession.status == "active"` (Claude is still processing a prior task), the Agent Task Manager immediately returns `ToolResponse{error: "agent_busy", message: "Ellen is still working on your previous request"}`. Gemini voices this to the user. Silent drops and queuing are not used — behavior is deterministic and audible.

**Agent roster injection** — when a Gemini Live session starts, the backend calls `GET /api/v1/agents` and appends a roster block to the Gemini system prompt:

```
Available agents:
- ellen: personal assistant, calendar/email/tasks tools, voice: en-US-Journey-F
- shijing: user risk analyst, user profile + journey tools, voice: en-US-Journey-D
- eva: financial analyst, transaction + bank data tools, voice: en-US-Journey-O
- ming: fraud investigator, ID check + async risk tools, voice: en-US-Neural2-D

Use dispatch_agent(name, task) for first contact.
Use resume_agent(agent_session_id, follow_up) for follow-up turns to an idle agent.
If resume_agent returns agent_busy, inform the user and try again shortly.
```

---

## 9. Multi-Turn Agent Flow (Use Case 6)

Follow-up questions are routed to the same agent session rather than spawning a new one.

- First user turn causes Gemini to call `dispatch_agent(ellen, task)`; Agent Task Manager spawns a new session with id `A1`.
- Agent SDK runs the agent autonomously (tool calls, reasoning). The SDK Agent Runner waits for the full `ResultMessage`, then synthesizes whole-message TTS.
- While the agent runs, the backend emits `agent_status{thinking, elapsed_ms}` heartbeat every 10 s.
- If no result within 30 s, the task is cancelled: `agent_status{timeout}` is sent, `sdk_session_id` is cleared (to prevent resuming a broken session), and a fallback phrase is synthesized.
- User asks a follow-up; Gemini calls `resume_agent(A1, follow_up)`.
  - If `A1.status == "idle"`: Agent Task Manager re-invokes the SDK Agent Runner with `resume=A1.sdk_session_id`, which continues the agent with full context preserved by the SDK session.
  - If `A1.status == "active"`: immediately return `ToolResponse{error: "agent_busy"}`.
- The agent responds faster (~5 s) because the SDK session preserves full prior context; whole-message TTS fires on the complete response.
- Session state is held in-memory by the Agent Task Manager with a **2-hour TTL**; the cleanup task cancels orphaned asyncio tasks when TTL expires.

```
iOS App          FastAPI Backend          Gemini Live         Agent Task Mgr     Agent SDK (Ellen)
   │                    │                      │                     │                   │
   │── [bin] audio ─────►──── PCM ─────────────►                     │                   │
   │                    │◄── ToolCall:          │                    │                   │
   │                    │    dispatch_agent     │                    │                   │
   │                    │    (ellen, task) ──────────────────────────►                   │
   │                    │                       │             spawn_session(ellen)       │
   │                    │                       │             agent_session_id=A1 ───────► query(task)
   │                    │◄── ToolResponse{A1} ──│                    │                   │
   │◄── [JSON] status{dispatched} ─────────────│                     │   [agent loop]    │
   │◄── [bin] audio_resp│◄── "Ellen is on it" ──│                    │   [tool calls +   │
   │◄── [JSON] status{thinking, 10s} ──────────│                     │    reasoning]     │
   │                    │                       │                    │◄── ResultMessage ─│
   │                    │                       │             synth(full_text)→PCM→[bin] │
   │◄── [bin] agent_aud─│◄── PCM (whole msg) ───────────────────────│                   │
   │◄── [JSON] status{done} ───────────────────│                     │                   │
   │                    │                       │                    │                   │
   │  [user follow-up]  │                       │                    │                   │
   │── [bin] audio ─────►──── PCM ─────────────►                     │                   │
   │                    │◄── ToolCall:          │                    │                   │
   │                    │    resume_agent       │                    │                   │
   │                    │    (A1, follow_up) ────────────────────────►                   │
   │                    │                       │  [status==idle]    │                   │
   │                    │                       │  resume=sdk_sess_id│                   │
   │                    │                       │  query(follow_up) ──────────────────────► (resume)
   │                    │◄── ToolResponse{ok} ──│                    │   [~5s]           │
   │◄── [bin] audio_resp│◄── "checking..." ─────│                    │◄── ResultMessage ─│
   │◄── [bin] agent_aud─│◄── PCM (whole msg) ───────────────────────│                   │
```

**Agent session state** held in `Agent Task Manager` (in-memory, keyed by `agent_session_id`, TTL = 2 hours):

```python
AgentSession:
    agent_session_id: str          # UUID v4
    agent_name: str                # e.g. "ellen"
    sdk_session_id: str | None     # Agent SDK session ID for resume (cleared on timeout)
    conversation_history: list     # [{role, content}, ...] — text summary for Gemini context only
    status: "active" | "idle" | "cancelled" | "timeout"
    claude_task: asyncio.Task      # cancelled on interrupt or TTL expiry
    parent_session_id: str         # links back to the user's conversation session
    created_at: float              # epoch; TTL enforced by periodic cleanup task
```

**Dual conversation state:** `sdk_session_id` points to the Agent SDK's internal session which holds the full conversation context (tool calls, reasoning, history). `conversation_history` is a lightweight text-only summary fed back to the Gemini moderator so it knows what agents said. These serve different purposes and are not redundant.
