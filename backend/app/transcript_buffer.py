"""TranscriptBuffer -- accumulates streaming transcript fragments per session.

Extracted from GeminiLiveProxy to give transcript buffering a single owner
(Single Responsibility Principle).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine

if TYPE_CHECKING:
    from app.models import ConversationSession

logger = logging.getLogger(__name__)

# Type alias for the async send_json callback
SendJsonFn = Callable[["ConversationSession", dict[str, Any]], Coroutine[Any, Any, None]]


class TranscriptBuffer:
    """Accumulates word-level transcript fragments and emits partials/finals."""

    def __init__(self, send_json: SendJsonFn | None = None) -> None:
        self.send_json = send_json
        self._user_buf: dict[str, str] = {}
        self._moderator_buf: dict[str, str] = {}

    # -- Public helpers used by tests --

    @property
    def _user_transcript_buf(self) -> dict[str, str]:
        """Expose user buffer for backward-compatible test access."""
        return self._user_buf

    @property
    def _moderator_transcript_buf(self) -> dict[str, str]:
        """Expose moderator buffer for backward-compatible test access."""
        return self._moderator_buf

    # -- Accumulation --

    async def accumulate_user(
        self, session: "ConversationSession", text: str
    ) -> None:
        """Append user transcript fragment and emit a partial."""
        sid = session.session_id
        self._user_buf[sid] = self._user_buf.get(sid, "") + text
        if self.send_json:
            await self.send_json(
                session,
                {
                    "type": "transcript",
                    "speaker": "user",
                    "text": self._user_buf[sid],
                    "is_final": False,
                },
            )

    async def accumulate_moderator(
        self, session: "ConversationSession", text: str
    ) -> None:
        """Append moderator transcript fragment and emit a partial."""
        sid = session.session_id
        self._moderator_buf[sid] = self._moderator_buf.get(sid, "") + text
        if self.send_json:
            await self.send_json(
                session,
                {
                    "type": "transcript",
                    "speaker": "moderator",
                    "text": self._moderator_buf[sid],
                    "is_final": False,
                },
            )

    # -- Flush --

    async def flush(self, session: "ConversationSession") -> None:
        """Send is_final=True for any buffered transcripts and clear them."""
        sid = session.session_id
        user_text = self._user_buf.pop(sid, "")
        mod_text = self._moderator_buf.pop(sid, "")
        if user_text and self.send_json:
            await self.send_json(
                session,
                {
                    "type": "transcript",
                    "speaker": "user",
                    "text": user_text,
                    "is_final": True,
                },
            )
        if mod_text and self.send_json:
            await self.send_json(
                session,
                {
                    "type": "transcript",
                    "speaker": "moderator",
                    "text": mod_text,
                    "is_final": True,
                },
            )
        # Persist completed turn to session history for agent context
        if user_text:
            session.transcript_history.append({"speaker": "user", "text": user_text})
        if mod_text:
            session.transcript_history.append({"speaker": "moderator", "text": mod_text})

    def get_user(self, sid: str) -> str:
        """Return current user buffer content (for logging)."""
        return self._user_buf.get(sid, "")

    def get_moderator(self, sid: str) -> str:
        """Return current moderator buffer content (for logging)."""
        return self._moderator_buf.get(sid, "")
