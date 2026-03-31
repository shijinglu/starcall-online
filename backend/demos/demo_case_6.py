#!/usr/bin/env python3
"""Case 6: Multi-turn follow-up with the same deep agent.

User delegates analysis to Ellen, then asks successive follow-up questions
that are routed back to the same running agent session.
"""

import asyncio
from demo_harness import run_demo

CONVERSATION = [
    "Ellen, analyze the chargeback spike from last night.",
    "which merchant had the highest volume?",
    "pull the top 5 user IDs behind those chargebacks.",
]

DELAYS = [15, 12, 12]

if __name__ == "__main__":
    asyncio.run(run_demo(
        case_name="demo_case_6",
        conversation=CONVERSATION,
        delays=DELAYS,
        final_wait=15,
    ))
