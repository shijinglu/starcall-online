"""Dynamic ask_agent MCP tool — injected into every deep agent at runtime.

Builds a per-agent MCP server containing a single ``ask_agent`` tool whose
``agent_name`` enum is derived from the live registry (excluding the calling
agent itself).  This keeps inter-agent delegation fully configurable without
hardcoding agent names.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.a2a.delegation import ask_agent_handler

if TYPE_CHECKING:
    from claude_agent_sdk import SdkMcpServer

logger = logging.getLogger(__name__)


def build_ask_agent_server(
    source_agent: str,
    peer_names: list[str],
) -> "SdkMcpServer":
    """Return an MCP server with a single ``ask_agent`` tool for *source_agent*.

    The tool's ``agent_name`` parameter is constrained to *peer_names*
    (all registered agents minus the caller).
    """

    @tool(
        "ask_agent",
        "Ask another agent a question. Use when you need expertise "
        "outside your domain. Do NOT ask yourself.",
        {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "enum": peer_names,
                    "description": "Name of the agent to consult",
                },
                "question": {
                    "type": "string",
                    "description": "A clear, specific question or task for the other agent",
                },
            },
            "required": ["agent_name", "question"],
        },
    )
    async def ask_agent(args: dict[str, Any]) -> dict[str, Any]:
        from app.sdk_agent_runner import _active_delegation_chains

        args["source_agent"] = source_agent
        args["delegation_chain"] = _active_delegation_chains.get(source_agent, [])
        return await ask_agent_handler(args)

    return create_sdk_mcp_server("ask_agent", tools=[ask_agent])
