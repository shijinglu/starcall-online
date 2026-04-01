# A2A Protocol Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Google Agent-to-Agent (A2A) protocol as a middleware layer between Gemini Live API and Claude Agent SDK, enabling agents to discover each other and collaborate directly (agent-to-agent communication).

**Architecture:** Each of the four agents (ellen, eva, ming, shijing) becomes an A2A server hosted as sub-applications inside the existing FastAPI process. The `AgentTaskManager` dispatches tasks via A2A client calls instead of direct `SDKAgentRunner` invocations. A new `ask_agent` MCP tool allows any agent to delegate sub-tasks to peer agents via A2A, with cycle detection and depth limits. The existing voice pipeline (TTS rephraser → Google TTS → OutputController) remains unchanged — A2A only replaces the orchestration layer.

**Tech Stack:** `a2a-sdk[http-server]` (Python, Starlette/FastAPI integration), existing `claude-agent-sdk`, `google-genai`, FastAPI

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `app/a2a/__init__.py` | Package marker |
| `app/a2a/agent_cards.py` | Build `AgentCard` objects from `AgentRegistry` entries |
| `app/a2a/executor.py` | `ClaudeA2AExecutor(AgentExecutor)` — wraps `SDKAgentRunner.run()` as A2A task execution |
| `app/a2a/server.py` | Create per-agent A2A server apps and mount them on the FastAPI app |
| `app/a2a/client.py` | A2A client wrapper for dispatching tasks to agent A2A servers |
| `app/a2a/delegation.py` | `ask_agent` MCP tool — enables inter-agent A2A calls with cycle/depth guards |
| `tests/unit/test_a2a_agent_cards.py` | Tests for agent card generation |
| `tests/unit/test_a2a_executor.py` | Tests for ClaudeA2AExecutor |
| `tests/unit/test_a2a_client.py` | Tests for A2A client response extraction |
| `tests/unit/test_a2a_delegation.py` | Tests for ask_agent tool, cycle detection, depth limits |
| `tests/unit/test_a2a_dispatch.py` | Tests for AgentTaskManager A2A dispatch path |
| `tests/component/test_a2a_roundtrip.py` | End-to-end A2A send_message round-trip |

### Modified files

| File | Change |
|------|--------|
| `pyproject.toml` | Add `a2a-sdk[http-server]` dependency |
| `app/main.py` | Mount A2A sub-apps at startup |
| `app/agent_task_manager.py` | Replace direct `SDKAgentRunner.run()` with A2A client dispatch |
| `app/tools/mcp_servers.py` | Add `ask_agent` tool to each agent's MCP server |
| `app/config.py` | Add `A2A_BASE_URL`, `A2A_MAX_DELEGATION_DEPTH` config |
| `app/models.py` | Add `delegation_chain` field to `AgentSession` |

---

## Chunk 1: Foundation — Dependency, Config, Agent Cards

### Task 1: Add a2a-sdk dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add a2a-sdk to dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
"a2a-sdk[http-server]>=0.3",
```

- [ ] **Step 2: Install and verify**

Run:
```bash
cd backend && uv sync
```

Then verify import:
```bash
.venv/bin/python -c "from a2a.server.apps import A2AStarletteApplication; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add a2a-sdk for agent-to-agent protocol"
```

---

### Task 2: Add A2A configuration constants

**Files:**
- Modify: `backend/app/config.py`

- [ ] **Step 1: Read `app/config.py` to find the right insertion point**

Read the file and locate the section with other runtime constants (near `CLEANUP_INTERVAL_SECONDS`).

- [ ] **Step 2: Add A2A config constants**

Add after the existing runtime constants:

```python
# A2A protocol
A2A_BASE_URL: str = os.getenv("A2A_BASE_URL", "http://localhost:8000")
A2A_MAX_DELEGATION_DEPTH: int = int(os.getenv("A2A_MAX_DELEGATION_DEPTH", "2"))
```

- [ ] **Step 3: Verify import**

Run:
```bash
.venv/bin/python -c "from app.config import A2A_BASE_URL, A2A_MAX_DELEGATION_DEPTH; print(A2A_BASE_URL, A2A_MAX_DELEGATION_DEPTH)"
```
Expected: `http://localhost:8000 2`

- [ ] **Step 4: Commit**

```bash
git add app/config.py
git commit -m "config: add A2A protocol settings"
```

---

### Task 3: Build AgentCard objects from AgentRegistry

**Files:**
- Create: `backend/app/a2a/__init__.py`
- Create: `backend/app/a2a/agent_cards.py`
- Test: `backend/tests/unit/test_a2a_agent_cards.py`

- [ ] **Step 1: Create the package marker**

Create `backend/app/a2a/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/unit/test_a2a_agent_cards.py`:

```python
"""Tests for A2A agent card generation."""

import pytest

from app.a2a.agent_cards import build_agent_card, build_all_agent_cards
from app.registry import AgentRegistry


class TestBuildAgentCard:
    def test_card_has_correct_name(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry)
        assert card.name == "eva"

    def test_card_has_description_from_registry(self):
        registry = AgentRegistry()
        entry = registry.get("eva")
        card = build_agent_card("eva", registry)
        assert entry.description in card.description

    def test_card_has_skills_from_tool_set(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry)
        skill_ids = [s.id for s in card.skills]
        assert "transaction_read" in skill_ids
        assert "bank_data_read" in skill_ids
        assert "chargeback_read" in skill_ids

    def test_card_url_includes_agent_name(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry, base_url="http://localhost:8000")
        assert "/a2a/eva" in card.url

    def test_card_capabilities_include_streaming(self):
        registry = AgentRegistry()
        card = build_agent_card("eva", registry)
        assert card.capabilities.streaming is True

    def test_unknown_agent_raises(self):
        registry = AgentRegistry()
        with pytest.raises(KeyError):
            build_agent_card("nonexistent", registry)


class TestBuildAllAgentCards:
    def test_returns_all_four_agents(self):
        registry = AgentRegistry()
        cards = build_all_agent_cards(registry)
        assert set(cards.keys()) == {"ellen", "eva", "ming", "shijing"}

    def test_each_card_is_agent_card_type(self):
        from a2a.types import AgentCard

        registry = AgentRegistry()
        cards = build_all_agent_cards(registry)
        for card in cards.values():
            assert isinstance(card, AgentCard)
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_agent_cards.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.a2a.agent_cards'`

- [ ] **Step 4: Write implementation**

Create `backend/app/a2a/agent_cards.py`:

```python
"""Build A2A AgentCard objects from the AgentRegistry."""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from app.config import A2A_BASE_URL
from app.registry import AgentRegistry


def build_agent_card(
    agent_name: str,
    registry: AgentRegistry,
    base_url: str = A2A_BASE_URL,
) -> AgentCard:
    """Build an A2A AgentCard for a single agent."""
    entry = registry.get(agent_name)
    if entry is None:
        raise KeyError(f"Unknown agent: {agent_name}")

    skills = [
        AgentSkill(
            id=tool_name,
            name=tool_name,
            description=f"{agent_name} tool: {tool_name}",
            tags=[agent_name],
        )
        for tool_name in entry.tool_set
    ]

    return AgentCard(
        name=agent_name,
        description=entry.description,
        url=f"{base_url}/a2a/{agent_name}",
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
        default_input_modes=["text"],
        default_output_modes=["text"],
    )


def build_all_agent_cards(
    registry: AgentRegistry,
    base_url: str = A2A_BASE_URL,
) -> dict[str, AgentCard]:
    """Build AgentCards for all registered agents."""
    return {
        name: build_agent_card(name, registry, base_url)
        for name in registry.entries
    }
```

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_agent_cards.py -v
```
Expected: all 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/a2a/ tests/unit/test_a2a_agent_cards.py
git commit -m "feat(a2a): agent card generation from registry"
```

---

## Chunk 2: A2A Executor — Wrapping Claude SDK as A2A Task Execution

### Task 4: Create ClaudeA2AExecutor

**Files:**
- Create: `backend/app/a2a/executor.py`
- Test: `backend/tests/unit/test_a2a_executor.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_a2a_executor.py`:

```python
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
    # Embed agent_name in metadata for routing
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
        # First positional arg is an AgentSession
        agent_session = call_args[0][0]
        assert agent_session.agent_name == "eva"
        # Second positional arg is the task text
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

        # Should have enqueued at least one event (the result)
        assert eq.enqueue_event.call_count >= 1

    @pytest.mark.asyncio
    async def test_cancel_sets_cancelled(self):
        runner = AsyncMock()
        registry = MagicMock()
        executor = ClaudeA2AExecutor(sdk_runner=runner, registry=registry)
        ctx = _make_context()
        eq = _make_event_queue()

        # cancel should not raise
        await executor.cancel(ctx, eq)
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_executor.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.a2a.executor'`

- [ ] **Step 3: Write implementation**

Create `backend/app/a2a/executor.py`:

```python
"""A2A AgentExecutor that wraps the Claude Agent SDK runner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState
from a2a.server.tasks import TaskUpdater
from a2a.utils import new_agent_text_message

from app.models import AgentSession

if TYPE_CHECKING:
    from app.registry import AgentRegistry
    from app.sdk_agent_runner import SDKAgentRunner

logger = logging.getLogger(__name__)


class ClaudeA2AExecutor(AgentExecutor):
    """Runs a Claude Agent SDK session as an A2A task."""

    def __init__(
        self,
        sdk_runner: "SDKAgentRunner",
        registry: "AgentRegistry",
    ) -> None:
        self.sdk_runner = sdk_runner
        self.registry = registry

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        agent_name = (context.metadata or {}).get("agent_name", "")
        task_text = context.get_user_input()
        task_id = context.task_id or "unknown"
        context_id = context.context_id or "unknown"

        logger.info(
            "A2A execute: agent=%s, task_id=%s, text=%.100s",
            agent_name, task_id, task_text,
        )

        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()

        try:
            agent_session = AgentSession(agent_name=agent_name)
            result_text = await self.sdk_runner.run(agent_session, task_text)

            await event_queue.enqueue_event(
                new_agent_text_message(
                    result_text or "No result.", context_id, task_id
                )
            )
            await updater.complete()

        except Exception as exc:
            logger.exception("A2A execute failed for agent=%s", agent_name)
            await updater.failed(
                new_agent_text_message(
                    f"Agent error: {exc}", context_id, task_id
                )
            )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        logger.info("A2A cancel requested for task_id=%s", context.task_id)
        updater = TaskUpdater(
            event_queue,
            context.task_id or "unknown",
            context.context_id or "unknown",
        )
        await updater.cancel()
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_executor.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/a2a/executor.py tests/unit/test_a2a_executor.py
git commit -m "feat(a2a): ClaudeA2AExecutor wrapping SDK runner"
```

---

## Chunk 3: A2A Server — Mount Per-Agent Servers on FastAPI

### Task 5: Create A2A server factory and mount logic

**Files:**
- Create: `backend/app/a2a/server.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Read `app/main.py` to understand the startup flow**

Read the full file to find where components are wired together and the FastAPI app is created. Look for the `create_app()` function or equivalent.

- [ ] **Step 2: Write the A2A server module**

Create `backend/app/a2a/server.py`:

```python
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
    """Mount an A2A sub-application for each registered agent.

    Returns a dict of agent_name -> task_store for external access.
    """
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
```

- [ ] **Step 3: Wire A2A mount into `app/main.py`**

Read `app/main.py` and find where the `SDKAgentRunner` and `AgentRegistry` are created. The FastAPI instance is named `application` (not `app`), and the SDK runner is named `sdk_agent_runner`. Add the A2A mount call after they are initialized. Add the following import and call:

```python
from app.a2a.server import mount_a2a_servers
from app.config import A2A_BASE_URL
```

Then after the line where `sdk_agent_runner` is created (and before `application.include_router()`), add:

```python
# Mount A2A servers for inter-agent communication
mount_a2a_servers(application, agent_registry, sdk_agent_runner, base_url=A2A_BASE_URL)
```

- [ ] **Step 4: Verify the server starts and agent cards are accessible**

Run the backend:
```bash
cd backend && timeout 10 .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | head -20
```

Then in another terminal (or after):
```bash
curl -s http://localhost:8000/a2a/eva/.well-known/agent.json | python -m json.tool | head -20
```

Expected: JSON with `"name": "eva"`, `"url"`, `"skills"`, etc.

- [ ] **Step 5: Commit**

```bash
git add app/a2a/server.py app/main.py
git commit -m "feat(a2a): mount per-agent A2A servers on FastAPI"
```

---

## Chunk 4: A2A Client — Dispatch Tasks via A2A Protocol

### Task 6: Create A2A client wrapper

**Files:**
- Create: `backend/app/a2a/client.py`
- Test: `backend/tests/unit/test_a2a_client.py`

- [ ] **Step 1: Write the A2A client module**

Create `backend/app/a2a/client.py`:

```python
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
    """Send a task to an agent via A2A and return the result text.

    Args:
        agent_name: Name of the target agent (e.g., "eva").
        task_text: The task description / question.
        context_id: Optional context ID for multi-turn conversations.
        task_id: Optional task ID to continue an existing task.
        base_url: Base URL of the backend server.
        metadata: Optional metadata dict (e.g., {"agent_name": "eva"}).

    Returns:
        The agent's text response.
    """
    agent_url = f"{base_url}/a2a/{agent_name}"

    async with httpx.AsyncClient(timeout=180.0) as http_client:
        resolver = A2ACardResolver(
            httpx_client=http_client, base_url=agent_url
        )
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

        # Extract text from the response task/message
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

        logger.warning(
            "A2A response for %s had no extractable text", agent_name
        )
        return "No response from agent."
```

- [ ] **Step 2: Write the client unit test**

Create `backend/tests/unit/test_a2a_client.py`:

```python
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

            # Mock the async context manager
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
```

- [ ] **Step 3: Run client tests**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_client.py -v
```
Expected: all 2 tests PASS

- [ ] **Step 4: Commit**

```bash
git add app/a2a/client.py tests/unit/test_a2a_client.py
git commit -m "feat(a2a): A2A client for dispatching tasks to agents"
```

---

### Task 7: Wire AgentTaskManager to dispatch via A2A

**Files:**
- Modify: `backend/app/agent_task_manager.py`
- Test: `backend/tests/unit/test_a2a_dispatch.py`

- [ ] **Step 1: Read `app/agent_task_manager.py` fully**

Read the file to understand the `_run_agent` method — specifically where `self._runner.run(agent_session, task)` is called. Note: the runner field is `self._runner` (not `self.sdk_runner`), and the call is wrapped in `asyncio.wait_for()`.

- [ ] **Step 2: Add A2A dispatch path to `_run_agent`**

Find this pattern in `_run_agent`:

**Before:**
```python
full_text = await asyncio.wait_for(
    self._runner.run(agent_session, task),
    timeout=AGENT_TASK_TIMEOUT_SECONDS,
)
```

**After:**
```python
full_text = await asyncio.wait_for(
    send_task_to_agent(
        agent_name=agent_session.agent_name,
        task_text=task,
        metadata={"agent_name": agent_session.agent_name},
    ),
    timeout=AGENT_TASK_TIMEOUT_SECONDS,
)
```

Add the import at the top of the file:
```python
from app.a2a.client import send_task_to_agent
```

Keep the rest of `_run_agent` unchanged — the TTS rephrasing and audio enqueuing still happen after this call. Do NOT add `agent_session.status = "idle"` — it's already set later in the method.

- [ ] **Step 3: Write unit test for the new dispatch path**

Create `backend/tests/unit/test_a2a_dispatch.py`:

```python
"""Tests for AgentTaskManager dispatching via A2A."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.models import AgentSession


class TestA2ADispatchPath:
    @pytest.mark.asyncio
    async def test_dispatch_calls_send_task_to_agent(self):
        """Verify _run_agent calls send_task_to_agent instead of SDK runner."""
        with patch("app.agent_task_manager.send_task_to_agent", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "Test result"

            # Import after patching
            from app.agent_task_manager import AgentTaskManager

            # Create minimal mocks for dependencies
            manager = MagicMock(spec=AgentTaskManager)
            manager._tts = AsyncMock()
            manager._tts.synthesize = AsyncMock(return_value=b"\x00" * 3200)
            manager._rephraser = AsyncMock(return_value="Test result")

            agent_session = AgentSession(agent_name="eva")

            mock_send.assert_not_called()
            await mock_send("eva", "Analyze transactions", metadata={"agent_name": "eva"})
            mock_send.assert_called_once()
```

- [ ] **Step 4: Run tests**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_dispatch.py tests/unit/ -v
```
Expected: All unit tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent_task_manager.py tests/unit/test_a2a_dispatch.py
git commit -m "feat(a2a): dispatch agent tasks via A2A protocol"
```

---

## Chunk 5: Inter-Agent Delegation — The `ask_agent` Tool

### Task 8: Create the ask_agent delegation tool with safety guards

**Files:**
- Create: `backend/app/a2a/delegation.py`
- Test: `backend/tests/unit/test_a2a_delegation.py`
- Modify: `backend/app/models.py`

- [ ] **Step 1: Add delegation_chain to AgentSession**

Read `app/models.py` and add a `delegation_chain` field to `AgentSession`:

```python
delegation_chain: list[str] = field(default_factory=list)
```

This tracks which agents have been involved in the current delegation chain (for cycle detection).

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/unit/test_a2a_delegation.py`:

```python
"""Tests for inter-agent delegation via A2A."""

import pytest
from unittest.mock import AsyncMock, patch

from app.a2a.delegation import (
    check_delegation_allowed,
    DelegationError,
)


class TestCheckDelegationAllowed:
    def test_allows_first_delegation(self):
        # No chain yet — should be allowed
        check_delegation_allowed(
            source_agent="eva",
            target_agent="ming",
            delegation_chain=[],
            max_depth=2,
        )

    def test_rejects_self_delegation(self):
        with pytest.raises(DelegationError, match="self-delegation"):
            check_delegation_allowed(
                source_agent="eva",
                target_agent="eva",
                delegation_chain=[],
                max_depth=2,
            )

    def test_rejects_cycle(self):
        with pytest.raises(DelegationError, match="cycle"):
            check_delegation_allowed(
                source_agent="ming",
                target_agent="eva",
                delegation_chain=["eva", "ming"],
                max_depth=5,
            )

    def test_rejects_depth_exceeded(self):
        with pytest.raises(DelegationError, match="depth"):
            check_delegation_allowed(
                source_agent="shijing",
                target_agent="ellen",
                delegation_chain=["eva", "ming"],
                max_depth=2,
            )

    def test_allows_at_max_depth(self):
        # Chain has 1 entry, max_depth=2 → depth would be 2, allowed
        check_delegation_allowed(
            source_agent="ming",
            target_agent="ellen",
            delegation_chain=["eva"],
            max_depth=2,
        )
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_delegation.py -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Write implementation**

Create `backend/app/a2a/delegation.py`:

```python
"""Inter-agent delegation via A2A with cycle and depth guards."""

from __future__ import annotations

import json
import logging

from app.config import A2A_MAX_DELEGATION_DEPTH

logger = logging.getLogger(__name__)


class DelegationError(Exception):
    """Raised when a delegation is not allowed."""


def check_delegation_allowed(
    source_agent: str,
    target_agent: str,
    delegation_chain: list[str],
    max_depth: int = A2A_MAX_DELEGATION_DEPTH,
) -> None:
    """Validate that a delegation from source to target is safe.

    Raises DelegationError if:
    - source == target (self-delegation)
    - target already in chain (cycle)
    - chain length >= max_depth (depth exceeded)
    """
    if source_agent == target_agent:
        raise DelegationError(
            f"Blocked self-delegation: {source_agent} → {target_agent}"
        )
    if target_agent in delegation_chain:
        raise DelegationError(
            f"Blocked cycle: {' → '.join(delegation_chain)} → "
            f"{source_agent} → {target_agent}"
        )
    if len(delegation_chain) >= max_depth:
        raise DelegationError(
            f"Blocked depth exceeded ({len(delegation_chain)}/{max_depth}): "
            f"{' → '.join(delegation_chain)} → {source_agent} → {target_agent}"
        )


async def ask_agent_handler(args: dict) -> dict:
    """MCP tool handler: ask another agent a question via A2A.

    Args (from MCP tool call):
        agent_name: str — target agent name
        question: str — the question/task to send
        delegation_chain: list[str] — current chain (injected by runtime)

    Returns:
        MCP tool response with the agent's answer.
    """
    from app.a2a.client import send_task_to_agent

    target = args["agent_name"]
    question = args["question"]
    chain = args.get("delegation_chain", [])
    source = args.get("source_agent", "unknown")

    try:
        check_delegation_allowed(source, target, chain)
    except DelegationError as exc:
        logger.warning("Delegation blocked: %s", exc)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"error": "delegation_blocked", "reason": str(exc)}
                    ),
                }
            ]
        }

    new_chain = chain + [source]
    logger.info(
        "A2A delegation: %s → %s, chain=%s, question=%.100s",
        source, target, new_chain, question,
    )

    try:
        result = await send_task_to_agent(
            agent_name=target,
            task_text=question,
            metadata={
                "agent_name": target,
                "delegation_chain": new_chain,
            },
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"agent": target, "response": result}
                    ),
                }
            ]
        }
    except Exception as exc:
        logger.exception("A2A delegation to %s failed", target)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"error": "delegation_failed", "reason": str(exc)}
                    ),
                }
            ]
        }
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_a2a_delegation.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/a2a/delegation.py app/models.py tests/unit/test_a2a_delegation.py
git commit -m "feat(a2a): inter-agent delegation with cycle/depth guards"
```

---

### Task 9: Register ask_agent as an MCP tool for all agents

**Files:**
- Modify: `backend/app/tools/mcp_servers.py`

- [ ] **Step 1: Read `app/tools/mcp_servers.py` fully**

Read the file to understand the tool registration pattern.

- [ ] **Step 2: Add the ask_agent tool definition**

Add the following tool definition alongside the existing tools. Use the same positional-arg `@tool` decorator pattern used by all other tools in this file:

```python
from app.a2a.delegation import ask_agent_handler

@tool(
    "ask_agent",
    "Ask another agent a question. Use this when you need expertise "
    "outside your domain. Available agents: ellen (calendar/email/tasks), "
    "eva (transactions/bank data/chargebacks), ming (fraud/ID checks/risk), "
    "shijing (user profiles/journeys/risk scores). "
    "Do NOT ask yourself.",
    {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "enum": ["ellen", "eva", "ming", "shijing"],
                "description": "Name of the agent to ask",
            },
            "question": {
                "type": "string",
                "description": "The question or task for the other agent",
            },
        },
        "required": ["agent_name", "question"],
    },
)
async def ask_agent(args: dict[str, Any]) -> dict[str, Any]:
    return await ask_agent_handler(args)
```

**Important — delegation chain injection:** The `ask_agent_handler` needs `source_agent` and `delegation_chain` in the args dict, but Claude only passes `agent_name` and `question` from the MCP schema. To solve this, create a **per-agent wrapper factory** that injects the source agent name:

```python
def _make_ask_agent_for(source_name: str):
    """Create an ask_agent handler that knows its source agent name."""
    @tool(
        "ask_agent",
        "Ask another agent a question. Use this when you need expertise "
        "outside your domain. Do NOT ask yourself.",
        {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "enum": [n for n in ["ellen", "eva", "ming", "shijing"] if n != source_name],
                    "description": "Name of the agent to ask",
                },
                "question": {
                    "type": "string",
                    "description": "The question or task for the other agent",
                },
            },
            "required": ["agent_name", "question"],
        },
    )
    async def ask_agent(args: dict[str, Any]) -> dict[str, Any]:
        args["source_agent"] = source_name
        return await ask_agent_handler(args)

    return ask_agent
```

The `delegation_chain` is propagated via the A2A metadata: when `ask_agent_handler` calls `send_task_to_agent`, it passes `metadata={"delegation_chain": new_chain}`. The A2A executor receives this metadata and passes it through to the next agent's MCP context. The `delegation_chain` flows through A2A task metadata, not MCP tool args — so the handler reads it from the A2A request context, not from Claude's tool call.

- [ ] **Step 3: Add ask_agent to each agent's MCP server**

Use the factory to create per-agent `ask_agent` tools, then add them to `AGENT_MCP_SERVERS`:

```python
_ask_ellen = _make_ask_agent_for("ellen")
_ask_eva = _make_ask_agent_for("eva")
_ask_ming = _make_ask_agent_for("ming")
_ask_shijing = _make_ask_agent_for("shijing")

AGENT_MCP_SERVERS: dict[str, Any] = {
    "ellen": create_sdk_mcp_server("ellen_tools", tools=[calendar_read, email_send, task_list, _ask_ellen]),
    "eva": create_sdk_mcp_server("eva_tools", tools=[transaction_read, bank_data_read, chargeback_read, _ask_eva]),
    "ming": create_sdk_mcp_server("ming_tools", tools=[id_check, async_risk_check, fraud_signal_read, _ask_ming]),
    "shijing": create_sdk_mcp_server("shijing_tools", tools=[user_profile_read, user_journey_read, risk_score_read, _ask_shijing]),
}
```

Note: The `enum` in each factory-generated tool excludes the source agent (e.g., Ellen's `ask_agent` can only target eva, ming, shijing — preventing self-delegation at the schema level).

- [ ] **Step 4: Run existing MCP tests to verify nothing broke**

Run:
```bash
cd backend && .venv/bin/python -m pytest tests/unit/test_mcp_servers.py -v
```
Expected: all existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/mcp_servers.py
git commit -m "feat(a2a): add ask_agent MCP tool to all agents"
```

---

## Chunk 6: Agent Prompt Updates — Teach Agents About Collaboration

### Task 10: Update agent system prompts

**Files:**
- Modify: `backend/prompts/ellen.txt`
- Modify: `backend/prompts/eva.txt`
- Modify: `backend/prompts/ming.txt`
- Modify: `backend/prompts/shijing.txt`

- [ ] **Step 1: Read all four prompt files**

Read each file to understand the current persona instructions.

- [ ] **Step 2: Update "You have access to" line and append collaboration instructions**

For each prompt file, first update the existing "You have access to" line (typically near the top) to include `ask_agent`. For example, in `eva.txt`, change:
```
You have access to: transaction_read, bank_data_read, chargeback_read tools.
```
to:
```
You have access to: transaction_read, bank_data_read, chargeback_read, ask_agent tools.
```

Do the same for all four agents. Then append the collaboration block at the end of each file. Customize the peer list to exclude the agent itself:

**For `eva.txt`** (append):
```
## Collaborating with Other Agents

You have an `ask_agent` tool that lets you consult other agents when their expertise is needed:
- **ellen**: Calendar, email, and task management
- **ming**: Fraud investigation, ID verification, risk checks
- **shijing**: User risk profiles, user journeys, risk scoring

Use `ask_agent` when:
- You find a flagged transaction and need ming to run fraud checks on it
- You need shijing's user risk profile to contextualize transaction patterns
- You need ellen to check if there are related tasks or communications

Do NOT use `ask_agent` for tasks you can handle yourself with your own tools. Keep delegations focused — send a clear, specific question, not a vague request.
```

**For `ming.txt`** (append — list ellen, eva, shijing):
```
## Collaborating with Other Agents

You have an `ask_agent` tool that lets you consult other agents when their expertise is needed:
- **ellen**: Calendar, email, and task management
- **eva**: Transaction analysis, bank data, chargebacks
- **shijing**: User risk profiles, user journeys, risk scoring

Use `ask_agent` when:
- You need eva's transaction data to correlate with fraud signals
- You need shijing's user risk profile for a fraud investigation
- You need ellen to flag a task or send an alert email

Do NOT use `ask_agent` for tasks you can handle yourself with your own tools. Keep delegations focused — send a clear, specific question, not a vague request.
```

**For `ellen.txt`** (append — list eva, ming, shijing):
```
## Collaborating with Other Agents

You have an `ask_agent` tool that lets you consult other agents when their expertise is needed:
- **eva**: Transaction analysis, bank data, chargebacks
- **ming**: Fraud investigation, ID verification, risk checks
- **shijing**: User risk profiles, user journeys, risk scoring

Use `ask_agent` when:
- You need eva's transaction summary to include in an email or report
- You need ming's fraud assessment to flag a high-priority task
- You need shijing's user profile data for a personalized communication

Do NOT use `ask_agent` for tasks you can handle yourself with your own tools. Keep delegations focused — send a clear, specific question, not a vague request.
```

**For `shijing.txt`** (append — list ellen, eva, ming):
```
## Collaborating with Other Agents

You have an `ask_agent` tool that lets you consult other agents when their expertise is needed:
- **ellen**: Calendar, email, and task management
- **eva**: Transaction analysis, bank data, chargebacks
- **ming**: Fraud investigation, ID verification, risk checks

Use `ask_agent` when:
- You need eva's transaction data to correlate with user risk patterns
- You need ming's fraud signals to complement a risk profile
- You need ellen to schedule a review or send a notification

Do NOT use `ask_agent` for tasks you can handle yourself with your own tools. Keep delegations focused — send a clear, specific question, not a vague request.
```

- [ ] **Step 3: Commit**

```bash
cd backend && git add prompts/
git commit -m "feat(a2a): teach agents about inter-agent collaboration"
```

---

## Chunk 7: End-to-End Integration Test

### Task 11: A2A round-trip component test

**Files:**
- Create: `backend/tests/component/test_a2a_roundtrip.py`

- [ ] **Step 1: Write the component test**

Create `backend/tests/component/test_a2a_roundtrip.py`:

```python
"""Component test: A2A send_message round-trip.

Requires: ANTHROPIC_API_KEY set in environment.
Starts a temporary FastAPI server with A2A sub-apps and sends a task.
"""

import os
import pytest
import httpx
import uvicorn
import asyncio
from uuid import uuid4

from a2a.client import A2AClient, A2ACardResolver
from a2a.types import MessageSendParams, SendMessageRequest

pytestmark = pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)


@pytest.fixture
async def test_server():
    """Start the FastAPI app on a random port for testing."""
    from app.main import app  # the module-level FastAPI instance
    import socket

    # Find a free port
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    # Wait for server to be ready (poll health endpoint)
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient() as client:
        for _ in range(20):
            try:
                resp = await client.get(f"{base}/api/v1/health")
                if resp.status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.25)

    yield base

    server.should_exit = True
    await task


@pytest.mark.asyncio
async def test_a2a_eva_returns_transaction_analysis(test_server):
    """Send a task to Eva's A2A server and get a response."""
    base_url = f"{test_server}/a2a/eva"

    async with httpx.AsyncClient(timeout=120.0) as http_client:
        resolver = A2ACardResolver(
            httpx_client=http_client, base_url=base_url
        )
        card = await resolver.get_agent_card()
        assert card.name == "eva"

        client = A2AClient(httpx_client=http_client, agent_card=card)

        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(
                message={
                    "role": "user",
                    "parts": [
                        {
                            "kind": "text",
                            "text": "How many transactions were there in the last day?",
                        }
                    ],
                    "message_id": uuid4().hex,
                },
                metadata={"agent_name": "eva"},
            ),
        )

        response = await client.send_message(request)
        result = response.root.result

        # Should have completed with some text about transactions
        assert result is not None
```

- [ ] **Step 2: Run the test (requires API key and backend)**

Run:
```bash
cd backend && ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY .venv/bin/python -m pytest tests/component/test_a2a_roundtrip.py -v --timeout=180
```
Expected: PASS (may take 30-60s due to Claude SDK cold start)

- [ ] **Step 3: Commit**

```bash
git add tests/component/test_a2a_roundtrip.py
git commit -m "test(a2a): end-to-end round-trip component test"
```

---

## Chunk 8: Final Verification and Cleanup

### Task 12: Run full test suite and verify

- [ ] **Step 1: Run all unit tests**

```bash
cd backend && .venv/bin/python -m pytest tests/unit/ -v
```
Expected: All PASS

- [ ] **Step 2: Run all component tests (if API keys available)**

```bash
cd backend && .venv/bin/python -m pytest tests/component/ -v --timeout=180
```
Expected: All PASS (or skip if no API keys)

- [ ] **Step 3: Manual smoke test — start backend and verify Agent Cards**

```bash
cd backend && make run &
sleep 3
curl -s http://localhost:8000/a2a/eva/.well-known/agent.json | python -m json.tool
curl -s http://localhost:8000/a2a/ellen/.well-known/agent.json | python -m json.tool
curl -s http://localhost:8000/a2a/ming/.well-known/agent.json | python -m json.tool
curl -s http://localhost:8000/a2a/shijing/.well-known/agent.json | python -m json.tool
```
Expected: Each returns a valid Agent Card JSON with correct name, skills, and URL.

- [ ] **Step 4: Commit any final adjustments**

```bash
git add -A
git commit -m "chore(a2a): final cleanup and verification"
```

---

## Summary

| Chunk | Tasks | What it delivers |
|-------|-------|-----------------|
| 1. Foundation | 1-3 | Dependency, config, AgentCard generation |
| 2. Executor | 4 | Claude SDK wrapped as A2A AgentExecutor |
| 3. Server | 5 | Per-agent A2A servers mounted on FastAPI |
| 4. Client | 6-7 | A2A client + AgentTaskManager dispatch via A2A |
| 5. Delegation | 8-9 | `ask_agent` MCP tool with cycle/depth guards |
| 6. Prompts | 10 | Agent collaboration instructions |
| 7. Integration | 11 | End-to-end A2A round-trip test |
| 8. Verification | 12 | Full test suite pass, smoke test |

**Total: 12 tasks, ~50 steps**

After this plan is complete, the system supports:
- Agents discoverable via A2A Agent Cards at `/a2a/{agent_name}/.well-known/agent.json`
- Tasks dispatched via A2A protocol instead of direct SDK calls
- Inter-agent delegation: any agent can call `ask_agent` to consult peers
- Safety guards: cycle detection, depth limits, self-delegation prevention
