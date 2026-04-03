---
name: user-activity-read
description: Read user activity logs including logins, device changes, IP history, and security events. Use when investigating suspicious account behavior.
---

# User Activity Read

Retrieve recent user activity including login events, device changes, IP addresses, and security-related actions.

## Parameters
- **user_id** (string, optional): The user to look up. Defaults to "default".
- **days** (integer, optional): Lookback window in days. Defaults to 30.

## Response Format

Return a JSON block with the activity summary:

```json
{
  "user_id": "123456",
  "period_days": 30,
  "events": [
    {
      "timestamp": "2026-03-30T14:22:00Z",
      "event_type": "new_device_login",
      "details": {
        "device_id": "dev-9x8z7",
        "ip_address": "103.21.44.12",
        "geo_location": "Singapore"
      }
    },
    {
      "timestamp": "2026-03-30T14:25:00Z",
      "event_type": "password_reset",
      "details": {
        "initiated_by": "user",
        "ip_address": "103.21.44.12"
      }
    },
    {
      "timestamp": "2026-03-31T09:10:00Z",
      "event_type": "login",
      "details": {
        "ip_address": "118.140.67.8",
        "geo_location": "Hong Kong"
      }
    }
  ],
  "anomalies": [
    "new_device_login",
    "ip_geo_shift: California -> Singapore -> Hong Kong",
    "password_reset_after_device_change"
  ]
}
```

## Usage

When reviewing user activity, highlight any anomalous signals and assess whether the pattern is consistent with legitimate behavior or potential account takeover:

> Several anomalous signals identified. Three days ago, a new device login was detected with the IP shifting from California to Singapore. A password reset was triggered immediately after. The next day, the login IP changed to Hong Kong. This pattern overlaps with account takeover indicators, though the user reports the password reset was self-initiated.
