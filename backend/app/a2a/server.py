"""Create and mount per-agent A2A servers on the FastAPI app."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from app.a2a.agent_cards import build_agent_card
from app.a2a.executor import ClaudeA2AExecutor

if TYPE_CHECKING:
    from fastapi import FastAPI
    from app.registry import AgentRegistry
    from app.sdk_agent_runner import SDKAgentRunner

logger = logging.getLogger(__name__)


def mount_a2a_servers(
    app: "FastAPI",
    registry: "AgentRegistry",
    sdk_runner: "SDKAgentRunner",
    base_url: str,
) -> dict[str, InMemoryTaskStore]:
    """Mount an A2A sub-application for each registered agent."""
    task_stores: dict[str, InMemoryTaskStore] = {}
    executor = ClaudeA2AExecutor(sdk_runner=sdk_runner, registry=registry)

    for agent_name in registry.entries:
        card = build_agent_card(agent_name, registry, base_url=base_url)
        task_store = InMemoryTaskStore()
        task_stores[agent_name] = task_store

        handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
        )

        a2a_app = A2AStarletteApplication(
            agent_card=card,
            http_handler=handler,
        )
        app.mount(f"/a2a/{agent_name}", a2a_app.build())
        logger.info("A2A server mounted: /a2a/%s", agent_name)

    return task_stores
