"""Gemini tool declarations and moderator system prompt.

Extracted from gemini_proxy.py to isolate tool schema definitions and
prompt-building logic (Single Responsibility Principle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.registry import build_agent_roster_block

if TYPE_CHECKING:
    from app.registry import AgentRegistry

# ---------------------------------------------------------------------------
# Gemini tool declarations (injected into the Gemini Live session)
# ---------------------------------------------------------------------------

DISPATCH_AGENT_TOOL = {
    "name": "dispatch_agent",
    "description": "Delegate a task to a named deep-thinking agent. Use for first contact.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": ["ellen", "shijing", "eva", "ming"],
                "description": "Agent to dispatch",
            },
            "task": {
                "type": "string",
                "description": "Full task description for the agent",
            },
        },
        "required": ["name", "task"],
    },
}

RESUME_AGENT_TOOL = {
    "name": "resume_agent",
    "description": "Continue a prior conversation with an idle agent session.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent_session_id": {
                "type": "string",
                "description": "UUID returned from a prior dispatch_agent call",
            },
            "follow_up": {
                "type": "string",
                "description": "Follow-up question or instruction for the agent",
            },
        },
        "required": ["agent_session_id", "follow_up"],
    },
}

# ---------------------------------------------------------------------------
# Moderator system prompt
# ---------------------------------------------------------------------------

MODERATOR_PERSONA = """\
You are a fast AI moderator for a voice-first assistant system.
Your role:
- Answer simple queries directly and quickly.
- For complex analytical tasks, delegate to the appropriate deep-thinking agent \
by CALLING the dispatch_agent tool. You MUST use the function-calling API — \
never say or output the tool name or arguments as text.
- For follow-up questions to an existing agent session, CALL the resume_agent tool.
- Acknowledge delegations immediately with a brief, natural phrase \
("Ellen is on it!", "Let me check with Ming.").
- Keep your own responses concise — you are a facilitator, not the expert.
- Never fabricate agent capabilities. Only dispatch agents listed in the roster below.

IMPORTANT: When delegating, invoke the tool silently. \
Do NOT speak or output function names, argument syntax, or JSON. \
Just say a short acknowledgement and call the tool.

Audio format: your TTS output and the user's voice are both 16 kHz LINEAR16 PCM.
"""


def build_system_prompt(agent_registry: "AgentRegistry") -> str:
    """Assemble the full Gemini system prompt (persona + roster)."""
    roster = build_agent_roster_block(agent_registry.entries)
    return MODERATOR_PERSONA + "\n" + roster
