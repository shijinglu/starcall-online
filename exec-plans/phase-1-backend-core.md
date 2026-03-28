# Phase 1 — Backend Core: Session Manager, WebSocket Handler & REST API

## Goal
Build the skeleton FastAPI backend: session lifecycle management, WebSocket connection handling with binary/JSON frame routing, auth token validation, and all REST endpoints. No Gemini or Claude integration yet — external calls are stubbed.

---

## 1.1 Session Manager

### Responsibilities
- Maintain an in-memory dict `{session_id → ConversationSession}`.
- Issue single-use auth tokens with a 5-minute TTL.
- Track `gen_id` per session (authoritative counter for zombie-audio prevention).
- Run a background `asyncio` cleanup task every 60 seconds that:
  - Expires sessions that have exceeded `session_ttl` (2 hours) since last activity.
  - Cancels all `asyncio.Task` objects in `AgentSession.claude_task` for expired sessions.
  - Closes the associated WebSocket connection.
  - Removes the session from the dict.

### Key Operations

```python
class SessionManager:
    async def create_session() -> ConversationSession:
        """Allocate new session, generate auth_token, store in dict."""

    def validate_token(token: str) -> Optional[ConversationSession]:
        """Check token exists, not expired, not consumed. Return session or None."""

    def consume_token(session: ConversationSession) -> None:
        """Mark token as consumed. Second use returns None from validate_token."""

    def get_session(session_id: str) -> Optional[ConversationSession]:
        """Look up by session_id."""

    async def terminate_session(session_id: str) -> bool:
        """Cancel all agent tasks, close WS, remove from dict. Return False if not found."""

    def touch(session_id: str) -> None:
        """Update last_activity timestamp to extend TTL window."""

    def increment_gen_id(session_id: str) -> int:
        """Atomically increment gen_id (wrapping at 255). Return new value."""

    async def _cleanup_loop(self) -> None:
        """Background task: scan sessions every 60s, terminate expired ones."""
```

### Concurrency
- All session mutations happen inside `asyncio` event loop (single-threaded).
- No locks needed if the entire backend runs in a single event loop.
- `asyncio.Task.cancel()` is safe to call from the same loop.

---

## 1.2 WebSocket Handler

### Endpoint
```
WS /api/v1/conversation/live?token=<auth_token>
```

### Connection Lifecycle

```
1. HTTP Upgrade request arrives with ?token=<auth_token>
2. Extract token from query string
3. Call SessionManager.validate_token(token)
   → If invalid/expired/consumed: reject with HTTP 401 (before upgrade)
4. SessionManager.consume_token(session)
5. Bind WebSocket to session (session.ws_connection = ws)
6. Start bidirectional read/write loop
7. On close/disconnect: call SessionManager.terminate_session()
```

### Frame Routing (receive loop)

```python
async def ws_receive_loop(ws, session):
    async for message in ws:
        if isinstance(message, bytes):
            await handle_binary_frame(message, session)
        elif isinstance(message, str):
            await handle_json_frame(message, session)

async def handle_binary_frame(data: bytes, session):
    msg_type, speaker_id, gen_id, frame_seq, pcm = decode_frame(data)
    if msg_type == MsgType.AUDIO_CHUNK:
        await gemini_proxy.send_audio(session, pcm)
    else:
        await send_error(session, "INTERNAL", f"Unexpected client msg_type: {msg_type:#04x}")

async def handle_json_frame(raw: str, session):
    msg = json.loads(raw)
    match msg.get("type"):
        case "control":   await handle_control(msg, session)
        case "interrupt": await handle_interrupt(msg, session)
        case "agent_followup":
            # Fix 8: handle agent_followup to allow user text follow-ups to idle agents
            agent_session = session.agent_sessions.get(msg.get("agent_session_id"))
            if agent_session and agent_session.status == "idle":
                await agent_task_manager.resume(session, agent_session, msg["text"])
            elif agent_session and agent_session.status == "active":
                await send_json(session, {"type": "error", "code": "AGENT_BUSY",
                                          "message": "Agent is still working"})
            else:
                await send_json(session, {"type": "error", "code": "SESSION_NOT_FOUND",
                                          "message": "No such agent session"})
        case _:           await send_error(session, "INTERNAL", f"Unknown message type: {msg['type']}")
```

### Sending Frames

All outbound binary audio frames **must** stamp `gen_id` from the session:

```python
async def send_audio_response(session, pcm: bytes, frame_seq: int):
    frame = encode_frame(MsgType.AUDIO_RESPONSE, SpeakerId.MODERATOR,
                         session.gen_id, frame_seq, pcm)
    await session.ws_connection.send(frame)

async def send_agent_audio(session, agent_name: str, pcm: bytes, frame_seq: int):
    sid = AGENT_SPEAKER_IDS[agent_name]
    frame = encode_frame(MsgType.AGENT_AUDIO, sid, session.gen_id, frame_seq, pcm)
    await session.ws_connection.send(frame)

async def send_json(session, payload: dict):
    await session.ws_connection.send(json.dumps(payload))
```

### Interrupt Handling

```python
async def handle_interrupt(msg: dict, session: ConversationSession):
    mode = msg.get("mode", "cancel_all")
    new_gen = session_manager.increment_gen_id(session.session_id)
    await agent_task_manager.handle_interrupt(session, mode)
    await send_json(session, {"type": "interruption", "gen_id": new_gen})
```

---

## 1.3 REST Endpoints

### `POST /api/v1/sessions`

```python
@router.post("/api/v1/sessions", status_code=200)
async def create_session():
    session = await session_manager.create_session()
    return {
        "session_id": session.session_id,
        "auth_token": session.auth_token,
        "expires_at": datetime.utcfromtimestamp(session.token_expires_at).isoformat() + "Z"
    }
```

### `DELETE /api/v1/sessions/{session_id}`

```python
@router.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    ok = await session_manager.terminate_session(session_id)
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"status": "terminated"}
```

### `GET /api/v1/agents`

```python
@router.get("/api/v1/agents")
def list_agents():
    return {"agents": agent_registry.list_all()}
```

### `GET /api/v1/health`

```python
@router.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "active_sessions": session_manager.count()
    }
```

---

## 1.4 Agent Registry

Static configuration loaded at startup. No dynamic updates in v1.

```python
AGENT_REGISTRY = {
    "ellen": AgentRegistryEntry(
        name="ellen",
        description="Personal assistant — calendar, email, tasks",
        voice_id="en-US-Journey-F",
        speaker_id=1,
        system_prompt=ELLEN_SYSTEM_PROMPT,   # loaded from prompts/ellen.txt
        tool_set=["calendar_read", "email_send", "task_list"],
    ),
    "shijing": AgentRegistryEntry(
        name="shijing",
        description="User risk analyst — user profile and journey",
        voice_id="en-US-Journey-D",
        speaker_id=2,
        system_prompt=SHIJING_SYSTEM_PROMPT,
        tool_set=["user_profile_read", "user_journey_read", "risk_score_read"],
    ),
    "eva": AgentRegistryEntry(
        name="eva",
        description="Financial analyst — transactions and bank data",
        voice_id="en-US-Journey-O",
        speaker_id=3,
        system_prompt=EVA_SYSTEM_PROMPT,
        tool_set=["transaction_read", "bank_data_read", "chargeback_read"],
    ),
    "ming": AgentRegistryEntry(
        name="ming",
        description="Fraud investigator — ID checks and async risk",
        voice_id="en-US-Neural2-D",
        speaker_id=4,
        system_prompt=MING_SYSTEM_PROMPT,
        tool_set=["id_check", "async_risk_check", "fraud_signal_read"],
    ),
}
```

The Gemini system prompt roster block is built from this registry at session start:

```python
def build_agent_roster_block(registry: dict) -> str:
    lines = ["Available agents:"]
    for entry in registry.values():
        lines.append(f"- {entry.name}: {entry.description}, voice: {entry.voice_id}")
    lines += [
        "",
        "Use dispatch_agent(name, task) for first contact.",
        "Use resume_agent(agent_session_id, follow_up) for follow-up turns to an idle agent.",
        "If resume_agent returns agent_busy, inform the user and try again shortly.",
    ]
    return "\n".join(lines)
```

---

## 1.5 Gemini Tool Call Schemas (Function Declarations)

These are injected into the Gemini Live session at start time.

```python
DISPATCH_AGENT_TOOL = {
    "name": "dispatch_agent",
    "description": "Delegate a task to a named deep-thinking agent. Use for first contact.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": ["ellen", "shijing", "eva", "ming"],
                "description": "Agent to dispatch"
            },
            "task": {
                "type": "string",
                "description": "Full task description for the agent"
            }
        },
        "required": ["name", "task"]
    }
}

RESUME_AGENT_TOOL = {
    "name": "resume_agent",
    "description": "Continue a prior conversation with an idle agent session.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent_session_id": {
                "type": "string",
                "description": "UUID returned from a prior dispatch_agent call"
            },
            "follow_up": {
                "type": "string",
                "description": "Follow-up question or instruction for the agent"
            }
        },
        "required": ["agent_session_id", "follow_up"]
    }
}
```

---

## 1.6 Error Handling Convention

All errors sent to the client use the `error` JSON message type. HTTP-level errors only occur during the initial WS handshake (auth) or REST calls.

| Scenario                     | Transport   | Payload                                          |
|------------------------------|-------------|--------------------------------------------------|
| Invalid/expired WS token     | HTTP 401    | Plain text: "Unauthorized"                       |
| Session not found (DELETE)   | HTTP 404    | JSON: `{"detail": "Session not found"}`          |
| Unknown WS message type      | JSON frame  | `{"type":"error","code":"INTERNAL","message":"…"}`|
| Agent busy                   | Returned as ToolResponse, voiced by Gemini | — |

---

## 1.7 Unit Tests

All tests in `tests/unit/test_phase1_*.py`. No running backend, no network. External collaborators (`gemini_proxy`, `agent_task_manager`, WS connection) are replaced with `unittest.mock.AsyncMock` / `MagicMock`.

---

### SessionManager Tests (`test_session_manager.py`)

**Setup:** instantiate `SessionManager()` directly; no FastAPI app needed.

#### SM-01 — create_session returns a valid session
- **What:** `create_session()` produces a `ConversationSession` with a non-empty `session_id`, a UUID `auth_token`, `token_consumed=False`, and `token_expires_at` roughly 300 s in the future.
- **Mocks:** `time.time` patched to a fixed value so `expires_at` is deterministic.
- **Verify:** `session_id` starts with `"s-"`; `auth_token` is a valid UUID string; `token_expires_at - fixed_time ≈ 300`; session is stored in `manager._sessions`.

#### SM-02 — validate_token: valid unused token
- **What:** `validate_token(token)` returns the session when the token is valid and not consumed.
- **Mocks:** None.
- **Verify:** Returns the same `ConversationSession` object.

#### SM-03 — validate_token: expired token
- **What:** `validate_token` returns `None` when `token_expires_at < time.time()`.
- **Mocks:** Patch `time.time` to return a value 400 s after session creation.
- **Verify:** Returns `None`.

#### SM-04 — validate_token: consumed token
- **What:** After `consume_token(session)` is called, `validate_token` returns `None` for the same token.
- **Mocks:** None.
- **Verify:** `validate_token` returns `None`; `session.token_consumed == True`.

#### SM-05 — double consume_token is idempotent
- **What:** Calling `consume_token` twice does not raise; second `validate_token` still returns `None`.
- **Mocks:** None.
- **Verify:** No exception; token still invalid.

#### SM-06 — get_session: found and not found
- **What:** `get_session(session_id)` returns the session or `None`.
- **Verify:** Returns session for known id; `None` for unknown id.

#### SM-07 — touch updates last_activity
- **What:** `touch(session_id)` sets `session.last_activity` to a later timestamp.
- **Mocks:** Patch `time.time` to return `T1` at creation, then `T2 > T1` at touch time.
- **Verify:** `session.last_activity == T2`.

#### SM-08 — increment_gen_id increments and wraps
- **What:** `increment_gen_id` returns incrementing values; wraps from 255 → 0.
- **Verify:**
  - Initial call on a new session returns 1.
  - 255 consecutive calls return 1…255.
  - 256th call returns 0.
  - Return value equals `session.gen_id` after the call.

#### SM-09 — terminate_session removes from dict and returns True
- **What:** `terminate_session(session_id)` cancels agent tasks, closes WS, returns `True`.
- **Mocks:** `session.ws_connection = AsyncMock()`; `agent_session.claude_task = AsyncMock()` (not done).
- **Verify:** Session no longer in `manager._sessions`; `ws_connection.close()` called once; `claude_task.cancel()` called once per active task.

#### SM-10 — terminate_session on unknown id returns False
- **What:** `terminate_session("nonexistent")` returns `False` without raising.
- **Verify:** Return value is `False`.

#### SM-11 — cleanup_loop evicts expired sessions
- **What:** The cleanup coroutine terminates sessions whose `last_activity + session_ttl < now`.
- **Mocks:** Patch `asyncio.sleep` to a no-op so the loop runs immediately; set `session_ttl=1` and advance `time.time` by 2 s.
- **Verify:** Session removed from dict; WS closed; tasks cancelled.

#### SM-12 — cleanup_loop leaves non-expired sessions alone
- **What:** Sessions within their TTL are not terminated.
- **Mocks:** Same as SM-11 but `time.time` advanced by only 0.5 s.
- **Verify:** Session still present in dict.

---

### WebSocket Handler Tests (`test_ws_handler.py`)

**Setup:** Use FastAPI `TestClient` + `WebSocketTestSession` from `starlette.testclient`. Mock `SessionManager`, `GeminiProxy`, and `AgentTaskManager` at the module level before importing the router.

#### WS-01 — binary audio_chunk frame routed to gemini_proxy
- **What:** A valid binary frame (msg_type=0x01) sent over WS causes `gemini_proxy.send_audio_chunk` to be called with the PCM payload.
- **Mocks:** `session_manager.validate_token` returns a fake session; `gemini_proxy.send_audio_chunk = AsyncMock()`.
- **Verify:** `send_audio_chunk` called exactly once; argument is the PCM bytes from the frame (bytes[4:]).

#### WS-02 — binary frame with unknown msg_type sends error JSON
- **What:** A binary frame with msg_type=0xFF causes an `error` JSON frame to be sent back.
- **Mocks:** Same as WS-01.
- **Verify:** Client receives a text frame; parsed JSON has `type=="error"` and `code=="INTERNAL"`.

#### WS-03 — binary frame too short (< 4 bytes) sends error
- **What:** A 2-byte binary payload does not crash the server.
- **Verify:** `error` JSON sent back; no exception logged.

#### WS-04 — JSON control:start frame handled without error
- **What:** Sending `{"type":"control","action":"start"}` over WS is accepted silently.
- **Mocks:** `handle_control = AsyncMock()`.
- **Verify:** `handle_control` called with the parsed dict.

#### WS-05 — JSON interrupt frame increments gen_id and sends interruption
- **What:** Sending `{"type":"interrupt","mode":"cancel_all"}` results in `interruption{gen_id}` sent back.
- **Mocks:** `session_manager.increment_gen_id` returns 3; `agent_task_manager.handle_interrupt = AsyncMock()`.
- **Verify:** Client receives text frame `{"type":"interruption","gen_id":3}`; `handle_interrupt` called with `mode="cancel_all"`.

#### WS-06 — JSON interrupt defaults mode to cancel_all
- **What:** `{"type":"interrupt"}` (no `mode` field) treats mode as `"cancel_all"`.
- **Verify:** `handle_interrupt` called with `mode="cancel_all"`.

#### WS-07 — unknown JSON type sends error
- **What:** `{"type":"unknown_xyz"}` results in an error JSON response.
- **Verify:** `error` JSON with `code=="INTERNAL"` received by client.

#### WS-10 — agent_followup on idle agent calls resume (Fix 8)
- **What:** `{"type":"agent_followup","agent_session_id":"A1","text":"follow up"}` when the agent is idle calls `agent_task_manager.resume`.
- **Mocks:** `conv_session.agent_sessions = {"A1": AgentSession(status="idle",...)}`.
- **Verify:** `agent_task_manager.resume` called; no error sent.

#### WS-11 — agent_followup on active agent returns AGENT_BUSY error (Fix 8)
- **What:** Same message when agent has `status="active"` sends an error instead of calling resume.
- **Verify:** `send_json` called with `{"type":"error","code":"AGENT_BUSY",...}`; `agent_task_manager.resume` NOT called.

#### WS-12 — agent_followup with unknown agent_session_id returns SESSION_NOT_FOUND (Fix 8)
- **What:** `agent_session_id` not in `conv_session.agent_sessions` sends SESSION_NOT_FOUND.
- **Verify:** `send_json` called with `code=="SESSION_NOT_FOUND"`.

#### WS-08 — send_audio_response stamps gen_id from session
- **What:** `send_audio_response(session, pcm, frame_seq)` encodes a binary frame with `session.gen_id` at byte[2].
- **Mocks:** `session.ws_connection = AsyncMock()`; `session.gen_id = 7`.
- **Verify:** `ws_connection.send` called with bytes where `data[2] == 7` and `data[4:] == pcm`.

#### WS-09 — send_agent_audio uses correct speaker_id per agent
- **What:** `send_agent_audio(session, "shijing", pcm, 0)` encodes `speaker_id=0x02`.
- **Verify:** Sent bytes have `data[1] == 0x02`.

---

### REST Endpoint Tests (`test_rest_endpoints.py`)

**Setup:** `from fastapi.testclient import TestClient; client = TestClient(app)`. Mock `session_manager` and `agent_registry`.

#### REST-01 — POST /sessions returns correct schema
- **Mocks:** `session_manager.create_session` returns a fake `ConversationSession` with known values.
- **Verify:** Response 200; body has `session_id`, `auth_token`, `expires_at` (ISO8601 string ending in `Z`).

#### REST-02 — DELETE /sessions/{id} returns 200 on success
- **Mocks:** `session_manager.terminate_session` returns `True`.
- **Verify:** `{"status": "terminated"}`.

#### REST-03 — DELETE /sessions/{id} returns 404 on missing session
- **Mocks:** `session_manager.terminate_session` returns `False`.
- **Verify:** HTTP 404.

#### REST-04 — GET /agents returns all 4 agents
- **Mocks:** `agent_registry.list_all()` returns a fixed list of 4 `AgentRegistryEntry` objects.
- **Verify:** Response 200; `len(body["agents"]) == 4`; each agent has `name`, `voice_id`, `speaker_id`, `tool_set`.

#### REST-05 — GET /health returns ok and active_sessions count
- **Mocks:** `session_manager.count()` returns 2.
- **Verify:** `{"status":"ok", "active_sessions":2}`.

---

### build_agent_roster_block Tests (`test_registry.py`)

#### REG-01 — roster block contains all agent names
- **What:** `build_agent_roster_block(registry)` output contains `"ellen"`, `"shijing"`, `"eva"`, `"ming"`.
- **Mocks:** None.

#### REG-02 — roster block contains dispatch/resume instructions
- **What:** Output contains `"dispatch_agent"` and `"resume_agent"`.

#### REG-03 — roster block lists correct voice_ids
- **What:** Each voice ID appears in the output.

---

## Phase 1 Completion Criteria

- [ ] `POST /api/v1/sessions` returns `{session_id, auth_token, expires_at}` with correct TTL.
- [ ] WS connect with valid token → 101 Upgrade; WS connect with invalid/consumed token → 401.
- [ ] Consuming token twice on two concurrent WS connects → second connect gets 401.
- [ ] Binary `audio_chunk` frames received on WS are logged (stub: no Gemini yet).
- [ ] `interrupt` JSON message increments `session.gen_id` and sends `interruption{gen_id}` back.
- [ ] `DELETE /sessions/{id}` → 200; second DELETE → 404.
- [ ] `GET /api/v1/agents` returns all 4 agents with correct structure.
- [ ] `GET /api/v1/health` returns 200.
- [ ] Background cleanup task terminates sessions after configurable TTL.
- [ ] No memory leaks: after `terminate_session`, no references to the session remain in the manager dict.
