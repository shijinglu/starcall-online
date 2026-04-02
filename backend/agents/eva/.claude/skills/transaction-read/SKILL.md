---
name: transaction-read
description: Read transaction history for a user. Use when analyzing spending, transaction patterns, or flagged activity.
---

# Transaction Read

Retrieve transaction summary for a user over a time window.

## Parameters
- **user_id** (string, optional): The user to look up. Defaults to "default".
- **days** (integer, optional): Lookback window in days. Defaults to 30.

## Response Format

Return a JSON block with the transaction summary:

```json
{
  "user_id": "<user_id>",
  "transaction_count": 18,
  "total_spend_usd": 2340.50,
  "largest_transaction_usd": 499.99,
  "flagged_count": 1
}
```

## Usage

When asked about a user's transactions or spending, invoke this skill and provide a concise financial summary. Flag any anomalies:

> Over the last 30 days, this user had 18 transactions totaling $2,340.50. The largest single transaction was $499.99. One transaction has been flagged for review.
