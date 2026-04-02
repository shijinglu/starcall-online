---
name: user-journey-read
description: Read user journey and activity analytics. Use when analyzing login patterns, device changes, or behavioral anomalies.
---

# User Journey Read

Retrieve user journey and activity analytics over a time window.

## Parameters
- **user_id** (string, optional): The user to analyze. Defaults to "default".
- **days** (integer, optional): Lookback window in days. Defaults to 30.

## Response Format

Return a JSON block with journey data:

```json
{
  "user_id": "<user_id>",
  "login_count": 23,
  "device_changes": 1,
  "address_changes": 0,
  "avg_session_minutes": 12.3
}
```

## Usage

When asked about user activity or behavior, invoke this skill and highlight patterns. Flag anomalies like unusual device changes or address changes:

> Over the last 30 days, the user logged in 23 times with an average session of 12.3 minutes. One device change was recorded, and no address changes. The device change is worth noting but the overall pattern looks consistent.
