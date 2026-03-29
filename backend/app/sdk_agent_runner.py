"""SDKAgentRunner -- Claude Agent SDK wrapper with whole-message TTS."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    query,
)

from app.config import BACKEND_DIR, CLAUDE_MODEL, MAX_AGENT_BUDGET_USD, MAX_TOOL_ROUNDS
from app.tools.mcp_servers import AGENT_MCP_SERVERS

if TYPE_CHECKING:
    from app.models import AgentSession, ConversationSession
    from app.registry import AgentRegistry
    from app.tts_service import TTSService

logger = logging.getLogger(__name__)


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
        conv_session: "ConversationSession",
        deliver_fn: Any = None,
    ) -> None:
        """Run an agent task via the Agent SDK and deliver whole-message TTS.

        *deliver_fn* is an async callable ``(conv_session, agent_session, pcm) -> None``
        used to deliver or queue audio.  If None, audio is not sent (useful for testing).
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

        full_text = ""
        gen = query(prompt=task, options=options)
        try:
            async for message in gen:
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    agent_session.sdk_session_id = message.data.get(
                        "session_id"
                    )
                elif isinstance(message, ResultMessage):
                    agent_session.sdk_session_id = message.session_id
                    if message.subtype == "success" and message.result:
                        full_text = message.result
        finally:
            # Ensure subprocess cleanup on timeout / cancellation
            await gen.aclose()

        # Whole-message TTS
        if deliver_fn and full_text:
            pcm = await self._tts.synthesize(full_text, agent_session.agent_name)
            if pcm:
                await deliver_fn(conv_session, agent_session, pcm)

        # Update conversation history for Gemini context
        agent_session.conversation_history.append({"role": "user", "content": task})
        agent_session.conversation_history.append(
            {"role": "assistant", "content": full_text}
        )
