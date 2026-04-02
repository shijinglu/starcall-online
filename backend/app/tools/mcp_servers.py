"""MCP tool servers — wraps agent tool stubs for the Claude Agent SDK."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.tools import ellen_tools, eva_tools, ming_tools, shijing_tools
from app.a2a.delegation import ask_agent_handler

# ---------------------------------------------------------------------------
# Ellen tools
# ---------------------------------------------------------------------------


@tool("calendar_read", "Read calendar events for a given date.", {"date": str})
async def calendar_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await ellen_tools.calendar_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool(
    "email_send",
    "Send an email.",
    {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    },
)
async def email_send(args: dict[str, Any]) -> dict[str, Any]:
    result = await ellen_tools.email_send(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool("task_list", "List the user's tasks / to-do items.", {})
async def task_list(args: dict[str, Any]) -> dict[str, Any]:
    result = await ellen_tools.task_list(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


# ---------------------------------------------------------------------------
# Shijing tools
# ---------------------------------------------------------------------------


@tool("user_profile_read", "Read the user's profile.", {"user_id": str})
async def user_profile_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await shijing_tools.user_profile_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool(
    "user_journey_read",
    "Read user journey / activity analytics.",
    {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "days": {"type": "integer", "description": "Lookback window in days"},
        },
        "required": [],
    },
)
async def user_journey_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await shijing_tools.user_journey_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool("risk_score_read", "Read the user's risk score.", {"user_id": str})
async def risk_score_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await shijing_tools.risk_score_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


# ---------------------------------------------------------------------------
# Eva tools
# ---------------------------------------------------------------------------


@tool(
    "transaction_read",
    "Read transaction history.",
    {
        "type": "object",
        "properties": {
            "user_id": {"type": "string"},
            "days": {"type": "integer"},
        },
        "required": [],
    },
)
async def transaction_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await eva_tools.transaction_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool("bank_data_read", "Read bank account data.", {"user_id": str})
async def bank_data_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await eva_tools.bank_data_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool("chargeback_read", "Read chargeback and dispute data.", {"user_id": str})
async def chargeback_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await eva_tools.chargeback_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


# ---------------------------------------------------------------------------
# Ming tools
# ---------------------------------------------------------------------------


@tool("id_check", "Perform an identity verification check.", {"user_id": str})
async def id_check(args: dict[str, Any]) -> dict[str, Any]:
    result = await ming_tools.id_check(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool("async_risk_check", "Run an asynchronous risk check.", {"user_id": str})
async def async_risk_check(args: dict[str, Any]) -> dict[str, Any]:
    result = await ming_tools.async_risk_check(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


@tool("fraud_signal_read", "Read fraud detection signals.", {"user_id": str})
async def fraud_signal_read(args: dict[str, Any]) -> dict[str, Any]:
    result = await ming_tools.fraud_signal_read(**args)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


# ---------------------------------------------------------------------------
# Inter-agent delegation tool (per-agent variant)
# ---------------------------------------------------------------------------


def _make_ask_agent_for(source_name: str):
    """Create an ask_agent tool that knows its source agent name."""
    peer_names = [n for n in ["ellen", "eva", "ming", "shijing"] if n != source_name]

    @tool(
        "ask_agent",
        "Ask another agent a question. Use this when you need expertise "
        "outside your domain. Do NOT ask yourself.",
        {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "enum": peer_names,
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
        from app.sdk_agent_runner import _active_delegation_chains

        args["source_agent"] = source_name
        args["delegation_chain"] = _active_delegation_chains.get(source_name, [])
        return await ask_agent_handler(args)

    return ask_agent


_ask_ellen = _make_ask_agent_for("ellen")
_ask_eva = _make_ask_agent_for("eva")
_ask_ming = _make_ask_agent_for("ming")
_ask_shijing = _make_ask_agent_for("shijing")


# ---------------------------------------------------------------------------
# Per-agent MCP servers
# ---------------------------------------------------------------------------

AGENT_MCP_SERVERS = {
    "ellen": create_sdk_mcp_server(
        "ellen_tools", tools=[calendar_read, email_send, task_list, _ask_ellen]
    ),
    "shijing": create_sdk_mcp_server(
        "shijing_tools", tools=[user_profile_read, user_journey_read, risk_score_read, _ask_shijing]
    ),
    "eva": create_sdk_mcp_server(
        "eva_tools", tools=[transaction_read, bank_data_read, chargeback_read, _ask_eva]
    ),
    "ming": create_sdk_mcp_server(
        "ming_tools", tools=[id_check, async_risk_check, fraud_signal_read, _ask_ming]
    ),
}
