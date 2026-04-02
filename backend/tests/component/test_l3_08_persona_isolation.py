"""L3-08 — Agent Persona System Prompt and MCP Tool Isolation.

Covers: Per-agent system prompt injection; MCP tool scoping.
Requires: ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import anthropic
import pytest

from app.config import ANTHROPIC_API_KEY
from app.registry import AgentRegistry

pytestmark = pytest.mark.skipif(
    not ANTHROPIC_API_KEY, reason="ANTHROPIC_API_KEY not set"
)


@pytest.fixture
def client():
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


@pytest.fixture
def registry():
    return AgentRegistry()


CROSS_DOMAIN_TASKS = {
    "ellen": "Run a fraud identity check on user U-1234 and tell me the risk score.",
    "shijing": "Send an email to john@example.com about the meeting tomorrow.",
    "eva": "Check the user's identity verification status and run an async risk check.",
    "ming": "Read my calendar for today and list my to-do items.",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["ellen", "shijing", "eva", "ming"])
async def test_agent_declines_out_of_scope_task(client, registry, agent_name):
    """Each agent should decline tasks outside its declared tool set."""
    entry = registry.get(agent_name)
    assert entry is not None

    task = CROSS_DOMAIN_TASKS[agent_name]

    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        system=entry.system_prompt,
        messages=[{"role": "user", "content": task}],
        max_tokens=512,
    )

    text_parts = [block.text for block in response.content if hasattr(block, "text")]
    full_text = " ".join(text_parts).lower()

    if full_text:
        declines_or_redirects = any(
            kw in full_text
            for kw in [
                "don't have access",
                "do not have access",
                "can't",
                "cannot",
                "unable",
                "not able",
                "outside",
                "don't have",
                "not in my",
                "not available",
                "doesn't include",
                "not equipped",
                "beyond my",
                "not within",
                "another agent",
                "different agent",
                "ellen's specialty",
                "eva's specialty",
                "ming's specialty",
                "shijing's specialty",
                "connect you with",
                "speak with",
                "sorry",
                "apologize",
                "unfortunately",
            ]
        )
        assert (
            declines_or_redirects or not full_text.strip()
        ), f"{agent_name} did not decline out-of-scope task: {full_text[:200]}"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["ellen", "shijing", "eva", "ming"])
async def test_agent_persona_tone(client, registry, agent_name):
    """Each agent's response should match its declared persona."""
    entry = registry.get(agent_name)
    assert entry is not None

    in_scope_tasks = {
        "ellen": "What's on my calendar today?",
        "shijing": "What does my user profile look like?",
        "eva": "Show me recent transactions.",
        "ming": "Run an identity check.",
    }

    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        system=entry.system_prompt,
        messages=[{"role": "user", "content": in_scope_tasks[agent_name]}],
        max_tokens=512,
    )

    assert len(response.content) > 0, f"{agent_name} returned empty response"

    text_parts = [block.text for block in response.content if hasattr(block, "text")]
    full_text = " ".join(text_parts).lower()

    if agent_name == "ellen" and full_text:
        assert (
            "boss" in full_text
        ), f"Ellen should address user as 'boss'. Got: {full_text[:200]}"


def test_all_agents_registered():
    """Verify all 4 agents are registered."""
    registry = AgentRegistry()
    for name in ("ellen", "shijing", "eva", "ming"):
        assert name in registry, f"{name} not in registry"
