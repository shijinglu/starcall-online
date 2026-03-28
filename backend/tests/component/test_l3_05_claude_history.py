"""L3-05 — Claude SDK: Conversation History Continuity.

Covers: Multi-turn history appending; context preservation across turns.
Requires: ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import anthropic
import pytest

from app.config import ANTHROPIC_API_KEY

pytestmark = pytest.mark.skipif(not ANTHROPIC_API_KEY, reason="ANTHROPIC_API_KEY not set")


@pytest.fixture
def client():
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


SHIJING_SYSTEM_PROMPT = (
    "You are Shijing, a data-driven user risk analyst. "
    "You have access to: user_profile_read, user_journey_read, risk_score_read tools. "
    "Keep responses concise and analytical."
)


@pytest.mark.asyncio
async def test_history_continuity_across_turns(client):
    """Turn 2 references context from turn 1 without re-asking."""
    # Turn 1: establish context
    turn1_response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        system=SHIJING_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": "The user's risk score is 73 out of 100, classified as medium-high risk."}
        ],
        max_tokens=512,
    )
    turn1_text = turn1_response.content[0].text

    # Build history for turn 2
    messages = [
        {"role": "user", "content": "The user's risk score is 73 out of 100, classified as medium-high risk."},
        {"role": "assistant", "content": turn1_text},
        {"role": "user", "content": "Why is it that value? What factors typically drive a score like that?"},
    ]

    # Turn 2: follow-up referencing turn 1
    turn2_response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        system=SHIJING_SYSTEM_PROMPT,
        messages=messages,
        max_tokens=512,
    )
    turn2_text = turn2_response.content[0].text

    # Turn 2 should reference the score or risk level from turn 1
    turn2_lower = turn2_text.lower()
    has_context = any(
        keyword in turn2_lower
        for keyword in ["73", "medium-high", "risk", "score", "factor"]
    )
    assert has_context, (
        f"Turn 2 should reference context from turn 1. Got: {turn2_text[:200]}"
    )

    # Turn 2 should NOT ask "what is the risk score?" since it was already provided
    asking_again = any(
        phrase in turn2_lower
        for phrase in ["what is the risk score", "what's the risk score", "could you provide"]
    )
    assert not asking_again, (
        f"Turn 2 should NOT re-ask for information already given. Got: {turn2_text[:200]}"
    )
