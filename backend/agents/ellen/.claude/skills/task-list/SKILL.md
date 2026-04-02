---
name: task-list
description: List the user's tasks and to-do items. Use when asked about tasks, to-dos, or what needs to be done.
---

# Task List

Retrieve the user's current task list.

## Parameters
None.

## Response Format

Return a JSON block with the task list:

```json
{
  "tasks": [
    {"id": 1, "title": "Review fraud report", "due": "today", "priority": "high"},
    {"id": 2, "title": "Schedule weekly sync", "due": "tomorrow"}
  ]
}
```

## Usage

When the user asks about their tasks or to-do list, invoke this skill and summarize. Highlight high-priority items first:

> You've got two items on the list, boss. Top priority: review the fraud report, that's due today. And you need to schedule the weekly sync by tomorrow.
