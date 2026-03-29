"""SessionManager -- in-memory conversation session lifecycle and TTL enforcement."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.config import (
    AUTH_TOKEN_TTL_SECONDS,
    CLEANUP_INTERVAL_SECONDS,
    SESSION_TTL_SECONDS,
)
from app.models import ConversationSession

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages the in-memory dict of active conversation sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}
        # Token -> session_id for fast lookup
        self._token_index: dict[str, str] = {}
        self._cleanup_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_cleanup(self) -> None:
        """Start the background cleanup loop (call once at app startup)."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def create_session(self) -> ConversationSession:
        """Allocate a new session, generate auth_token, store in dict."""
        session = ConversationSession(
            token_expires_at=time.time() + AUTH_TOKEN_TTL_SECONDS,
            session_ttl=SESSION_TTL_SECONDS,
        )
        self._sessions[session.session_id] = session
        self._token_index[session.auth_token] = session.session_id
        logger.info(
            "Created session %s (token expires in %ss)",
            session.session_id,
            AUTH_TOKEN_TTL_SECONDS,
        )
        return session

    def validate_token(self, token: str) -> Optional[ConversationSession]:
        """Check token exists, not expired, not consumed.  Return session or None."""
        session_id = self._token_index.get(token)
        if session_id is None:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.token_consumed:
            return None
        if session.token_expires_at < time.time():
            return None
        return session

    def consume_token(self, session: ConversationSession) -> None:
        """Mark token as consumed.  Idempotent."""
        session.token_consumed = True

    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        return self._sessions.get(session_id)

    async def terminate_session(self, session_id: str) -> bool:
        """Cancel all agent tasks, close WS, remove from dict.  Return False if not found."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False

        # Remove token index entry
        self._token_index.pop(session.auth_token, None)

        # Cancel meeting sender task
        if session.meeting_sender_task and not session.meeting_sender_task.done():
            session.meeting_sender_task.cancel()

        # Cancel all agent tasks
        for agent_session in session.agent_sessions.values():
            if agent_session.claude_task and not agent_session.claude_task.done():
                agent_session.claude_task.cancel()

        # Close WebSocket
        if session.ws_connection is not None:
            try:
                await session.ws_connection.close()
            except Exception:
                logger.debug(
                    "WS close failed for session %s (already closed?)", session_id
                )

        # Signal audio queue to stop Gemini send loop
        try:
            session.audio_queue.put_nowait(None)
        except Exception:
            pass

        logger.info("Terminated session %s", session_id)
        return True

    def touch(self, session_id: str) -> None:
        """Update last_activity to extend the TTL window."""
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_activity = time.time()

    def increment_gen_id(self, session_id: str) -> int:
        """Atomically increment gen_id (wrapping at 255).  Return new value."""
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        session.gen_id = (session.gen_id + 1) & 0xFF
        return session.gen_id

    def count(self) -> int:
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Background cleanup
    # ------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Scan sessions every CLEANUP_INTERVAL_SECONDS; terminate expired ones."""
        while True:
            try:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                break

            now = time.time()
            expired_ids = [
                sid
                for sid, s in self._sessions.items()
                if (now - s.last_activity) > s.session_ttl
            ]
            for sid in expired_ids:
                logger.info("Cleaning up expired session %s", sid)
                await self.terminate_session(sid)
