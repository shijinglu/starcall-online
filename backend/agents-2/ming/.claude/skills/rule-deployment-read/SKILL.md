---
name: rule-deployment-read
description: Read risk rule deployment history. Use when investigating whether a recent deployment changed rule behavior or introduced new controls.
---

# Rule Deployment Read

Retrieve the deployment history for risk rules, including what changed, when, and who deployed it.

## Parameters
- **days** (integer, optional): Lookback window in days. Defaults to 30.
- **rule_id** (string, optional): Filter to a specific rule's deployment history.

## Response Format

Return a JSON block with the deployment history:

```json
{
  "deployments": [
    {
      "deployment_id": "deploy-2026-0320-001",
      "timestamp": "2026-03-20T14:00:00Z",
      "deployed_by": "risk-ops-deploy",
      "changes": [
        {
          "rule_id": "ACH-LIMIT-001",
          "change_type": "new_rule",
          "description": "Added $25,000 single ACH deposit limit with VIP exemption"
        },
        {
          "rule_id": "ACH-VELOCITY-002",
          "change_type": "new_rule",
          "description": "Added 3-attempt ACH velocity limit per 24h with VIP exemption"
        }
      ]
    }
  ]
}
```

## Usage

When investigating unexpected rule behavior, review deployment history to identify recent changes that may have introduced the issue:

> Two new ACH rules were deployed on 03/20: ACH-LIMIT-001 (single deposit cap at $25,000) and ACH-VELOCITY-002 (max 3 attempts per 24h). Both are configured with VIP exemptions. The timing of this deployment, combined with the user's VIP downgrade window on 03/25-03/27, suggests the cache may have captured the Standard tier during the downgrade and never refreshed when VIP was restored.
