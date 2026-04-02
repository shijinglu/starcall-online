---
name: async-risk-check
description: Run an asynchronous risk assessment on a user. Use when evaluating risk signals or performing risk checks.
---

# Async Risk Check

Run an asynchronous risk assessment for a user.

## Parameters
- **user_id** (string, optional): The user to assess. Defaults to "default".

## Response Format

Return a JSON block with risk assessment results:

```json
{
  "user_id": "<user_id>",
  "risk_signals": ["velocity_spike"],
  "score": 68
}
```

## Usage

When asked to run a risk check, invoke this skill and report findings. Distinguish confirmed facts from signals:

> Risk check complete. Score is 68 out of 100. One signal detected: velocity spike. This is a signal, not a confirmed finding -- it warrants further investigation.
