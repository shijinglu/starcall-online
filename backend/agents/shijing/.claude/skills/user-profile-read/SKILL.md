---
name: user-profile-read
description: Read a user's profile data. Use when looking up account details, verification status, or demographic information.
---

# User Profile Read

Retrieve profile information for a user.

## Parameters
- **user_id** (string, optional): The user to look up. Defaults to "default".

## Response Format

Return a JSON block with profile data:

```json
{
  "user_id": "<user_id>",
  "account_age_days": 847,
  "country": "US",
  "email_verified": true,
  "phone_verified": true
}
```

## Usage

When asked about a user's profile, invoke this skill and provide a data-driven summary. Flag any anomalies:

> The user is US-based with a mature account at 847 days old. Both email and phone are verified -- that's a positive trust signal.
