# Execution Plan: Barge-In Demo Scripts

**Date:** 2026-04-02
**Status:** Draft
**Scope:** Revamp `backend/demos/` infrastructure to support barge-in testing

---

## Overview

The current demo infrastructure (`demo_harness.py`, `run_demo.py`, `scripts/*.json`) runs conversations sequentially: each turn waits for the previous response to complete before sending the next utterance. This plan adds barge-in testing capability, where turns can overlap with in-progress responses to exercise the backend interrupt path (`OutputController.flush()`, Gemini VAD-triggered interruption in `gemini_proxy.py`).

---

## Phase 1: Extend JSON Script Format

**Goal:** Add barge-in fields to the script schema so turns can declare overlap behavior without breaking existing scripts.

> **Note:** This is the canonical schema definition. The physical-tests plan (`barge-in-physical-tests.md`) depends on this phase and does NOT independently modify `load_script()`. Both `run_demo.py` and `run_physical.py` consume the same schema via the shared `load_script()` in `demo_harness.py`.

### Tasks

**1.1** Define new optional fields per turn in the JSON schema:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `barge_in` | bool | false | This turn should be sent while the previous response is still in progress |
| `delay_from_prev_start` | float | null | Seconds after the *previous turn's send-start* to begin this turn. Enables overlap. Mutually exclusive with `delay_from_response_start`. |
| `delay_from_response_start` | float | null | Seconds after the *first moderator/agent audio response* from the previous turn. More realistic for testing barge-in during response playback. |
| `expect_interrupt` | bool | false | Assertion flag: expects an `{"type": "interruption"}` event after this turn |
| `min_interrupt_delay_ms` | int | null | If set, the interrupt event must appear within this many ms of send-start for verification to pass |

If `barge_in` is true and neither delay field is set, default to `delay_from_prev_start: 2.0`.

**1.2** Update `load_script()` in `demo_harness.py` to validate and default these new fields:
- `turn.setdefault("barge_in", False)`
- `turn.setdefault("delay_from_prev_start", None)`
- `turn.setdefault("delay_from_response_start", None)`
- `turn.setdefault("expect_interrupt", False)`
- `turn.setdefault("min_interrupt_delay_ms", None)`
- Validate that `delay_from_prev_start` and `delay_from_response_start` are not both set.

**1.3** Update `run_demo.py` to pass full turn dicts (not just text/wait lists) to `run_demo()`.

**Affected files:**
- `backend/demos/demo_harness.py` (`load_script`)
- `backend/demos/run_demo.py` (argument extraction)

**Acceptance criteria:**
- Existing `case_1.json` through `case_6.json` load and run unchanged (all new fields default to safe values).
- A script with `"barge_in": true` on a turn loads without error.

---

## Phase 2: Modify Send Loop for Overlapping Sends

**Goal:** Allow `run_demo()` to fire the next turn before the current moderator response is done.

### Tasks

**2.1** Refactor the conversation loop in `run_demo()`. The current loop is:
```
for each turn:
    generate TTS
    send_audio (blocking, ~real-time)
    wait for response_event (moderator final transcript)
```
Change to:
```
for each turn:
    if turn.barge_in:
        compute target_fire_time from delay_from_prev_start or delay_from_prev_end
        sleep until target_fire_time (do NOT wait for response_event)
    else:
        wait for response_event as before
    generate TTS (or pre-generate in parallel)
    send_audio
```

**2.2** Pre-generate TTS for barge-in turns. TTS generation takes ~200-500ms. For barge-in turns, generate TTS audio *before* the wait period so it is ready to fire immediately at the target time.

**2.3** Handle gen_id tracking. When a barge-in turn fires and the backend sends back `{"type": "interruption", "gen_id": N}`, the receive_loop should update a shared gen_id so subsequent frames use the new generation.

**2.4** Make `send_audio` cancellable. If a barge-in turn fires while a previous `send_audio` is still streaming silence frames, the previous send should be cancelled.

**Affected files:**
- `backend/demos/demo_harness.py`:
  - `run_demo()`: main loop restructure
  - `send_audio()`: add cancellation support
  - `receive_loop()`: update gen_id on interruption events

**Acceptance criteria:**
- A script with `"barge_in": true, "delay_from_prev_start": 3.0` fires the turn 3 seconds after the previous turn started sending.
- The previous turn's silence-sending is cancelled when a barge-in turn fires.
- Gen_id is updated after receiving an interruption event.

---

## Phase 3: Add Barge-In Timing Metrics

**Goal:** Capture and report latencies specific to barge-in scenarios.

### Tasks

**3.1** Extend `TurnTiming` dataclass with new fields:
- `is_barge_in: bool = False`
- `barge_in_fire_t: float = 0.0` -- when the barge-in turn actually started sending
- `barge_in_target_t: float = 0.0` -- when it was scheduled to fire
- `interrupt_received_t: float = 0.0` -- when the `{"type": "interruption"}` message arrived
- `time_to_silence_ms: float = 0.0` -- from barge-in fire to last audio chunk from backend
- `prev_turn_audio_interrupted: bool = False` -- whether the previous turn's moderator audio was still playing when this turn fired

**3.2** Update `receive_loop()` interruption handler to record `interrupt_received_t` on the current timing object.

**3.3** Update `print_timing_report()` to include a barge-in section for turns where `is_barge_in` is true:
- Barge-in firing accuracy (actual vs target)
- Time from barge-in send to interrupt-received
- Time-to-silence
- Whether the interrupt expectation was met

**3.4** Add a barge-in summary table:
```
BARGE-IN SUMMARY
Turn  Utterance       Fire Delay  Interrupt Rcvd  Time-to-Silence  Pass
7     "Hold on..."    3.02s       +180ms          +45ms            YES
```

**Affected files:**
- `backend/demos/demo_harness.py`:
  - `TurnTiming` dataclass
  - `receive_loop()`
  - `print_timing_report()`

**Acceptance criteria:**
- Barge-in turns show interrupt latency and time-to-silence in the timing report.
- Non-barge-in turns show no barge-in metrics (backward compatible).
- `expect_interrupt` mismatches are flagged with a FAIL marker.

---

## Phase 4: Create Barge-In Test Scripts

**Goal:** Ship concrete test scenarios as JSON scripts.

### Scripts

**4.1** `case_7_barge_in.json` -- Single mid-speech interruption:
- Turn 1: "Ellen, give me a detailed report on yesterday's transaction anomalies." (wait: 15)
- Turn 2: "Hold on, just the top 3 by dollar amount." (barge_in: true, delay_from_prev_start: 5.0, expect_interrupt: true, wait: 12)
- Turn 3: "Thanks, that's all." (wait: 8)

**4.2** `case_8_rapid_interrupt.json` -- Rapid-fire interruptions:
- Turn 1: "Summarize the fraud report." (wait: 10)
- Turn 2: "No wait." (barge_in: true, delay_from_prev_start: 3.0, expect_interrupt: true, wait: 5)
- Turn 3: "Actually, start with chargebacks." (barge_in: true, delay_from_prev_start: 2.0, expect_interrupt: true, wait: 5)
- Turn 4: "OK go ahead." (wait: 15)

**4.3** `case_9_interrupt_agent.json` -- Interrupt during agent audio playback:
- Turn 1: "Shijing, analyze the ACH return spike from yesterday." (wait: 20)
- Turn 2: "Stop, I changed my mind. Eva, look into it instead." (barge_in: true, delay_from_response_start: 2.0, expect_interrupt: true, wait: 20)

**4.4** `case_10_no_interrupt_baseline.json` -- Baseline with same utterances but no barge-in (for latency comparison).

**Affected files:**
- `backend/demos/scripts/case_7_barge_in.json` (new)
- `backend/demos/scripts/case_8_rapid_interrupt.json` (new)
- `backend/demos/scripts/case_9_interrupt_agent.json` (new)
- `backend/demos/scripts/case_10_no_interrupt_baseline.json` (new)

**Acceptance criteria:**
- All four scripts load via `run_demo.py --list` without error.
- `case_7` triggers at least one `[INTERRUPT]` log line in the demo output.
- `case_8` triggers at least two interruption events.

---

## Phase 5: Verify and Harden Interrupt Event Handling

**Goal:** Ensure the receive_loop and backend interrupt path are robust under overlapping audio.

### Tasks

**5.1** Audit the receive_loop interruption handler. Add:
- Update `gen_id_ref` so subsequent `send_audio` calls use the new gen_id.
- If `current` timing exists and `expect_interrupt` is set, mark it as satisfied.
- Track interrupt count per turn for the timing report.

**5.2** Handle the edge case where `response_event` fires after a barge-in. Add a secondary event (`interrupt_event`) or use `asyncio.wait` on both events.

**5.3** Test `send_audio` cancellation safety. Verify the WebSocket does not receive partial/corrupt frames.

**5.4** Validate gen_id monotonicity. Log a warning if a received gen_id is not strictly greater than the last known gen_id.

**Affected files:**
- `backend/demos/demo_harness.py`:
  - `receive_loop()`: interrupt handling expansion
  - `send_audio()`: cancellation point verification
  - `run_demo()`: dual-event wait logic

**Acceptance criteria:**
- Running `case_8_rapid_interrupt.json` completes without WebSocket errors or frame corruption.
- Gen_id warnings are logged if monotonicity is violated.
- All `expect_interrupt: true` turns either pass or print a clear FAIL.

---

## Sequencing and Dependencies

```
Phase 1 (schema)  ─────> Phase 2 (send loop)  ─────> Phase 5 (hardening)
                   \                            /
                    └──> Phase 3 (metrics) ────┘
                          Phase 4 (scripts) ──────────────────────────────┘
```

- Phase 1 is a prerequisite for all others.
- Phase 2 and Phase 3 can be developed in parallel after Phase 1.
- Phase 4 can be written at any time after Phase 1, but cannot be *run* until Phase 2 is done.
- Phase 5 depends on Phases 2 and 3 being complete.
