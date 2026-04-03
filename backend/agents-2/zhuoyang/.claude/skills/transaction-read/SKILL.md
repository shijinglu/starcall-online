---
name: transaction-read
description: Read transaction history for a user. Use when reviewing deposits, withdrawals, ACH activity, or identifying blocked transactions.
---

# Transaction Read

Retrieve transaction history for a user over a time window, including blocked or rejected transactions.

## Parameters
- **user_id** (string, optional): The user to look up. Defaults to "default".
- **days** (integer, optional): Lookback window in days. Defaults to 180.

## Response Format

Return a JSON block with the transaction summary:

```json
{
  "user_id": "123456",
  "period_days": 180,
  "total_transactions": 42,
  "total_settled_usd": 10000.00,
  "ach_rejections": 0,
  "recent_blocked": [
    {
      "timestamp": "2026-04-02T09:14:00Z",
      "type": "ACH_DEPOSIT",
      "amount_usd": 50000.00,
      "status": "BLOCKED",
      "reason": "risk_rule_layer"
    }
  ]
}
```

## Usage

When reviewing transaction history, summarize the pattern and highlight any anomalies or blocked transactions:

> Transaction history is clean -- no ACH rejections in the past 6 months. Lifetime settled ACH volume is approximately $10,000. Today's records show four blocked ACH deposit attempts of $50,000 each, roughly 30 minutes apart. All rejections occurred at the risk rule layer.
