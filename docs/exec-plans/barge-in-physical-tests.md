# Execution Plan: Barge-In Physical Device Testing

**Date:** 2026-04-02
**Status:** Draft
**Scope:** Revamp `run_physical.py` for barge-in testing on real iPhone hardware

---

## Problem Statement

The current physical device test runner (`run_physical.py`) executes conversation turns strictly sequentially: Mac speaks an utterance via `say`, blocks until speech finishes, waits N seconds polling logs, then moves to the next turn. This makes it impossible to test barge-in because barge-in requires the Mac to speak a new utterance *while the iPhone is still playing back audio from a previous response*.

---

## Phase 1: Script Schema (Dependency on Demo Scripts Plan)

**Goal**: Consume the unified barge-in script schema.

> **This phase is NOT independently implemented.** The schema extension (new fields: `barge_in`, `delay_from_prev_start`, `delay_from_response_start`, `expect_interrupt`, `min_interrupt_delay_ms`) is defined and implemented in the **barge-in-demo-scripts** plan, Phase 1. That plan owns the canonical `load_script()` changes in `demo_harness.py`. This plan is a downstream consumer.

**Dependency**: `barge-in-demo-scripts.md` Phase 1 must be completed first.

**Acceptance criteria:**
- All existing `case_*.json` scripts load without changes.
- New barge-in scripts (Phase 4 below) load and validate with the schema from the demo-scripts plan.

---

## Phase 2: Non-blocking Speech and Timer-based Turn Scheduling

**Goal**: Replace the blocking `say()` + sequential `poll_logs()` loop with a scheduler that can fire overlapping turns.

**Affected files**: `backend/demos/run_physical.py`

### Tasks

**2.1** Make `say()` non-blocking by adding an async variant:

```python
def say_async(text: str, voice: str = DEFAULT_VOICE) -> subprocess.Popen:
    """Start macOS say in background, return Popen handle."""
    return subprocess.Popen(["say", "-v", voice, text])
```

**2.2** Refactor the conversation loop in `run_demo()` into a two-mode dispatcher:

- **Sequential mode** (current behavior): For turns with only `wait`, keep the existing block-then-wait pattern.
- **Timed mode**: For turns with `delay_from_prev_start`, compute absolute fire times relative to the first turn's start, then use a scheduler loop.

Pseudocode:
```
fire_times = compute_absolute_fire_times(conversation)
conv_start = time.monotonic()
next_turn_idx = 0

while next_turn_idx < len(conversation):
    now = time.monotonic() - conv_start
    if now >= fire_times[next_turn_idx]:
        turn = conversation[next_turn_idx]
        tl.add(f"SAY [{next_turn_idx+1}]: \"{turn['text']}\"")
        active_say_proc = say_async(turn['text'], voice)
        next_turn_idx += 1
    log_offset = poll_logs_once(log_offset, tl)
    time.sleep(0.3)
```

**2.3** `compute_absolute_fire_times()` logic:
- Turn 0 always fires at t=0.
- **Barge-in turns** (have `delay_from_prev_start`): fire time = fire_time[N-1] + delay_from_prev_start. These turns MUST use `delay_from_prev_start` for deterministic timing.
- **Sequential turns** (no delay field): these are NOT pre-scheduled. They fire dynamically after the previous turn's `say` process completes + `wait` seconds. The scheduler tracks the actual `say` process completion via `Popen.poll()` rather than estimating duration.
- **Constraint**: Within a single script, do not mix the two modes for adjacent turns. A barge-in turn followed by a sequential turn is fine (the sequential turn waits for the barge-in's `say` to finish). But a sequential turn's fire time cannot be pre-computed, so it acts as a barrier in the schedule.

**2.4** Extract `poll_logs_once()` from the existing `poll_logs()` so the scheduler loop can call it on each tick without a nested sleep loop.

**Acceptance criteria:**
- Existing sequential scripts produce identical timeline output (no regression).
- A barge-in script fires turn N while turn N-1's response audio is still playing on the iPhone.

---

## Phase 3: Barge-in Verification in Log Analysis

**Goal**: Automatically verify that barge-in mechanics worked correctly after a test run.

**Affected files**: `backend/demos/run_physical.py`

### Tasks

**3.1** Add a `BargeInVerifier` class that runs after the conversation drain phase:

```python
class BargeInVerifier:
    def __init__(self, timeline: Timeline, script: dict): ...
    def verify(self) -> list[VerificationResult]: ...
```

For each turn where `expect_interrupt` is true, the verifier checks:

| Check | Pass condition |
|-------|---------------|
| Interrupt fired | An `INTERRUPT: barge-in` event exists in the timeline after this turn's SAY start |
| Interrupt timing | Interrupt event occurred within `min_interrupt_delay_ms` of SAY start (if specified) |
| Audio flushed | `items_flushed > 0` in the interrupt event |
| Playback cut | An `INTERRUPT: ... playback cut` event exists near the interrupt |
| New response started | A GEMINI heard / AGENT dispatched event follows the interrupt |

**3.2** Print a BARGE-IN VERIFICATION section in the terminal output:

```
BARGE-IN VERIFICATION
=====================
Turn 3 -> Turn 4 (barge-in):
  [PASS] Interrupt fired at 12.3s (1.2s after SAY start)
  [PASS] Flushed 2 items from response queue
  [PASS] Playback cut: ellen at 3.4s/8.1s (42% played)
  [PASS] New response within 2.5s of interrupt

Turn 5 -> Turn 6 (barge-in):
  [FAIL] No interrupt event found within 5s window
```

**3.3** Save verification results alongside the existing timeline file.

**Acceptance criteria:**
- Sequential scripts produce "No barge-in turns to verify" (no false positives).
- Barge-in scripts produce per-turn PASS/FAIL verdicts.

---

## Phase 4: Physical Barge-in Test Scripts

**Goal**: Create test scripts that exercise realistic barge-in scenarios on physical hardware.

> **Note:** These scripts use the same unified JSON schema as the demo-scripts plan. The same script files can be used by both `run_demo.py` (WebSocket) and `run_physical.py` (physical device). Physical-specific tuning is handled via the `delay_from_prev_start` values (larger to account for acoustic propagation).

### Scripts

**4.1** `barge_in_simple.json` -- Single mid-response interruption:
```json
{
  "name": "Barge-in: simple mid-response interrupt",
  "description": "User asks for a long agent report, then interrupts mid-playback.",
  "final_wait": 15,
  "conversation": [
    {"text": "hello", "wait": 8},
    {"text": "Ellen, give me a detailed summary of yesterday's transaction volume, chargebacks, and risk alerts.", "wait": 5},
    {
      "text": "stop, just give me the top 3 chargebacks by dollar amount",
      "barge_in": true,
      "delay_from_prev_start": 8,
      "expect_interrupt": true,
      "min_interrupt_delay_ms": 3000
    }
  ]
}
```

**4.2** `barge_in_multi_agent.json` -- Interrupt while agent queue has pending items:
```json
{
  "name": "Barge-in: multi-agent queue flush",
  "description": "Two agent tasks queued, user interrupts during first agent's playback.",
  "final_wait": 20,
  "conversation": [
    {"text": "hello", "wait": 8},
    {"text": "Pull my calendar for today and also review the fraud alerts.", "wait": 3},
    {
      "text": "actually never mind the fraud alerts, just the calendar",
      "barge_in": true,
      "delay_from_prev_start": 10,
      "expect_interrupt": true
    }
  ]
}
```

**4.3** `barge_in_rapid.json` -- Two rapid interruptions:
```json
{
  "name": "Barge-in: rapid double interrupt",
  "description": "User interrupts twice in quick succession.",
  "final_wait": 15,
  "conversation": [
    {"text": "hello", "wait": 8},
    {"text": "Give me a full rundown of today's schedule.", "wait": 3},
    {
      "text": "wait",
      "barge_in": true,
      "delay_from_prev_start": 6,
      "expect_interrupt": true
    },
    {
      "text": "actually go ahead",
      "barge_in": true,
      "delay_from_prev_start": 3,
      "expect_interrupt": false
    }
  ]
}
```

**Physical constraint tuning guide:**
- `delay_from_prev_start` should be at least 5s for a barge-in to land during response playback (~2s Mac TTS + ~1s air propagation + ~1s Gemini VAD + ~1s agent dispatch)
- AEC settling time is ~500ms after playback starts; interruptions earlier may not be picked up cleanly
- Minimum practical gap between consecutive barge-ins: ~3s (Gemini re-enter listening state)

**Acceptance criteria:**
- All scripts load via `run_physical.py --list`.
- `barge_in_simple` produces at least one INTERRUPT event when run against a live device.

---

## Phase 5: Timeline Report Enhancements

**Goal**: Surface barge-in metrics at a glance in the timeline report.

**Affected files**: `backend/demos/run_physical.py` (Timeline class and `print_report()`)

### Tasks

**5.1** Add computed metrics to the timeline report footer:

| Metric | Definition |
|--------|-----------|
| Interrupt latency | Time from barge-in SAY start to INTERRUPT event in backend logs |
| Playback coverage | Percentage of agent audio played before cut |
| Recovery time | Time from INTERRUPT to next GEMINI heard event |
| Queue depth at interrupt | Number of items flushed |

**5.2** Add a BARGE-IN METRICS table to `print_report()`:

```
BARGE-IN METRICS
================
  Turn  Trigger        Interrupt Latency  Playback%  Recovery  Flushed
  3->4  SAY at 12.1s   1.2s               42%        2.5s      2 items
  5->6  SAY at 22.4s   0.9s               31%        1.8s      1 item
```

**5.3** Add `--verify-only` CLI flag that re-reads a saved timeline + log snapshot and runs the barge-in verifier without re-running the test.

**Acceptance criteria:**
- Sequential test timelines show "No barge-in events" in the metrics section.
- Barge-in test timelines show the metrics table with populated values.

---

## Sequencing

```
[barge-in-demo-scripts Phase 1] --> Phase 1 (schema dep) --> Phase 2 (scheduler) --> Phase 3 (verification)
                                                                                 --> Phase 4 (scripts)
                                                                                            |
                                                                          Phase 5 (reports) <--+
```

- **Phase 1 depends on barge-in-demo-scripts.md Phase 1** (shared schema in `load_script()`).
- Phase 2 depends on Phase 1.
- Phases 3, 4, and 5 can proceed in parallel after Phase 2.

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| AEC fails to suppress Mac speech during barge-in, causing double-trigger | Spurious interrupts | Add `aec_settling_ms` field; insert silence gap before barge-in |
| `say` command duration unpredictable | Barge-in fires too early/late | Measure actual duration; store calibration factor |
| iPhone speaker volume too low for Mac speech during playback | Barge-in never triggers | Use `--volume 80+`; position Mac speaker closer to iPhone mic |
| Backend Response Queue not yet merged | Cannot test end-to-end | Verifier detects "interrupt expected but not found" -- scripts still run, just report failures |
