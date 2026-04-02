---
name: calendar-read
description: Read calendar events for a given date. Use when the user asks about their schedule, meetings, or appointments.
---

# Calendar Read

Look up calendar events for the requested date.

## Parameters
- **date** (string, optional): The date to look up. Defaults to "today".

## Response Format

Return a JSON block with the date and list of events:

```json
{
  "date": "<requested date>",
  "events": [
    {"time": "9:00 AM", "title": "Team standup"},
    {"time": "2:00 PM", "title": "Product review"}
  ]
}
```

## Usage

When the user asks about their calendar or schedule, invoke this skill, then summarize the events in a natural, TTS-friendly sentence. Example:

> You have two things on the calendar today, boss: a team standup at 9 AM and a product review at 2 PM.
