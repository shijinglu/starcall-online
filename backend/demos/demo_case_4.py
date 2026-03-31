#!/usr/bin/env python3
"""Case 4: Quick lookup — moderator-only, no deep agent.

User asks time zone questions and sets a reminder.
The fast moderator handles everything directly with tool calls.
"""

import asyncio
from demo_harness import run_demo

CONVERSATION = [
    "what time is it in london right now?",
    "and what about tokyo?",
    "thanks, set a reminder for me to join a call at 9 AM tokyo time",
]

DELAYS = [8, 8, 10]

if __name__ == "__main__":
    asyncio.run(run_demo(
        case_name="demo_case_4",
        conversation=CONVERSATION,
        delays=DELAYS,
        final_wait=5,
    ))
