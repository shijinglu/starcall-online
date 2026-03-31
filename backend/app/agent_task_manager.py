"""AgentTaskManager -- dispatch, resume, interrupt, heartbeat, meeting queue."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.codec import AGENT_SPEAKER_IDS, MsgType, encode_frame
from app.config import AGENT_TASK_TIMEOUT_SECONDS, THINKING_HEARTBEAT_INTERVAL_SECONDS
from app.models import AgentSession

if TYPE_CHECKING:
    from app.models import ConversationSession
    from app.registry import AgentRegistry
    from app.sdk_agent_runner import SDKAgentRunner
    from app.tts_service import TTSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fallback phrases per agent (used on 30-s timeout)
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

        # Fix 6: Emit thinking{elapsed_ms=0} immediately so the UI spinner shows instantly
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

        # If meeting mode is active (more than one agent), add to queue
        active_agents = [
            s
            for s in conv_session.agent_sessions.values()
            if s.status == "active" and s.agent_session_id != agent_session.agent_session_id
        ]
        entering_meeting_mode = (
            len(conv_session.meeting_queue) == 0 and len(active_agents) >= 1
        )
        already_in_meeting_mode = len(conv_session.meeting_queue) > 0

        if entering_meeting_mode or already_in_meeting_mode:
            if entering_meeting_mode:
                # Retroactively add already-active agents to the head of the queue
                # so their audio (which may arrive later via TTS) gets properly drained.
                for active in active_agents:
                    if active.agent_session_id not in conv_session.meeting_queue:
                        conv_session.meeting_queue.append(active.agent_session_id)
                        logger.info(
                            "Meeting mode: retroactively queued %s (%s)",
                            active.agent_name,
                            active.agent_session_id,
                        )

            conv_session.meeting_queue.append(agent_session.agent_session_id)
            # Launch the meeting sender task if not already running
            if (
                conv_session.meeting_sender_task is None
                or conv_session.meeting_sender_task.done()
            ):
                conv_session.meeting_sender_task = asyncio.create_task(
                    self._meeting_mode_audio_sender(conv_session)
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
        agent_session.completion_event = asyncio.Event()
        agent_session.audio_buffer = []
        agent_session.current_frame_seq = 0

        # Fix 6: immediate thinking indicator
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

        Barge-in (cancel_all) flushes Gemini audio but does NOT cancel
        background agent tasks -- those are long-running and should
        survive speech interruptions.
        """
        if mode == "cancel_all":
            # Do NOT cancel agent tasks -- they are background work that
            # should survive barge-in.  Only flush the meeting queue's
            # *buffered audio* so stale TTS doesn't play after the
            # interruption.
            for agent_session in conv_session.agent_sessions.values():
                agent_session.audio_buffer.clear()
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
            # Cancel meeting sender task
            if (
                conv_session.meeting_sender_task
                and not conv_session.meeting_sender_task.done()
            ):
                conv_session.meeting_sender_task.cancel()
            conv_session.meeting_queue.clear()

        elif mode == "skip_speaker":
            # Cancel only the TTS stream for the head-of-queue agent
            if conv_session.meeting_queue:
                head_id = conv_session.meeting_queue.pop(0)
                head_session = conv_session.agent_sessions.get(head_id)
                if head_session:
                    head_session.audio_buffer.clear()
                    head_session.completion_event.set()
            # Do NOT cancel Claude tasks -- agents may still be computing

    # ------------------------------------------------------------------
    # Internal: run agent with timeout, heartbeat, and delivery
    # ------------------------------------------------------------------

    async def _run_agent(
        self,
        conv_session: "ConversationSession",
        agent_session: AgentSession,
        task: str,
    ) -> None:
        """Wrapper: runs the deep agent with timeout + heartbeat."""
        async with self._agent_semaphore:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(conv_session, agent_session)
            )
            try:
                await asyncio.wait_for(
                    self._runner.run(
                        agent_session,
                        task,
                        conv_session,
                        deliver_fn=self._deliver_or_queue,
                    ),
                    timeout=AGENT_TASK_TIMEOUT_SECONDS,
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
            except asyncio.TimeoutError:
                logger.warning(
                    "Agent %s timed out after %ss",
                    agent_session.agent_name, AGENT_TASK_TIMEOUT_SECONDS,
                )
                await self._handle_timeout(conv_session, agent_session)
            except asyncio.CancelledError:
                agent_session.status = "cancelled"
            except Exception as exc:
                logger.error(
                    "Agent %s (%s) crashed: %s",
                    agent_session.agent_name,
                    agent_session.agent_session_id,
                    exc,
                    exc_info=True,
                )
                agent_session.status = "idle"
            finally:
                heartbeat_task.cancel()
                agent_session.completion_event.set()  # Fix 3A: unblock meeting sender

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
        """Handle 30-s timeout: set status, emit event, synthesize fallback."""
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

        # Fix 9: Append a sentinel assistant turn so resume() doesn't produce
        # malformed consecutive-user-messages history.
        fallback = FALLBACK_PHRASES.get(agent_session.agent_name, "Request timed out.")

        pcm = await self._tts.synthesize(fallback, agent_session.agent_name)
        if pcm and self.send_agent_audio:
            await self.send_agent_audio(
                conv_session, agent_session.agent_name, pcm, frame_seq=0
            )

    # ------------------------------------------------------------------
    # Audio delivery / meeting queue
    # ------------------------------------------------------------------

    async def _deliver_or_queue(
        self,
        conv_session: "ConversationSession",
        agent_session: AgentSession,
        pcm_chunk: bytes,
    ) -> None:
        """Deliver audio directly or buffer for meeting mode.

        Fix 3B: Head-of-queue agent streams directly; non-head agents buffer.
        """
        if not conv_session.meeting_queue:
            # No meeting mode -- deliver directly
            frame_seq = agent_session.next_frame_seq()
            if self.send_agent_audio:
                await self.send_agent_audio(
                    conv_session, agent_session.agent_name, pcm_chunk, frame_seq
                )
        elif conv_session.meeting_queue[0] == agent_session.agent_session_id:
            # Fix 3B: This agent is at the head of the queue -- stream directly
            frame_seq = agent_session.next_frame_seq()
            if self.send_agent_audio:
                await self.send_agent_audio(
                    conv_session, agent_session.agent_name, pcm_chunk, frame_seq
                )
        else:
            # Not at head -- buffer until it's this agent's turn
            agent_session.audio_buffer.append(pcm_chunk)

    # ------------------------------------------------------------------
    # Meeting-mode audio sender (Fix 3A: event-driven, no polling)
    # ------------------------------------------------------------------

    async def _meeting_mode_audio_sender(
        self,
        conv_session: "ConversationSession",
    ) -> None:
        """Background task that drains meeting_queue sequentially."""
        try:
            while conv_session.meeting_queue:
                agent_session_id = conv_session.meeting_queue[0]
                agent_session = conv_session.agent_sessions.get(agent_session_id)
                if agent_session is None:
                    conv_session.meeting_queue.pop(0)
                    continue

                # Fix 3A: Event-driven wait -- no polling sleep
                await agent_session.completion_event.wait()

                # Drain any remaining buffered audio
                frame_seq = agent_session.current_frame_seq
                for pcm_chunk in agent_session.audio_buffer:
                    frame = encode_frame(
                        MsgType.AGENT_AUDIO,
                        AGENT_SPEAKER_IDS.get(agent_session.agent_name, 0),
                        conv_session.gen_id,
                        frame_seq,
                        pcm_chunk,
                    )
                    if conv_session.ws_connection:
                        await conv_session.ws_connection.send(frame)
                    frame_seq = (frame_seq + 1) & 0xFF
                agent_session.audio_buffer.clear()

                conv_session.meeting_queue.pop(0)

                # Emit meeting_status update
                completed = sum(
                    1
                    for s in conv_session.agent_sessions.values()
                    if s.status in ("idle", "timeout", "cancelled")
                )
                total = len(conv_session.agent_sessions)
                if self.send_json:
                    await self.send_json(
                        conv_session,
                        {
                            "type": "meeting_status",
                            "gen_id": conv_session.gen_id,
                            "total_agents": total,
                            "completed": completed,
                            "pending": list(conv_session.meeting_queue),
                            "failed": [
                                s.agent_name
                                for s in conv_session.agent_sessions.values()
                                if s.status == "timeout"
                            ],
                        },
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Meeting mode audio sender crashed: %s", exc, exc_info=True)
