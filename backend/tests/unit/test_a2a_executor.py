"""Tests for ClaudeA2AExecutor."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.a2a.executor import ClaudeA2AExecutor


def _make_context(text: str = "Analyze transactions", agent_name: str = "eva"):
    """Build a minimal RequestContext-like object."""
    ctx = MagicMock()
    ctx.get_user_input.return_value = text
    ctx.task_id = "task-123"
    ctx.context_id = "ctx-456"
    ctx.current_task = None
    ctx.metadata = {"agent_name": agent_name}
    return ctx


def _make_event_queue():
    queue = AsyncMock()
    queue.enqueue_event = AsyncMock()
    return queue


class TestClaudeA2AExecutor:
    @pytest.mark.asyncio
    async def test_execute_calls_sdk_runner(self):
        runner = AsyncMock()
        runner.run = AsyncMock(return_value="Transaction analysis result")
        registry = MagicMock()

        executor = ClaudeA2AExecutor(sdk_runner=runner, registry=registry)
        ctx = _make_context("Analyze transactions", "eva")
        eq = _make_event_queue()

        await executor.execute(ctx, eq)

        runner.run.assert_called_once()
        call_args = runner.run.call_args
        agent_session = call_args[0][0]
        assert agent_session.agent_name == "eva"
        assert call_args[0][1] == "Analyze transactions"

    @pytest.mark.asyncio
    async def test_execute_enqueues_result_message(self):
        runner = AsyncMock()
        runner.run = AsyncMock(return_value="Result text here")
        registry = MagicMock()

        executor = ClaudeA2AExecutor(sdk_runner=runner, registry=registry)
        ctx = _make_context("Do something", "ellen")
        eq = _make_event_queue()

        await executor.execute(ctx, eq)

        assert eq.enqueue_event.call_count >= 1

    @pytest.mark.asyncio
    async def test_cancel_does_not_raise(self):
        runner = AsyncMock()
        registry = MagicMock()
        executor = ClaudeA2AExecutor(sdk_runner=runner, registry=registry)
        ctx = _make_context()
        eq = _make_event_queue()

        await executor.cancel(ctx, eq)
