#!/usr/bin/env python3
"""Case 2: Natural conversation with both fast and slow responses.

User asks for TODO items and metrics review. Moderator acknowledges fast,
dispatches Ellen for deep work. Multi-turn follow-ups with the agent.
"""

import asyncio
from demo_harness import run_demo

CONVERSATION = [
    "hello",
    "help me pull the TODO items from work for today",
    "also, help me review yesterday business metrics and brief me with a summary.",
    "skip the morning schedules, help me check if there are arrangements at dinner time",
    "yes, cancel that for me please",
    "review the metrics",
]

DELAYS = [8, 12, 12, 15, 12, 10]

if __name__ == "__main__":
    asyncio.run(run_demo(
        case_name="demo_case_2",
        conversation=CONVERSATION,
        delays=DELAYS,
        final_wait=10,
    ))
