"""A2A client for dispatching tasks to agent A2A servers."""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx
from a2a.client import A2AClient, A2ACardResolver
from a2a.types import (
    MessageSendParams,
    SendMessageRequest,
)

from app.config import A2A_BASE_URL

logger = logging.getLogger(__name__)


async def send_task_to_agent(
    agent_name: str,
    task_text: str,
    context_id: str | None = None,
    task_id: str | None = None,
    base_url: str = A2A_BASE_URL,
    metadata: dict | None = None,
) -> str:
    """Send a task to an agent via A2A and return the result text."""
    agent_url = f"{base_url}/a2a/{agent_name}"

    async with httpx.AsyncClient(timeout=180.0) as http_client:
        resolver = A2ACardResolver(httpx_client=http_client, base_url=agent_url)
        agent_card = await resolver.get_agent_card()
        client = A2AClient(httpx_client=http_client, agent_card=agent_card)

        message_payload: dict = {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": task_text}],
                "message_id": uuid4().hex,
            },
        }
        if metadata:
            message_payload["metadata"] = metadata

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(**message_payload),
        )

        response = await client.send_message(request)

        # Extract text from the response
        result = response.root.result
        if hasattr(result, "artifacts") and result.artifacts:
            for artifact in result.artifacts:
                for part in artifact.parts:
                    if hasattr(part, "text"):
                        return part.text
        if hasattr(result, "messages") and result.messages:
            for msg in reversed(result.messages):
                if msg.role == "agent":
                    for part in msg.parts:
                        if hasattr(part, "text"):
                            return part.text

        logger.warning("A2A response for %s had no extractable text", agent_name)
        return "No response from agent."
