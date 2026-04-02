---
name: email-send
description: Send an email on behalf of the user. Use when asked to compose, draft, or send email.
---

# Email Send

Send an email message.

## Parameters
- **to** (string, required): Recipient email address.
- **subject** (string, required): Email subject line.
- **body** (string, required): Email body text.

## Response Format

Return a JSON block confirming the send:

```json
{
  "status": "sent",
  "message_id": "<generated uuid>"
}
```

## Usage

When the user asks you to send an email, confirm the recipient, subject, and body before sending. After sending, report success in a natural sentence:

> Done, boss. I've sent the email to jane@example.com with the subject "Weekly Update".
