---
name: bank-data-read
description: Read bank account data for a user. Use when analyzing account details, balances, or account age.
---

# Bank Data Read

Retrieve bank account information for a user.

## Parameters
- **user_id** (string, optional): The user to look up. Defaults to "default".

## Response Format

Return a JSON block with the bank data:

```json
{
  "user_id": "<user_id>",
  "bank": "Chase",
  "account_type": "checking",
  "balance_usd": 4820.33,
  "account_age_days": 1240
}
```

## Usage

When asked about a user's bank information, invoke this skill and summarize:

> The user has a Chase checking account with a balance of $4,820.33. The account is about 3.4 years old.
