"""REST endpoint: GET /api/v1/agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from app.registry import AgentRegistry

router = APIRouter(prefix="/api/v1", tags=["agents"])

_agent_registry: "AgentRegistry" = None  # type: ignore[assignment]


def init_agents_router(agent_registry: "AgentRegistry") -> None:
    global _agent_registry
    _agent_registry = agent_registry


@router.get("/agents")
def list_agents():
    """List available agents and their personas/capabilities."""
    return {"agents": _agent_registry.list_all()}
