"""Tests for agent_comm message emission through callback registry."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_task_manager import AgentTaskManager, _comm_callbacks
from app.models import AgentSession, ConversationSession


def _make_conv_session() -> ConversationSession:
    cs = ConversationSession()
    cs.gen_id = 5
    return cs


def _make_agent_session(agent_name: str = "shijing") -> AgentSession:
    return AgentSession(agent_name=agent_name, parent_session_id="parent-1")


@pytest.mark.asyncio
async def test_comm_callback_sends_agent_comm_json():
    """When on_text fires, an agent_comm JSON message should be sent via send_json."""
    sent_messages = []

    async def fake_send_json(conv_session, msg):
        sent_messages.append(msg)

    async def fake_send_task(agent_name, task_text, metadata=None):
        # Simulate the executor calling on_text via the registry
        cb = _comm_callbacks.get(agent_name)
        if cb:
            on_text_fn = cb
            await on_text_fn(agent_name, "Investigating wire reversal")
        return "Final agent result"

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=True)
    runner = AsyncMock()
    tts = AsyncMock()
    tts.synthesize = AsyncMock(return_value=b"\x00" * 3200)

    mgr = AgentTaskManager(
        agent_registry=registry,
        agent_runner=runner,
        tts_service=tts,
        send_json_fn=fake_send_json,
    )

    conv = _make_conv_session()
    conv.output_controller = MagicMock()

    with patch("app.agent_task_manager.send_task_to_agent", side_effect=fake_send_task), \
         patch("app.agent_task_manager.rephrase_for_tts", return_value="Final result"):
        agent_session = _make_agent_session("shijing")
        conv.agent_sessions[agent_session.agent_session_id] = agent_session
        await mgr._run_agent(conv, agent_session, "Check the wire")

    comm_msgs = [m for m in sent_messages if m.get("type") == "agent_comm"]
    assert len(comm_msgs) >= 1
    assert comm_msgs[0]["from_agent"] == "shijing"
    assert comm_msgs[0]["text"] == "Investigating wire reversal"
    assert comm_msgs[0]["gen_id"] == 5
    assert comm_msgs[0]["to_agent"] is None


@pytest.mark.asyncio
async def test_comm_callback_cleaned_up_after_run():
    """Callback registry should be cleaned up even if send_task raises."""

    async def failing_send_task(agent_name, task_text, metadata=None):
        raise RuntimeError("boom")

    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=True)
    runner = AsyncMock()
    tts = AsyncMock()

    mgr = AgentTaskManager(
        agent_registry=registry,
        agent_runner=runner,
        tts_service=tts,
        send_json_fn=AsyncMock(),
    )

    conv = _make_conv_session()
    agent_session = _make_agent_session("eva")
    conv.agent_sessions[agent_session.agent_session_id] = agent_session

    with patch("app.agent_task_manager.send_task_to_agent", side_effect=failing_send_task):
        await mgr._run_agent(conv, agent_session, "Analyze transactions")

    assert "eva" not in _comm_callbacks
