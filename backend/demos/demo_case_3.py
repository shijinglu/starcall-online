#!/usr/bin/env python3
"""Case 3: Call a meeting with multiple deep agents.

User summons shijing, eva, and ming to investigate an ACH return spike.
Moderator acts as emcee while agents work in parallel and report back.
"""

import asyncio
from demo_harness import run_demo

CONVERSATION = [
    "help me call shijing, eva and ming",
    "From yesterday's metrics, I saw a spike in ACH return case, what is going on?",
    "no! just be quick",
    "ok, please continue research, each of you write a summary of your findings, I would like to see your investigations in my mailbox in 10 minutes",
]

DELAYS = [10, 20, 15, 15]

if __name__ == "__main__":
    asyncio.run(run_demo(
        case_name="demo_case_3",
        conversation=CONVERSATION,
        delays=DELAYS,
        final_wait=20,
    ))
