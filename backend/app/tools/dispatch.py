"""Central tool dispatcher -- routes tool names to stub functions."""

from __future__ import annotations

import logging

from app.tools import ellen_tools, eva_tools, ming_tools, shijing_tools

logger = logging.getLogger(__name__)

TOOL_MAP: dict[str, object] = {
    # Ellen
    "calendar_read": ellen_tools.calendar_read,
    "email_send": ellen_tools.email_send,
    "task_list": ellen_tools.task_list,
    # Shijing
    "user_profile_read": shijing_tools.user_profile_read,
    "user_journey_read": shijing_tools.user_journey_read,
    "risk_score_read": shijing_tools.risk_score_read,
    # Eva
    "transaction_read": eva_tools.transaction_read,
    "bank_data_read": eva_tools.bank_data_read,
    "chargeback_read": eva_tools.chargeback_read,
    # Ming
    "id_check": ming_tools.id_check,
    "async_risk_check": ming_tools.async_risk_check,
    "fraud_signal_read": ming_tools.fraud_signal_read,
}


async def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    """Invoke the stub tool by name and return its result dict."""
    fn = TOOL_MAP.get(tool_name)
    if fn is None:
        logger.warning("Unknown tool requested: %s", tool_name)
        return {"error": f"Unknown tool: {tool_name}"}
    return await fn(**tool_input)  # type: ignore[operator]
