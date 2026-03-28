# Phase 2 — Gemini Live Integration: Audio Proxy, VAD & Tool Call Dispatch

## Goal
Connect the FastAPI backend to the Gemini Live API. Audio from the iOS client flows to Gemini for STT + VAD; Gemini's TTS audio flows back to the client. Tool calls emitted by Gemini (`dispatch_agent`, `resume_agent`) are intercepted and routed to the Agent Task Manager. Phase 1 stubs are replaced with real Gemini calls.

---

## 2.1 Gemini Live API Overview

The backend uses `google-genai` Python SDK (`google.genai.live`) to establish a bidirectional streaming session per conversation. The session carries:

- **Inbound** (client → Gemini): PCM audio chunks, text turns (for tool responses)
- **Outbound** (Gemini → client/backend): TTS audio, transcripts, tool calls, interruption events

Each conversation session creates exactly one Gemini Live session. The Gemini session is torn down when the conversation session ends.

---

## 2.2 Gemini Live Proxy Module

### Session Initialization

```python
class GeminiLiveProxy:
    async def start_session(self, conversation_session: ConversationSession) -> None:
        """
        Initialize a Gemini Live session for a conversation.

        Steps:
        1. Build system prompt = base_moderator_prompt + agent_roster_block
        2. Inject tool declarations: [DISPATCH_AGENT_TOOL, RESUME_AGENT_TOOL]
        3. Open genai.live session with:
           - model: "gemini-2.0-flash-live"
           - config: system_instruction, tools, response_modalities=["AUDIO"]
           - audio output format: LINEAR16 at 16kHz
        4. Store the live session handle in conversation_session.gemini_session
        5. Launch two async tasks:
           - _audio_send_loop: feeds PCM from conversation_session.audio_queue → Gemini
           - _response_receive_loop: receives Gemini events → routes to WS or Agent Task Manager
        """

    async def send_audio_chunk(self, session: ConversationSession, pcm: bytes) -> None:
        """Enqueue PCM into session.audio_queue for the send loop."""

    async def send_tool_response(self, session: ConversationSession, tool_call_id: str, result: dict) -> None:
        """Send a function call response back to Gemini as a text turn."""

    async def close_session(self, session: ConversationSession) -> None:
        """Cancel send/receive tasks, close the Gemini live connection."""
```

### Audio Send Loop

```python
async def _audio_send_loop(self, session: ConversationSession):
    """
    Reads PCM chunks from session.audio_queue (asyncio.Queue).
    Sends each chunk to Gemini Live as realtime_input audio.
    Loops until the queue receives a sentinel (None) or the session closes.
    """
    while True:
        pcm = await session.audio_queue.get()
        if pcm is None:   # sentinel = stop
            break
        await session.gemini_session.send(
            input={"realtime_input": {"media_chunks": [{"data": pcm, "mime_type": "audio/pcm;rate=16000"}]}}
        )
```

### Response Receive Loop

```python
async def _response_receive_loop(self, session: ConversationSession):
    """
    Consumes events from the Gemini Live session.
    Routes each event type to the appropriate handler.

    Fix 5: Wrap the entire loop in try/except. A single unhandled exception
    previously killed the background task silently, leaving the WS open but dead.
    """
    try:
        async for response in session.gemini_session.receive():
            try:
                # 1. Audio output → binary frame to iOS client
                if response.data:  # raw audio bytes
                    frame_seq = session.next_frame_seq()
                    await ws_handler.send_audio_response(session, response.data, frame_seq)

                # 2. Transcript → JSON text frame to client
                if response.text:
                    await ws_handler.send_json(session, {
                        "type": "transcript",
                        "speaker": "moderator",
                        "text": response.text,
                        "is_final": True
                    })

                # 3. Tool call → route to Agent Task Manager
                if response.tool_call:
                    for fn_call in response.tool_call.function_calls:
                        await self._handle_tool_call(session, fn_call)

                # 4. Gemini-side interruption event → forward to client (idempotent)
                if response.server_content and response.server_content.interrupted:
                    await ws_handler.send_json(session, {
                        "type": "interruption",
                        "gen_id": session.gen_id
                    })
            except Exception as e:
                logger.error(f"[session={session.session_id}] Error routing Gemini response: {e}", exc_info=True)
                await ws_handler.send_json(session, {
                    "type": "error", "code": "INTERNAL",
                    "message": f"Internal error processing response: {e}"
                })
    except Exception as e:
        # The outer Gemini session itself died (network, API error, etc.)
        logger.error(f"[session={session.session_id}] Gemini session died: {e}", exc_info=True)
        await ws_handler.send_json(session, {
            "type": "error", "code": "INTERNAL",
            "message": "Moderator connection lost. Please start a new session."
        })
        await session_manager.terminate_session(session.session_id)
```

---

## 2.3 Tool Call Handling

### `dispatch_agent`

```python
async def _handle_dispatch_agent(self, session: ConversationSession, fn_call):
    """
    Called when Gemini emits a dispatch_agent function call.

    Steps:
    1. Extract name, task from fn_call.args
    2. Validate name is in AgentRegistry; if not → return ToolResponse error
    3. Call agent_task_manager.dispatch(session, name, task)
       → returns agent_session_id immediately (non-blocking)
    4. Send ToolResponse{dispatched, agent_session_id} back to Gemini via send_tool_response
       → Gemini immediately produces its "Ellen is on it!" TTS acknowledgment
    5. Send agent_status{dispatched} JSON to iOS client
    """
    name = fn_call.args["name"]
    task = fn_call.args["task"]

    if name not in agent_registry:
        await self.send_tool_response(session, fn_call.id, {
            "error": "unknown_agent", "message": f"No agent named '{name}'"
        })
        return

    agent_session_id = await agent_task_manager.dispatch(session, name, task)
    await self.send_tool_response(session, fn_call.id, {
        "status": "dispatched",
        "agent_session_id": agent_session_id
    })
    await ws_handler.send_json(session, {
        "type": "agent_status",
        "agent_name": name,
        "agent_session_id": agent_session_id,
        "status": "dispatched",
        "gen_id": session.gen_id
    })
```

### `resume_agent`

```python
async def _handle_resume_agent(self, session: ConversationSession, fn_call):
    """
    Called when Gemini emits a resume_agent function call.

    Steps:
    1. Extract agent_session_id, follow_up from fn_call.args
    2. Look up AgentSession in session.agent_sessions
    3. If not found → ToolResponse{error: "session_not_found"}
    4. If AgentSession.status == "active" → ToolResponse{error: "agent_busy", message: "..."}
       (Gemini will voice this immediately; no queuing)
    5. If AgentSession.status == "idle" → call agent_task_manager.resume(agent_session, follow_up)
       → ToolResponse{status: "resumed"}
    """
    agent_session_id = fn_call.args["agent_session_id"]
    follow_up = fn_call.args["follow_up"]

    agent_session = session.agent_sessions.get(agent_session_id)
    if agent_session is None:
        await self.send_tool_response(session, fn_call.id, {
            "error": "session_not_found", "message": f"No agent session {agent_session_id}"
        })
        return

    if agent_session.status == "active":
        await self.send_tool_response(session, fn_call.id, {
            "error": "agent_busy",
            "message": f"{agent_session.agent_name.capitalize()} is still working on your previous request"
        })
        return

    await agent_task_manager.resume(session, agent_session, follow_up)
    await self.send_tool_response(session, fn_call.id, {"status": "resumed"})
```

---

## 2.4 System Prompt Construction

The Gemini Live session system prompt is assembled at session start from two parts:

### Part 1: Moderator Persona (static)

```
You are a fast AI moderator for a voice-first assistant system.
Your role:
- Answer simple queries directly and quickly.
- For complex analytical tasks, delegate to the appropriate deep-thinking agent using dispatch_agent.
- For follow-up questions to an existing agent session, use resume_agent.
- Acknowledge delegations immediately with a brief, natural phrase ("Ellen is on it!", "Let me check with Ming.").
- Keep your own responses concise — you are a facilitator, not the expert.
- Never fabricate agent capabilities. Only dispatch agents listed in the roster below.

Audio format: your TTS output and the user's voice are both 16 kHz LINEAR16 PCM.
```

### Part 2: Agent Roster (dynamic, built from AgentRegistry)

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

## 2.5 Gemini-Side Barge-In (Deduplication)

The client sends an `interrupt` JSON when it detects barge-in via RMS. About 200 ms later, Gemini Live independently detects the user speaking and emits its own interruption event. The backend must handle both without double-counting:

```python
# In _response_receive_loop:
if response.server_content and response.server_content.interrupted:
    # gen_id was already incremented by the client-side interrupt handler.
    # Just forward a confirmatory interruption message — client will no-op if gen_id unchanged.
    await ws_handler.send_json(session, {
        "type": "interruption",
        "gen_id": session.gen_id   # same gen_id, already advanced
    })
    # Do NOT increment gen_id again here.
```

---

## 2.6 Audio Queue Integration (Phase 1 → Phase 2 wiring)

In Phase 1, the WebSocket handler received `audio_chunk` binary frames and logged them (stub). In Phase 2, the handler feeds them to the Gemini proxy:

```python
async def handle_binary_frame(data: bytes, session: ConversationSession):
    msg_type, speaker_id, gen_id, frame_seq, pcm = decode_frame(data)
    if msg_type == MsgType.AUDIO_CHUNK:
        session_manager.touch(session.session_id)
        await gemini_proxy.send_audio_chunk(session, pcm)
```

---

## 2.7 Transcript Forwarding to Client

Gemini Live produces partial and final transcripts. Forward them:

```python
# Partial transcript (while user is still speaking)
{
    "type": "transcript",
    "speaker": "user",
    "text": "What is my risk...",
    "is_final": false
}

# Final transcript (after VAD end-of-utterance)
{
    "type": "transcript",
    "speaker": "user",
    "text": "What is my risk score?",
    "is_final": true
}
```

---

## 2.8 Unit Tests

All tests in `tests/unit/test_phase2_*.py`. No real Gemini API calls. The `genai.live` session, `ws_handler`, and `agent_task_manager` are fully mocked.

**Common test fixture:**
```python
@pytest.fixture
def conv_session():
    s = ConversationSession()
    s.ws_connection = AsyncMock()
    s.audio_queue = asyncio.Queue()
    s.gen_id = 0
    return s

@pytest.fixture
def proxy():
    p = GeminiLiveProxy(
        ws_handler=AsyncMock(),
        agent_task_manager=AsyncMock(),
        session_manager=MagicMock(),
    )
    return p
```

---

### Audio Queue Tests (`test_gemini_audio_queue.py`)

#### GQ-01 — send_audio_chunk enqueues PCM
- **What:** `send_audio_chunk(session, pcm)` puts `pcm` into `session.audio_queue`.
- **Mocks:** None (uses real `asyncio.Queue`).
- **Verify:** `session.audio_queue.get_nowait() == pcm`.

#### GQ-02 — send_audio_chunk with None sentinel stops the send loop
- **What:** Putting `None` into `audio_queue` causes `_audio_send_loop` to exit its loop.
- **Mocks:** `session.gemini_session.send = AsyncMock()`.
- **Verify:** After draining all real PCM items followed by `None`, the loop task reaches completion without hanging.

#### GQ-03 — audio_send_loop forwards each chunk to Gemini send
- **What:** For each non-None item in `audio_queue`, `gemini_session.send` is called once with the correct mime type.
- **Mocks:** `session.gemini_session.send = AsyncMock()`; pre-load queue with 3 PCM blobs + sentinel.
- **Verify:** `send` called exactly 3 times; each call payload contains `"audio/pcm;rate=16000"`.

---

### Response Receive Loop Routing Tests (`test_gemini_receive_loop.py`)

**Setup:** `session.gemini_session` is a mock async generator that yields pre-defined fake response objects.

#### RR-01 — audio data response → send_audio_response called
- **What:** A response with `response.data = b'\x00' * 320` (fake PCM) causes `ws_handler.send_audio_response` to be called.
- **Mocks:** Fake response object; `ws_handler.send_audio_response = AsyncMock()`.
- **Verify:** `send_audio_response` called once with the fake PCM bytes.

#### RR-02 — text response → send_json called with transcript
- **What:** A response with `response.text = "Hello"` causes `ws_handler.send_json` to be called with a `transcript` message.
- **Mocks:** `ws_handler.send_json = AsyncMock()`.
- **Verify:** `send_json` called; payload `{"type":"transcript","speaker":"moderator","text":"Hello","is_final":True}`.

#### RR-03 — tool_call response dispatches to _handle_tool_call
- **What:** A response with a `tool_call` containing a `dispatch_agent` function call routes to `_handle_dispatch_agent`.
- **Mocks:** `proxy._handle_dispatch_agent = AsyncMock()`.
- **Verify:** `_handle_dispatch_agent` called with the function call object.

#### RR-04 — server_content.interrupted → send_json with interruption (no gen_id increment)
- **What:** A response where `server_content.interrupted == True` causes `send_json` with `{"type":"interruption","gen_id": session.gen_id}`.
- **Mocks:** `session.gen_id = 3`; `ws_handler.send_json = AsyncMock()`.
- **Verify:** Sent payload has `gen_id == 3` (same as before, NOT incremented).

#### RR-05 — response with no fields is a no-op
- **What:** A response with `data=None`, `text=None`, `tool_call=None`, `server_content=None` causes no calls.
- **Verify:** `ws_handler.send_audio_response` not called; `ws_handler.send_json` not called.

---

### dispatch_agent Handler Tests (`test_handle_dispatch_agent.py`)

#### DA-01 — valid agent name → dispatches task and sends ToolResponse + agent_status
- **What:** `_handle_dispatch_agent` with `name="ellen"` calls `agent_task_manager.dispatch` and then `send_tool_response` + `ws_handler.send_json`.
- **Mocks:** `agent_task_manager.dispatch = AsyncMock(return_value="A1")`; `proxy.send_tool_response = AsyncMock()`; `ws_handler.send_json = AsyncMock()`.
- **Verify:**
  - `agent_task_manager.dispatch` called with `(conv_session, "ellen", task_string)`.
  - `send_tool_response` called with `{"status":"dispatched","agent_session_id":"A1"}`.
  - `ws_handler.send_json` called with `{"type":"agent_status","agent_name":"ellen","agent_session_id":"A1","status":"dispatched","gen_id":0}`.

#### DA-02 — unknown agent name → ToolResponse error, no dispatch
- **What:** `_handle_dispatch_agent` with `name="bob"` sends an error ToolResponse and does NOT call `agent_task_manager.dispatch`.
- **Verify:** `send_tool_response` called with `{"error":"unknown_agent",...}`; `agent_task_manager.dispatch` NOT called.

#### DA-03 — ToolResponse is sent before agent_status JSON
- **What:** Call order: `send_tool_response` must be awaited before `ws_handler.send_json` so Gemini gets the result before the client gets the status.
- **Mocks:** Track call order via `AsyncMock` side_effect and a shared list.
- **Verify:** `send_tool_response` appears before `send_json` in the call list.

---

### resume_agent Handler Tests (`test_handle_resume_agent.py`)

#### RA-01 — unknown agent_session_id → ToolResponse session_not_found
- **What:** `_handle_resume_agent` when `session.agent_sessions` does not contain `agent_session_id`.
- **Mocks:** `conv_session.agent_sessions = {}`; `proxy.send_tool_response = AsyncMock()`.
- **Verify:** `send_tool_response` called with `{"error":"session_not_found",...}`; `agent_task_manager.resume` NOT called.

#### RA-02 — active agent → ToolResponse agent_busy immediately
- **What:** `AgentSession.status == "active"` → busy response, no resume called.
- **Mocks:** `conv_session.agent_sessions = {"A1": AgentSession(status="active", agent_name="ellen")}`.
- **Verify:** `send_tool_response` called with `{"error":"agent_busy",...}`; `agent_task_manager.resume` NOT called.

#### RA-03 — idle agent → calls resume and returns ToolResponse resumed
- **What:** `AgentSession.status == "idle"` → `agent_task_manager.resume` called; ToolResponse `{status:"resumed"}` sent.
- **Mocks:** `conv_session.agent_sessions = {"A1": AgentSession(status="idle",...)}`.
- **Verify:** `agent_task_manager.resume` called with `(conv_session, agent_session, "follow up text")`; `send_tool_response` called with `{"status":"resumed"}`.

#### RA-04 — cancelled or timeout agent is treated as resumable (status check only for "active")
- **What:** An agent with `status="cancelled"` or `status="timeout"` is NOT blocked by the busy guard.
- **Verify:** `agent_task_manager.resume` is called (not blocked).

---

### System Prompt Construction Tests (`test_system_prompt.py`)

#### SP-01 — build_system_prompt contains moderator persona and roster
- **What:** The assembled prompt includes the static moderator persona text and the dynamic roster block.
- **Mocks:** None (pure function).
- **Verify:** Output string contains `"You are a fast AI moderator"` and all 4 agent names.

#### SP-02 — roster block has dispatch/resume instructions
- **Verify:** Contains `"dispatch_agent"` and `"resume_agent"`.

#### SP-03 — roster block does not contain stale/hardcoded content
- **What:** Roster is built dynamically from the registry; removing an agent from the registry removes it from the prompt.
- **Mocks:** Registry with only 2 agents.
- **Verify:** Prompt contains only those 2 agent names.

---

## Phase 2 Completion Criteria

- [ ] Opening WS → Gemini Live session starts within 2 s (confirmed in logs).
- [ ] Sending PCM audio of a simple factual question → `transcript` JSON received from backend → `audio_response` binary frames received (Gemini TTS audio).
- [ ] Sending PCM audio of "analyze my spending patterns" → Gemini emits `dispatch_agent` tool call (confirmed in logs).
- [ ] ToolResponse `{dispatched}` sent back within 1 s of tool call (stub: agent_task_manager not yet real).
- [ ] Agent roster block appears in Gemini system prompt; Gemini uses only `ellen/shijing/eva/ming` as agent names.
- [ ] Gemini-side interruption event does not re-increment `gen_id`.
- [ ] Closing the WS connection → Gemini session is torn down cleanly (no dangling async tasks).
