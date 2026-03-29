"""FastAPI application factory and startup wiring."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI

from app.agent_task_manager import AgentTaskManager
from app.gemini_proxy import GeminiLiveProxy
from app.registry import AgentRegistry
from app.routers.agents import init_agents_router
from app.routers.agents import router as agents_router
from app.routers.health import init_health_router
from app.routers.health import router as health_router
from app.routers.sessions import init_sessions_router
from app.routers.sessions import router as sessions_router
from app.sdk_agent_runner import SDKAgentRunner
from app.session_manager import SessionManager
from app.tts_service import TTSService
from app.ws.handler import (
    init_ws_handler,
    send_agent_audio,
    send_audio_response,
    send_json_msg,
)
from app.ws.handler import (
    router as ws_router,
)

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
        ),
    ],
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build and wire the FastAPI application."""
    application = FastAPI(
        title="AI Conversation Backend",
        version="0.1.0",
        docs_url="/docs",
    )

    # --- Instantiate services ---
    session_manager = SessionManager()
    agent_registry = AgentRegistry()
    tts_service = TTSService(agent_registry)
    sdk_agent_runner = SDKAgentRunner(agent_registry, tts_service)
    agent_task_manager = AgentTaskManager(
        agent_registry=agent_registry,
        agent_runner=sdk_agent_runner,
        tts_service=tts_service,
        send_json_fn=send_json_msg,
        send_agent_audio_fn=send_agent_audio,
    )
    gemini_proxy = GeminiLiveProxy(
        agent_registry=agent_registry,
        agent_task_manager=agent_task_manager,
        session_manager=session_manager,
        send_audio_response_fn=send_audio_response,
        send_json_fn=send_json_msg,
    )

    # --- Wire module-level references ---
    init_ws_handler(session_manager, gemini_proxy, agent_task_manager)
    init_sessions_router(session_manager)
    init_agents_router(agent_registry)
    init_health_router(session_manager)

    # --- Register routers ---
    application.include_router(ws_router)
    application.include_router(sessions_router)
    application.include_router(agents_router)
    application.include_router(health_router)

    # --- Startup / shutdown hooks ---
    @application.on_event("startup")
    async def on_startup():
        session_manager.start_cleanup()
        logger.info("AI Conversation Backend started")

    @application.on_event("shutdown")
    async def on_shutdown():
        logger.info("Shutting down -- terminating all sessions")
        for sid in list(session_manager._sessions.keys()):
            await session_manager.terminate_session(sid)

    return application


app = create_app()
