---
name: test-case-diag
description: Translate a user-flow test case into a conversation script, run it on a physical device, collect logs, and produce a diagnostic summary comparing actual behavior to the planned flow
---

# Test Case Diagnostics

End-to-end skill: takes a user-flow description, converts it to a runnable conversation script, executes it on a physical iPhone, and diagnoses the results.

---

## Step 1: Collect the test case from the user

Ask the user:

> Please paste or describe the test case you'd like to run.
> You can copy a user flow from `docs/overview.md` (e.g. Example Case 2) or write your own in the same conversational format.

Wait for the user to provide the test case before proceeding.

---

## Step 2: Translate to a conversation script

Read the test case and convert it into the JSON script format used by `backend/demos/run_physical.py`.

Reference format (from `backend/demos/scripts/case_2.json`):

```json
{
  "name": "Case N: <short title>",
  "description": "<one-line summary of what this test validates>",
  "final_wait": <seconds to wait after last turn for trailing agent responses>,
  "conversation": [
    {"text": "<user utterance>", "wait": <seconds to wait for response>},
    ...
  ]
}
```

### Translation rules

1. **Only include user utterances** — lines prefixed with `(user):`. Skip `(app):`, `(ellen):`, `(eva):`, etc. — those are *expected* responses, not inputs.
2. **Set `wait` based on expected complexity:**
   - Simple greeting / acknowledgment: `8`s
   - Moderator-only response (no deep agent): `10`s
   - Deep agent dispatch (single agent): `12-14`s
   - Multi-agent dispatch or complex reasoning: `18-20`s
   - Follow-up to an already-running agent: `12`s
3. **Set `final_wait`** based on how many agents are expected to respond after the last utterance:
   - 0 agents: `10`
   - 1 agent: `15`
   - 2+ agents: `20-25`
4. **Generate a timestamp-based filename:** `backend/demos/scripts/case_YYYYMMDD_HHMMSS.json` using the current date/time.

### Before running

- Show the user the generated JSON script and the expected conversation flow side-by-side.
- Ask the user to confirm or adjust wait times before proceeding.
- Save the confirmed script to the timestamped file.

---

## Step 3: Setup

Run the [setup script](./scripts/test_case_diag_setup.sh) to ensure the backend server is up:

```bash
cd /Users/shijinglu/Workspace/hackthon/.claude/skills/test-case-diag/scripts/
bash scripts/test_case_diag_setup.sh
```

This script checks whether the backend is running and starts it if needed. Wait for it to complete before proceeding.

---

## Step 4: Build and install the app

Before running the test, **always** rebuild the iOS app and deploy it to the **physical device** (not the simulator). This ensures the test runs against the latest code on real hardware.

### Preferred way: use devicectl + xcrun

```bash
xcrun devicectl device install app --device 'iPhone 3G' /Users/shijinglu/Library/Developer/Xcode/DerivedData/StarCall-gddnadvbmirjibdgyairnhyzddqj/Build/Products/Debug-iphoneos/StarCall.app 2>&1
```

AND then
```bash
xcrun devicectl device process launch --device 'iPhone 3G' com.shijinglu.StarCall 2>&1
```

### Alternative way: Use computer-use mcp to bring xcode front and 
1. Click the Stop button to stop current session
2. Click the Run (▶) button to re-build and re-deploy the app.

If this step failed, stop and ask user to fix computer-use

---

## Step 5: Run the test case

Execute the physical demo runner:

```bash
cd /Users/shijinglu/Workspace/hackthon/backend && \
  .venv/bin/python demos/run_physical.py demos/scripts/case_YYYYMMDD_HHMMSS.json --no-prompt
```

**Important:** Use `--no-prompt` so the runner doesn't block waiting for interactive input. The runner will:
- Detect the iPhone
- Launch VoiceAgent
- Speak each utterance via macOS TTS
- Poll backend logs and build a timeline
- Save a log snapshot and timeline to `backend/demos/output/`

Let the command run to completion (it may take several minutes). Use a timeout of at least 300 seconds (5 minutes).

---

## Step 6: Collect diagnostic artifacts

After the runner finishes, gather these artifacts:

### 6a. Runner output
The runner's stdout/stderr — already captured from Step 5.

### 6b. Backend logs
Read the latest log snapshot saved by the runner in `backend/demos/output/`. Also read the timeline file.

```bash
ls -t backend/demos/output/physical_*_timeline.txt | head -1
ls -t backend/demos/output/physical_*.log | head -1
```

Read both files.

### 6c. Backend app log (recent tail)
Read the tail of `backend/logs/app.log` (last 300 lines) for additional context like errors, warnings, or agent dispatch details.

### 6d. Mobile logs

Try fetching mobile logs with devicectl, for example: 
`xcrun devicectl device copy from --device 'iPhone 3G' --domain-type appDataContainer --domain-identifier com.shijinglu.StarCall --source Documents/Logs/StarCall.log --destination /tmp/StarCall_diag.log`

If we are running in simulator, and if mobile-mcp is available, attempt to capture device logs with mobile-mcp. 

If none accessible, note this in the report and proceed without them.

---

## Step 7: Diagnose — compare actual vs. planned

Using the original test case (the *expected* conversation flow) and the collected logs, analyze each of the following dimensions:

### 5a. User flow replication
For each turn in the original test case:
- Was the user utterance correctly heard by Gemini? (Check `GEMINI heard:` lines in logs)
- Did the expected responder reply? (e.g., if `(ellen):` was expected, was Ellen dispatched?)
- Was the response semantically appropriate? (Check moderator/agent transcript text)
- Were responses delivered in the expected order?

Build a turn-by-turn comparison table:

```
| Turn | Expected                        | Actual                          | Match? |
|------|---------------------------------|---------------------------------|--------|
| 1    | (user): "hello"                 | GEMINI heard: "hello"           | Yes    |
|      | (app): "hello, how can I help"  | MOD: "hello how can I help you" | Yes    |
| 2    | (user): "pull TODO items"       | GEMINI heard: "pull TODO..."    | Yes    |
|      | (app): "okie, notified Ellen"   | MOD: "sure, notifying Ellen"    | Yes    |
|      | (ellen): "hi boss, ..."         | AGENT ellen: "hi boss, ..."     | Yes    |
...
```

### 5b. Barge-in behavior
- Did the user ever speak while audio was playing? (Look for `INTERRUPT` events in the timeline)
- If barge-in occurred, did playback stop promptly? (Check time between interrupt event and next user speech)
- Were there any cases where barge-in *should* have happened but didn't?

### 5c. Agent interference
- Did multiple agents speak simultaneously or overlap? (Check timeline for overlapping agent audio windows)
- Did the moderator speak over an agent or vice versa?
- Was the output controller's queuing working correctly? (Check `enqueue` vs actual playback order)

### 5d. Follow-up routing
- When the user asked a follow-up question, was it routed to the correct agent (same agent that was already handling the topic)?
- Or was a new agent unnecessarily dispatched?

### 5e. Timing and responsiveness
- Moderator first-response latency for each turn
- Agent dispatch-to-first-audio latency
- Any turns where response took abnormally long (> 15s)
- Any turns where no response was received at all (timeouts)

### 5f. Errors and anomalies
- Any WebSocket errors or disconnections?
- Any backend exceptions in the logs?
- Any TTS failures?
- Any unexpected agent behavior?

### 6f. UI Freezes or glitches
- Is the MAIN THREAD BLOCKED?
- Are there any diagnostic logs showing UI errors or warnings?

---

## Step 8: Compose the test result summary

Produce a structured report with these sections:

```
# Test Case Diagnostic Report
**Script:** <filename>
**Date:** <timestamp>
**Duration:** <total test duration>

## Overall Verdict: PASS / PARTIAL / FAIL

## Turn-by-Turn Comparison
<table from 5a>

## Barge-in Analysis
<findings from 5b>

## Agent Coordination
<findings from 5c and 5d>

## Timing Summary
| Turn | Utterance               | Mod Latency | Agent Latency | Total |
|------|-------------------------|-------------|---------------|-------|
| ...  | ...                     | ...         | ...           | ...   |

## Issues Found
1. <issue description + severity (critical/warning/info)>
2. ...

## Recommendations
- <actionable fix or investigation suggestion>
- ...
```

### Verdict criteria
- **PASS**: All user utterances heard correctly, all expected responders replied appropriately, no agent interference, barge-in works, no errors.
- **PARTIAL**: Most flow works but with minor issues (slight ordering differences, one missed follow-up, non-critical warnings).
- **FAIL**: Core flow broken — utterances not heard, wrong agent responds, agents interfere, barge-in broken, or backend errors prevent completion.
