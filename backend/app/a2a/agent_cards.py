"""Build A2A AgentCard objects from the AgentRegistry."""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from app.config import A2A_BASE_URL
from app.registry import AgentRegistry


def build_agent_card(
    agent_name: str,
    registry: AgentRegistry,
    base_url: str = A2A_BASE_URL,
) -> AgentCard:
    """Build an A2A AgentCard for a single agent."""
    entry = registry.get(agent_name)
    if entry is None:
        raise KeyError(f"Unknown agent: {agent_name}")

    skills = [
        AgentSkill(
            id=tool_name,
            name=tool_name,
            description=f"{agent_name} tool: {tool_name}",
            tags=[agent_name],
        )
        for tool_name in entry.tool_set
    ]

    return AgentCard(
        name=agent_name,
        description=entry.description,
        url=f"{base_url}/a2a/{agent_name}",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )


def build_all_agent_cards(
    registry: AgentRegistry,
    base_url: str = A2A_BASE_URL,
) -> dict[str, AgentCard]:
    """Build AgentCards for all registered agents."""
    return {
        name: build_agent_card(name, registry, base_url)
        for name in registry.entries
    }
