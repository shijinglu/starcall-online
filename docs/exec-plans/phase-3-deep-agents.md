# Phase 3 — Deep Agents: Task Manager, Claude SDK Runner, TTS & Meeting Mode

## Goal
Implement the full slow-path pipeline: Agent Task Manager dispatches Claude SDK tasks, Deep Agent Runner streams sentences to the TTS Service, and audio arrives at the client progressively. Meeting Mode queues multiple agents and delivers audio sequentially. Barge-in cancellation (both modes) is fully operational.

---

## 3.1 Agent Task Manager

### State Machine Per Agent Session

```
          dispatch()
idle ─────────────────► active ──────────────────► idle
                          │  (task completes)
                          │
                          ├── asyncio.wait_for timeout (30s) ──► timeout
                          │
                          └── interrupt(cancel_all) ────────────► cancelled
```

### Core Methods

```python
class AgentTaskManager:

    async def dispatch(
        self,
        conv_session: ConversationSession,
        agent_name: str,
        task: str
    ) -> str:
        """
        Creates a new AgentSession, spawns an asyncio.Task wrapping _run_agent(),
        stores the task in agent_session.claude_task.
        Returns agent_session_id immediately (non-blocking).

        Fix 6: Emit agent_status{thinking, elapsed_ms=0} immediately at dispatch time,
        before spawning the task. This ensures the UI spinner appears within milliseconds
        of dispatch rather than after the first 10s heartbeat cycle.

        The running task:
        1. Calls deep_agent_runner.run(agent_session, task) with stream=True
        2. Wraps the entire run in asyncio.wait_for(timeout=AGENT_TASK_TIMEOUT)
        3. Emits agent_status{thinking, elapsed_ms} heartbeat updates every THINKING_HEARTBEAT_INTERVAL seconds
        4. On completion: sets status="idle", emits agent_status{done}
        5. On timeout: sets status="timeout", emits agent_status{timeout},
           synthesizes + sends fallback phrase audio
        6. On CancelledError: sets status="cancelled" (no further events)
        """
        agent_session = AgentSession(agent_name=agent_name, ...)
        conv_session.agent_sessions[agent_session.agent_session_id] = agent_session

        # Fix 6: Emit thinking immediately at t=0 — no more 10s grey dot
        await ws_handler.send_json(conv_session, {
            "type": "agent_status",
            "agent_name": agent_name,
            "agent_session_id": agent_session.agent_session_id,
            "status": "thinking",
            "elapsed_ms": 0,
            "gen_id": conv_session.gen_id
        })

        agent_session.claude_task = asyncio.create_task(_run_agent(conv_session, agent_session, task))
        return agent_session.agent_session_id
        # (remainder of method body above is the pseudocode for the task, not called inline)

    async def resume(
        self,
        conv_session: ConversationSession,
        agent_session: AgentSession,
        follow_up: str
    ) -> None:
        """
        Appends follow_up to agent_session.conversation_history as a user turn.
        Sets agent_session.status = "active".
        Spawns a new asyncio.Task (same agent, continued history).
        """

    async def handle_interrupt(
        self,
        conv_session: ConversationSession,
        mode: str  # "cancel_all" | "skip_speaker"
    ) -> None:
        """
        cancel_all:
          - Call task.cancel() on ALL active AgentSession.claude_task in conv_session
          - Clear conv_session.meeting_queue
          - Cancel any in-flight TTS stream

        skip_speaker:
          - Cancel only the TTS stream for the currently-playing agent
            (does NOT cancel Claude tasks — agents may still be computing)
          - Advance meeting_queue: pop the head entry and start delivering
            the next agent's pre-computed audio
          - Agents that already finished are preserved
        """
```

### Heartbeat Emission

The heartbeat loop runs concurrently with the Claude task:

```python
async def _heartbeat_loop(conv_session, agent_session):
    start = time.time()
    while agent_session.status == "active":
        await asyncio.sleep(THINKING_HEARTBEAT_INTERVAL)
        if agent_session.status != "active":
            break
        elapsed_ms = int((time.time() - start) * 1000)
        await ws_handler.send_json(conv_session, {
            "type": "agent_status",
            "agent_name": agent_session.agent_name,
            "agent_session_id": agent_session.agent_session_id,
            "status": "thinking",
            "elapsed_ms": elapsed_ms,
            "gen_id": conv_session.gen_id
        })
```

### Timeout & Fallback

```python
FALLBACK_PHRASES = {
    "ellen":   "Sorry, I took too long on that. Let me try again shortly.",
    "shijing": "Apologies, the analysis timed out. I'll circle back.",
    "eva":     "That financial query timed out. Please try again.",
    "ming":    "Fraud investigation timed out. I'll retry.",
}

async def _handle_timeout(conv_session, agent_session):
    agent_session.status = "timeout"
    await ws_handler.send_json(conv_session, {
        "type": "agent_status",
        "agent_name": agent_session.agent_name,
        "agent_session_id": agent_session.agent_session_id,
        "status": "timeout",
        "gen_id": conv_session.gen_id
    })
    # Fix 9: Append a sentinel assistant turn so that a future resume() does not
    # produce malformed history (two consecutive user messages).
    agent_session.conversation_history.append({
        "role": "assistant",
        "content": FALLBACK_PHRASES[agent_session.agent_name]
    })
    fallback = FALLBACK_PHRASES[agent_session.agent_name]
    pcm = await tts_service.synthesize(fallback, agent_session.agent_name)
    await ws_handler.send_agent_audio(conv_session, agent_session.agent_name, pcm, frame_seq=0)
```

---

## 3.2 Deep Agent Runner

### Responsibilities
- Wrap Claude SDK call with per-agent system prompt + tool set.
- Call Claude with `stream=True`.
- Split the streaming token output at sentence boundaries.
- Yield each complete sentence to the TTS pipeline immediately.
- Append the full response to `agent_session.conversation_history`.

### Sentence Boundary Splitter

The splitter must handle common abbreviations to avoid false splits.

```python
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "vs", "etc", "approx", "dept", "est"}

def split_into_sentences(token_stream) -> Generator[str, None, None]:
    """
    Accumulates tokens from a streaming generator.
    Yields a complete sentence when a sentence-ending punctuation is detected,
    but only if the preceding word is NOT a known abbreviation.

    Algorithm:
    1. Maintain a buffer of accumulated text.
    2. On each token, append to buffer.
    3. Scan the buffer for '.', '?', '!' followed by whitespace or end-of-stream.
    4. Before splitting, check the word before the punctuation:
       - If it's a known abbreviation (case-insensitive), do NOT split.
    5. When a valid split point is found, yield the sentence and clear the buffer.
    6. At stream end, yield any remaining text in the buffer (incomplete sentence).
    """
```

### Claude SDK Call

```python
class DeepAgentRunner:

    async def run(
        self,
        agent_session: AgentSession,
        task: str,
        conv_session: ConversationSession
    ) -> None:
        """
        1. Build messages list:
           messages = agent_session.conversation_history + [{"role": "user", "content": task}]
        2. Get system prompt and tools from AgentRegistry
        3. Call Anthropic client with stream=True:
           async with anthropic_client.messages.stream(
               model="claude-opus-4-6",
               system=system_prompt,
               messages=messages,
               tools=tool_definitions,
               max_tokens=4096,
           ) as stream:
               async for text_chunk in stream.text_stream:
                   yield text_chunk
        4. Run split_into_sentences(stream.text_stream):
           for sentence in split_into_sentences(token_gen):
               pcm = await tts_service.synthesize(sentence, agent_session.agent_name)
               await _deliver_or_queue(conv_session, agent_session, pcm)
        5. Fix 4: Handle tool_use blocks emitted by Claude.
           When stream stop_reason == "tool_use", extract tool_use content blocks,
           dispatch each to the appropriate stub in app/tools/<agent_name>_tools.py,
           append tool_result to messages, and re-invoke Claude.
           Loop until stop_reason == "end_turn" or max_tool_rounds exceeded.
           Example tool dispatch:
             tool_result = await dispatch_tool(tool_use.name, tool_use.input)
             messages.append({"role": "assistant", "content": [tool_use_block]})
             messages.append({"role": "user", "content": [{"type": "tool_result",
                               "tool_use_id": tool_use.id, "content": str(tool_result)}]})
        6. Append full response to conversation_history:
           full_response = await stream.get_final_message()
           agent_session.conversation_history.append({"role": "user", "content": task})
           agent_session.conversation_history.append({"role": "assistant", "content": full_response.content})
        """
```

### Meeting Mode Delivery

In meeting mode, audio from multiple agents must play sequentially. The `_deliver_or_queue` function checks whether this agent is the currently-playing one or needs to be queued:

```python
# Fix 3 (Part B) + Fix 7: Store agent_session_id (not agent name) in meeting_queue.
# The head-of-queue agent streams directly; others buffer.
async def _deliver_or_queue(conv_session, agent_session, pcm_chunk):
    if not conv_session.meeting_queue:
        # No meeting mode: deliver directly
        frame_seq = agent_session.next_frame_seq()
        await ws_handler.send_agent_audio(conv_session, agent_session.agent_name, pcm_chunk, frame_seq)
    elif conv_session.meeting_queue[0] == agent_session.agent_session_id:
        # Fix 3B: This agent is at the head of the queue — stream directly, no buffering
        frame_seq = agent_session.next_frame_seq()
        await ws_handler.send_agent_audio(conv_session, agent_session.agent_name, pcm_chunk, frame_seq)
    else:
        # Not yet at head — buffer until it's this agent's turn
        agent_session.audio_buffer.append(pcm_chunk)
```

---

## 3.3 Meeting Mode

### Overview

Meeting Mode is triggered when Gemini dispatches multiple agents in a single turn (e.g., "Everyone, what's my risk profile?"). The agents compute **concurrently**, but audio is delivered **sequentially** in FIFO order based on dispatch order.

### meeting_queue Structure

```python
# Fix 7: meeting_queue stores agent_session_ids (UUIDs), NOT agent names.
# This allows O(1) lookup in conv_session.agent_sessions dict.
conv_session.meeting_queue: list[str]  # ordered list of agent_session_ids

# Fix 7: _find_agent_session is a simple dict lookup (not a linear search by name)
def _find_agent_session(conv_session: ConversationSession, agent_session_id: str) -> AgentSession:
    return conv_session.agent_sessions[agent_session_id]

# dispatch() appends agent_session.agent_session_id to meeting_queue (not agent_name)
# _deliver_or_queue() compares agent_session.agent_session_id to meeting_queue[0]
```

### Sequential Audio Delivery

```python
async def _meeting_mode_audio_sender(conv_session: ConversationSession):
    """
    Background task that runs while meeting_queue is non-empty.

    Fix 3A: Replace 0.5s polling loop with asyncio.Event signaling.
    Each AgentSession carries a completion_event (asyncio.Event) set when the task finishes.
    The sender awaits the event instead of sleeping, eliminating up to 2s of polling dead air.

    Fix 3B: The head-of-queue agent already streams directly via _deliver_or_queue.
    This sender only needs to drain the audio_buffer for agents that were behind the head
    at the time their TTS fired. In the common case (serial dispatch), this buffer is empty
    and the sender immediately moves to the next queue entry.

    Algorithm:
    1. Peek at the first agent_session_id in meeting_queue
    2. Await agent_session.completion_event (set by _run_agent on finish, timeout, or cancel)
    3. Drain any remaining audio_buffer chunks (PCM that arrived before head-of-queue)
    4. Send meeting_status JSON update
    5. Pop the entry from meeting_queue, move to next
    6. Repeat until queue empty
    7. Send final meeting_status{completed: N, pending: [], failed: []}
    """
    while conv_session.meeting_queue:
        agent_session_id = conv_session.meeting_queue[0]
        agent_session = _find_agent_session(conv_session, agent_session_id)

        # Fix 3A: Event-driven wait — no polling sleep
        await agent_session.completion_event.wait()

        # Drain any remaining buffered audio (from frames that arrived before head-of-queue)
        frame_seq = agent_session.current_frame_seq
        for pcm_chunk in agent_session.audio_buffer:
            current_gen = conv_session.gen_id
            frame = encode_frame(MsgType.AGENT_AUDIO,
                                 AGENT_SPEAKER_IDS[agent_session.agent_name],
                                 current_gen, frame_seq, pcm_chunk)
            await conv_session.ws_connection.send(frame)
            frame_seq = (frame_seq + 1) & 0xFF
        agent_session.audio_buffer.clear()

        conv_session.meeting_queue.pop(0)

        completed = sum(
            1 for s in conv_session.agent_sessions.values()
            if s.status in ("idle", "timeout", "cancelled")
        )
        total = len(conv_session.agent_sessions)
        await ws_handler.send_json(conv_session, {
            "type": "meeting_status",
            "gen_id": conv_session.gen_id,
            "total_agents": total,
            "completed": completed,
            "pending": list(conv_session.meeting_queue),
            "failed": [s.agent_name for s in conv_session.agent_sessions.values()
                       if s.status == "timeout"]
        })

# Fix 3A: In _run_agent, signal completion_event in the finally block
async def _run_agent(conv_session, agent_session, task):
    try:
        await asyncio.wait_for(
            deep_agent_runner.run(agent_session, task, conv_session),
            timeout=AGENT_TASK_TIMEOUT
        )
        agent_session.status = "idle"
        await ws_handler.send_json(conv_session, {
            "type": "agent_status", "agent_name": agent_session.agent_name,
            "agent_session_id": agent_session.agent_session_id,
            "status": "done", "gen_id": conv_session.gen_id
        })
    except asyncio.TimeoutError:
        await _handle_timeout(conv_session, agent_session)
    except asyncio.CancelledError:
        agent_session.status = "cancelled"
    finally:
        agent_session.completion_event.set()   # unblock _meeting_mode_audio_sender

# Fix 7: AgentSession must include completion_event
@dataclass
class AgentSession:
    agent_session_id: str
    agent_name: str
    status: str = "active"
    claude_task: Optional[asyncio.Task] = None
    conversation_history: list = field(default_factory=list)
    audio_buffer: list = field(default_factory=list)
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    current_frame_seq: int = 0  # last frame_seq used by _deliver_or_queue

# Fix 3: Also cancel _meeting_mode_audio_sender task on cancel_all interrupt
# In handle_interrupt(cancel_all):
#   if conv_session.meeting_sender_task and not conv_session.meeting_sender_task.done():
#       conv_session.meeting_sender_task.cancel()
#   conv_session.meeting_queue.clear()
```

---

## 3.3b Agent Tool Implementations (Fix 4)

All 12 agent tools must have stub implementations that return plausible demo data. Without this, Claude emits `tool_use` response blocks that `DeepAgentRunner.run()` cannot handle, causing it to hang or fail silently.

### Directory Structure

```
app/tools/
├── __init__.py
├── dispatch.py        # Central dispatcher: tool name → function
├── ellen_tools.py     # calendar_read, email_send, task_list
├── shijing_tools.py   # user_profile_read, user_journey_read, risk_score_read
├── eva_tools.py       # transaction_read, bank_data_read, chargeback_read
└── ming_tools.py      # id_check, async_risk_check, fraud_signal_read
```

### Stub Tool Implementations

```python
# app/tools/ellen_tools.py
import uuid

async def calendar_read(date: str, **kwargs) -> dict:
    return {"date": date, "events": [
        {"time": "9:00 AM", "title": "Team standup"},
        {"time": "2:00 PM", "title": "Product review"}
    ]}

async def email_send(to: str, subject: str, body: str, **kwargs) -> dict:
    return {"status": "sent", "message_id": str(uuid.uuid4())}

async def task_list(**kwargs) -> dict:
    return {"tasks": [
        {"id": 1, "title": "Review fraud report", "due": "today", "priority": "high"},
        {"id": 2, "title": "Schedule weekly sync", "due": "tomorrow"}
    ]}

# app/tools/shijing_tools.py
async def user_profile_read(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "account_age_days": 847, "country": "US",
            "email_verified": True, "phone_verified": True}

async def user_journey_read(user_id: str, days: int = 30, **kwargs) -> dict:
    return {"user_id": user_id, "login_count": 23, "device_changes": 1,
            "address_changes": 0, "avg_session_minutes": 12.3}

async def risk_score_read(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "risk_score": 42, "risk_tier": "medium",
            "last_updated": "2026-03-27"}

# app/tools/eva_tools.py
async def transaction_read(user_id: str, days: int = 30, **kwargs) -> dict:
    return {"user_id": user_id, "transaction_count": 18, "total_spend_usd": 2340.50,
            "largest_transaction_usd": 499.99, "flagged_count": 1}

async def bank_data_read(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "bank": "Chase", "account_type": "checking",
            "balance_usd": 4820.33, "account_age_days": 1240}

async def chargeback_read(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "chargeback_count_12m": 0, "dispute_count_12m": 1}

# app/tools/ming_tools.py
async def id_check(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "identity_verified": True, "document_type": "drivers_license",
            "match_confidence": 0.97}

async def async_risk_check(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "risk_signals": ["velocity_spike"], "score": 68}

async def fraud_signal_read(user_id: str, **kwargs) -> dict:
    return {"user_id": user_id, "signals": [
        {"type": "device_fingerprint_mismatch", "confidence": 0.72, "detected_at": "2026-03-25"}
    ]}
```

### Central Dispatcher

```python
# app/tools/dispatch.py
from app.tools import ellen_tools, shijing_tools, eva_tools, ming_tools

TOOL_MAP = {
    "calendar_read":       ellen_tools.calendar_read,
    "email_send":          ellen_tools.email_send,
    "task_list":           ellen_tools.task_list,
    "user_profile_read":   shijing_tools.user_profile_read,
    "user_journey_read":   shijing_tools.user_journey_read,
    "risk_score_read":     shijing_tools.risk_score_read,
    "transaction_read":    eva_tools.transaction_read,
    "bank_data_read":      eva_tools.bank_data_read,
    "chargeback_read":     eva_tools.chargeback_read,
    "id_check":            ming_tools.id_check,
    "async_risk_check":    ming_tools.async_risk_check,
    "fraud_signal_read":   ming_tools.fraud_signal_read,
}

async def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    fn = TOOL_MAP.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}
    return await fn(**tool_input)
```

---

## 3.4 TTS Service

### Responsibilities
- Accept a sentence string and agent name.
- Call Google Cloud TTS with the agent's `voice_id`.
- Return raw PCM bytes (LINEAR16, 16 kHz).
- Per-sentence latency target: < 1.5 s.

```python
class TTSService:

    async def synthesize(self, text: str, agent_name: str) -> bytes:
        """
        1. Look up voice_id from AgentRegistry[agent_name].voice_id
        2. Call Google Cloud TTS with one automatic retry on transient failure (Fix 5):
           for attempt in range(2):
               try:
                   response = await tts_client.synthesize_speech(
                       input=SynthesisInput(text=text),
                       voice=VoiceSelectionParams(language_code="en-US", name=voice_id),
                       audio_config=AudioConfig(audio_encoding=AudioEncoding.LINEAR16,
                                                sample_rate_hertz=16000)
                   )
                   return response.audio_content  # raw PCM bytes
               except Exception as e:
                   if attempt == 0:
                       logger.warning(f"TTS attempt 1 failed ({agent_name}): {e}, retrying")
                       await asyncio.sleep(0.5)
                   else:
                       logger.error(f"TTS failed after 2 attempts ({agent_name}): {e}")
                       return b''  # empty audio; DeepAgentRunner skips this sentence gracefully
        """
```

### Pipelining

TTS is called per-sentence, not per-full-response. The first sentence of an agent's response is synthesized and sent to the client **before Claude finishes generating the rest of the response**:

```
Claude streams tokens:
  t=0s  → sentence 1 complete → TTS call → PCM → client (first audio ~1-2s after dispatch)
  t=3s  → sentence 2 complete → TTS call → PCM → client (while Claude still generating)
  t=8s  → sentence 3 complete → TTS call → PCM → client
  t=10s → Claude done
```

---

## 3.5 Session TTL Cleanup (2-Hour TTL)

The Session Manager's background cleanup task (introduced in Phase 1) is extended here to also clean up `AgentSession` objects:

```python
async def _cleanup_loop():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for session_id, conv_session in list(sessions.items()):
            idle_time = now - conv_session.last_activity
            if idle_time > conv_session.session_ttl:
                # Cancel all agent tasks
                for agent_session in conv_session.agent_sessions.values():
                    if agent_session.claude_task and not agent_session.claude_task.done():
                        agent_session.claude_task.cancel()
                # Close WebSocket
                if conv_session.ws_connection:
                    await conv_session.ws_connection.close()
                # Remove from dict
                del sessions[session_id]
```

---

## 3.6 Agent System Prompts (Skeletal)

Each agent has a dedicated system prompt file. Key elements:

### ellen (Personal Assistant)
```
You are Ellen, a warm and efficient personal assistant.
You have access to: calendar_read, email_send, task_list tools.
You ONLY use tools in your tool_set. You do not have access to financial data, risk scores, or fraud signals.
Keep responses concise and action-oriented. Address the user as "boss".
```

### shijing (User Risk Analyst)
```
You are Shijing, a user risk analyst specializing in user profiles and journey analysis.
You have access to: user_profile_read, user_journey_read, risk_score_read tools.
You do not have access to transaction data, bank statements, or fraud ID checks.
Provide data-driven analysis. Flag anomalies in user journeys.
```

### eva (Financial Analyst)
```
You are Eva, a financial analyst specializing in transaction and bank data.
You have access to: transaction_read, bank_data_read, chargeback_read tools.
You do not have access to user identity data or fraud risk scores.
Present financial patterns clearly. Quantify volumes and trends.
```

### ming (Fraud Investigator)
```
You are Ming, a fraud investigator specializing in identity verification and async risk signals.
You have access to: id_check, async_risk_check, fraud_signal_read tools.
You do not have access to financial transactions or calendar data.
Be precise about confidence levels. Clearly distinguish confirmed facts from signals.
```

---

## 3.7 Unit Tests

All tests in `tests/unit/test_phase3_*.py`. No real Claude, Gemini, or TTS calls. The `DeepAgentRunner`, `TTSService`, and WS handler are mocked at collaborator boundaries.

**Common fixture:**
```python
@pytest.fixture
def conv_session():
    s = ConversationSession()
    s.ws_connection = AsyncMock()
    s.gen_id = 2
    s.meeting_queue = []
    s.agent_sessions = {}
    return s

@pytest.fixture
def mock_tts():
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b'\x00' * 3200)
    return tts

@pytest.fixture
def mock_runner():
    return AsyncMock()
```

---

### AgentTaskManager — Dispatch Tests (`test_agent_dispatch.py`)

#### ATM-01 — dispatch creates AgentSession with status=active
- **What:** After `dispatch(conv_session, "ellen", "task")`, `conv_session.agent_sessions` contains a new `AgentSession` with `status="active"` and `agent_name="ellen"`.
- **Mocks:** Mock `DeepAgentRunner.run` to return immediately (no-op coroutine).
- **Verify:** `len(conv_session.agent_sessions) == 1`; entry has `status=="active"`.

#### ATM-02 — dispatch returns agent_session_id before task completes
- **What:** `dispatch` is non-blocking — it returns the `agent_session_id` string without awaiting the Claude task.
- **Mocks:** `DeepAgentRunner.run` is an `AsyncMock` that sleeps for 5 s.
- **Verify:** `dispatch` returns in < 0.1 s; the returned string is a valid UUID.

#### ATM-03 — dispatch spawns an asyncio.Task (not a coroutine)
- **What:** The Claude work runs as an independent `asyncio.Task` stored in `agent_session.claude_task`.
- **Mocks:** Track whether an `asyncio.Task` is created.
- **Verify:** `agent_session.claude_task` is an instance of `asyncio.Task`; it is not done immediately.

#### ATM-04 — task completion sets status=idle and sends agent_status{done}
- **What:** When the wrapped Claude task finishes, the session status becomes `"idle"` and a `done` event is sent to the WS.
- **Mocks:** `DeepAgentRunner.run` completes immediately; `ws_handler.send_json = AsyncMock()`.
- **Verify:** After awaiting the task, `agent_session.status == "idle"`; `send_json` called with `{"type":"agent_status","status":"done",...}`.

#### ATM-01b — dispatch immediately emits thinking{elapsed_ms=0} (Fix 6)
- **What:** `dispatch()` sends `{"type":"agent_status","status":"thinking","elapsed_ms":0}` **before** spawning the asyncio.Task.
- **Mocks:** `ws_handler.send_json = AsyncMock()`; track call order.
- **Verify:** First `send_json` call after `dispatch()` has `status=="thinking"` and `elapsed_ms==0`; this call precedes task creation.

#### ATM-06b — resume with history ending in user-turn causes no error (Fix 9 guard)
- **What:** After a timeout that appended a sentinel assistant turn, `resume()` appends another user turn correctly (no consecutive user turns).
- **Setup:** `agent_session.conversation_history = [{"role":"user",...}, {"role":"assistant","content":"<fallback>"}]`.
- **Verify:** After `resume(follow_up="new question")`, history ends with `{"role":"user","content":"new question"}`; no consecutive user turns.

#### ATM-05 — dispatch on unknown agent name raises or returns error
- **What:** `dispatch(conv_session, "unknown_agent", "task")` raises `KeyError` or `ValueError`.
- **Verify:** Exception raised; no `AgentSession` created.

---

### AgentTaskManager — Resume Tests (`test_agent_resume.py`)

#### ATM-06 — resume appends follow_up to conversation_history
- **What:** `resume(conv_session, agent_session, "follow_up text")` appends `{"role":"user","content":"follow_up text"}` to `agent_session.conversation_history`.
- **Mocks:** `DeepAgentRunner.run = AsyncMock()`.
- **Verify:** Last entry of `agent_session.conversation_history` has `role=="user"` and the follow-up text.

#### ATM-07 — resume sets status=active before spawning task
- **What:** `agent_session.status` is `"active"` immediately after `resume` returns (before the task finishes).
- **Verify:** `agent_session.status == "active"` right after `await resume(...)`.

#### ATM-08 — resume spawns a new Task, not the old one
- **What:** The `claude_task` field is replaced with a new `asyncio.Task` on each resume call.
- **Mocks:** Set `agent_session.claude_task = done_old_task`.
- **Verify:** After resume, `agent_session.claude_task` is a different object than `done_old_task`.

---

### AgentTaskManager — Interrupt Tests (`test_interrupt_handling.py`)

#### ATM-09 — handle_interrupt(cancel_all) cancels all active tasks
- **What:** Three active agent sessions each have a running asyncio.Task; `handle_interrupt(session, "cancel_all")` cancels all of them.
- **Mocks:** Three `MagicMock` tasks with `done() == False`; attach to three `AgentSession` objects.
- **Verify:** Each task's `.cancel()` method called exactly once.

#### ATM-10 — handle_interrupt(cancel_all) clears meeting_queue
- **What:** `conv_session.meeting_queue = ["ellen","shijing"]`; after `cancel_all`, `meeting_queue` is empty.
- **Verify:** `conv_session.meeting_queue == []`.

#### ATM-11 — handle_interrupt(cancel_all) does not cancel already-done tasks
- **What:** Tasks with `done() == True` are skipped.
- **Mocks:** One done task, one not done.
- **Verify:** Only the non-done task's `.cancel()` is called.

#### ATM-12 — handle_interrupt(skip_speaker) does NOT cancel Claude tasks
- **What:** `skip_speaker` mode leaves all `AgentSession.claude_task` objects untouched.
- **Mocks:** Two active tasks.
- **Verify:** Neither task's `.cancel()` is called.

#### ATM-13 — handle_interrupt(skip_speaker) pops the first meeting_queue entry
- **What:** `meeting_queue = ["ellen","shijing"]`; after `skip_speaker`, queue becomes `["shijing"]`.
- **Verify:** `conv_session.meeting_queue == ["shijing"]`.

---

### Heartbeat Tests (`test_heartbeat.py`)

#### HB-01 — heartbeat sends thinking event after each interval
- **What:** The heartbeat loop sends a `{"type":"agent_status","status":"thinking"}` JSON for each sleep cycle.
- **Mocks:** `asyncio.sleep = AsyncMock()`; set `agent_session.status = "active"` initially; flip to `"idle"` after 2 sleep calls.
- **Verify:** `ws_handler.send_json` called exactly 2 times with `status=="thinking"`.

#### HB-02 — heartbeat includes increasing elapsed_ms
- **What:** Each heartbeat carries `elapsed_ms` that is greater than or equal to the previous one.
- **Mocks:** Patch `time.time` to return `T`, `T+10`, `T+20`.
- **Verify:** First heartbeat `elapsed_ms ≈ 10000`; second ≈ 20000.

#### HB-03 — heartbeat stops when status changes to idle
- **What:** Once `agent_session.status` changes to `"idle"`, the heartbeat loop exits without sending further events.
- **Mocks:** Flip status to `"idle"` after first sleep; `asyncio.sleep` is a no-op.
- **Verify:** `send_json` called at most once (for the one heartbeat before status changed).

#### HB-04 — heartbeat stops immediately on cancelled status
- **What:** If `agent_session.status == "cancelled"` from the start, no heartbeat is sent.
- **Verify:** `send_json` not called.

---

### Timeout & Fallback Tests (`test_timeout.py`)

#### TO-01 — timeout sets agent status to "timeout"
- **What:** After `_handle_timeout(conv_session, agent_session)`, `agent_session.status == "timeout"`.
- **Mocks:** `tts_service.synthesize = AsyncMock(return_value=b'...')`; `ws_handler` mocked.
- **Verify:** Status field updated before any async calls complete.

#### TO-02 — timeout sends agent_status{timeout} JSON
- **What:** `_handle_timeout` sends `{"type":"agent_status","status":"timeout",...}` to the WS.
- **Verify:** `ws_handler.send_json` called with correct payload.

#### TO-03 — timeout synthesizes fallback phrase for each agent
- **What:** Each of the 4 agent names triggers a TTS call with the agent-specific fallback string.
- **Mocks:** `tts_service.synthesize = AsyncMock(return_value=b'\x00'*100)`.
- **Verify:** For each agent, `synthesize` called with `(FALLBACK_PHRASES[agent_name], agent_name)`.

#### TO-04 — timeout sends exactly one agent_audio frame (the fallback)
- **What:** `ws_handler.send_agent_audio` called exactly once after timeout.
- **Verify:** Call count == 1.

#### TO-05 — all 4 agents have a non-empty fallback phrase
- **What:** `FALLBACK_PHRASES` dict contains entries for `"ellen"`, `"shijing"`, `"eva"`, `"ming"` with non-empty strings.
- **Mocks:** None (pure data check).
- **Verify:** All 4 entries exist and are non-empty strings.

---

### _deliver_or_queue Tests (`test_deliver_or_queue.py`)

#### DQ-01 — no meeting_queue → direct send_agent_audio call
- **What:** When `conv_session.meeting_queue == []`, `_deliver_or_queue` calls `ws_handler.send_agent_audio` immediately.
- **Mocks:** `ws_handler.send_agent_audio = AsyncMock()`.
- **Verify:** `send_agent_audio` called once with the pcm chunk.

#### DQ-02 — meeting_queue non-empty → appends to audio_buffer
- **What:** When `conv_session.meeting_queue != []`, `_deliver_or_queue` appends `pcm` to `agent_session.audio_buffer` without calling `send_agent_audio`.
- **Verify:** `agent_session.audio_buffer` contains the pcm chunk; `send_agent_audio` NOT called.

#### DQ-03 — multiple direct-mode calls increment frame_seq
- **What:** Three consecutive `_deliver_or_queue` calls in non-meeting mode produce frames with frame_seq 0, 1, 2.
- **Verify:** Capture `frame_seq` args passed to `send_agent_audio`; they are 0, 1, 2.

---

### Meeting Mode Audio Sender Tests (`test_meeting_mode.py`)

#### MM-01 — sender waits for agent to finish before draining audio
- **What:** `_meeting_mode_audio_sender` polls until `agent_session.status != "active"`, then drains.
- **Mocks:** `asyncio.sleep = AsyncMock()` (no-op); flip `agent_session.status` from `"active"` to `"idle"` after 2 sleep calls.
- **Verify:** `ws_connection.send` is not called while status is active; called after flip.

#### MM-02 — sender drains all buffered audio chunks for each agent
- **What:** If `agent_session.audio_buffer = [pcm1, pcm2, pcm3]`, all 3 chunks are sent.
- **Mocks:** `agent_session.status = "idle"` (already done); `conv_session.ws_connection.send = AsyncMock()`.
- **Verify:** `ws_connection.send` called 3 times; each call is a binary frame with correct header.

#### MM-03 — frame_seq increments per chunk and wraps at 255
- **What:** 257 buffered chunks produce frame_seq 0–255, then 0 again.
- **Verify:** Parse byte[3] of each sent frame; sequence is 0,1,…,255,0,1.

#### MM-04 — sender processes agents in meeting_queue order
- **What:** Queue is `["ellen","shijing"]`; Ellen drains first, then Shijing.
- **Mocks:** Both agents already `idle` with pre-filled `audio_buffer`.
- **Verify:** Ellen's frames arrive before Shijing's in the `ws_connection.send` call list.

#### MM-05 — meeting_status is sent after each agent completes
- **What:** After each agent's audio is drained, `ws_handler.send_json` is called with `{"type":"meeting_status",...}`.
- **Mocks:** `ws_handler.send_json = AsyncMock()`.
- **Verify:** `send_json` called N times for N agents; last call has `completed == N`.

#### MM-06 — failed/timeout agents are included in failed list
- **What:** An agent with `status=="timeout"` appears in `meeting_status.failed`.
- **Mocks:** Two agents: one `idle`, one `timeout`.
- **Verify:** Final `meeting_status` JSON has `failed=[timeout_agent_name]`.

#### MM-07 — meeting_queue stores agent_session_ids, not agent names (Fix 7)
- **What:** After `dispatch(conv_session, "ellen", task)` with meeting mode active, `conv_session.meeting_queue[0]` is the UUID `agent_session_id`, not the string `"ellen"`.
- **Verify:** `conv_session.meeting_queue[0]` is a UUID string; `conv_session.agent_sessions[meeting_queue[0]]` returns the AgentSession.

#### MM-08 — sender uses asyncio.Event, not sleep polling (Fix 3)
- **What:** `_meeting_mode_audio_sender` calls `completion_event.wait()` instead of polling `asyncio.sleep(0.5)`.
- **Mocks:** Create an `asyncio.Event`; set it in a 0ms task; ensure sender proceeds immediately.
- **Verify:** `asyncio.sleep` is NOT called; `completion_event.wait()` is called; sender drains audio after event is set.

#### MM-09 — head-of-queue agent audio is streamed directly (Fix 3)
- **What:** `_deliver_or_queue` for the head-of-queue agent calls `ws_handler.send_agent_audio` immediately rather than appending to `audio_buffer`.
- **Setup:** `conv_session.meeting_queue = ["A1"]`; `agent_session.agent_session_id = "A1"`.
- **Verify:** `ws_handler.send_agent_audio` called; `agent_session.audio_buffer` remains empty.

#### MM-10 — non-head-of-queue agent audio is buffered (Fix 3)
- **What:** `_deliver_or_queue` for an agent at index 1+ appends to `audio_buffer` instead of sending.
- **Setup:** `conv_session.meeting_queue = ["A1", "A2"]`; `agent_session.agent_session_id = "A2"`.
- **Verify:** `ws_handler.send_agent_audio` NOT called; `agent_session.audio_buffer.count == 1`.

#### TO-01-ext — timeout appends sentinel assistant turn to history (Fix 9)
- **What:** After `_handle_timeout`, `agent_session.conversation_history` ends with `{"role":"assistant","content": FALLBACK_PHRASES[agent_name]}`.
- **Mocks:** Same mocks as TO-01.
- **Verify:** `agent_session.conversation_history[-1]["role"] == "assistant"`.

---

## Phase 3 Completion Criteria

- [ ] `dispatch("ellen", task)` → spawns asyncio.Task → returns `agent_session_id` in < 100 ms.
- [ ] `agent_status{thinking, elapsed_ms=0}` emitted immediately at dispatch time — no 10s grey dot. (Fix 6)
- [ ] Claude streams tokens → first sentence yielded in < 5 s → TTS called → `agent_audio` binary frame sent to client.
- [ ] Subsequent sentences arrive progressively, not all at once.
- [ ] `agent_status{thinking}` heartbeat updates elapsed_ms every 10 s while Claude is running.
- [ ] After 30 s with no completion: `agent_status{timeout}` + fallback `agent_audio` frame sent + sentinel assistant turn appended to history. (Fix 9)
- [ ] `interrupt{mode: cancel_all}` → all Claude tasks cancelled → `_meeting_mode_audio_sender` task cancelled → no further `agent_audio` frames. (Fix 3)
- [ ] `interrupt{mode: skip_speaker}` → current TTS stream cancelled → next queued agent's audio starts.
- [ ] Meeting mode (4 agents): agents compute concurrently; head-of-queue agent audio streams directly; non-head agents buffer. Event-driven delivery (no 0.5s polling). (Fix 3)
- [ ] Meeting mode `meeting_queue` stores agent_session_ids; `_find_agent_session` is O(1) dict lookup. (Fix 7)
- [ ] All 12 agent tools have stub implementations returning demo data; Claude `tool_use` blocks are handled. (Fix 4)
- [ ] TTS failures retry once then return empty audio (sentence skipped, not crash). (Fix 5)
- [ ] `resume_agent` on idle session: history preserved, second response faster than first.
- [ ] `resume_agent` on idle session after timeout: sentinel assistant turn prevents malformed consecutive-user-turns history. (Fix 9)
- [ ] `resume_agent` on active session: `ToolResponse{error: agent_busy}` returned immediately.
- [ ] Session TTL expiry cancels all agent tasks and closes WS.
