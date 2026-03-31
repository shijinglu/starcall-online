"""Component tests for SDKAgentRunner (mocked SDK)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from app.models import AgentSession, ConversationSession
from app.registry import AgentRegistry
from app.tts_service import TTSService

SESSION_ID = "sdk-session-123"
RESULT_TEXT = "The risk score is 42, which is in the medium tier."


def _make_system_msg() -> SystemMessage:
    return SystemMessage(subtype="init", data={"session_id": SESSION_ID})


def _make_result_msg() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=1,
        session_id=SESSION_ID,
        result=RESULT_TEXT,
    )


async def fake_query_gen(**kwargs):
    """Fake async generator mimicking query() output."""
    yield _make_system_msg()
    yield _make_result_msg()


@pytest.fixture
def registry():
    return AgentRegistry()


@pytest.fixture
def tts_service(registry):
    return TTSService(registry)


@pytest.mark.asyncio
async def test_run_captures_session_id(registry, tts_service):
    """SDKAgentRunner should capture sdk_session_id from SystemMessage."""
    from app.sdk_agent_runner import SDKAgentRunner

    runner = SDKAgentRunner(registry, tts_service)
    agent_session = AgentSession(agent_name="shijing")

    with patch("app.sdk_agent_runner.query") as mock_query:
        mock_query.return_value = fake_query_gen()
        await runner.run(agent_session, "What is the risk?")

    assert agent_session.sdk_session_id == SESSION_ID


@pytest.mark.asyncio
async def test_run_calls_tts_with_full_text(registry, tts_service):
    """SDKAgentRunner.run() should return the result text (TTS is caller's job now)."""
    from app.sdk_agent_runner import SDKAgentRunner

    runner = SDKAgentRunner(registry, tts_service)
    agent_session = AgentSession(agent_name="shijing")

    with patch("app.sdk_agent_runner.query") as mock_query:
        mock_query.return_value = fake_query_gen()
        result = await runner.run(agent_session, "What is the risk?")

    assert result == RESULT_TEXT


@pytest.mark.asyncio
async def test_run_appends_conversation_history(registry, tts_service):
    """SDKAgentRunner should append to conversation_history for Gemini context."""
    from app.sdk_agent_runner import SDKAgentRunner

    runner = SDKAgentRunner(registry, tts_service)
    agent_session = AgentSession(agent_name="shijing")

    with patch("app.sdk_agent_runner.query") as mock_query:
        mock_query.return_value = fake_query_gen()
        await runner.run(agent_session, "What is the risk?")

    assert len(agent_session.conversation_history) == 2
    assert agent_session.conversation_history[0]["role"] == "user"
    assert agent_session.conversation_history[1]["role"] == "assistant"
    assert "42" in agent_session.conversation_history[1]["content"]
