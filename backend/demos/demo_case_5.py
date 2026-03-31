#!/usr/bin/env python3
"""Case 5: Delegated task, user moves on, agent reports back proactively.

User fires off a background task to Ellen, immediately asks an unrelated
question. Ellen reports back when ready without user prompting.
"""

import asyncio
from demo_harness import run_demo

CONVERSATION = [
    "Ellen, please draft a summary of last week's fraud incidents and send it to the team.",
    "also, what is the current ACH processing volume?",
    "good. anything urgent in there?",
]

DELAYS = [10, 10, 15]

if __name__ == "__main__":
    asyncio.run(run_demo(
        case_name="demo_case_5",
        conversation=CONVERSATION,
        delays=DELAYS,
        final_wait=15,
    ))
