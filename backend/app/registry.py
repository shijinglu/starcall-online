"""AgentRegistry -- loads agent configs from folder or falls back to hardcoded.

At import time, scans ``AGENTS_DIR`` for subfolders containing ``agent.yaml``
and ``prompt.txt``.  If none are found (or the directory doesn't exist), falls
back to the original hardcoded registry so existing deployments keep working.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import AGENTS_DIR, PROMPTS_DIR
from app.models import AgentRegistryEntry

logger = logging.getLogger(__name__)

_REQUIRED_YAML_KEYS = {"name", "description", "voice_id", "speaker_id", "tool_set"}


# ---------- Folder-based discovery ----------


def _load_agent_from_folder(folder: Path) -> AgentRegistryEntry:
    """Load a single agent from *folder*/agent.yaml + prompt.txt."""
    yaml_path = folder / "agent.yaml"
    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    missing = _REQUIRED_YAML_KEYS - set(cfg)
    if missing:
        raise ValueError(f"{yaml_path}: missing keys {missing}")

    prompt_path = folder / "prompt.txt"
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("No prompt.txt in %s — using empty prompt", folder)
        system_prompt = ""

    return AgentRegistryEntry(
        name=cfg["name"],
        description=cfg["description"],
        voice_id=cfg["voice_id"],
        speaker_id=int(cfg["speaker_id"]),
        system_prompt=system_prompt,
        tool_set=list(cfg["tool_set"]),
        agent_dir=folder,
    )


def _discover_agents(agents_dir: Path) -> dict[str, AgentRegistryEntry]:
    """Scan *agents_dir* subfolders and return discovered agents."""
    agents: dict[str, AgentRegistryEntry] = {}
    for child in sorted(agents_dir.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "agent.yaml").exists():
            logger.debug("Skipping %s — no agent.yaml", child.name)
            continue
        try:
            entry = _load_agent_from_folder(child)
            agents[entry.name] = entry
            logger.info("Discovered agent '%s' from %s", entry.name, child)
        except Exception:
            logger.warning("Failed to load agent from %s", child, exc_info=True)
    return agents


# ---------- Hardcoded fallback ----------


def _load_prompt(filename: str) -> str:
    """Load a prompt text file from the prompts/ directory."""
    path = PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s — using empty string", path)
        return ""


def _hardcoded_registry() -> dict[str, AgentRegistryEntry]:
    """Original hardcoded registry (backward compat)."""
    return {
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


# ---------- Build registry ----------


def _build_registry() -> dict[str, AgentRegistryEntry]:
    """Build registry from folder discovery, falling back to hardcoded."""
    if AGENTS_DIR.is_dir():
        discovered = _discover_agents(AGENTS_DIR)
        if discovered:
            logger.info("Loaded %d agent(s) from %s", len(discovered), AGENTS_DIR)
            return discovered
        logger.warning(
            "AGENTS_DIR %s exists but no valid agents found; using hardcoded fallback",
            AGENTS_DIR,
        )
    return _hardcoded_registry()


AGENT_REGISTRY: dict[str, AgentRegistryEntry] = _build_registry()


# ---------- Registry wrapper ----------


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


def build_agent_roster_block(
    registry: dict[str, AgentRegistryEntry] | None = None,
) -> str:
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
