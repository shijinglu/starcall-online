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
    if source_agent == target_agent:
        raise DelegationError(f"Blocked self-delegation: {source_agent} → {target_agent}")
    if target_agent in delegation_chain:
        raise DelegationError(
            f"Blocked cycle: {' → '.join(delegation_chain)} → {source_agent} → {target_agent}"
        )
    if len(delegation_chain) >= max_depth:
        raise DelegationError(
            f"Blocked depth exceeded ({len(delegation_chain)}/{max_depth}): "
            f"{' → '.join(delegation_chain)} → {source_agent} → {target_agent}"
        )


async def ask_agent_handler(args: dict) -> dict:
    from app.a2a.client import send_task_to_agent

    target = args["agent_name"]
    question = args["question"]
    chain = args.get("delegation_chain", [])
    source = args.get("source_agent", "unknown")

    try:
        check_delegation_allowed(source, target, chain)
    except DelegationError as exc:
        logger.warning("Delegation blocked: %s", exc)
        return {"content": [{"type": "text", "text": json.dumps({"error": "delegation_blocked", "reason": str(exc)})}]}

    new_chain = chain + [source]
    logger.info("A2A delegation: %s → %s, chain=%s, question=%.100s", source, target, new_chain, question)

    try:
        result = await send_task_to_agent(
            agent_name=target,
            task_text=question,
            metadata={"agent_name": target, "delegation_chain": new_chain},
        )
        return {"content": [{"type": "text", "text": json.dumps({"agent": target, "response": result})}]}
    except Exception as exc:
        logger.exception("A2A delegation to %s failed", target)
        return {"content": [{"type": "text", "text": json.dumps({"error": "delegation_failed", "reason": str(exc)})}]}
