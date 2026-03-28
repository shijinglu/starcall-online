# Phase 5 — Detailed Test Cases & Execution Plan

## Testing Philosophy

Tests are organized in three levels matching the High-Level Test Design. Each test case here is a refined, implementation-ready version of the high-level cases, with explicit:
- **What is covered** — the precise behavior being verified
- **Mocks / Dependencies** — what must be stubbed and what real services are needed
- **Preconditions** — state required before the test runs
- **Verification steps** — specific assertions

---

## Level 3: Unit & Component Tests (No Backend/iOS Services)

These are the fastest tests. Pure-Python for backend; pure-Swift XCTestCase for iOS. External API calls use real credentials (not mocked) for L3 component tests.

---

### T-U-01 — Binary Frame Codec: Encode/Decode Round-Trip

**File:** `tests/unit/test_codec.py`
**Mocks:** None. Pure Python — no external dependencies.

**Covers:**
- `encode_frame` → `decode_frame` preserves all 4 header fields and PCM payload exactly.
- Edge cases for each header byte.

**Test Cases:**

| ID | Input | Expected |
|----|-------|----------|
| T-U-01a | msg_type=0x01, speaker_id=0x00, gen_id=0x00, frame_seq=0x00, pcm=b'' | All fields preserved, pcm=b'' |
| T-U-01b | msg_type=0x03, speaker_id=0x04, gen_id=0xFF, frame_seq=0xFF, pcm=3200 random bytes | All fields preserved, pcm identical |
| T-U-01c | frame_seq wraps: encode with frame_seq=256 | frame_seq stored as 0x00 (& 0xFF) |
| T-U-01d | gen_id wraps: encode with gen_id=256 | gen_id stored as 0x00 |
| T-U-01e | Data too short (< 4 bytes) | `decode_frame` raises `ValueError` |
| T-U-01f | All speaker_id values 0x00–0x04 | Each decoded correctly |
| T-U-01g | PCM payload is exactly 3200 bytes (100 ms chunk) | Payload returned byte-for-byte |
| T-U-01h | msg_type=0x99 (unknown) | Encodes/decodes without error; caller is responsible for validation |

**Assertions:**
```python
msg_type_out, speaker_id_out, gen_id_out, frame_seq_out, pcm_out = decode_frame(encoded)
assert msg_type_out == msg_type_in
assert speaker_id_out == speaker_id_in
assert gen_id_out == gen_id_in & 0xFF
assert frame_seq_out == frame_seq_in & 0xFF
assert pcm_out == pcm_in
```

---

### T-U-02 — Sentence Boundary Splitter

**File:** `tests/unit/test_sentence_splitter.py`
**Mocks:** None. Pure Python.

**Covers:**
- Correct splitting at `.`, `?`, `!`.
- Abbreviation handling (no false splits).
- Incomplete sentence at stream end.
- Empty stream.

**Test Cases:**

| ID | Input token stream | Expected output sentences |
|----|--------------------|--------------------------|
| T-U-02a | `["The risk score is high.", " You should review this."]` | `["The risk score is high.", " You should review this."]` |
| T-U-02b | `["Dr.", " Smith reviewed the case."]` | `["Dr. Smith reviewed the case."]` (no split at "Dr.") |
| T-U-02c | `["Mr.", " Jones is the ", "analyst."]` | `["Mr. Jones is the analyst."]` |
| T-U-02d | `["Is this correct? Yes,", " it is."]` | `["Is this correct?", " Yes, it is."]` |
| T-U-02e | `["Incomplete sentence without end"]` | `["Incomplete sentence without end"]` (yielded at stream end) |
| T-U-02f | `[]` | `[]` (no output) |
| T-U-02g | `["One.", "Two.", "Three."]` | `["One.", "Two.", "Three."]` |
| T-U-02h | `["There are 3.5 million", " users."]` | `["There are 3.5 million users."]` (no split on "3.5") |
| T-U-02i | `["...and finally done!"]` | `["...and finally done!"]` |
| T-U-02j | Tokens split mid-word: `["sen", "tence."]` | `["sentence."]` (tokens concatenated before split) |

---

### T-U-03 — gen_id Arithmetic

**File:** `tests/unit/test_gen_id.py`
**Mocks:** None.

**Covers:**
- `increment_gen_id` wraps at 255 → 0.
- `max(clientGen, serverGen)` semantics on the iOS side.

**Test Cases:**
- T-U-03a: `gen_id=0` → `increment` → `1`
- T-U-03b: `gen_id=254` → `increment` → `255`
- T-U-03c: `gen_id=255` → `increment` → `0` (wrapping)
- T-U-03d: `current_gen=5, frame.gen_id=4` → discard (4 < 5)
- T-U-03e: `current_gen=5, frame.gen_id=5` → accept (equal)
- T-U-03f: `current_gen=5, frame.gen_id=6` → accept (newer)
- T-U-03g: `current_gen=254, server_gen=1` → take max → 254 (wrap-around: 1 is numerically less but logically newer — note: this edge case requires clarification; recommend `(serverGen - currentGen) & 0xFF < 128` comparison for wrap safety)

---

### T-U-04 — Agent Registry

**File:** `tests/unit/test_registry.py`
**Mocks:** None.

**Test Cases:**
- T-U-04a: `registry.list_all()` returns exactly 4 entries.
- T-U-04b: Each entry has non-empty `name`, `description`, `voice_id`, `speaker_id`, `tool_set`.
- T-U-04c: `speaker_id` values are unique (1, 2, 3, 4).
- T-U-04d: `voice_id` strings match expected values from design doc.
- T-U-04e: `build_agent_roster_block()` includes all 4 agent names.
- T-U-04f: `build_agent_roster_block()` includes `dispatch_agent` and `resume_agent` instructions.

---

### T-U-05 — JSON Schema Validation

**File:** `tests/unit/test_schemas.py`
**Mocks:** None. Uses Pydantic models.

**Test Cases:**
- T-U-05a: Valid `control` JSON parses correctly.
- T-U-05b: `interrupt` JSON with `mode="cancel_all"` parses; missing `mode` defaults to `"cancel_all"`.
- T-U-05c: `agent_status` JSON with all fields parses correctly.
- T-U-05d: `meeting_status` JSON with `pending=[]` and `failed=[]` parses correctly.
- T-U-05e: `interruption` JSON with `gen_id` parses correctly.
- T-U-05f: Unknown `type` field in JSON → model raises `ValidationError` or returns `None` for `type`.

---

### T-C-01 — Gemini Live API: STT + TTS Round-Trip (Component)

**File:** `tests/component/test_gemini_live.py`
**Mocks:** None.
**Real Dependencies:** `GEMINI_API_KEY` env var set. Network access to Gemini Live API.
**Precondition:** Valid API key with quota for Gemini Live.

**Covers:**
- API connectivity, authentication.
- STT transcription accuracy.
- TTS audio output format.
- VAD end-of-utterance detection.

**Test Cases:**

| ID | Input | Verify |
|----|-------|--------|
| T-C-01a | Pre-recorded PCM of "What is two plus two?" (16kHz int16) | Transcript contains "two" or "2". TTS audio blob returned. No auth errors. |
| T-C-01b | 2 seconds of silence after question | VAD end-of-utterance fires within 3 s of silence start |
| T-C-01c | TTS audio format | Output is raw PCM, 16kHz, int16, mono (verify by byte count / duration ratio) |
| T-C-01d | Session open without API key | Raises authentication error (not a crash) |

---

### T-C-02 — Gemini Live: dispatch_agent Tool Call Emission

**File:** `tests/component/test_gemini_tool_calls.py`
**Mocks:** None (real Gemini).
**Real Dependencies:** Gemini Live API with tool injection.

**Covers:**
- Gemini emits `dispatch_agent` when prompted with a complex analytical question.
- Tool call fields match the declared schema.
- Gemini does not hallucinate agent names.

**Test Cases:**

| ID | Prompt text | Verify |
|----|-------------|--------|
| T-C-02a | "Ellen, analyze my spending patterns this month and give me a risk summary." | `dispatch_agent` emitted; `name="ellen"`; `task` non-empty |
| T-C-02b | "From yesterday's metrics, I saw a spike in ACH return cases. What's going on?" | `dispatch_agent` emitted; `name` is one of the 4 known agents |
| T-C-02c | "What time is it in London?" (simple query) | No `dispatch_agent` emitted; audio response returned directly |
| T-C-02d | Ask for an agent named "bob" (not in roster) | Gemini does not emit `dispatch_agent` or emits with a valid name |
| T-C-02e | Check tool call `name` field | Value is one of: `["ellen", "shijing", "eva", "ming"]` — never any other string |

---

### T-C-03 — Gemini Live: resume_agent Tool Call Emission

**File:** `tests/component/test_gemini_tool_calls.py`
**Mocks:** Tool response simulation (reply to dispatch_agent with a fake `agent_session_id`).
**Real Dependencies:** Gemini Live API.

**Test Cases:**
- T-C-03a: First turn triggers `dispatch_agent(ellen, task1)`. Reply with `{dispatched, agent_session_id="A1"}`. Then ask follow-up "What about last month?". Verify Gemini emits `resume_agent(A1, "What about last month?")` — not a second `dispatch_agent`.
- T-C-03b: Same as above but the follow-up is directed at a different topic. Verify Gemini correctly identifies which tool to use.

---

### T-C-04 — Claude SDK: Streaming Token Output

**File:** `tests/component/test_claude_streaming.py`
**Mocks:** None.
**Real Dependencies:** `ANTHROPIC_API_KEY`, network.

**Test Cases:**

| ID | What is tested | Verification |
|----|---------------|-------------|
| T-C-04a | First sentence yielded in < 5 s | Measure time from stream start to first sentence boundary detected |
| T-C-04b | Subsequent sentences arrive progressively | At least 3 sentences yielded with measurable time gaps (not all at once) |
| T-C-04c | All sentences concatenated = full response | Join all sentences; compare to `stream.get_final_message().content` |
| T-C-04d | No sentence cut mid-word | Each yielded sentence passes: `sentence.split()` — all tokens are complete words |
| T-C-04e | Ellen persona prompt limits tool access | Run with ellen prompt; ask to perform fraud ID check; response declines or cannot |
| T-C-04f | Empty prompt | Claude returns an error or empty response without crashing |

---

### T-C-05 — Claude SDK: Conversation History Continuity

**File:** `tests/component/test_claude_history.py`
**Mocks:** None. Real Claude.

**Test Cases:**
- T-C-05a: Turn 1: "What is the main risk in the ACH return spike?". Record response. Turn 2: "Why is the value that high?" → Response references context from turn 1 without re-asking. Verify by checking that turn-2 response does not re-ask for the ACH return spike context.
- T-C-05b: Turn 1: "Call me Shijing." Turn 2: "What did I say my name was?" → Response contains "Shijing" (context preserved).
- T-C-05c: Long conversation (10 turns) → Claude response is coherent and references earlier turns.

---

### T-C-06 — Claude SDK: 30-Second Timeout

**File:** `tests/component/test_claude_timeout.py`
**Mocks:** Wrap with `asyncio.wait_for(timeout=30)`. Use a prompt that produces very long output.

**Test Cases:**
- T-C-06a: Submit a prompt requesting a 10,000-word essay. Wrap in `asyncio.wait_for(timeout=30)`. Verify `asyncio.TimeoutError` raised at approximately 30 s (±5 s).
- T-C-06b: After timeout, no SDK exceptions or coroutine leaks remain (no `asyncio.Task` in PENDING state for this call after 35 s).
- T-C-06c: Any partial sentences received before timeout are well-formed (end with a complete word, not mid-word).

---

### T-C-07 — Google Cloud TTS: Per-Sentence Synthesis

**File:** `tests/component/test_tts_service.py`
**Mocks:** None.
**Real Dependencies:** `GOOGLE_APPLICATION_CREDENTIALS` env var, network.

**Test Cases:**

| ID | Input | Verify |
|----|-------|--------|
| T-C-07a | Short sentence (< 10 words) with voice `en-US-Journey-F` (Ellen) | PCM returned; duration 1–5 s; latency < 1.5 s |
| T-C-07b | Medium sentence (15–20 words) with voice `en-US-Journey-D` (Shijing) | PCM returned; duration proportional to length |
| T-C-07c | Long sentence (30+ words) with voice `en-US-Journey-O` (Eva) | PCM returned; no error |
| T-C-07d | All 4 voice IDs produce distinct non-empty audio | 4 blobs, all non-empty, pairwise byte-different (not identical) |
| T-C-07e | PCM format validation | Byte count divisible by 2 (int16); duration = `byte_count / 2 / 16000` seconds is > 0 |
| T-C-07f | Empty string input | Error raised or empty PCM returned without crash |

---

### T-C-08 — Agent Persona System Prompt Isolation

**File:** `tests/component/test_agent_personas.py`
**Mocks:** None. Real Claude.

**Test Cases:**
- T-C-08a: Ellen asked to "run a fraud ID check on user 1234" → response declines or states inability; does not fabricate fraud data.
- T-C-08b: Ming asked to "pull my calendar for tomorrow" → response declines; does not fabricate calendar entries.
- T-C-08c: Eva asked to "check KYC identity verification status" → response declines or redirects; does not access identity data.
- T-C-08d: Each agent's tone/persona matches declared description (qualitative check: Ellen is "warm assistant", Ming is "investigator").
- T-C-08e: Each agent uses only tools in its declared `tool_set` when a valid in-scope task is given.

---

## Level 2: Backend Integration Tests

Backend running locally. No iOS app. Uses a Python WebSocket client to send frames and observe responses.

**Setup:**
```bash
uvicorn app.main:app --port 8001 --env-file .env.test
# .env.test has real GEMINI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_APPLICATION_CREDENTIALS
```

---

### T-I-01 — Session Lifecycle REST API

**File:** `tests/integration/test_session_lifecycle.py`
**Mocks:** None. Real backend.

| Sub-test | Operation | Verify |
|----------|-----------|--------|
| T-I-01a | `POST /api/v1/sessions` | 200; body has `session_id` (non-empty), `auth_token` (UUID format), `expires_at` (ISO8601 in future) |
| T-I-01b | `GET /api/v1/health` | 200; `status == "ok"` |
| T-I-01c | `GET /api/v1/agents` | 200; 4 agents; each has `name`, `voice_id`, `speaker_id`, `tool_set` |
| T-I-01d | `DELETE /api/v1/sessions/{session_id}` | 200; `status == "terminated"` |
| T-I-01e | `DELETE /api/v1/sessions/{session_id}` again (after T-I-01d) | 404 |
| T-I-01f | Use expired auth_token to open WS (wait 5 min or mock TTL=1s) | WS connect rejected with HTTP 401 |
| T-I-01g | Two concurrent `POST /sessions` calls | Two distinct `session_id` and `auth_token` values |

---

### T-I-02 — WebSocket Auth Token Validation

**File:** `tests/integration/test_ws_auth.py`
**Mocks:** None.

| Sub-test | Token used | Expected |
|----------|-----------|---------|
| T-I-02a | Valid unused token | HTTP 101 (Upgrade accepted) |
| T-I-02b | No token (no `?token=` param) | HTTP 401 |
| T-I-02c | Malformed UUID (`?token=notauuid`) | HTTP 401 |
| T-I-02d | Already-consumed token (open WS twice with same token) | First: 101; Second: 401 |
| T-I-02e | Token for a deleted session | 401 |
| T-I-02f | Token from a different session | 401 |

---

### T-I-03 — Binary Frame Routing: Audio to Gemini

**File:** `tests/integration/test_ws_audio.py`
**Mocks:** None. Real Gemini.
**Precondition:** Pre-recorded 16kHz int16 PCM file of "What is 2 + 2?" (~2 s, ~64 kB).

| Sub-test | Operation | Verify |
|----------|-----------|--------|
| T-I-03a | Send 20× binary frames (100ms each, msg_type=0x01) | Server logs show Gemini receive; `transcript` JSON received with text containing "2" or "four" |
| T-I-03b | After transcript: `audio_response` binary frames received | At least 1 binary frame; header byte[0]=0x02; gen_id=0x00 (no interrupt yet) |
| T-I-03c | All outbound binary frames carry consistent `gen_id` | Parse byte[2] of each received binary frame; all equal the same value |
| T-I-03d | Send audio with invalid msg_type (0xFF) | Server responds with `error` JSON; does not crash |
| T-I-03e | Send audio frame with only 3 bytes (too short) | Server responds with `error` JSON or silently drops |

---

### T-I-04 — Agent Dispatch via Audio

**File:** `tests/integration/test_agent_dispatch.py`
**Mocks:** None. Real Gemini + Claude + TTS.
**Precondition:** Pre-recorded PCM of "Ellen, analyze my spending patterns and give me a risk summary."
**Timeout:** 45 s for full test.

| Sub-test | Verify |
|----------|--------|
| T-I-04a | Within 2 s: `agent_status{dispatched, agent_name="ellen"}` JSON received |
| T-I-04b | Within 12 s: at least one `agent_status{thinking, elapsed_ms}` JSON received |
| T-I-04c | Within 40 s: at least one `agent_audio` binary frame received; header byte[0]=0x03; byte[1]=0x01 (Ellen's speaker_id) |
| T-I-04d | At least 2 consecutive `agent_audio` frames received (sentence-level streaming confirmed) |
| T-I-04e | `agent_status{done}` received after all audio frames |
| T-I-04f | `gen_id` on all `agent_audio` frames matches the current session `gen_id` |

---

### T-I-05 — Agent Timeout (30 s Hard Limit)

**File:** `tests/integration/test_agent_timeout.py`
**Mocks:** Mock the Claude SDK call to hang indefinitely (`asyncio.sleep(999)`) using a test-mode flag.
**Precondition:** Backend started with `MOCK_CLAUDE_HANG=1` env var.

| Sub-test | Verify |
|----------|--------|
| T-I-05a | `agent_status{thinking}` received within 12 s |
| T-I-05b | `agent_status{timeout}` received at approximately 30 s (±5 s) |
| T-I-05c | At least 1 `agent_audio` frame received after timeout (fallback phrase) |
| T-I-05d | No further `agent_audio` frames after the fallback |
| T-I-05e | Backend log shows `asyncio.Task.cancel()` was called |

---

### T-I-06 — Resume Agent: Idle → Continues with History

**File:** `tests/integration/test_agent_resume.py`
**Mocks:** None. Real Claude.

**Steps:**
1. Dispatch Ellen with "What is the main risk in this user's profile?"
2. Wait for `agent_status{done}`.
3. Send `interrupt` (or let Gemini naturally call `resume_agent`) with follow-up "Why is that the primary risk?"

| Sub-test | Verify |
|----------|--------|
| T-I-06a | Second response arrives under the same `agent_session_id` (no new UUID) |
| T-I-06b | Second response time < 15 s (faster than first turn due to preserved context) |
| T-I-06c | `agent_audio` frames from second turn carry the same `speaker_id=0x01` (Ellen) |
| T-I-06d | `agent_status{done}` received for the second turn |

---

### T-I-07 — Resume Agent: Busy → Error Response

**File:** `tests/integration/test_agent_resume_busy.py`
**Mocks:** Mock Claude to hang for 30 s.

| Sub-test | Verify |
|----------|--------|
| T-I-07a | Dispatch Ellen with a slow task; within 3 s send `resume_agent(A1, "follow-up")` via simulated Gemini tool call |
| T-I-07b | Backend returns `ToolResponse{error: "agent_busy"}` immediately (< 500 ms) |
| T-I-07c | Original Ellen task continues; no additional `agent_status{cancelled}` event |
| T-I-07d | Backend logs confirm the resume was rejected |

---

### T-I-08 — Interrupt Modes

**File:** `tests/integration/test_interrupt.py`
**Mocks:** Mock Claude to stream slowly (1 sentence per 5 s).

#### T-I-08a — cancel_all
1. Dispatch all 4 agents in meeting mode.
2. While at least 2 are `thinking`, send `{"type": "interrupt", "mode": "cancel_all"}`.

| Verify | Assertion |
|--------|-----------|
| `interruption` JSON received | `gen_id` incremented by 1 |
| All Claude tasks cancelled | Backend logs show `Task.cancel()` ×4 |
| `meeting_queue` cleared | No further `agent_audio` frames arrive after confirmation |
| New Gemini response | `audio_response` frames arrive promptly for new user input |

#### T-I-08b — skip_speaker
1. Dispatch all 4 agents. Wait for all `agent_status{done}`.
2. First agent's audio begins playing.
3. Send `{"type": "interrupt", "mode": "skip_speaker"}`.

| Verify | Assertion |
|--------|-----------|
| First agent audio stops | No further frames from first agent |
| `gen_id` incremented | `interruption{gen_id: N}` received |
| Second agent audio starts | `agent_audio` frames with `speaker_id` of second agent arrive promptly |
| No Claude tasks cancelled | Backend logs show NO `Task.cancel()` calls (agents already done) |
| `meeting_status` continues | Eventually reaches `{completed: 4}` |

---

### T-I-09 — gen_id Stamping Consistency

**File:** `tests/integration/test_gen_id.py`
**Mocks:** None.

| Sub-test | Operation | Verify |
|----------|-----------|--------|
| T-I-09a | Record `gen_id` from first `audio_response` frame (byte[2]) | `gen_id == 0` at session start |
| T-I-09b | Send 1 `interrupt` | All subsequent frames have `gen_id == 1` |
| T-I-09c | Send 2nd `interrupt` | All subsequent frames have `gen_id == 2` |
| T-I-09d | `interruption` JSON `gen_id` matches frame headers | `json.gen_id == frame.header[2]` |
| T-I-09e | 255 interrupts (wrap) | Frame `gen_id` wraps to 0x00; no error |

---

### T-I-10 — Meeting Mode Sequential Audio Delivery

**File:** `tests/integration/test_meeting_mode.py`
**Mocks:** None (or mock Claude to return quickly with fixed sentences).
**Precondition:** Audio prompt designed to trigger all 4 agents simultaneously.

| Sub-test | Verify |
|----------|--------|
| T-I-10a | `meeting_status` JSON events appear with monotonically increasing `completed` count |
| T-I-10b | No two agents' audio streams overlap: parse frame timestamps; agent N+1 first frame timestamp > agent N last frame timestamp |
| T-I-10c | Each agent's frames carry correct `speaker_id`: 0x01 for ellen, 0x02 for shijing, etc. |
| T-I-10d | Final `meeting_status`: `{total_agents:4, completed:4, pending:[], failed:[]}` |
| T-I-10e | Total end-to-end time is < 90 s |

---

### T-I-11 — Session TTL / Cleanup

**File:** `tests/integration/test_session_ttl.py`
**Mocks:** Backend started with `SESSION_TTL_SECONDS=10` (test mode).

| Sub-test | Verify |
|----------|--------|
| T-I-11a | Open session, dispatch an agent, then wait 15 s without sending anything | WS closed by server (receive returns `ConnectionClosedOK` or `ConnectionClosedError`) |
| T-I-11b | Backend logs show all asyncio tasks for that session cancelled | Log line: "Cleanup: cancelled N tasks for session S" |
| T-I-11c | After TTL: `GET /api/v1/health` shows `active_sessions` decremented | Count goes down by 1 |
| T-I-11d | `DELETE /api/v1/sessions/{id}` (after TTL) returns 404 | Session no longer in manager dict |

---

### T-I-12 — Agent List Endpoint

**File:** `tests/integration/test_agents_endpoint.py`
**Mocks:** None.

| Sub-test | Verify |
|----------|--------|
| T-I-12a | `GET /api/v1/agents` returns exactly 4 agents | `len(response["agents"]) == 4` |
| T-I-12b | All 4 names present | Set of names == `{"ellen", "shijing", "eva", "ming"}` |
| T-I-12c | Each agent has non-empty `voice_id` | All `voice_id` strings are non-empty |
| T-I-12d | `speaker_id` values are 1, 2, 3, 4 (unique, in range) | Set of speaker_ids == `{1, 2, 3, 4}` |
| T-I-12e | Each `tool_set` is a non-empty list | All tool_set lengths ≥ 1 |

---

## Level 1: Full-Stack Tests (iOS App + Backend)

**Tooling:** `mobile-mcp` for UI automation, pre-recorded PCM files for audio injection, backend logs.
**Setup:** iOS Simulator running the app, backend at `localhost:8000`.

---

### T-E-01 — Simple Voice Query End-to-End

**Precondition:** App launched, backend healthy.

| Step | Action | Verify |
|------|--------|--------|
| 1 | Tap "Start" | `POST /sessions` called (log); WS connected (log) |
| 2 | Inject audio: "What is 2 plus 2?" | `transcript` JSON received; UI shows transcript text |
| 3 | Wait 5 s | `audio_response` frames received; audio plays back |
| 4 | Verify no agent dispatch | No `agent_status` events in logs |
| 5 | Tap "Stop" | `DELETE /sessions/{id}` called; WS closed |

---

### T-E-02 — Complex Query Triggers Agent Dispatch

| Step | Action | Verify |
|------|--------|--------|
| 1 | Start session | — |
| 2 | Inject audio: "Ellen, analyze my spending patterns..." | Within 2 s: spinner appears on Ellen card in UI |
| 3 | Listen for Gemini acknowledgment | "Ellen is on it!" audio heard before agent audio |
| 4 | Wait 40 s | Agent audio plays in distinct voice (Ellen = Journey-F) |
| 5 | Verify streaming | At least 2 `agent_audio` frames received (sentence-level) |
| 6 | Check completion | `agent_status{done}` in logs; spinner disappears |
| 7 | Verify no overlap | Moderator audio does not play during agent audio |

---

### T-E-03 — Barge-In During Agent Playback

| Step | Action | Verify |
|------|--------|--------|
| 1 | Start session, trigger agent (same as T-E-02) | Agent audio begins playing |
| 2 | Within first 5 s of agent audio: inject loud audio | Agent audio stops within ~200 ms |
| 3 | Check WS logs | `interrupt` JSON sent to backend |
| 4 | Check backend response | `interruption{gen_id: N}` received |
| 5 | Verify no stale audio | No further frames from old gen_id play |
| 6 | New response | New Gemini audio begins promptly |
| 7 | Check backend logs | Claude tasks cancelled |

---

### T-E-04 — Meeting Mode (4 Agents in Parallel)

| Step | Action | Verify |
|------|--------|--------|
| 1 | Start session | — |
| 2 | Inject audio: "Everyone, what's my risk profile?" | 4 `agent_status{dispatched}` events |
| 3 | Wait for `meeting_status` | Events show `{total_agents:4, completed:N}` incrementing |
| 4 | Wait 90 s | All 4 agent voices heard sequentially (not overlapping) |
| 5 | Verify order | Agent N+1 starts only after agent N finishes |
| 6 | Final status | `{completed:4, pending:[], failed:[]}` in logs |

---

### T-E-05 — Session Expiry / Network Drop Recovery

| Step | Action | Verify |
|------|--------|--------|
| 1 | Start session, confirm WS connected | — |
| 2 | Kill backend process (5 s) | App shows "reconnecting" state (no crash) |
| 3 | Restart backend | App attempts WS reconnect (exponential backoff in logs) |
| 4 | Old token consumed | App calls `POST /sessions` to get fresh token |
| 5 | Reconnect success | App returns to `active` state; audio capture resumes |

---

## Test Execution Plan

### Order of Execution

```
Phase 0 (Foundation) complete
    │
    ▼
T-U-01 through T-U-05 (Unit tests) — run on every commit, < 10 s total
    │
    ▼
Phase 1 (Backend Core) complete
    │
    ▼
T-I-01, T-I-02 (Session lifecycle, WS auth) — run on backend deploy, ~1 min
    │
    ▼
Phase 2 (Gemini Integration) complete
    │
    ▼
T-C-01, T-C-02, T-C-03 (Gemini component) — run manually, ~5 min (API cost)
T-I-03 (Audio routing to Gemini) — ~2 min
    │
    ▼
Phase 3 (Deep Agents) complete
    │
    ▼
T-C-04 through T-C-08 (Claude + TTS component) — run manually, ~10 min (API cost)
T-I-04 through T-I-12 (Backend integration) — run on backend deploy, ~20 min
    │
    ▼
Phase 4 (iOS App) complete
    │
    ▼
T-E-01 through T-E-05 (Full-stack) — run manually before demo, ~30 min
```

### CI/CD Recommendations

| Test Group | When to Run | Estimated Time |
|------------|------------|----------------|
| T-U-* (unit tests) | Every git push | < 30 s |
| T-I-01, T-I-02 (auth/session) | Every backend deploy | < 2 min |
| T-I-03–T-I-12 (integration) | Daily scheduled run | ~25 min |
| T-C-* (component, uses real APIs) | Pre-release, manually | ~15 min |
| T-E-* (full-stack) | Pre-demo, manually | ~35 min |

### Mock Strategy Summary

| Test Level | Claude SDK | Gemini Live | Google TTS |
|------------|-----------|------------|------------|
| Unit (T-U-*) | Not used | Not used | Not used |
| Component (T-C-*) | Real | Real | Real |
| Integration (T-I-*) | Real (some tests mock for timeout/hang) | Real | Real |
| Full-stack (T-E-*) | Real | Real | Real |

**Mock injection points** (for T-I-05, T-I-07):
- Backend reads `MOCK_CLAUDE_HANG=1` env var → Deep Agent Runner sleeps instead of calling Claude.
- Backend reads `MOCK_CLAUDE_SLOW=1` → yields 1 sentence per 5 s.
