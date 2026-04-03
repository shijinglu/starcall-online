---
name: risk-rule-read
description: Read active risk rules and their configurations. Use when investigating which rules are firing on transactions or deposits.
---

# Risk Rule Read

Retrieve active risk rules, including their thresholds, conditions, and recent firing history.

## Parameters
- **rule_id** (string, optional): Specific rule ID to look up.
- **category** (string, optional): Filter by category (e.g., "ach", "wire", "card"). Defaults to all.

## Response Format

Return a JSON block with the rule details:

```json
{
  "rules": [
    {
      "rule_id": "ACH-LIMIT-001",
      "name": "ACH Single Deposit Limit",
      "category": "ach",
      "condition": "single_ach_deposit > threshold",
      "threshold_usd": 25000.00,
      "action": "BLOCK",
      "vip_exempt": true,
      "deployed_at": "2026-03-20T00:00:00Z",
      "last_fired": "2026-04-02T09:14:00Z",
      "fire_count_24h": 4
    }
  ]
}
```

## Usage

When investigating blocked transactions, identify which rules fired and whether their configuration is correct:

> Rule ACH-LIMIT-001 is active -- it blocks any single ACH deposit exceeding $25,000. It fired 4 times in the last 24 hours against user 123456. The rule is configured to exempt VIP users, so the VIP exemption logic needs to be verified.
