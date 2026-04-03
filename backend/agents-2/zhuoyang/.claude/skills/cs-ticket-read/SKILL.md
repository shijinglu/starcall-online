---
name: cs-ticket-read
description: Read customer support tickets. Use when investigating escalated customer issues, complaints, or service requests.
---

# CS Ticket Read

Retrieve a customer support ticket by ticket ID or look up recent tickets for a user.

## Parameters
- **ticket_id** (string, optional): Specific ticket ID to retrieve (e.g., "US-20240402-8821").
- **user_id** (string, optional): Look up recent tickets for a user. Defaults to "default".

## Response Format

Return a JSON block with the ticket details:

```json
{
  "ticket_id": "US-20240402-8821",
  "user_id": "123456",
  "submitted_at": "2026-04-02T09:34:00Z",
  "priority": "High",
  "tier": "VIP1",
  "status": "Open",
  "subject": "ACH deposit blocked",
  "message": "I've been trying to deposit $50,000 via ACH all morning and keep getting blocked. This has never happened before. I'm a long-time customer, can someone please look into this urgently?"
}
```

## Usage

When investigating a customer issue, start by reading the ticket to understand the reported problem, priority, and customer tier:

> Ticket #US-20240402-8821 retrieved. The user reports being unable to complete a $50,000 ACH deposit. Submitted at 09:34 AM, flagged as High Priority. The account is associated with VIP1 tier. Pulling transaction history next.
