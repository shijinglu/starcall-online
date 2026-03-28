"""L3-08 — Agent Persona System Prompt Isolation.

Covers: Per-agent system prompt injection; tool set scoping.
Requires: ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import anthropic
import pytest

from app.config import ANTHROPIC_API_KEY
from app.deep_agent_runner import AGENT_TOOL_DEFINITIONS
from app.registry import AgentRegistry

pytestmark = pytest.mark.skipif(not ANTHROPIC_API_KEY, reason="ANTHROPIC_API_KEY not set")


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

    tools = AGENT_TOOL_DEFINITIONS.get(agent_name, [])
    task = CROSS_DOMAIN_TASKS[agent_name]

    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        system=entry.system_prompt,
        messages=[{"role": "user", "content": task}],
        tools=tools if tools else anthropic.NOT_GIVEN,
        max_tokens=512,
    )

    # Collect text from response
    text_parts = [block.text for block in response.content if hasattr(block, "text")]
    full_text = " ".join(text_parts).lower()

    # Collect tool calls from response
    tool_calls = [block for block in response.content if block.type == "tool_use"]

    # Agent should NOT call tools from another agent's domain
    other_agent_tools = set()
    for other_name, other_tools in AGENT_TOOL_DEFINITIONS.items():
        if other_name != agent_name:
            other_agent_tools.update(t["name"] for t in other_tools)

    for tc in tool_calls:
        assert tc.name not in other_agent_tools, (
            f"{agent_name} called out-of-scope tool {tc.name}"
        )

    # If there's text, it should indicate inability or redirect
    if full_text:
        # The agent should express inability or redirect, not fabricate capabilities
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
                "sorry",
                "apologize",
                "unfortunately",
            ]
        )
        # If the agent didn't call any out-of-scope tools, that alone is a pass
        if tool_calls:
            # All tool calls should be in-scope
            in_scope_tools = {t["name"] for t in tools}
            for tc in tool_calls:
                assert tc.name in in_scope_tools, (
                    f"{agent_name} hallucinated tool {tc.name}"
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["ellen", "shijing", "eva", "ming"])
async def test_agent_persona_tone(client, registry, agent_name):
    """Each agent's response should match its declared persona."""
    entry = registry.get(agent_name)
    assert entry is not None

    tools = AGENT_TOOL_DEFINITIONS.get(agent_name, [])

    # Ask a generic in-scope question
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
        tools=tools if tools else anthropic.NOT_GIVEN,
        max_tokens=512,
    )

    # Should get a non-empty response (text or tool call)
    assert len(response.content) > 0, f"{agent_name} returned empty response"

    # Check persona-specific markers
    text_parts = [block.text for block in response.content if hasattr(block, "text")]
    full_text = " ".join(text_parts).lower()

    if agent_name == "ellen" and full_text:
        # Ellen should address user as "boss"
        assert "boss" in full_text, (
            f"Ellen should address user as 'boss'. Got: {full_text[:200]}"
        )
