"""WebSocket endpoint and binary/JSON frame routing."""

from __future__ import annotations

import json
import logging
import time
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
from app.output_controller import OutputController

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
    """Enqueue moderator audio into the OutputController."""
    if session.output_controller is not None:
        session.output_controller.enqueue_moderator_audio(pcm, session.gen_id)


async def send_agent_audio(
    session: "ConversationSession",
    agent_name: str,
    pcm: bytes,
    frame_seq: int,
) -> None:
    """Enqueue agent audio into the OutputController."""
    if session.output_controller is not None:
        session.output_controller.enqueue_agent_audio(agent_name, pcm, session.gen_id)


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

    # Initialize the output controller for this session
    oc = OutputController()
    session.output_controller = oc
    oc.start(
        ws=ws,
        tts_fn=_agent_task_manager._tts.synthesize,
        gen_id_fn=lambda: session.gen_id,
    )

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
        sid = session.session_id
        total_frames = _ws_frame_counters.pop(sid, 0)
        _ws_frame_last_log.pop(sid, None)
        logger.info(
            "WS disconnected for session %s "
            "(total iOS audio frames received=%d)",
            sid,
            total_frames,
        )
        if session.output_controller:
            await session.output_controller.stop()
        await _gemini_proxy.close_session(session)
        await _session_manager.terminate_session(sid)


# ------------------------------------------------------------------
# Frame routing
# ------------------------------------------------------------------


# DIAG: per-session frame counters for WS audio health monitoring
_ws_frame_counters: dict[str, int] = {}
_ws_frame_last_log: dict[str, float] = {}
_WS_DIAG_INTERVAL = 10.0  # log every 10 seconds


async def _handle_binary_frame(data: bytes, session: "ConversationSession") -> None:
    """Route an incoming binary frame."""
    try:
        msg_type, speaker_id, gen_id, frame_seq, pcm = decode_frame(data)
    except ValueError as exc:
        await send_error(session, "INTERNAL", str(exc))
        return

    if msg_type == MsgType.AUDIO_CHUNK:
        _session_manager.touch(session.session_id)

        # DIAG: periodic log of iOS audio frame reception
        sid = session.session_id
        _ws_frame_counters[sid] = _ws_frame_counters.get(sid, 0) + 1
        now = time.monotonic()
        last = _ws_frame_last_log.get(sid, 0.0)
        if now - last >= _WS_DIAG_INTERVAL:
            _ws_frame_last_log[sid] = now
            logger.info(
                "[session=%s] DIAG WS-RECV: iOS audio frames received=%d, "
                "audio_queue_size=%d, gemini_session_alive=%s",
                sid,
                _ws_frame_counters[sid],
                session.audio_queue.qsize(),
                session.gemini_session is not None,
            )

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
    logger.info(
        "DIAG-ECHO: [session=%s] Client sent interrupt mode=%s old_gen=%d",
        session.session_id,
        mode,
        session.gen_id,
    )
    new_gen = _session_manager.increment_gen_id(session.session_id)
    flushed_responses = []
    if session.output_controller:
        flushed_responses = session.output_controller.flush(gen_id=new_gen)
    # Log flushed text responses to transcript (never spoken, but context preserved)
    for resp in flushed_responses:
        session.transcript_history.append({
            "speaker": resp.agent_name,
            "text": resp.spoken_text,
            "flushed": True,
        })
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
