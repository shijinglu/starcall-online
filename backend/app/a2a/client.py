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
    agent_url = f"{base_url}/a2a/{agent_name}/"

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

        # Extract text from the response.
        # The A2A SDK returns different shapes depending on the agent:
        #   - Message with parts (direct result)
        #   - Task with artifacts/messages
        result = response.root.result

        texts: list[str] = []

        # Case 1: result is a Message with parts directly
        if hasattr(result, "parts") and result.parts:
            for part in result.parts:
                # Parts may be a union type with a .root accessor
                actual = getattr(part, "root", part)
                if hasattr(actual, "text") and actual.text:
                    texts.append(actual.text)

        # Case 2: result has artifacts (Task-style response)
        if not texts and hasattr(result, "artifacts") and result.artifacts:
            for artifact in result.artifacts:
                for part in artifact.parts:
                    actual = getattr(part, "root", part)
                    if hasattr(actual, "text") and actual.text:
                        texts.append(actual.text)

        # Case 3: result has messages (Task-style response)
        if not texts and hasattr(result, "messages") and result.messages:
            for msg in reversed(result.messages):
                if msg.role == "agent":
                    for part in msg.parts:
                        actual = getattr(part, "root", part)
                        if hasattr(actual, "text") and actual.text:
                            texts.append(actual.text)
                    if texts:
                        break

        if texts:
            return "\n\n".join(texts)

        logger.warning(
            "A2A response for %s had no extractable text, "
            "result_type=%s, repr=%.300s",
            agent_name,
            type(result).__name__,
            repr(result)[:300],
        )
        return "No response from agent."
