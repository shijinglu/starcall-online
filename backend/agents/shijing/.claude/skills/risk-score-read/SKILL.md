---
name: risk-score-read
description: Read a user's risk score and tier. Use when evaluating overall risk level or risk classification.
---

# Risk Score Read

Retrieve the risk score and tier for a user.

## Parameters
- **user_id** (string, optional): The user to evaluate. Defaults to "default".

## Response Format

Return a JSON block with risk score data:

```json
{
  "user_id": "<user_id>",
  "risk_score": 42,
  "risk_tier": "medium",
  "last_updated": "2026-03-27"
}
```

## Usage

When asked about a user's risk level, invoke this skill and provide context on what the score means:

> The user's risk score is 42 out of 100, placing them in the medium tier. Last updated March 27th. This is within normal range but warrants monitoring.
