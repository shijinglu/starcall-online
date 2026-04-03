---
name: vip-exemption-check
description: Check whether VIP exemptions are correctly applied for a user. Use when verifying if a VIP user is being incorrectly subjected to standard risk controls.
---

# VIP Exemption Check

Verify whether a user's VIP status is being correctly recognized by the risk rule engine and whether exemptions are applied.

## Parameters
- **user_id** (string, required): The user to check.
- **rule_id** (string, optional): Specific rule to check exemption for. Defaults to all VIP-exempt rules.

## Response Format

Return a JSON block with the exemption status:

```json
{
  "user_id": "123456",
  "current_vip_tier": "VIP1",
  "exemption_status": "NOT_APPLIED",
  "expected": "EXEMPT",
  "reason": "vip_tier_cache_stale",
  "cache_vip_tier": "Standard",
  "cache_last_refreshed": "2026-03-26T00:00:00Z",
  "rules_affected": ["ACH-LIMIT-001", "ACH-VELOCITY-002"]
}
```

## Usage

When a VIP user is being blocked by rules they should be exempt from, verify the exemption status and identify the root cause:

> VIP exemption is NOT being applied for user 123456. The risk engine cache shows the user as "Standard" tier, last refreshed on 03/26. However, the user's current VIP tier is VIP1. The stale cache is causing the exemption to fail. Rules ACH-LIMIT-001 and ACH-VELOCITY-002 are both affected.
