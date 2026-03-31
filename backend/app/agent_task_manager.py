"""AgentTaskManager -- dispatch, resume, interrupt, heartbeat."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from app.config import AGENT_TASK_TIMEOUT_SECONDS, THINKING_HEARTBEAT_INTERVAL_SECONDS
from app.models import AgentSession
from app.tts_rephraser import rephrase_for_tts

if TYPE_CHECKING:
    from app.models import ConversationSession
    from app.registry import AgentRegistry
    from app.sdk_agent_runner import SDKAgentRunner
    from app.tts_service import TTSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback phrases per agent (used on timeout)
# ---------------------------------------------------------------------------

FALLBACK_PHRASES: dict[str, str] = {
    "ellen": "Sorry, I took too long on that. Let me try again shortly.",
    "shijing": "Apologies, the analysis timed out. I'll circle back.",
    "eva": "That financial query timed out. Please try again.",
    "ming": "Fraud investigation timed out. I'll retry.",
}


class AgentTaskManager:
    """Manages dispatching, resuming, and interrupting deep-agent tasks."""

    def __init__(
        self,
        agent_registry: "AgentRegistry",
        agent_runner: "SDKAgentRunner",
        tts_service: "TTSService",
        send_json_fn=None,
        send_agent_audio_fn=None,
    ) -> None:
        self._registry = agent_registry
        self._runner = agent_runner
        self._tts = tts_service
        # Pluggable output functions (set by ws handler at startup)
        self.send_json = send_json_fn
        self.send_agent_audio = send_agent_audio_fn
        self._agent_semaphore = asyncio.Semaphore(8)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        conv_session: "ConversationSession",
        agent_name: str,
        task: str,
    ) -> str:
        """Create a new AgentSession and spawn a background task.

        Returns agent_session_id immediately (non-blocking).
        """
        if agent_name not in self._registry:
            raise ValueError(f"Unknown agent: {agent_name}")

        agent_session = AgentSession(
            agent_name=agent_name,
            parent_session_id=conv_session.session_id,
        )
        conv_session.agent_sessions[agent_session.agent_session_id] = agent_session

        # Emit thinking status immediately so the UI spinner shows instantly
        if self.send_json:
            await self.send_json(
                conv_session,
                {
                    "type": "agent_status",
                    "agent_name": agent_name,
                    "agent_session_id": agent_session.agent_session_id,
                    "status": "thinking",
                    "elapsed_ms": 0,
                    "gen_id": conv_session.gen_id,
                },
            )

        agent_session.claude_task = asyncio.create_task(
            self._run_agent(conv_session, agent_session, task)
        )
        return agent_session.agent_session_id

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------

    async def resume(
        self,
        conv_session: "ConversationSession",
        agent_session: AgentSession,
        follow_up: str,
    ) -> None:
        """Append follow_up to history and re-run the agent."""
        agent_session.status = "active"
        agent_session.current_frame_seq = 0

        # Immediate thinking indicator
        if self.send_json:
            await self.send_json(
                conv_session,
                {
                    "type": "agent_status",
                    "agent_name": agent_session.agent_name,
                    "agent_session_id": agent_session.agent_session_id,
                    "status": "thinking",
                    "elapsed_ms": 0,
                    "gen_id": conv_session.gen_id,
                },
            )

        agent_session.claude_task = asyncio.create_task(
            self._run_agent(conv_session, agent_session, follow_up)
        )

    # ------------------------------------------------------------------
    # Interrupt
    # ------------------------------------------------------------------

    async def handle_interrupt(
        self,
        conv_session: "ConversationSession",
        mode: str,
    ) -> None:
        """Handle a barge-in interrupt.

        Audio flushing is handled by the OutputController.  This method
        only manages agent task lifecycle.
        """
        if mode == "cancel_all":
            # Barge-in: don't cancel agent tasks, they are background work.
            # OutputController.flush() already cleared queued audio.
            for agent_session in conv_session.agent_sessions.values():
                agent_session.current_frame_seq = 0

        elif mode == "cancel_agents":
            # Explicit full cancellation (e.g. session stop / user request).
            for agent_session in conv_session.agent_sessions.values():
                if agent_session.claude_task and not agent_session.claude_task.done():
                    logger.info(
                        "handle_interrupt: cancelling agent=%s (%s)",
                        agent_session.agent_name,
                        agent_session.agent_session_id,
                    )
                    agent_session.claude_task.cancel()

    # ------------------------------------------------------------------
    # Internal: run agent with timeout, heartbeat, and delivery
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        conv_session: "ConversationSession",
        agent_session: AgentSession,
        task: str,
    ) -> None:
        """Wrapper: runs the deep agent with timeout + heartbeat.

        The timeout covers only the SDK agent work.  TTS synthesis and
        audio delivery happen *after* the timeout window so a slow
        subprocess cold-start doesn't eat into TTS time.
        """
        full_text = ""
        t0 = time.time()
        try:
            sem_wait_start = time.time()
            async with self._agent_semaphore:
                logger.info(
                    "DIAG _run_agent [%s]: semaphore acquired in %.3fs, "
                    "timeout=%ss",
                    agent_session.agent_name,
                    time.time() - sem_wait_start,
                    AGENT_TASK_TIMEOUT_SECONDS,
                )
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(conv_session, agent_session)
                )
                try:
                    full_text = await asyncio.wait_for(
                        self._runner.run(agent_session, task),
                        timeout=AGENT_TASK_TIMEOUT_SECONDS,
                    )
                    logger.info(
                        "DIAG _run_agent [%s]: SDK completed in %.1fs, "
                        "result_len=%d",
                        agent_session.agent_name,
                        time.time() - t0,
                        len(full_text),
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "DIAG _run_agent [%s]: TIMED OUT after %.1fs "
                        "(limit=%ss)",
                        agent_session.agent_name,
                        time.time() - t0,
                        AGENT_TASK_TIMEOUT_SECONDS,
                    )
                    await self._handle_timeout(conv_session, agent_session)
                    return
                except asyncio.CancelledError:
                    logger.info(
                        "DIAG _run_agent [%s]: CANCELLED after %.1fs",
                        agent_session.agent_name, time.time() - t0,
                    )
                    agent_session.status = "cancelled"
                    return
                except Exception as exc:
                    logger.error(
                        "Agent %s (%s) crashed after %.1fs: %s",
                        agent_session.agent_name,
                        agent_session.agent_session_id,
                        time.time() - t0,
                        exc,
                        exc_info=True,
                    )
                    agent_session.status = "idle"
                    return
                finally:
                    heartbeat_task.cancel()

            # Rephrase + TTS + delivery outside the timeout window
            if full_text:
                logger.info(
                    "DIAG _run_agent [%s]: full_text len=%d, preview=%.200s",
                    agent_session.agent_name,
                    len(full_text),
                    full_text,
                )
                rephrase_start = time.time()
                spoken_text = await rephrase_for_tts(full_text)
                logger.info(
                    "DIAG _run_agent [%s]: rephrase took %.1fs, "
                    "before=%d chars, after=%d chars, spoken_preview=%.300s",
                    agent_session.agent_name,
                    time.time() - rephrase_start,
                    len(full_text),
                    len(spoken_text),
                    spoken_text,
                )
                tts_start = time.time()
                pcm = await self._tts.synthesize(spoken_text, agent_session.agent_name)
                logger.info(
                    "DIAG _run_agent [%s]: TTS took %.1fs, pcm=%s, "
                    "expected_duration=%.1fs",
                    agent_session.agent_name,
                    time.time() - tts_start,
                    f"{len(pcm)} bytes" if pcm else "None",
                    len(pcm) / (16000 * 2) if pcm else 0,  # 16kHz 16-bit
                )
                if pcm:
                    # Deliver via OutputController (serialized, chunked)
                    if conv_session.output_controller:
                        conv_session.output_controller.enqueue_agent_audio(
                            agent_session.agent_name, pcm, conv_session.gen_id
                        )
                    elif self.send_agent_audio:
                        # Fallback for tests without OutputController
                        await self.send_agent_audio(
                            conv_session, agent_session.agent_name, pcm,
                            agent_session.next_frame_seq()
                        )
                else:
                    logger.warning(
                        "DIAG _run_agent [%s]: TTS returned empty PCM, "
                        "no audio will be delivered",
                        agent_session.agent_name,
                    )

            agent_session.status = "idle"
            if self.send_json:
                await self.send_json(
                    conv_session,
                    {
                        "type": "agent_status",
                        "agent_name": agent_session.agent_name,
                        "agent_session_id": agent_session.agent_session_id,
                        "status": "done",
                        "gen_id": conv_session.gen_id,
                    },
                )
        except Exception as exc:
            logger.error(
                "Unexpected error in _run_agent [%s]: %s",
                agent_session.agent_name, exc, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat_loop(
        self,
        conv_session: "ConversationSession",
        agent_session: AgentSession,
    ) -> None:
        """Emit agent_status{thinking} heartbeat every THINKING_HEARTBEAT_INTERVAL."""
        start = time.time()
        while agent_session.status == "active":
            await asyncio.sleep(THINKING_HEARTBEAT_INTERVAL_SECONDS)
            if agent_session.status != "active":
                break
            elapsed_ms = int((time.time() - start) * 1000)
            if self.send_json:
                await self.send_json(
                    conv_session,
                    {
                        "type": "agent_status",
                        "agent_name": agent_session.agent_name,
                        "agent_session_id": agent_session.agent_session_id,
                        "status": "thinking",
                        "elapsed_ms": elapsed_ms,
                        "gen_id": conv_session.gen_id,
                    },
                )

    # ------------------------------------------------------------------
    # Timeout handling
    # ------------------------------------------------------------------

    async def _handle_timeout(
        self,
        conv_session: "ConversationSession",
        agent_session: AgentSession,
    ) -> None:
        """Handle timeout: set status, emit event, synthesize fallback."""
        agent_session.status = "timeout"
        agent_session.sdk_session_id = None  # don't resume a broken session

        if self.send_json:
            await self.send_json(
                conv_session,
                {
                    "type": "agent_status",
                    "agent_name": agent_session.agent_name,
                    "agent_session_id": agent_session.agent_session_id,
                    "status": "timeout",
                    "gen_id": conv_session.gen_id,
                },
            )

        fallback = FALLBACK_PHRASES.get(agent_session.agent_name, "Request timed out.")

        pcm = await self._tts.synthesize(fallback, agent_session.agent_name)
        if pcm:
            if conv_session.output_controller:
                conv_session.output_controller.enqueue_agent_audio(
                    agent_session.agent_name, pcm, conv_session.gen_id
                )
            elif self.send_agent_audio:
                await self.send_agent_audio(
                    conv_session, agent_session.agent_name, pcm, frame_seq=0
                )
