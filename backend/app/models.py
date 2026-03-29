"""Internal data models for the AI Conversation backend."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

AgentStatus = Literal["active", "idle", "cancelled", "timeout"]


@dataclass
class AgentSession:
    """State for a single deep-agent invocation within a conversation."""

    agent_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = ""
    conversation_history: list[dict[str, Any]] = field(default_factory=list)
    status: AgentStatus = "active"
    claude_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
    parent_session_id: str = ""
    created_at: float = field(default_factory=time.time)
    sdk_session_id: str | None = None

    # Meeting-mode fields (Fix 3 / Fix 7)
    audio_buffer: list[bytes] = field(default_factory=list)
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    current_frame_seq: int = 0

    def next_frame_seq(self) -> int:
        seq = self.current_frame_seq
        self.current_frame_seq = (self.current_frame_seq + 1) & 0xFF
        return seq


@dataclass
class ConversationSession:
    """Top-level session state binding iOS client, Gemini, and agent tasks."""

    session_id: str = field(default_factory=lambda: "s-" + str(uuid.uuid4()))
    auth_token: str = field(default_factory=lambda: str(uuid.uuid4()))
    token_expires_at: float = field(default_factory=lambda: time.time() + 300)  # 5 min
    token_consumed: bool = False

    ws_connection: Any = None  # WebSocket connection object
    gemini_session: Any = None  # Gemini Live session handle
    gemini_ctx: Any = None  # Async context manager for Gemini Live connection

    agent_sessions: dict[str, AgentSession] = field(default_factory=dict)
    gen_id: int = 0

    # Audio queue feeding the Gemini audio send loop
    audio_queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # type: ignore[type-arg]

    # Meeting mode
    meeting_queue: list[str] = field(default_factory=list)  # agent_session_ids (Fix 7)
    meeting_sender_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    last_activity: float = field(default_factory=time.time)
    session_ttl: float = 7200.0  # 2 hours

    # Frame sequence for moderator audio
    _moderator_frame_seq: int = 0

    def next_frame_seq(self) -> int:
        """Return the next moderator-audio frame sequence number."""
        seq = self._moderator_frame_seq
        self._moderator_frame_seq = (self._moderator_frame_seq + 1) & 0xFF
        return seq


@dataclass
class AgentRegistryEntry:
    """Static agent descriptor loaded at startup."""

    name: str
    description: str
    voice_id: str
    speaker_id: int
    system_prompt: str
    tool_set: list[str]
    subagents: dict = field(default_factory=dict)
