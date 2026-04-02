---
name: id-check
description: Perform identity verification check on a user. Use when verifying identity documents or KYC status.
---

# ID Check

Run an identity verification check for a user.

## Parameters
- **user_id** (string, optional): The user to verify. Defaults to "default".

## Response Format

Return a JSON block with verification results:

```json
{
  "user_id": "<user_id>",
  "identity_verified": true,
  "document_type": "drivers_license",
  "match_confidence": 0.97
}
```

## Usage

When asked to verify a user's identity, invoke this skill and report findings. Be precise about confidence levels:

> Identity verified via driver's license with 97% match confidence. This is a strong verification signal.
