"""SDKAgentRunner -- Claude Agent SDK wrapper with whole-message TTS."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from app.config import BACKEND_DIR, CLAUDE_MODEL, MAX_AGENT_BUDGET_USD, MAX_TOOL_ROUNDS
from app.tools.mcp_servers import AGENT_MCP_SERVERS

if TYPE_CHECKING:
    from app.models import AgentSession, ConversationSession
    from app.registry import AgentRegistry
    from app.tts_service import TTSService

logger = logging.getLogger(__name__)

# Thread-local delegation chain storage: agent_name -> chain
_active_delegation_chains: dict[str, list[str]] = {}


class SDKAgentRunner:
    """Runs a Claude agent via the Agent SDK with whole-message TTS."""

    def __init__(
        self,
        agent_registry: "AgentRegistry",
        tts_service: "TTSService",
    ) -> None:
        self._registry = agent_registry
        self._tts = tts_service

    async def run(
        self,
        agent_session: "AgentSession",
        task: str,
        on_text: Callable[[str, str], Awaitable[None]] | None = None,
        delegation_chain: list[str] | None = None,
    ) -> str:
        """Run an agent task via the Agent SDK.

        Returns the agent's result text (empty string on failure).
        TTS and audio delivery are handled by the caller so they don't
        count against the agent timeout budget.
        """
        entry = self._registry.get(agent_session.agent_name)
        if entry is None:
            raise ValueError(f"Unknown agent: {agent_session.agent_name}")

        mcp_server = AGENT_MCP_SERVERS[agent_session.agent_name]
        server_key = f"{agent_session.agent_name}_tools"

        options = ClaudeAgentOptions(
            model=CLAUDE_MODEL,
            system_prompt=entry.system_prompt,
            mcp_servers={server_key: mcp_server},
            allowed_tools=[f"mcp__{server_key}__*"],
            max_turns=MAX_TOOL_ROUNDS,
            max_budget_usd=MAX_AGENT_BUDGET_USD,
            permission_mode="bypassPermissions",
            resume=agent_session.sdk_session_id,
            setting_sources=["project"],
            cwd=str(BACKEND_DIR),
            agents=entry.subagents if entry.subagents else None,
        )

        agent = agent_session.agent_name
        t0 = time.monotonic()
        logger.info("[%s] starting agent task: %s", agent, task[:200])

        # Register delegation chain so the ask_agent tool closure can read it
        _active_delegation_chains[agent] = delegation_chain or []

        full_text = ""
        gen = query(prompt=task, options=options)
        logger.info("[%s] DIAG: query() returned at T+%.1fs", agent, time.monotonic() - t0)
        try:
            msg_count = 0
            async for message in gen:
                msg_count += 1
                if msg_count == 1:
                    logger.info(
                        "[%s] DIAG: first message at T+%.1fs type=%s",
                        agent, time.monotonic() - t0, type(message).__name__,
                    )
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    agent_session.sdk_session_id = message.data.get(
                        "session_id"
                    )
                    logger.info(
                        "[%s] session initialized: %s",
                        agent,
                        agent_session.sdk_session_id,
                    )

                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, ThinkingBlock):
                            logger.info(
                                "[%s] thinking: %s",
                                agent,
                                block.thinking[:300],
                            )
                        elif isinstance(block, TextBlock):
                            logger.info(
                                "[%s] text: %s",
                                agent,
                                block.text[:300],
                            )
                            if on_text:
                                await on_text(agent, block.text)
                        elif isinstance(block, ToolUseBlock):
                            logger.info(
                                "[%s] tool_call: %s(%s)",
                                agent,
                                block.name,
                                block.input,
                            )

                elif isinstance(message, UserMessage):
                    for block in (
                        message.content
                        if isinstance(message.content, list)
                        else []
                    ):
                        if isinstance(block, ToolResultBlock):
                            snippet = str(block.content)[:200]
                            logger.info(
                                "[%s] tool_result: %s%s",
                                agent,
                                snippet,
                                " (ERROR)" if block.is_error else "",
                            )

                elif isinstance(message, ResultMessage):
                    agent_session.sdk_session_id = message.session_id
                    if message.subtype == "success" and message.result:
                        full_text = message.result
                    logger.info(
                        "[%s] result: subtype=%s turns=%d cost=$%.4f "
                        "result_len=%d text=%s",
                        agent,
                        message.subtype,
                        message.num_turns,
                        message.total_cost_usd or 0,
                        len(full_text),
                        (full_text[:200] + "...") if len(full_text) > 200 else full_text,
                    )
                    # DIAG: Log all text blocks accumulated vs ResultMessage.result
                    if isinstance(message.result, str):
                        logger.info(
                            "[%s] DIAG result detail: result_len=%d, "
                            "result_tail=%.200s",
                            agent,
                            len(message.result),
                            message.result[-200:] if message.result else "(empty)",
                        )
        finally:
            # Ensure subprocess cleanup on timeout / cancellation
            logger.info(
                "[%s] DIAG: generator loop exited at T+%.1fs, msgs=%d, closing...",
                agent, time.monotonic() - t0, msg_count,
            )
            close_start = time.monotonic()
            await gen.aclose()
            logger.info(
                "[%s] DIAG: gen.aclose() took %.1fs",
                agent, time.monotonic() - close_start,
            )
            # Clean up delegation chain registration
            _active_delegation_chains.pop(agent, None)

        # Update conversation history for Gemini context
        agent_session.conversation_history.append({"role": "user", "content": task})
        agent_session.conversation_history.append(
            {"role": "assistant", "content": full_text}
        )

        return full_text
