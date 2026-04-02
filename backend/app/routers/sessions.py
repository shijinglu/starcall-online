"""REST endpoints for session lifecycle: POST/DELETE /api/v1/sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from app.session_manager import SessionManager


class CreateSessionRequest(BaseModel):
    listener_mode: bool = False

router = APIRouter(prefix="/api/v1", tags=["sessions"])

# Set at startup from main.py
_session_manager: "SessionManager" = None  # type: ignore[assignment]


def init_sessions_router(session_manager: "SessionManager") -> None:
    global _session_manager
    _session_manager = session_manager


@router.post("/sessions")
async def create_session(request: CreateSessionRequest | None = None):
    """Create a new conversation session.

    Returns session_id, auth_token, and token expiration.
    """
    listener_mode = request.listener_mode if request else False
    session = await _session_manager.create_session(listener_mode=listener_mode)
    expires_at = datetime.fromtimestamp(session.token_expires_at, tz=timezone.utc)
    return {
        "session_id": session.session_id,
        "auth_token": session.auth_token,
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Terminate a session, cancel all agent tasks, invalidate token."""
    ok = await _session_manager.terminate_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "terminated"}
