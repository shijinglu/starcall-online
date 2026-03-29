# Agent SDK Migration Design

> Migrate from raw Anthropic client SDK (`anthropic`) to Claude Agent SDK (`claude-agent-sdk`) to unlock skills, subagents, MCP, hooks, memory, and plugins for the deep agents, while simplifying the codebase.

## Goals

1. **Unlock Agent SDK features** — skills, subagents, memory, plugins for Ellen, Shijing, Eva, Ming
2. **Simplify the codebase** — eliminate hand-rolled tool loop, tool dispatch, conversation history management

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| TTS integration | Whole-message (wait for full response) | Simpler code, accept higher latency |
| Tool scoping | Open — built-in tools + custom MCP tools | Refine restrictions later |
| Session pattern | `query()` with `resume=session_id` | Stateless calls, no persistent client object |
| Orchestration | Keep `AgentTaskManager`, slim it down | Remove what SDK handles (tool loop, history) |
| Skills/subagents | Infrastructure only | Placeholder dirs, add specifics later |
| Migration strategy | Full replace (Approach 1) | Agents are structurally identical, change is contained |

## Section 1: New Dependency & Package Changes

- Replace `anthropic>=0.28` with `claude-agent-sdk` in `pyproject.toml`
- The Agent SDK depends on `anthropic` internally, so both aren't needed
- Requires Claude Code CLI installed on the host

**Config changes in `config.py`:**

| Config | Change |
|--------|--------|
| `CLAUDE_MODEL` | Kept, passed to `ClaudeAgentOptions.model` |
| `MAX_TOOL_ROUNDS` | Replaced by `ClaudeAgentOptions.max_turns` |
| `MAX_AGENT_BUDGET_USD` | New — optional cost cap per agent invocation |
| `BACKEND_DIR` | New — working directory for SDK process |

## Section 2: Custom Tools as MCP Servers

The four tool modules (`ellen_tools.py`, `shijing_tools.py`, `eva_tools.py`, `ming_tools.py`) stay as implementations. Each gets wrapped with `@tool` decorators and bundled into per-agent MCP servers.

**New file: `backend/app/tools/mcp_servers.py`**

```python
import json
from claude_agent_sdk import tool, create_sdk_mcp_server
from app.tools import ellen_tools, eva_tools, ming_tools, shijing_tools

# -- Shijing tools --

@tool("user_profile_read", "Read the user's profile", {"user_id": str})
async def user_profile_read(args):
    result = await shijing_tools.user_profile_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}

@tool("user_journey_read", "Read user journey / activity analytics", {"user_id": str, "days": int})
async def user_journey_read(args):
    result = await shijing_tools.user_journey_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}

@tool("risk_score_read", "Read the user's risk score", {"user_id": str})
async def risk_score_read(args):
    result = await shijing_tools.risk_score_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}

# -- Ellen, Eva, Ming tools follow same pattern --
# ...

AGENT_MCP_SERVERS = {
    "ellen": create_sdk_mcp_server("ellen_tools", tools=[calendar_read, email_send, task_list]),
    "shijing": create_sdk_mcp_server("shijing_tools", tools=[user_profile_read, user_journey_read, risk_score_read]),
    "eva": create_sdk_mcp_server("eva_tools", tools=[transaction_read, bank_data_read, chargeback_read]),
    "ming": create_sdk_mcp_server("ming_tools", tools=[id_check, async_risk_check, fraud_signal_read]),
}
```

**Deleted:**
- `AGENT_TOOL_DEFINITIONS` dict from `deep_agent_runner.py`
- `tools/dispatch.py` and `TOOL_MAP`

**Kept:**
- `ellen_tools.py`, `shijing_tools.py`, `eva_tools.py`, `ming_tools.py` — async functions remain the actual implementations

## Section 3: New Agent Runner

`DeepAgentRunner` is replaced by `SDKAgentRunner` — a thin wrapper around `query()`.

**New file: `backend/app/sdk_agent_runner.py`**

```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage, SystemMessage

from app.config import CLAUDE_MODEL, MAX_TOOL_ROUNDS, MAX_AGENT_BUDGET_USD, BACKEND_DIR
from app.tools.mcp_servers import AGENT_MCP_SERVERS

if TYPE_CHECKING:
    from app.models import AgentSession, ConversationSession
    from app.registry import AgentRegistry
    from app.tts_service import TTSService

logger = logging.getLogger(__name__)


class SDKAgentRunner:
    """Runs a Claude agent via the Agent SDK with whole-message TTS."""

    def __init__(self, agent_registry: AgentRegistry, tts_service: TTSService) -> None:
        self._registry = agent_registry
        self._tts = tts_service

    async def run(
        self,
        agent_session: AgentSession,
        task: str,
        conv_session: ConversationSession,
        deliver_fn=None,
    ) -> None:
        entry = self._registry.get(agent_session.agent_name)
        if entry is None:
            raise ValueError(f"Unknown agent: {agent_session.agent_name}")

        mcp_server = AGENT_MCP_SERVERS[agent_session.agent_name]

        options = ClaudeAgentOptions(
            model=CLAUDE_MODEL,
            system_prompt=entry.system_prompt,
            mcp_servers={f"{agent_session.agent_name}_tools": mcp_server},
            max_turns=MAX_TOOL_ROUNDS,
            max_budget_usd=MAX_AGENT_BUDGET_USD,
            permission_mode="bypassPermissions",
            resume=agent_session.sdk_session_id,
            setting_sources=["project"],
            cwd=str(BACKEND_DIR),
            agents=entry.subagents if entry.subagents else None,
        )

        full_text = ""
        async for message in query(prompt=task, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                agent_session.sdk_session_id = message.session_id
            elif isinstance(message, ResultMessage):
                agent_session.sdk_session_id = message.session_id
                full_text = message.result or full_text

        # Whole-message TTS
        if deliver_fn and full_text:
            pcm = await self._tts.synthesize(full_text, agent_session.agent_name)
            if pcm:
                await deliver_fn(conv_session, agent_session, pcm)

        # Update conversation history for Gemini context
        agent_session.conversation_history.append({"role": "user", "content": task})
        agent_session.conversation_history.append({"role": "assistant", "content": full_text})
```

**Key differences from `DeepAgentRunner`:**
- No manual streaming loop, no sentence splitting, no tool dispatch — SDK handles all of it
- Session continuity via `resume=session_id` instead of manually passing conversation history
- TTS fires once with full response text (not per-sentence)
- ~50 lines vs ~160 lines

**Deleted:** `deep_agent_runner.py` entirely (including `split_into_sentences`, `ABBREVIATIONS`, `_SENTENCE_END_RE`)

## Section 4: Model Changes

**`AgentSession` (models.py):**

```python
@dataclass
class AgentSession:
    # ... existing fields ...
    sdk_session_id: str | None = None  # NEW — Agent SDK session ID for resume
```

**`AgentRegistryEntry` (models.py):**

```python
@dataclass
class AgentRegistryEntry:
    name: str
    description: str
    voice_id: str
    speaker_id: int
    system_prompt: str
    tool_set: list[str]
    subagents: dict = field(default_factory=dict)  # NEW — AgentDefinition dict
```

## Section 5: AgentTaskManager Simplification

**Stays unchanged:**
- `dispatch()` — creates AgentSession, spawns background task, emits "thinking"
- `resume()` — reactivates idle agent
- `handle_interrupt()` — cancel_all and skip_speaker
- `_run_agent()` — timeout wrapper, heartbeat, status emission
- `_heartbeat_loop()` — periodic thinking status
- Meeting-mode audio queuing

**Changes:**
- Constructor type hint: `SDKAgentRunner` instead of `DeepAgentRunner`
- `_handle_timeout()` — remove manual conversation history append (SDK manages history via sessions). Still synthesize and deliver fallback audio.

## Section 6: Skills & Subagents Infrastructure

**Directory structure:**

```
backend/
  .claude/
    skills/
      shijing/SKILL.md     # placeholder
      ellen/SKILL.md        # placeholder
      eva/SKILL.md          # placeholder
      ming/SKILL.md         # placeholder
    agents/                 # empty, for future subagent definitions
```

Enabled by `setting_sources=["project"]` and `cwd=str(BACKEND_DIR)` in `SDKAgentRunner`.

To add a skill later: drop a `SKILL.md` in the agent's skill directory.
To add a subagent later: add an `AgentDefinition` to the registry entry's `subagents` dict and include `"Agent"` in `allowed_tools`.

## File-Level Change Summary

| File | Action | Notes |
|------|--------|-------|
| `pyproject.toml` | Edit | Replace `anthropic>=0.28` with `claude-agent-sdk` |
| `config.py` | Edit | Add `MAX_AGENT_BUDGET_USD`, `BACKEND_DIR` |
| `models.py` | Edit | Add `sdk_session_id` to `AgentSession`, `subagents` to `AgentRegistryEntry` |
| `registry.py` | Edit | No structural change, `subagents` comes from model |
| `sdk_agent_runner.py` | Create | New runner wrapping Agent SDK |
| `tools/mcp_servers.py` | Create | `@tool` wrappers + `AGENT_MCP_SERVERS` dict |
| `agent_task_manager.py` | Edit | Type hint `SDKAgentRunner`, remove history append from timeout |
| `main.py` | Edit | Import `SDKAgentRunner` instead of `DeepAgentRunner` |
| `deep_agent_runner.py` | Delete | Fully replaced |
| `tools/dispatch.py` | Delete | SDK handles dispatch |
| `backend/.claude/skills/` | Create | Placeholder SKILL.md per agent |
| `backend/.claude/agents/` | Create | Empty dir for future subagent definitions |
| Tests referencing `DeepAgentRunner` | Edit | Update imports and mocks |

**Not touched:**
- `ellen_tools.py`, `shijing_tools.py`, `eva_tools.py`, `ming_tools.py`
- `gemini_proxy.py`
- `tts_service.py`
- `session_manager.py`
- `ws/handler.py` (unless it imports `DeepAgentRunner` directly)

## Section 7: Subprocess Lifecycle & Operational Concerns

The Agent SDK spawns a Claude Code CLI subprocess for each `query()` call. This has implications for a FastAPI async backend:

### Timeout and process cleanup

`AgentTaskManager._run_agent()` wraps the runner with `asyncio.wait_for(..., timeout=30s)`. When the timeout fires:
- The `query()` async generator receives a `CancelledError`
- The SDK is responsible for terminating its subprocess on generator cleanup (`__aexit__` / `aclose()`)
- **Mitigation:** In `SDKAgentRunner.run()`, wrap the `query()` loop in a try/finally that explicitly calls `aclose()` on the async generator to ensure cleanup:

```python
async def run(self, agent_session, task, conv_session, deliver_fn=None):
    ...
    gen = query(prompt=task, options=options)
    try:
        async for message in gen:
            ...
    finally:
        await gen.aclose()  # ensure subprocess cleanup on timeout/cancel
```

### Concurrent process limits

In meeting mode, up to 4 agents run simultaneously = 4 subprocesses. This is acceptable for a single-user demo. For production with multiple concurrent WebSocket clients, add an `asyncio.Semaphore` in `AgentTaskManager` to cap concurrent agent invocations:

```python
class AgentTaskManager:
    def __init__(self, ...):
        ...
        self._agent_semaphore = asyncio.Semaphore(8)  # max concurrent agents

    async def _run_agent(self, conv_session, agent_session, task):
        async with self._agent_semaphore:
            # existing timeout + heartbeat logic
```

### Deployment prerequisite

The Claude Code CLI must be installed on the host. Add to deployment docs:
```bash
npm install -g @anthropic-ai/claude-code
```

### Session state after timeout

When `asyncio.wait_for` kills a `query()` mid-execution, the SDK session may be in an inconsistent state (e.g., expecting a tool result). The `resume=session_id` call on a subsequent `resume()` may fail. **Mitigation:** On timeout, clear `sdk_session_id` so the next invocation starts a fresh session rather than attempting to resume a broken one:

```python
async def _handle_timeout(self, conv_session, agent_session):
    agent_session.status = "timeout"
    agent_session.sdk_session_id = None  # don't resume a broken session
    # ... rest of timeout handling
```

## Section 8: Conversation History Clarification

After migration, there are two sources of conversation state:

| Source | Purpose | Managed by |
|--------|---------|------------|
| `sdk_session_id` | Claude's full conversation context (tool calls, reasoning, history) | Agent SDK sessions |
| `agent_session.conversation_history` | Summary fed back to Gemini moderator so it knows what agents said | Application code |

These serve different purposes and are not redundant. `conversation_history` is a lightweight text-only summary for Gemini; the SDK session contains the full rich context. The runner appends to `conversation_history` after each invocation purely for Gemini's benefit.

## Section 9: Test File Disposition

| Test file | Current dependency | Action | Rationale |
|-----------|-------------------|--------|-----------|
| `tests/unit/test_sentence_splitter.py` | `split_into_sentences` from `deep_agent_runner` | **Delete** | Sentence splitting removed (whole-message TTS) |
| `tests/component/test_l3_04_claude_streaming.py` | `split_into_sentences`, raw `anthropic` | **Delete** | Tests streaming sentence splitting which no longer exists |
| `tests/component/test_l3_05_claude_history.py` | raw `anthropic` | **Keep as dev dependency test** | Tests raw SDK behavior; keep `anthropic` in dev deps |
| `tests/component/test_l3_06_claude_timeout.py` | raw `anthropic` | **Rewrite** | Rewrite to test SDK subprocess timeout behavior |
| `tests/component/test_l3_08_persona_isolation.py` | `AGENT_TOOL_DEFINITIONS`, raw `anthropic` | **Rewrite** | Rewrite to verify MCP tool scoping per agent |

### New tests to create

| Test file | Purpose |
|-----------|---------|
| `tests/unit/test_mcp_servers.py` | Verify `@tool` wrappers call through to underlying tool functions |
| `tests/component/test_sdk_agent_runner.py` | Mock `query()`, verify session ID capture, TTS call, history append |
| `tests/component/test_sdk_timeout.py` | Verify subprocess cleanup on `asyncio.wait_for` timeout |
| `tests/component/test_sdk_resume.py` | Verify `resume=session_id` produces a valid continuation |

### Dev dependency note

Keep `anthropic` as a dev/test dependency in `pyproject.toml` for any retained raw-SDK tests:
```toml
[project.optional-dependencies]
dev = ["anthropic>=0.28", ...]
```
