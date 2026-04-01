"""Tests for A2A client response extraction."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_a2a_mocks():
    """Set up common httpx/A2A client mocks. Returns (patches_ctx, mock_response_holder)."""

    class ResponseHolder:
        response = None

    holder = ResponseHolder()

    def _start_patches():
        p1 = patch("app.a2a.client.httpx.AsyncClient")
        p2 = patch("app.a2a.client.A2ACardResolver")
        p3 = patch("app.a2a.client.A2AClient")
        mock_httpx = p1.start()
        mock_resolver_cls = p2.start()
        mock_client_cls = p3.start()

        mock_resolver = AsyncMock()
        mock_resolver.get_agent_card = AsyncMock(return_value=MagicMock())
        mock_resolver_cls.return_value = mock_resolver

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=lambda req: holder.response)
        mock_client_cls.return_value = mock_client

        mock_httpx.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

        return [p1, p2, p3]

    return _start_patches, holder


class TestSendTaskToAgent:
    @pytest.mark.asyncio
    async def test_extracts_text_from_direct_message_parts(self):
        """Case 1: result is a Message with parts directly (real A2A response)."""
        from app.a2a.client import send_task_to_agent

        # Simulate: Part(root=TextPart(text="..."))
        mock_text_part = MagicMock(spec=["text", "kind", "metadata"])
        mock_text_part.text = "Transaction analysis result"
        mock_part = MagicMock(spec=["root"])
        mock_part.root = mock_text_part

        mock_result = MagicMock(spec=["parts", "role", "message_id", "context_id"])
        mock_result.parts = [mock_part]

        mock_response = MagicMock()
        mock_response.root.result = mock_result

        start_patches, holder = _make_a2a_mocks()
        holder.response = mock_response
        patches = start_patches()
        try:
            result = await send_task_to_agent("eva", "Analyze transactions")
            assert result == "Transaction analysis result"
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_extracts_text_from_artifacts(self):
        """Case 2: result has artifacts (Task-style response)."""
        from app.a2a.client import send_task_to_agent

        mock_text_part = MagicMock(spec=["text", "kind"])
        mock_text_part.text = "Artifact result"
        mock_part = MagicMock(spec=["root"])
        mock_part.root = mock_text_part
        mock_artifact = MagicMock(spec=["parts"])
        mock_artifact.parts = [mock_part]

        # No direct 'parts' on result, but has artifacts
        mock_result = MagicMock(spec=["artifacts", "messages", "status"])
        mock_result.artifacts = [mock_artifact]
        mock_result.messages = []

        mock_response = MagicMock()
        mock_response.root.result = mock_result

        start_patches, holder = _make_a2a_mocks()
        holder.response = mock_response
        patches = start_patches()
        try:
            result = await send_task_to_agent("eva", "Analyze transactions")
            assert result == "Artifact result"
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_extracts_text_from_messages_when_no_artifacts(self):
        """Case 3: result has messages (Task-style response)."""
        from app.a2a.client import send_task_to_agent

        mock_text_part = MagicMock(spec=["text", "kind"])
        mock_text_part.text = "Response from messages"
        mock_part = MagicMock(spec=["root"])
        mock_part.root = mock_text_part
        mock_msg = MagicMock(spec=["role", "parts"])
        mock_msg.role = "agent"
        mock_msg.parts = [mock_part]

        mock_result = MagicMock(spec=["artifacts", "messages", "status"])
        mock_result.artifacts = []
        mock_result.messages = [mock_msg]

        mock_response = MagicMock()
        mock_response.root.result = mock_result

        start_patches, holder = _make_a2a_mocks()
        holder.response = mock_response
        patches = start_patches()
        try:
            result = await send_task_to_agent("eva", "Analyze transactions")
            assert result == "Response from messages"
        finally:
            for p in patches:
                p.stop()
