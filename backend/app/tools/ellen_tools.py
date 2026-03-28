"""Ellen's tool stubs -- personal assistant (calendar, email, tasks)."""

from __future__ import annotations

import uuid


async def calendar_read(date: str = "today", **kwargs) -> dict:
    """Return plausible calendar events for *date*."""
    return {
        "date": date,
        "events": [
            {"time": "9:00 AM", "title": "Team standup"},
            {"time": "2:00 PM", "title": "Product review"},
        ],
    }


async def email_send(to: str = "", subject: str = "", body: str = "", **kwargs) -> dict:
    """Stub: pretend to send an email."""
    return {"status": "sent", "message_id": str(uuid.uuid4())}


async def task_list(**kwargs) -> dict:
    """Return a plausible to-do list."""
    return {
        "tasks": [
            {"id": 1, "title": "Review fraud report", "due": "today", "priority": "high"},
            {"id": 2, "title": "Schedule weekly sync", "due": "tomorrow"},
        ]
    }
