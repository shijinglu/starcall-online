# Instructions: Generating a High-Level Test Plan

Given a high-level system design document, produce a high-level test plan. Do **not** write test code. Instead describe each test case in terms of: what it covers, how to set up and operate it, and what to verify.

Focus on **real tests** rather than heavy mocked method level unit tests. Organize all test cases into the following three levels.

---

## Test Levels

### Level 1 — Full-Stack Tests
Both the client app (iOS, web, etc.) and the backend service are running. Rich automation tooling is available: e.g., `mobile-mcp` for UI automation, `curl`/HTTP scripts for backend endpoints, log readers for server output.

### Level 2 — Backend-Only Tests
The backend service is running. No client app. Tests drive the backend directly via HTTP, WebSocket clients, or CLI scripts. Inject synthetic inputs (e.g., raw audio files, JSON payloads) in place of real client events.

### Level 3 — Component Tests (Unit tests with middleware available)
Neither the client app nor the backend service is running. But erxternal dependencies, like DB, redis, external API are avaialble.

---

## General Principles

1. **Automatable.** Every test must be executable without human interaction: use UI automation tools for Level 1, scripted HTTP/WS clients for Level 2, and unit test frameworks or SDK calls for Level 3.

2. **Flow coverage over code coverage.** Coverage means all meaningful data paths and user journeys are exercised. Classical line/branch coverage is not the goal.

3. **Prefer lower levels.** Level 3 tests are cheapest to run and maintain — maximize them. Use Level 2 when the logic under test lives in backend state management. Reserve Level 1 for behaviors that can only be validated end-to-end through the UI.

4. **Test boundaries, not internals.** Define tests by their observable inputs and outputs. Avoid assertions on intermediate variables or internal state unless there is no other way to verify correctness.

5. **Minimize overlap.** Each test case should cover something not already covered by another. If a flow is already verified at Level 3, do not duplicate it at Level 2 or Level 1 unless the higher-level context adds meaningful signal.

---

## Output Format

For each test case, provide:

- **ID and name** — short, unique identifier and a descriptive title.
- **What it covers** — which flow(s) or behaviors from the design this test validates.
- **Setup / prerequisites** — what must be in place before the test runs.
- **Operation** — step-by-step actions (tool calls, script invocations, injected inputs).
- **Verify** — concrete, observable outcomes that confirm the test passed.

End the document with a **coverage summary table** mapping every significant flow from the design to the test case(s) that cover it.
