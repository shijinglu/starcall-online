"""Tests for A2A client response extraction."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSendTaskToAgent:
    @pytest.mark.asyncio
    async def test_extracts_text_from_artifacts(self):
        from app.a2a.client import send_task_to_agent

        mock_part = MagicMock()
        mock_part.text = "Transaction analysis result"
        mock_artifact = MagicMock()
        mock_artifact.parts = [mock_part]
        mock_result = MagicMock()
        mock_result.artifacts = [mock_artifact]
        mock_result.messages = []

        mock_response = MagicMock()
        mock_response.root.result = mock_result

        with patch("app.a2a.client.httpx.AsyncClient") as mock_httpx, \
             patch("app.a2a.client.A2ACardResolver") as mock_resolver_cls, \
             patch("app.a2a.client.A2AClient") as mock_client_cls:
            mock_card = MagicMock()
            mock_resolver = AsyncMock()
            mock_resolver.get_agent_card = AsyncMock(return_value=mock_card)
            mock_resolver_cls.return_value = mock_resolver

            mock_client = AsyncMock()
            mock_client.send_message = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            mock_http = AsyncMock()
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await send_task_to_agent("eva", "Analyze transactions")
            assert result == "Transaction analysis result"

    @pytest.mark.asyncio
    async def test_extracts_text_from_messages_when_no_artifacts(self):
        from app.a2a.client import send_task_to_agent

        mock_part = MagicMock()
        mock_part.text = "Response from messages"
        mock_msg = MagicMock()
        mock_msg.role = "agent"
        mock_msg.parts = [mock_part]
        mock_result = MagicMock()
        mock_result.artifacts = []
        mock_result.messages = [mock_msg]

        mock_response = MagicMock()
        mock_response.root.result = mock_result

        with patch("app.a2a.client.httpx.AsyncClient") as mock_httpx, \
             patch("app.a2a.client.A2ACardResolver") as mock_resolver_cls, \
             patch("app.a2a.client.A2AClient") as mock_client_cls:
            mock_card = MagicMock()
            mock_resolver = AsyncMock()
            mock_resolver.get_agent_card = AsyncMock(return_value=mock_card)
            mock_resolver_cls.return_value = mock_resolver

            mock_client = AsyncMock()
            mock_client.send_message = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            mock_http = AsyncMock()
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await send_task_to_agent("eva", "Analyze transactions")
            assert result == "Response from messages"
