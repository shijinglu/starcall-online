"""WebSocket endpoint and binary/JSON frame routing."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.codec import (
    AGENT_SPEAKER_IDS,
    MsgType,
    SpeakerId,
    decode_frame,
    encode_frame,
)

if TYPE_CHECKING:
    from app.agent_task_manager import AgentTaskManager
    from app.gemini_proxy import GeminiLiveProxy
    from app.models import ConversationSession
    from app.session_manager import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()

# These are set during app startup (see main.py)
_session_manager: "SessionManager" = None  # type: ignore[assignment]
_gemini_proxy: "GeminiLiveProxy" = None  # type: ignore[assignment]
_agent_task_manager: "AgentTaskManager" = None  # type: ignore[assignment]


def init_ws_handler(
    session_manager: "SessionManager",
    gemini_proxy: "GeminiLiveProxy",
    agent_task_manager: "AgentTaskManager",
) -> None:
    """Wire collaborators into the module-level references."""
    global _session_manager, _gemini_proxy, _agent_task_manager
    _session_manager = session_manager
    _gemini_proxy = gemini_proxy
    _agent_task_manager = agent_task_manager


# ------------------------------------------------------------------
# Outbound helpers
# ------------------------------------------------------------------


async def send_audio_response(
    session: "ConversationSession", pcm: bytes, frame_seq: int
) -> None:
    """Send a moderator TTS binary frame to the iOS client."""
    frame = encode_frame(
        MsgType.AUDIO_RESPONSE, SpeakerId.MODERATOR, session.gen_id, frame_seq, pcm
    )
    if session.ws_connection is not None:
        try:
            await session.ws_connection.send_bytes(frame)
        except Exception:
            logger.debug("Failed to send audio response (WS closed?)")


async def send_agent_audio(
    session: "ConversationSession",
    agent_name: str,
    pcm: bytes,
    frame_seq: int,
) -> None:
    """Send a deep-agent TTS binary frame to the iOS client."""
    sid = AGENT_SPEAKER_IDS.get(agent_name, 0)
    frame = encode_frame(MsgType.AGENT_AUDIO, sid, session.gen_id, frame_seq, pcm)
    if session.ws_connection is not None:
        try:
            await session.ws_connection.send_bytes(frame)
        except Exception:
            logger.debug("Failed to send agent audio (WS closed?)")


async def send_json_msg(
    session: "ConversationSession", payload: dict[str, Any]
) -> None:
    """Send a JSON text frame to the iOS client."""
    if session.ws_connection is not None:
        try:
            await session.ws_connection.send_json(payload)
        except Exception:
            logger.debug("Failed to send JSON (WS closed?)")


async def send_error(session: "ConversationSession", code: str, message: str) -> None:
    await send_json_msg(session, {"type": "error", "code": code, "message": message})


# ------------------------------------------------------------------
# WebSocket endpoint
# ------------------------------------------------------------------


@router.websocket("/api/v1/conversation/live")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    """Main bidirectional WebSocket endpoint for the conversation."""
    # Validate auth token before accepting the upgrade
    session = _session_manager.validate_token(token)
    if session is None:
        await ws.close(code=4001, reason="Unauthorized")
        return

    # Consume token (single-use)
    _session_manager.consume_token(session)

    await ws.accept()
    session.ws_connection = ws
    logger.info("WS connected for session %s", session.session_id)

    # Start Gemini Live session
    try:
        await _gemini_proxy.start_session(session)
    except Exception as exc:
        logger.error("Failed to start Gemini session: %s", exc, exc_info=True)
        await send_error(session, "INTERNAL", f"Failed to start moderator: {exc}")
        await ws.close()
        return

    try:
        while True:
            message = await ws.receive()
            msg_type = message.get("type")

            if msg_type == "websocket.disconnect":
                break
            elif msg_type == "websocket.receive":
                if "bytes" in message and message["bytes"]:
                    await _handle_binary_frame(message["bytes"], session)
                elif "text" in message and message["text"]:
                    await _handle_json_frame(message["text"], session)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(
            "WS error for session %s: %s", session.session_id, exc, exc_info=True
        )
    finally:
        logger.info("WS disconnected for session %s", session.session_id)
        await _gemini_proxy.close_session(session)
        await _session_manager.terminate_session(session.session_id)


# ------------------------------------------------------------------
# Frame routing
# ------------------------------------------------------------------


async def _handle_binary_frame(data: bytes, session: "ConversationSession") -> None:
    """Route an incoming binary frame."""
    try:
        msg_type, speaker_id, gen_id, frame_seq, pcm = decode_frame(data)
    except ValueError as exc:
        await send_error(session, "INTERNAL", str(exc))
        return

    if msg_type == MsgType.AUDIO_CHUNK:
        _session_manager.touch(session.session_id)
        await _gemini_proxy.send_audio_chunk(session, pcm)
    else:
        await send_error(
            session, "INTERNAL", f"Unexpected client msg_type: {msg_type:#04x}"
        )


async def _handle_json_frame(raw: str, session: "ConversationSession") -> None:
    """Route an incoming JSON text frame."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        await send_error(session, "INTERNAL", f"Invalid JSON: {exc}")
        return

    msg_type = msg.get("type")

    if msg_type == "control":
        await _handle_control(msg, session)
    elif msg_type == "interrupt":
        await _handle_interrupt(msg, session)
    elif msg_type == "agent_followup":
        await _handle_agent_followup(msg, session)
    else:
        await send_error(session, "INTERNAL", f"Unknown message type: {msg_type}")


async def _handle_control(msg: dict, session: "ConversationSession") -> None:
    """Handle control messages (start, stop, pause)."""
    action = msg.get("action")
    logger.info("Control action=%s for session %s", action, session.session_id)

    if action == "stop":
        await _gemini_proxy.close_session(session)
        await _session_manager.terminate_session(session.session_id)
    # "start" and "pause" are handled implicitly (start = WS open, pause = stop sending audio)


async def _handle_interrupt(msg: dict, session: "ConversationSession") -> None:
    """Handle barge-in interrupt from the client."""
    mode = msg.get("mode", "cancel_all")
    new_gen = _session_manager.increment_gen_id(session.session_id)
    await _agent_task_manager.handle_interrupt(session, mode)
    await send_json_msg(session, {"type": "interruption", "gen_id": new_gen})


async def _handle_agent_followup(msg: dict, session: "ConversationSession") -> None:
    """Handle agent_followup: route follow-up text to an existing agent session (Fix 8)."""
    agent_session_id = msg.get("agent_session_id")
    text = msg.get("text", "")

    agent_session = session.agent_sessions.get(agent_session_id)  # type: ignore[arg-type]
    if agent_session is None:
        await send_json_msg(
            session,
            {
                "type": "error",
                "code": "SESSION_NOT_FOUND",
                "message": "No such agent session",
            },
        )
        return

    if agent_session.status == "active":
        await send_json_msg(
            session,
            {
                "type": "error",
                "code": "AGENT_BUSY",
                "message": "Agent is still working",
            },
        )
        return

    await _agent_task_manager.resume(session, agent_session, text)
