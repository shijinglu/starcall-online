---
name: vip-history-read
description: Read VIP tier history for a user. Use when investigating tier transitions, downgrades, or gaps in VIP status.
---

# VIP History Read

Retrieve the full VIP tier history for a user, including upgrades, downgrades, and restoration events.

## Parameters
- **user_id** (string, required): The user to look up.

## Response Format

Return a JSON block with the VIP tier history:

```json
{
  "user_id": "123456",
  "current_tier": "VIP1",
  "history": [
    {
      "tier": "VIP1",
      "from": "2026-02-16",
      "to": "2026-03-25",
      "reason": "initial_qualification"
    },
    {
      "tier": "Standard",
      "from": "2026-03-25",
      "to": "2026-03-27",
      "reason": "automated_downgrade: monthly_volume_check"
    },
    {
      "tier": "VIP1",
      "from": "2026-03-28",
      "to": null,
      "reason": "manual_restoration: cs_escalation"
    }
  ]
}
```

## Usage

When investigating VIP-related issues, trace the full tier history to identify gaps or transitions that may have caused downstream effects:

> The user has held VIP1 status since 02/16/2026, with a brief interruption from 03/25 to 03/27 when they were downgraded to Standard due to an automated monthly volume check. VIP status was manually restored on 03/28 via CS escalation. This downgrade window may correlate with stale cache entries in the risk engine.
