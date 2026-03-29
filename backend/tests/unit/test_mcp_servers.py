"""Unit tests for MCP tool wrappers."""

from __future__ import annotations

import json

import pytest

from app.tools.mcp_servers import (
    AGENT_MCP_SERVERS,
    async_risk_check,
    bank_data_read,
    calendar_read,
    chargeback_read,
    email_send,
    fraud_signal_read,
    id_check,
    risk_score_read,
    task_list,
    transaction_read,
    user_journey_read,
    user_profile_read,
)


class TestMCPToolWrappers:
    """Verify each @tool wrapper delegates to the underlying function."""

    @pytest.mark.asyncio
    async def test_calendar_read(self):
        result = await calendar_read.handler({"date": "today"})
        assert "content" in result
        assert result["content"][0]["type"] == "text"
        data = json.loads(result["content"][0]["text"])
        assert "events" in data

    @pytest.mark.asyncio
    async def test_email_send(self):
        result = await email_send.handler({"to": "a@b.com", "subject": "Hi", "body": "Hello"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert data["status"] == "sent"

    @pytest.mark.asyncio
    async def test_task_list(self):
        result = await task_list.handler({})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "tasks" in data

    @pytest.mark.asyncio
    async def test_user_profile_read(self):
        result = await user_profile_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert data["user_id"] == "test"

    @pytest.mark.asyncio
    async def test_user_journey_read(self):
        result = await user_journey_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "login_count" in data

    @pytest.mark.asyncio
    async def test_risk_score_read(self):
        result = await risk_score_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "risk_score" in data

    @pytest.mark.asyncio
    async def test_transaction_read(self):
        result = await transaction_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "transaction_count" in data

    @pytest.mark.asyncio
    async def test_bank_data_read(self):
        result = await bank_data_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "bank" in data

    @pytest.mark.asyncio
    async def test_chargeback_read(self):
        result = await chargeback_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "chargeback_count_12m" in data

    @pytest.mark.asyncio
    async def test_id_check(self):
        result = await id_check.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert data["identity_verified"] is True

    @pytest.mark.asyncio
    async def test_async_risk_check(self):
        result = await async_risk_check.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "risk_signals" in data

    @pytest.mark.asyncio
    async def test_fraud_signal_read(self):
        result = await fraud_signal_read.handler({"user_id": "test"})
        assert "content" in result
        data = json.loads(result["content"][0]["text"])
        assert "signals" in data


class TestAgentMCPServers:
    """Verify the AGENT_MCP_SERVERS registry."""

    def test_all_agents_present(self):
        assert set(AGENT_MCP_SERVERS.keys()) == {"ellen", "shijing", "eva", "ming"}
