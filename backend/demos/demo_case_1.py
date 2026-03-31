#!/usr/bin/env python3
"""Case 1: Natural conversation flow with fast response.

User asks about Gemini news, moderator misunderstands, user corrects with
barge-in style follow-up. No deep agents — moderator handles everything.
"""

import asyncio
from demo_harness import run_demo

CONVERSATION = [
    "tell me something new about gemini",
    "no, no, no, it is not the crypto exchange gemini, I am talking about Google AI product Gemini.",
]

DELAYS = [10, 12]

if __name__ == "__main__":
    asyncio.run(run_demo(
        case_name="demo_case_1",
        conversation=CONVERSATION,
        delays=DELAYS,
        final_wait=5,
    ))
