# Test Results & Setup Guide

**Date:** 2026-03-28
**Platform:** macOS Darwin 24.6.0, Python 3.12.10

---

## Test Summary

| Level | Test ID | Description | Tests | Status |
|-------|---------|-------------|-------|--------|
| Unit | L3-09 | Binary frame header encode/decode | 19 | PASSED |
| Unit | L3-10 | Sentence boundary splitter | 13 | PASSED |
| L3 | L3-01 | Gemini Live STT+VAD+TTS round-trip | 1+1skip | PASSED |
| L3 | L3-02 | Gemini Live tool call emission | 2 | PASSED |
| L3 | L3-03 | Gemini Live resume_agent tool call | 1 | PASSED |
| L3 | L3-04 | Claude SDK streaming token output | 2 | PASSED |
| L3 | L3-05 | Claude SDK conversation history | 1 | PASSED |
| L3 | L3-06 | Claude SDK 30s timeout behavior | 2 | PASSED |
| L3 | L3-07 | Google Cloud TTS per-sentence | 6 | SKIPPED |
| L3 | L3-08 | Agent persona system prompt isolation | 8 | PASSED |
| L2 | L2-01 | Session lifecycle REST API | 4 | PENDING |
| L2 | L2-02 | WebSocket auth token validation | 4 | PENDING |
| L2 | L2-12 | Agent list endpoint | 3 | PENDING |

**Totals: 49 passed, 7 skipped (TTS creds + no PCM file), 11 pending (need running backend)**

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

## Setup Required for Skipped/Pending Tests

### Google Cloud TTS (L3-07) — 6 tests skipped

`GOOGLE_APPLICATION_CREDENTIALS` points to a placeholder path (`/path/to/tts-service-account.json`).

**Fix:**
1. Create a Google Cloud project with the Text-to-Speech API enabled
2. Create a service account with TTS permissions
3. Download the JSON key file
4. Update `.env`:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/tts-service-account.json
   ```

### Gemini Audio Round-Trip (L3-01 audio test) — 1 test skipped

Requires a pre-recorded 16kHz int16 PCM audio file.

**Fix:**
```bash
export TEST_AUDIO_PCM_FILE=/path/to/question.pcm
```

### Integration Tests (L2-01, L2-02, L2-12) — 11 tests pending

Need the backend running on `localhost:8000`.

**Fix:**
1. Start backend: `make run`
2. Run tests: `make test-integration`
3. Optionally set `TEST_BACKEND_URL` for a different host

---

## Configuration Notes

### Gemini Model

The project uses `gemini-2.5-flash-native-audio-latest` with API version `v1alpha`.
This is configured in `app/config.py` as `GEMINI_MODEL` and can be overridden via `.env`.

The model `gemini-2.0-flash-live` listed in the original design doc is no longer available.
The replacement model supports `bidiGenerateContent` (live streaming), tool calls, and audio output.

---

## Test Architecture

```
tests/
├── unit/                          # Pure logic tests, no external calls
│   ├── test_codec.py              # L3-09: Binary frame encode/decode (19 tests)
│   └── test_sentence_splitter.py  # L3-10: Sentence boundary detection (13 tests)
├── component/                     # L3: Direct external API calls
│   ├── test_l3_01_gemini_live.py       # Gemini Live connectivity + audio
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

These tests from the design doc require more complex setup:

| Test ID | Description | Blocker |
|---------|-------------|---------|
| L2-03 | Binary frame routing to Gemini | Needs pre-recorded PCM + live Gemini session |
| L2-04 | Agent dispatch via simulated tool call | Needs full Gemini+Claude integration |
| L2-05 | Agent 30s timeout | Needs mock Claude or very slow prompt |
| L2-06 | Resume agent (idle) | Needs full dispatch+resume flow |
| L2-07 | Resume agent (busy) | Needs concurrent agent dispatch |
| L2-08 | Interrupt modes (cancel_all, skip_speaker) | Needs meeting mode with multiple agents |
| L2-09 | gen_id stamping consistency | Needs binary frame inspection during interrupt |
| L2-10 | Meeting mode sequential delivery | Needs 4 concurrent agents + timing |
| L2-11 | Session TTL cleanup | Needs configurable short TTL + wait |
