"""REST endpoint: GET /api/v1/health."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from app.session_manager import SessionManager

router = APIRouter(tags=["health"])

_session_manager: "SessionManager" = None  # type: ignore[assignment]


def init_health_router(session_manager: "SessionManager") -> None:
    global _session_manager
    _session_manager = session_manager


@router.get("/health")
def health():
    """Root health check — returns 200 OK for load-balancer probes."""
    return {"status": "ok"}


@router.get("/api/v1/health")
def health_detailed():
    """Detailed service health check."""
    return {
        "status": "ok",
        "version": "0.1.0",
        "active_sessions": _session_manager.count(),
    }
