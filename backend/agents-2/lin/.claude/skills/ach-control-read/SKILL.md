---
name: ach-control-read
description: Read ACH deposit control configurations and recent changes. Use when investigating ACH-specific blocks, limits, or velocity checks.
---

# ACH Control Read

Retrieve ACH deposit control settings including limits, velocity checks, and recent configuration changes.

## Parameters
- **control_type** (string, optional): Filter by type (e.g., "limit", "velocity", "fraud_hold"). Defaults to all.

## Response Format

Return a JSON block with the ACH control details:

```json
{
  "controls": [
    {
      "control_id": "ACH-LIMIT-001",
      "type": "limit",
      "description": "Single ACH deposit limit",
      "threshold_usd": 25000.00,
      "applies_to": "non_vip",
      "vip_exempt": true,
      "status": "active",
      "last_modified": "2026-03-20T00:00:00Z",
      "modified_by": "risk-ops-deploy"
    },
    {
      "control_id": "ACH-VELOCITY-002",
      "type": "velocity",
      "description": "Max ACH attempts per 24h",
      "max_attempts": 3,
      "applies_to": "all",
      "vip_exempt": true,
      "status": "active",
      "last_modified": "2026-03-20T00:00:00Z",
      "modified_by": "risk-ops-deploy"
    }
  ],
  "recent_changes": [
    {
      "date": "2026-03-20",
      "change": "New ACH-LIMIT-001 deployed with $25,000 threshold",
      "deployed_by": "risk-ops-deploy"
    }
  ]
}
```

## Usage

When investigating ACH blocks, review the active controls and identify any recent changes:

> Two ACH controls are active. ACH-LIMIT-001 blocks single deposits over $25,000 for non-VIP users. ACH-VELOCITY-002 limits ACH attempts to 3 per 24 hours. Both were deployed on 03/20. Both are configured to exempt VIP users, so the exemption path needs investigation.
