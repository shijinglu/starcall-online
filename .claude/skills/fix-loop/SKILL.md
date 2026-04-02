---
name: fix-loop
description: Iterative diagnose-and-fix loop. Takes a test procedure, runs it, adds diagnostic logs on failure, re-runs to confirm hypotheses, then implements fixes. Repeats until all issues are resolved or 10 iterations are reached.
---

# Fix Loop

Iterative debugging skill: execute a test procedure, diagnose failures with targeted logging, confirm hypotheses, and implement fixes in a tight loop.

---

## Step 1: Collect the test procedure

The user provides a `<test-procedure>` — instructions for running an end-to-end test, a skill, a script, or any repeatable procedure that produces observable output.

If the user did not provide a test procedure in their message, ask:

> Please describe or paste the test procedure you'd like me to run in a loop until it passes.
> This can be a shell command, a skill invocation, a multi-step manual process, or any repeatable procedure.

Wait for the user to provide the procedure before proceeding.

Save the procedure verbatim for re-execution in later iterations.

---

## Step 2: Execute the test procedure

Run the `<test-procedure>` exactly as described. Capture all output (stdout, stderr, exit codes, log files, screenshots — whatever the procedure produces).

**Important:** Use appropriate timeouts. Default to 120s per command unless the procedure specifies otherwise.

Record:
- The full command(s) executed
- All output and exit codes
- Timestamp of execution

---

## Step 3: Examine the result

Analyze the output from Step 2:

- **If the procedure succeeded with no errors:** Report success and stop. You are done.
- **If there are errors or unexpected behavior:** Proceed to Step 4.

When examining results, look for:
- Non-zero exit codes
- Exception tracebacks or error messages
- Unexpected output vs. expected output
- Missing output that should be present
- Warnings that indicate underlying problems

Summarize the observed failure(s) clearly before proceeding:

```
## Iteration N — Failure Summary
- **What failed:** <specific error or unexpected behavior>
- **Where:** <file, line, component>
- **Error message:** <exact text>
```

---

## Step 4: Add diagnostic logs

Based on the failure observed in Step 3, form a hypothesis about the root cause and add **targeted diagnostic logging** to confirm or reject it.

### Guidelines for diagnostic logging:
1. **Be surgical** — add logging only at the points that will confirm or reject your hypothesis. Do not scatter print statements everywhere.
2. **Log contextual data** — include variable values, state, and control flow indicators, not just "reached here" messages.
3. **Prefix diagnostic logs clearly** — use a prefix like `[FIX-LOOP DIAG]` so they are easy to find and remove later.
4. **Preserve existing behavior** — diagnostic logging must not change control flow or introduce side effects.

Example:
```python
print(f"[FIX-LOOP DIAG] variable_name={variable_name}, state={state}")
```
```swift
print("[FIX-LOOP DIAG] value=\(value), condition=\(condition)")
```

Document what you added and why:

```
## Diagnostic Logging Added
- **Hypothesis:** <what you think is wrong>
- **Logging added at:** <file:line — what it captures>
- **Expected if hypothesis is correct:** <what the log output should show>
- **Expected if hypothesis is wrong:** <what the log output should show instead>
```

---

## Step 5: Re-run the test procedure

Execute the `<test-procedure>` again, exactly as in Step 2. Capture all output including the new diagnostic logs.

---

## Step 6: Evaluate diagnostic results

Examine the diagnostic log output from Step 5:

### If hypothesis is CONFIRMED:
- The diagnostic logs show evidence consistent with your hypothesis.
- Proceed to **Step 7** (propose and implement a fix).

### If hypothesis is REJECTED:
- The diagnostic logs contradict your hypothesis.
- **Remove or update the diagnostic logs** that are no longer relevant.
- Form a new hypothesis based on what the diagnostics revealed.
- Go back to **Step 4** with the new hypothesis.

Document the evaluation:

```
## Hypothesis Evaluation — Iteration N
- **Hypothesis:** <what you tested>
- **Diagnostic output:** <relevant log lines>
- **Verdict:** CONFIRMED / REJECTED
- **Reasoning:** <why the evidence supports this verdict>
```

---

## Step 7: Propose and implement a fix

Now that the root cause is confirmed:

1. **Propose the fix** — describe what you will change and why. Keep the fix minimal and targeted.
2. **Remove all `[FIX-LOOP DIAG]` logging** added during diagnosis — do not leave diagnostic scaffolding in the code.
3. **Implement the fix** — edit the code.
4. **Re-run the test procedure** (go back to Step 2) to verify the fix resolves the issue.

If the fix resolves this issue but reveals a new/different issue, continue the loop from Step 3.

---

## Iteration tracking

Maintain a running log of iterations:

```
## Fix Loop Progress
| Iter | Hypothesis                  | Verdict    | Action Taken                |
|------|-----------------------------|------------|-----------------------------|
| 1    | <hypothesis>                | CONFIRMED  | Fixed <what>                |
| 2    | <hypothesis>                | REJECTED   | New hypothesis: <what>      |
| 3    | <hypothesis>                | CONFIRMED  | Fixed <what>                |
...
```

---

## Termination conditions

Stop the loop when ANY of these are true:

1. **All issues fixed** — the test procedure passes cleanly. Report success.
2. **10 iterations reached** — stop and report what was tried, what worked, and what remains unresolved.
3. **Blocked** — the fix requires information or access you don't have. Stop and ask the user.

On termination, always:
- Remove ALL `[FIX-LOOP DIAG]` diagnostic logging from the codebase.
- Provide a final summary:

```
## Fix Loop Final Report
- **Total iterations:** N
- **Issues found:** <count>
- **Issues fixed:** <count>
- **Remaining issues:** <list, if any>
- **Changes made:** <list of files modified and what was changed>
```
