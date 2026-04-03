---
name: system-config-read
description: Read system configuration settings. Use when investigating whether a config change caused unexpected behavior in risk rules or user tier management.
---

# System Config Read

Retrieve system configuration values for risk engine, caching, and tier management subsystems.

## Parameters
- **subsystem** (string, optional): Filter by subsystem (e.g., "risk_engine", "tier_cache", "ach_gateway"). Defaults to all.

## Response Format

Return a JSON block with the configuration:

```json
{
  "subsystem": "tier_cache",
  "config": {
    "cache_ttl_hours": 72,
    "refresh_trigger": "tier_change_event",
    "fallback_on_miss": "Standard",
    "last_full_refresh": "2026-03-25T00:00:00Z"
  }
}
```

## Usage

When investigating system behavior, check relevant configurations and identify whether settings may be contributing to the issue:

> The tier cache has a TTL of 72 hours and refreshes on tier change events. The fallback on cache miss is "Standard". The last full refresh was on 03/25 -- which coincides with the user's VIP downgrade. If the tier restoration on 03/28 did not emit a change event, the cache would still hold the "Standard" entry.
