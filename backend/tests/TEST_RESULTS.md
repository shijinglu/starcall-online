# Test Results & Setup Guide

**Date:** 2026-03-28
**Platform:** macOS Darwin 24.6.0, Python 3.12.10

---

## Test Summary

| Level | Test ID | Description | Status | Notes |
|-------|---------|-------------|--------|-------|
| Unit | L3-09 | Binary frame header encode/decode | 19 PASSED | `tests/unit/test_codec.py` |
| Unit | L3-10 | Sentence boundary splitter | 13 PASSED | `tests/unit/test_sentence_splitter.py` |
| L3 | L3-01 | Gemini Live STT+VAD+TTS round-trip | FAILED | Gemini API key rejected (see setup) |
| L3 | L3-02 | Gemini Live tool call emission | FAILED | Same Gemini API key issue |
| L3 | L3-03 | Gemini Live resume_agent tool call | FAILED | Same Gemini API key issue |
| L3 | L3-04 | Claude SDK streaming token output | 2 PASSED | Sentences arrive progressively |
| L3 | L3-05 | Claude SDK conversation history | 1 PASSED | Multi-turn context preserved |
| L3 | L3-06 | Claude SDK 30s timeout behavior | 2 PASSED | TimeoutError raised, no leaks |
| L3 | L3-07 | Google Cloud TTS per-sentence | 6 SKIPPED | No service account configured |
| L3 | L3-08 | Agent persona system prompt isolation | 8 PASSED | All 4 agents scoped correctly |
| L2 | L2-01 | Session lifecycle REST API | 4 PENDING | Requires running backend |
| L2 | L2-02 | WebSocket auth token validation | 4 PENDING | Requires running backend |
| L2 | L2-12 | Agent list endpoint | 3 PENDING | Requires running backend |

**Totals: 45 passed, 4 failed (Gemini key), 6 skipped (TTS creds), 11 pending (need backend)**

---

## How to Run

### Unit Tests (no external deps)
```bash
make test-unit
# or: uv run pytest tests/unit/ -v
```

### Component Tests (L3 — requires API keys in .env)
```bash
make test-component
# or: uv run pytest tests/component/ -v
```

### Integration Tests (L2 — requires running backend)
```bash
# Terminal 1: start the backend
make run

# Terminal 2: run integration tests
make test-integration
# or: RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/ -v
```

### All Tests
```bash
make test
```

---

## Setup Required for Failing/Skipped Tests

### Gemini Live API (L3-01, L3-02, L3-03)

The Gemini Live API tests fail with:
```
API Key not found. Please pass a valid API key.
```

**Fix:** The `GEMINI_API_KEY` in `.env` may be invalid or the Gemini Live API (`gemini-2.0-flash-live`) may not be enabled for this key.

1. Go to https://aistudio.google.com/apikey and generate/verify the key
2. Ensure the key has access to the Gemini 2.0 Flash Live model
3. Update `GEMINI_API_KEY` in `backend/.env`
4. Optionally set `TEST_AUDIO_PCM_FILE` env var pointing to a pre-recorded 16kHz int16 PCM file to test the audio round-trip path (L3-01 audio test)

### Google Cloud TTS (L3-07)

All 6 TTS tests are skipped because `GOOGLE_APPLICATION_CREDENTIALS` points to a placeholder path.

**Fix:**
1. Create a Google Cloud project with the Text-to-Speech API enabled
2. Create a service account with TTS permissions
3. Download the JSON key file
4. Update `.env`:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/tts-service-account.json
   ```

### Integration Tests (L2-01, L2-02, L2-12)

These need the backend running on `localhost:8000`.

**Fix:**
1. Start the backend: `make run`
2. In another terminal: `make test-integration`
3. Optionally set `TEST_BACKEND_URL` env var to point to a different host

---

## Test Architecture

```
tests/
├── unit/                          # Pure logic tests, no external calls
│   ├── test_codec.py              # L3-09: Binary frame encode/decode
│   └── test_sentence_splitter.py  # L3-10: Sentence boundary detection
├── component/                     # L3: Direct external API calls
│   ├── test_l3_01_gemini_live.py  # Gemini Live connectivity
│   ├── test_l3_02_gemini_tool_call.py  # dispatch_agent tool emission
│   ├── test_l3_03_gemini_resume.py     # resume_agent follow-up
│   ├── test_l3_04_claude_streaming.py  # Streaming + sentence splitting
│   ├── test_l3_05_claude_history.py    # Multi-turn history continuity
│   ├── test_l3_06_claude_timeout.py    # 30s timeout + cancellation
│   ├── test_l3_07_tts_synthesis.py     # Google TTS per-sentence
│   └── test_l3_08_persona_isolation.py # Per-agent tool scoping
└── integration/                   # L2: Backend-only (needs running server)
    ├── test_l2_01_session_lifecycle.py # REST session CRUD
    ├── test_l2_02_ws_auth.py           # WebSocket token validation
    └── test_l2_12_agent_list.py        # Agent registry endpoint
```

---

## Tests Not Yet Implemented

These tests from the design doc require more complex setup (mocks, timing control, or concurrent audio streams):

| Test ID | Description | Why Not Implemented |
|---------|-------------|---------------------|
| L2-03 | Binary frame routing to Gemini | Needs valid Gemini key + pre-recorded PCM audio |
| L2-04 | Agent dispatch via simulated tool call | Needs valid Gemini key + backend running |
| L2-05 | Agent 30s timeout | Needs mock Claude to hang or a very slow prompt |
| L2-06 | Resume agent (idle) | Needs full dispatch+resume flow with Gemini |
| L2-07 | Resume agent (busy) | Needs concurrent agent dispatch |
| L2-08 | Interrupt modes (cancel_all, skip_speaker) | Needs meeting mode with multiple agents |
| L2-09 | gen_id stamping consistency | Needs binary frame inspection during interrupt |
| L2-10 | Meeting mode sequential delivery | Needs 4 concurrent agents + timing verification |
| L2-11 | Session TTL cleanup | Needs configurable short TTL + wait |

These tests are complex integration scenarios that require:
- A working Gemini Live API connection
- Pre-recorded audio files for voice input
- Precise timing control for meeting mode and barge-in tests
- Potentially mock services for timeout tests
