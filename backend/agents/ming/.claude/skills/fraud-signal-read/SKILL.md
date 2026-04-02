---
name: fraud-signal-read
description: Read fraud detection signals for a user. Use when investigating potential fraud, suspicious activity, or device anomalies.
---

# Fraud Signal Read

Retrieve fraud detection signals for a user.

## Parameters
- **user_id** (string, optional): The user to investigate. Defaults to "default".

## Response Format

Return a JSON block with fraud signals:

```json
{
  "user_id": "<user_id>",
  "signals": [
    {
      "type": "device_fingerprint_mismatch",
      "confidence": 0.72,
      "detected_at": "2026-03-25"
    }
  ]
}
```

## Usage

When asked about fraud signals, invoke this skill and report each signal with its confidence level and detection date:

> One fraud signal detected: device fingerprint mismatch on March 25th with 72% confidence. This is a moderate-confidence signal -- it suggests the user may have accessed the account from an unrecognized device.
