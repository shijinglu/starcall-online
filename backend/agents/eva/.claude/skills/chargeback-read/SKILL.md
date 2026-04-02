---
name: chargeback-read
description: Read chargeback and dispute data for a user. Use when investigating chargebacks, disputes, or payment reversals.
---

# Chargeback Read

Retrieve chargeback and dispute history for a user.

## Parameters
- **user_id** (string, optional): The user to look up. Defaults to "default".

## Response Format

Return a JSON block with chargeback data:

```json
{
  "user_id": "<user_id>",
  "chargeback_count_12m": 0,
  "dispute_count_12m": 1
}
```

## Usage

When asked about chargebacks or disputes, invoke this skill and summarize:

> No chargebacks in the last 12 months. There's been one dispute filed during that period.
