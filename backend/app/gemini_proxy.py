"""GeminiLiveProxy -- bidirectional streaming proxy to the Gemini Live API."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

# Re-export tool declarations so existing imports keep working
from app.gemini_tools import (  # noqa: F401
    DISPATCH_AGENT_TOOL,
    MODERATOR_PERSONA,
    RESUME_AGENT_TOOL,
    build_system_prompt,
)
from app.transcript_buffer import TranscriptBuffer

if TYPE_CHECKING:
    from app.agent_task_manager import AgentTaskManager
    from app.models import ConversationSession
    from app.registry import AgentRegistry
    from app.session_manager import SessionManager

logger = logging.getLogger(__name__)


class GeminiLiveProxy:
    """Manages a bidirectional Gemini Live session per conversation."""

    _MAX_RECONNECT_ATTEMPTS = 3

    def __init__(
        self,
        agent_registry: "AgentRegistry",
        agent_task_manager: "AgentTaskManager",
        session_manager: "SessionManager",
        send_audio_response_fn=None,
        send_json_fn=None,
    ) -> None:
        self._registry = agent_registry
        self._atm = agent_task_manager
        self._sm = session_manager
        # Pluggable output functions (set by ws handler)
        self.send_audio_response = send_audio_response_fn
        self.send_json = send_json_fn
        self._tasks: dict[
            str, list[asyncio.Task]
        ] = {}  # session_id -> [send_task, recv_task]
        # Per-session Gemini client + config (needed for reconnection)
        self._gemini_clients: dict[str, Any] = {}
        self._gemini_configs: dict[str, Any] = {}
        self._session_resumption_handles: dict[str, str] = {}
        self._reconnect_count: dict[str, int] = {}
        # Transcript buffering (delegated)
        self._transcript = TranscriptBuffer(send_json=send_json_fn)

    # Backward-compatible accessors used by tests
    @property
    def _user_transcript_buf(self) -> dict[str, str]:
        return self._transcript._user_transcript_buf

    @property
    def _moderator_transcript_buf(self) -> dict[str, str]:
        return self._transcript._moderator_transcript_buf

    async def _flush_transcript_bufs(self, session: "ConversationSession") -> None:
        await self._transcript.flush(session)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def start_session(self, conv_session: "ConversationSession") -> None:
        """Open a Gemini Live session and start send/receive loops."""
        from google import genai
        from google.genai import types

        from app.config import GEMINI_API_KEY, GEMINI_MODEL

        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(api_version="v1alpha"),
        )
        system_prompt = build_system_prompt(self._registry)

        config = types.LiveConnectConfig(
            system_instruction=types.Content(parts=[types.Part(text=system_prompt)]),
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(**DISPATCH_AGENT_TOOL),
                        types.FunctionDeclaration(**RESUME_AGENT_TOOL),
                    ]
                )
            ],
            response_modalities=["AUDIO"],
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=80000,
                sliding_window=types.SlidingWindow(target_tokens=40000),
            ),
            session_resumption=types.SessionResumptionConfig(),
        )

        sid = conv_session.session_id
        self._gemini_clients[sid] = client
        self._gemini_configs[sid] = config
        self._reconnect_count[sid] = 0

        ctx = client.aio.live.connect(model=GEMINI_MODEL, config=config)
        session = await ctx.__aenter__()
        conv_session.gemini_session = session
        conv_session.gemini_ctx = ctx

        send_task = asyncio.create_task(self._audio_send_loop(conv_session))
        recv_task = asyncio.create_task(self._response_receive_loop(conv_session))
        self._tasks[conv_session.session_id] = [send_task, recv_task]

        # DIAG: monitor task liveness — log when either loop exits early
        async def _watch_tasks(sid: str) -> None:
            done, _pending = await asyncio.wait(
                [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                name = "SEND-LOOP" if t is send_task else "RECV-LOOP"
                exc = t.exception() if not t.cancelled() else None
                logger.warning(
                    "[session=%s] DIAG TASK-WATCH: %s exited first "
                    "(cancelled=%s, exception=%s). Other still running: "
                    "send=%s recv=%s",
                    sid,
                    name,
                    t.cancelled(),
                    exc,
                    not send_task.done(),
                    not recv_task.done(),
                )

        asyncio.create_task(_watch_tasks(conv_session.session_id))

        logger.info("Gemini Live session started for %s", conv_session.session_id)

    async def _reconnect_session(
        self, conv_session: "ConversationSession"
    ) -> bool:
        """Attempt to transparently reconnect a Gemini Live session.

        Returns True if reconnection succeeded, False otherwise.
        """
        from google.genai import types

        from app.config import GEMINI_MODEL

        sid = conv_session.session_id
        attempt = self._reconnect_count.get(sid, 0) + 1
        self._reconnect_count[sid] = attempt

        if attempt > self._MAX_RECONNECT_ATTEMPTS:
            logger.warning(
                "[session=%s] Max reconnect attempts (%d) exceeded",
                sid,
                self._MAX_RECONNECT_ATTEMPTS,
            )
            return False

        client = self._gemini_clients.get(sid)
        config = self._gemini_configs.get(sid)
        if not client or not config:
            logger.warning("[session=%s] No stored client/config for reconnect", sid)
            return False

        # Use session resumption handle if available
        handle = self._session_resumption_handles.get(sid)
        if handle:
            config = config.model_copy(
                update={
                    "session_resumption": types.SessionResumptionConfig(
                        handle=handle
                    )
                }
            )
            logger.info(
                "[session=%s] Reconnecting with resumption handle (attempt %d/%d)",
                sid,
                attempt,
                self._MAX_RECONNECT_ATTEMPTS,
            )
        else:
            logger.info(
                "[session=%s] Reconnecting without handle (attempt %d/%d)",
                sid,
                attempt,
                self._MAX_RECONNECT_ATTEMPTS,
            )

        try:
            # Close old session quietly
            old_ctx = getattr(conv_session, "gemini_ctx", None)
            if old_ctx:
                try:
                    await old_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            conv_session.gemini_session = None
            conv_session.gemini_ctx = None

            ctx = client.aio.live.connect(model=GEMINI_MODEL, config=config)
            session = await ctx.__aenter__()
            conv_session.gemini_session = session
            conv_session.gemini_ctx = ctx
            logger.info("[session=%s] Reconnected successfully", sid)
            return True
        except Exception as exc:
            logger.error(
                "[session=%s] Reconnection failed: %s", sid, exc, exc_info=True
            )
            return False

    async def send_audio_chunk(
        self, session: "ConversationSession", pcm: bytes
    ) -> None:
        """Enqueue PCM into the session's audio queue for the send loop."""
        await session.audio_queue.put(pcm)

    async def send_tool_response(
        self,
        session: "ConversationSession",
        fn_name: str,
        result: dict,
        fn_call_id: str | None = None,
    ) -> None:
        """Send a function call response back to Gemini."""
        if session.gemini_session is None:
            logger.warning("Cannot send tool response -- Gemini session is None")
            return
        from google.genai import types

        fn_response = types.FunctionResponse(
            name=fn_name,
            id=fn_call_id,
            response=result,
        )
        logger.info(
            "[session=%s] Sending tool response for %s (id=%s): %s",
            session.session_id,
            fn_name,
            fn_call_id,
            result,
        )
        await session.gemini_session.send_tool_response(function_responses=fn_response)

    async def close_session(self, session: "ConversationSession") -> None:
        """Cancel send/receive tasks and close the Gemini connection."""
        tasks = self._tasks.pop(session.session_id, [])
        for t in tasks:
            if not t.done():
                t.cancel()

        # Signal send loop to stop
        try:
            session.audio_queue.put_nowait(None)
        except Exception:
            pass

        if session.gemini_session is not None:
            try:
                ctx = getattr(session, "gemini_ctx", None)
                if ctx is not None:
                    await ctx.__aexit__(None, None, None)
                else:
                    await session.gemini_session.close()
            except Exception:
                pass
            session.gemini_session = None
            session.gemini_ctx = None

        # Clean up reconnection state
        sid = session.session_id
        self._gemini_clients.pop(sid, None)
        self._gemini_configs.pop(sid, None)
        self._session_resumption_handles.pop(sid, None)
        self._reconnect_count.pop(sid, None)

    # ------------------------------------------------------------------
    # Audio send loop
    # ------------------------------------------------------------------

    async def _audio_send_loop(self, session: "ConversationSession") -> None:
        """Read PCM from audio_queue and forward to Gemini Live.

        During reconnection the gemini_session may be temporarily None.
        The loop waits briefly and retries rather than stopping, so audio
        continues flowing after a successful reconnect.
        """
        from google.genai import types

        chunks_sent = 0
        none_wait_count = 0
        send_errors = 0
        _MAX_NONE_WAITS = 50  # ~5 seconds of waiting for reconnection
        try:
            while True:
                pcm = await session.audio_queue.get()
                if pcm is None:  # sentinel -> stop
                    logger.info(
                        "[session=%s] DIAG SEND-LOOP: got sentinel, stopping "
                        "(chunks_sent=%d, send_errors=%d)",
                        session.session_id,
                        chunks_sent,
                        send_errors,
                    )
                    break
                if session.gemini_session is None:
                    # Wait briefly for reconnection
                    none_wait_count += 1
                    if none_wait_count > _MAX_NONE_WAITS:
                        logger.info(
                            "[session=%s] DIAG SEND-LOOP: gemini_session is None "
                            "after %d waits, stopping (chunks_sent=%d)",
                            session.session_id,
                            none_wait_count,
                            chunks_sent,
                        )
                        break
                    if none_wait_count == 1:
                        logger.warning(
                            "[session=%s] DIAG SEND-LOOP: gemini_session became None, "
                            "waiting for reconnect (chunks_sent=%d)",
                            session.session_id,
                            chunks_sent,
                        )
                    await asyncio.sleep(0.1)
                    continue  # drop this chunk but keep looping
                none_wait_count = 0  # reset on successful session
                try:
                    await session.gemini_session.send_realtime_input(
                        audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                    )
                except Exception as send_exc:
                    send_errors += 1
                    logger.warning(
                        "[session=%s] DIAG SEND-LOOP: send error #%d "
                        "(chunks_sent=%d, type=%s): %s",
                        session.session_id,
                        send_errors,
                        chunks_sent,
                        type(send_exc).__name__,
                        send_exc,
                    )
                    continue
                chunks_sent += 1
                if chunks_sent == 1 or chunks_sent % 100 == 0:
                    logger.info(
                        "[session=%s] Audio chunks sent: %d (latest %d bytes), "
                        "queue_size=%d",
                        session.session_id,
                        chunks_sent,
                        len(pcm),
                        session.audio_queue.qsize(),
                    )
        except asyncio.CancelledError:
            logger.info(
                "[session=%s] DIAG SEND-LOOP: cancelled (chunks_sent=%d, "
                "send_errors=%d)",
                session.session_id,
                chunks_sent,
                send_errors,
            )
        except Exception as exc:
            logger.error(
                "[session=%s] DIAG SEND-LOOP: unexpected error (chunks_sent=%d, "
                "send_errors=%d): %s",
                session.session_id,
                chunks_sent,
                send_errors,
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Response receive loop
    # ------------------------------------------------------------------

    async def _response_receive_loop(self, session: "ConversationSession") -> None:
        """Consume events from the Gemini Live session and route them.

        Uses the low-level _receive() to read individual messages instead of
        the high-level receive() which breaks on turn_complete and requires
        generator recreation -- problematic for multi-turn voice sessions.

        On session death, attempts transparent reconnection using session
        resumption handles before giving up.
        """
        import time as _time

        msgs_received = 0
        exit_reason = "unknown"
        last_heartbeat = _time.monotonic()
        _HEARTBEAT_INTERVAL = 10.0  # log every 10s of silence
        try:
            while True:
                if session.gemini_session is None:
                    exit_reason = "gemini_session_is_None"
                    logger.warning(
                        "[session=%s] DIAG RECV-LOOP: gemini_session is None, "
                        "exiting (msgs_received=%d)",
                        session.session_id,
                        msgs_received,
                    )
                    break

                # DIAG: use wait_for so we can emit heartbeats during silence
                try:
                    response = await asyncio.wait_for(
                        session.gemini_session._receive(),
                        timeout=_HEARTBEAT_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    oc = session.output_controller
                    oc_state = oc.state.value if oc else "N/A"
                    since_turn_complete = (
                        _time.monotonic() - session._diag_turn_complete_at
                        if hasattr(session, '_diag_turn_complete_at') else -1
                    )
                    logger.info(
                        "[session=%s] DIAG RECV-LOOP: heartbeat — no Gemini "
                        "event for %.0fs, msgs_received=%d, "
                        "audio_queue_size=%d, oc_state=%s, "
                        "since_turn_complete=%.1fs",
                        session.session_id,
                        _time.monotonic() - last_heartbeat,
                        msgs_received,
                        session.audio_queue.qsize(),
                        oc_state,
                        since_turn_complete,
                    )
                    continue
                last_heartbeat = _time.monotonic()
                if response is None:
                    exit_reason = "receive_returned_None"
                    logger.warning(
                        "[session=%s] DIAG RECV-LOOP: _receive() returned None "
                        "-- stream ended (msgs_received=%d, "
                        "audio_queue_size=%d)",
                        session.session_id,
                        msgs_received,
                        session.audio_queue.qsize(),
                    )
                    break
                msgs_received += 1
                try:
                    await self._route_response(session, response)
                except Exception as exc:
                    logger.error(
                        "[session=%s] Error routing Gemini response: %s",
                        session.session_id,
                        exc,
                        exc_info=True,
                    )
                    if self.send_json:
                        await self.send_json(
                            session,
                            {
                                "type": "error",
                                "code": "INTERNAL",
                                "message": f"Internal error processing response: {exc}",
                            },
                        )
        except asyncio.CancelledError:
            exit_reason = "cancelled"
            logger.info(
                "[session=%s] DIAG RECV-LOOP: cancelled (msgs_received=%d)",
                session.session_id,
                msgs_received,
            )
        except Exception as exc:
            exit_reason = f"exception:{type(exc).__name__}"
            logger.info(
                "[session=%s] DIAG RECV-LOOP: exception exit "
                "(msgs_received=%d, type=%s)",
                session.session_id,
                msgs_received,
                type(exc).__name__,
            )
            await self._handle_session_death(session, exc)
        finally:
            logger.info(
                "[session=%s] DIAG RECV-LOOP: EXITED reason=%s, msgs_received=%d",
                session.session_id,
                exit_reason,
                msgs_received,
            )

    async def _route_response(
        self, session: "ConversationSession", response: Any
    ) -> None:
        """Route a single Gemini response to the appropriate handler."""
        import time as _time

        sid = session.session_id
        oc = session.output_controller
        oc_state = oc.state.value if oc else "N/A"

        # 0. Capture session resumption handle updates
        sru = getattr(response, "session_resumption_update", None)
        if sru and getattr(sru, "new_handle", None):
            self._session_resumption_handles[sid] = sru.new_handle
            logger.debug("[session=%s] Updated resumption handle", sid)

        # 1. Audio output -> binary frame to iOS client
        if response.data:
            frame_seq = session.next_frame_seq()
            # DIAG: track when Gemini starts/continues producing audio
            data_len = len(response.data) if response.data else 0
            if not hasattr(session, '_diag_gemini_audio_start'):
                session._diag_gemini_audio_start = _time.monotonic()
                session._diag_gemini_audio_chunks = 0
            session._diag_gemini_audio_chunks += 1
            if session._diag_gemini_audio_chunks == 1:
                logger.info(
                    "[session=%s] DIAG GEMINI-OUTPUT: first audio chunk "
                    "(%d bytes), oc_state=%s",
                    sid, data_len, oc_state,
                )
            if self.send_audio_response:
                await self.send_audio_response(session, response.data, frame_seq)

        # 2. Transcriptions -> buffer and send partials
        sc = response.server_content
        if sc:
            await self._handle_transcriptions(session, sc)

        # 2b. Fallback: text-only response (non-audio model turn)
        if response.text and not (sc and getattr(sc, "output_transcription", None)):
            if self.send_json:
                await self.send_json(
                    session,
                    {
                        "type": "transcript",
                        "speaker": "moderator",
                        "text": response.text,
                        "is_final": True,
                    },
                )

        # 3. Tool call -> route to Agent Task Manager
        if response.tool_call:
            for fn_call in response.tool_call.function_calls:
                await self._handle_tool_call(session, fn_call)

        # 4. Gemini-side interruption event — finalize buffers
        if sc and getattr(sc, "interrupted", False):
            since_turn_complete = (
                _time.monotonic() - session._diag_turn_complete_at
                if hasattr(session, '_diag_turn_complete_at') else -1
            )
            logger.info(
                "DIAG-ECHO: [session=%s] Gemini-side interruption "
                "(VAD thinks user spoke — likely echo). "
                "oc_state=%s, since_turn_complete=%.1fs, "
                "user_buf=%r mod_buf=%r",
                sid,
                oc_state,
                since_turn_complete,
                self._transcript.get_user(sid)[-200:],
                self._transcript.get_moderator(sid)[-200:],
            )
            await self._transcript.flush(session)
            if self.send_json:
                await self.send_json(
                    session,
                    {
                        "type": "interruption",
                        "gen_id": session.gen_id,
                    },
                )

        # 5. turn_complete — finalize transcript buffers
        if sc and getattr(sc, "turn_complete", False):
            await self._transcript.flush(session)
            # DIAG: record turn_complete timestamp and audio output stats
            gemini_audio_chunks = getattr(session, '_diag_gemini_audio_chunks', 0)
            gemini_audio_elapsed = (
                _time.monotonic() - session._diag_gemini_audio_start
                if hasattr(session, '_diag_gemini_audio_start') else 0
            )
            # Reset audio output tracking for next turn
            session._diag_gemini_audio_chunks = 0
            if hasattr(session, '_diag_gemini_audio_start'):
                del session._diag_gemini_audio_start
            session._diag_turn_complete_at = _time.monotonic()
            logger.info(
                "[session=%s] DIAG TURN-COMPLETE: audio_queue_size=%d, "
                "oc_state=%s, gemini_audio_chunks_this_turn=%d, "
                "turn_duration=%.1fs",
                session.session_id,
                session.audio_queue.qsize(),
                oc_state,
                gemini_audio_chunks,
                gemini_audio_elapsed,
            )

    async def _handle_transcriptions(
        self, session: "ConversationSession", sc: Any
    ) -> None:
        """Process input/output transcription fragments from server_content."""
        import time as _time

        sid = session.session_id
        oc = session.output_controller
        oc_state = oc.state.value if oc else "N/A"

        inp = getattr(sc, "input_transcription", None)
        if inp and getattr(inp, "text", None):
            await self._transcript.accumulate_user(session, inp.text)
            # DIAG: correlate hearing with output state and turn timing
            since_turn_complete = (
                _time.monotonic() - session._diag_turn_complete_at
                if hasattr(session, '_diag_turn_complete_at') else -1
            )
            logger.info(
                "DIAG-ECHO: [session=%s] Gemini heard user speech: %r "
                "[oc_state=%s, since_turn_complete=%.1fs]",
                sid,
                self._transcript.get_user(sid)[-200:],
                oc_state,
                since_turn_complete,
            )

        out = getattr(sc, "output_transcription", None)
        if out and getattr(out, "text", None):
            await self._transcript.accumulate_moderator(session, out.text)

    async def _handle_session_death(
        self, session: "ConversationSession", exc: Exception
    ) -> None:
        """Handle Gemini session death -- attempt reconnection or terminate."""
        logger.error(
            "[session=%s] Gemini session died: %s",
            session.session_id,
            exc,
            exc_info=True,
        )
        reconnected = await self._reconnect_session(session)
        if reconnected:
            # Restart the receive loop in a new task
            recv_task = asyncio.create_task(
                self._response_receive_loop(session)
            )
            tasks = self._tasks.get(session.session_id)
            if tasks and len(tasks) > 1:
                tasks[1] = recv_task
            if self.send_json:
                await self.send_json(
                    session,
                    {
                        "type": "status",
                        "message": "Reconnected to moderator.",
                    },
                )
            return
        # Reconnection failed -- notify client and terminate
        if self.send_json:
            await self.send_json(
                session,
                {
                    "type": "error",
                    "code": "INTERNAL",
                    "message": "Moderator connection lost. Please start a new session.",
                },
            )
        await self._sm.terminate_session(session.session_id)

    # ------------------------------------------------------------------
    # Tool call dispatchers
    # ------------------------------------------------------------------

    async def _handle_tool_call(self, session: "ConversationSession", fn_call) -> None:
        name = fn_call.name
        logger.info(
            "[session=%s] Tool call received: %s (id=%s) args=%s",
            session.session_id,
            name,
            fn_call.id,
            fn_call.args,
        )
        if name == "dispatch_agent":
            await self._handle_dispatch_agent(session, fn_call)
        elif name == "resume_agent":
            await self._handle_resume_agent(session, fn_call)
        else:
            logger.warning("Unknown Gemini tool call: %s", name)
            await self.send_tool_response(
                session,
                name or "unknown",
                {
                    "error": "unknown_tool",
                    "message": f"No handler for tool '{name}'",
                },
                fn_call_id=fn_call.id,
            )

    async def _handle_dispatch_agent(
        self, session: "ConversationSession", fn_call
    ) -> None:
        """Handle dispatch_agent tool call from Gemini."""
        agent_name = fn_call.args.get("name", "")
        task = fn_call.args.get("task", "")
        logger.info(
            "[session=%s] Dispatching agent %s with task: %s",
            session.session_id,
            agent_name,
            task[:120],
        )

        if agent_name not in self._registry:
            await self.send_tool_response(
                session,
                "dispatch_agent",
                {
                    "error": "unknown_agent",
                    "message": f"No agent named '{agent_name}'",
                },
                fn_call_id=fn_call.id,
            )
            return

        agent_session_id = await self._atm.dispatch(session, agent_name, task)

        await self.send_tool_response(
            session,
            "dispatch_agent",
            {
                "status": "dispatched",
                "agent_session_id": agent_session_id,
            },
            fn_call_id=fn_call.id,
        )

        if self.send_json:
            await self.send_json(
                session,
                {
                    "type": "agent_status",
                    "agent_name": agent_name,
                    "agent_session_id": agent_session_id,
                    "status": "dispatched",
                    "gen_id": session.gen_id,
                },
            )

    async def _handle_resume_agent(
        self, session: "ConversationSession", fn_call
    ) -> None:
        """Handle resume_agent tool call from Gemini."""
        agent_session_id = fn_call.args.get("agent_session_id", "")
        follow_up = fn_call.args.get("follow_up", "")

        agent_session = session.agent_sessions.get(agent_session_id)
        if agent_session is None:
            await self.send_tool_response(
                session,
                "resume_agent",
                {
                    "error": "session_not_found",
                    "message": f"No agent session {agent_session_id}",
                },
                fn_call_id=fn_call.id,
            )
            return

        if agent_session.status == "active":
            await self.send_tool_response(
                session,
                "resume_agent",
                {
                    "error": "agent_busy",
                    "message": f"{agent_session.agent_name.capitalize()} is still working on your previous request",
                },
                fn_call_id=fn_call.id,
            )
            return

        await self._atm.resume(session, agent_session, follow_up)
        await self.send_tool_response(
            session, "resume_agent", {"status": "resumed"}, fn_call_id=fn_call.id
        )
