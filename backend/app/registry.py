"""AgentRegistry -- static in-memory map of agent name -> AgentRegistryEntry.

Loaded once at import time.  The Gemini system prompt roster block is
built dynamically from this registry via ``build_agent_roster_block()``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import PROMPTS_DIR
from app.models import AgentRegistryEntry

logger = logging.getLogger(__name__)


def _load_prompt(filename: str) -> str:
    """Load a prompt text file from the prompts/ directory."""
    path = PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s — using empty string", path)
        return ""


# ---------- Registry ----------

AGENT_REGISTRY: dict[str, AgentRegistryEntry] = {
    "ellen": AgentRegistryEntry(
        name="ellen",
        description="Personal assistant — calendar, email, tasks",
        voice_id="en-US-Journey-F",
        speaker_id=1,
        system_prompt=_load_prompt("ellen.txt"),
        tool_set=["calendar_read", "email_send", "task_list"],
    ),
    "shijing": AgentRegistryEntry(
        name="shijing",
        description="User risk analyst — user profile and journey",
        voice_id="en-US-Journey-D",
        speaker_id=2,
        system_prompt=_load_prompt("shijing.txt"),
        tool_set=["user_profile_read", "user_journey_read", "risk_score_read"],
    ),
    "eva": AgentRegistryEntry(
        name="eva",
        description="Financial analyst — transactions and bank data",
        voice_id="en-US-Journey-O",
        speaker_id=3,
        system_prompt=_load_prompt("eva.txt"),
        tool_set=["transaction_read", "bank_data_read", "chargeback_read"],
    ),
    "ming": AgentRegistryEntry(
        name="ming",
        description="Fraud investigator — ID checks and async risk",
        voice_id="en-US-Neural2-D",
        speaker_id=4,
        system_prompt=_load_prompt("ming.txt"),
        tool_set=["id_check", "async_risk_check", "fraud_signal_read"],
    ),
}


class AgentRegistry:
    """Thin wrapper around the static registry dict."""

    def __init__(self, registry: dict[str, AgentRegistryEntry] | None = None) -> None:
        self._registry = registry if registry is not None else AGENT_REGISTRY

    def __contains__(self, name: str) -> bool:
        return name in self._registry

    def get(self, name: str) -> AgentRegistryEntry | None:
        return self._registry.get(name)

    def list_all(self) -> list[dict[str, Any]]:
        """Return a JSON-serialisable list of all agents."""
        return [
            {
                "name": e.name,
                "description": e.description,
                "voice_id": e.voice_id,
                "speaker_id": e.speaker_id,
                "tool_set": e.tool_set,
            }
            for e in self._registry.values()
        ]

    @property
    def entries(self) -> dict[str, AgentRegistryEntry]:
        return self._registry


def build_agent_roster_block(registry: dict[str, AgentRegistryEntry] | None = None) -> str:
    """Build the roster text block injected into the Gemini system prompt."""
    reg = registry if registry is not None else AGENT_REGISTRY
    lines = ["Available agents:"]
    for entry in reg.values():
        lines.append(f"- {entry.name}: {entry.description}, voice: {entry.voice_id}")
    lines += [
        "",
        "Use dispatch_agent(name, task) for first contact.",
        "Use resume_agent(agent_session_id, follow_up) for follow-up turns to an idle agent.",
        "If resume_agent returns agent_busy, inform the user and try again shortly.",
    ]
    return "\n".join(lines)
