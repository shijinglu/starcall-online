"""A2A AgentExecutor that wraps the Claude Agent SDK runner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.utils import new_agent_text_message

from app.agent_task_manager import _comm_callbacks
from app.models import AgentSession

if TYPE_CHECKING:
    from app.registry import AgentRegistry
    from app.sdk_agent_runner import SDKAgentRunner

logger = logging.getLogger(__name__)


class ClaudeA2AExecutor(AgentExecutor):
    """Runs a Claude Agent SDK session as an A2A task."""

    def __init__(self, sdk_runner: "SDKAgentRunner", registry: "AgentRegistry") -> None:
        self.sdk_runner = sdk_runner
        self.registry = registry

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        agent_name = (context.metadata or {}).get("agent_name", "")
        delegation_chain = (context.metadata or {}).get("delegation_chain", [])
        task_text = context.get_user_input()
        task_id = context.task_id or "unknown"
        context_id = context.context_id or "unknown"

        logger.info("A2A execute: agent=%s, task_id=%s, chain=%s, text=%.100s", agent_name, task_id, delegation_chain, task_text)

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()

        try:
            agent_session = AgentSession(agent_name=agent_name)
            # For delegated agents, use the root agent's callback so comm events
            # flow through the original WebSocket connection.
            root_agent = delegation_chain[0] if delegation_chain else agent_name
            on_text = _comm_callbacks.get(root_agent)
            result_text = await self.sdk_runner.run(
                agent_session, task_text, on_text=on_text,
                delegation_chain=delegation_chain,
            )

            await event_queue.enqueue_event(
                new_agent_text_message(result_text or "No result.", context_id, task_id)
            )
            await updater.complete()

        except Exception as exc:
            logger.exception("A2A execute failed for agent=%s", agent_name)
            await updater.failed(
                new_agent_text_message(f"Agent error: {exc}", context_id, task_id)
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        logger.info("A2A cancel requested for task_id=%s", context.task_id)
        updater = TaskUpdater(
            event_queue, context.task_id or "unknown", context.context_id or "unknown",
        )
        await updater.cancel()
