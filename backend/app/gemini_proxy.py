"""GeminiLiveProxy -- bidirectional streaming proxy to the Gemini Live API."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.registry import build_agent_roster_block

if TYPE_CHECKING:
    from app.agent_task_manager import AgentTaskManager
    from app.models import ConversationSession
    from app.registry import AgentRegistry
    from app.session_manager import SessionManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini tool declarations (injected into the Gemini Live session)
# ---------------------------------------------------------------------------

DISPATCH_AGENT_TOOL = {
    "name": "dispatch_agent",
    "description": "Delegate a task to a named deep-thinking agent. Use for first contact.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "enum": ["ellen", "shijing", "eva", "ming"],
                "description": "Agent to dispatch",
            },
            "task": {
                "type": "string",
                "description": "Full task description for the agent",
            },
        },
        "required": ["name", "task"],
    },
}

RESUME_AGENT_TOOL = {
    "name": "resume_agent",
    "description": "Continue a prior conversation with an idle agent session.",
    "parameters": {
        "type": "object",
        "properties": {
            "agent_session_id": {
                "type": "string",
                "description": "UUID returned from a prior dispatch_agent call",
            },
            "follow_up": {
                "type": "string",
                "description": "Follow-up question or instruction for the agent",
            },
        },
        "required": ["agent_session_id", "follow_up"],
    },
}

# ---------------------------------------------------------------------------
# Moderator system prompt (static portion)
# ---------------------------------------------------------------------------

MODERATOR_PERSONA = """\
You are a fast AI moderator for a voice-first assistant system.
Your role:
- Answer simple queries directly and quickly.
- For complex analytical tasks, delegate to the appropriate deep-thinking agent using dispatch_agent.
- For follow-up questions to an existing agent session, use resume_agent.
- Acknowledge delegations immediately with a brief, natural phrase ("Ellen is on it!", "Let me check with Ming.").
- Keep your own responses concise — you are a facilitator, not the expert.
- Never fabricate agent capabilities. Only dispatch agents listed in the roster below.

Audio format: your TTS output and the user's voice are both 16 kHz LINEAR16 PCM.
"""


def build_system_prompt(agent_registry: "AgentRegistry") -> str:
    """Assemble the full Gemini system prompt (persona + roster)."""
    roster = build_agent_roster_block(agent_registry.entries)
    return MODERATOR_PERSONA + "\n" + roster


class GeminiLiveProxy:
    """Manages a bidirectional Gemini Live session per conversation."""

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
        )

        ctx = client.aio.live.connect(model=GEMINI_MODEL, config=config)
        session = await ctx.__aenter__()
        conv_session.gemini_session = session
        conv_session.gemini_ctx = ctx

        send_task = asyncio.create_task(self._audio_send_loop(conv_session))
        recv_task = asyncio.create_task(self._response_receive_loop(conv_session))
        self._tasks[conv_session.session_id] = [send_task, recv_task]

        logger.info("Gemini Live session started for %s", conv_session.session_id)

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

    # ------------------------------------------------------------------
    # Audio send loop
    # ------------------------------------------------------------------

    async def _audio_send_loop(self, session: "ConversationSession") -> None:
        """Read PCM from audio_queue and forward to Gemini Live."""
        from google.genai import types

        chunks_sent = 0
        try:
            while True:
                pcm = await session.audio_queue.get()
                if pcm is None:  # sentinel -> stop
                    logger.info("[session=%s] Audio send loop: got sentinel, stopping", session.session_id)
                    break
                if session.gemini_session is None:
                    logger.info("[session=%s] Audio send loop: gemini_session is None, stopping", session.session_id)
                    break
                await session.gemini_session.send_realtime_input(
                    audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
                )
                chunks_sent += 1
                if chunks_sent == 1 or chunks_sent % 100 == 0:
                    logger.info(
                        "[session=%s] Audio chunks sent: %d (latest %d bytes)",
                        session.session_id,
                        chunks_sent,
                        len(pcm),
                    )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                "[session=%s] Audio send loop error: %s",
                session.session_id,
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Response receive loop (Fix 5: wrapped in try/except)
    # ------------------------------------------------------------------

    async def _response_receive_loop(self, session: "ConversationSession") -> None:
        """Consume events from the Gemini Live session and route them.

        Uses the low-level _receive() to read individual messages instead of
        the high-level receive() which breaks on turn_complete and requires
        generator recreation -- problematic for multi-turn voice sessions.
        """
        try:
            while True:
                if session.gemini_session is None:
                    break
                response = await session.gemini_session._receive()
                if response is None:
                    logger.info(
                        "[session=%s] Gemini session returned None -- stream ended",
                        session.session_id,
                    )
                    break
                try:
                    # 1. Audio output -> binary frame to iOS client
                    if response.data:
                        frame_seq = session.next_frame_seq()
                        if self.send_audio_response:
                            await self.send_audio_response(
                                session, response.data, frame_seq
                            )

                    # 2. Transcript -> JSON text frame
                    if response.text:
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

                    # 4. Gemini-side interruption event
                    if response.server_content and getattr(
                        response.server_content, "interrupted", False
                    ):
                        if self.send_json:
                            await self.send_json(
                                session,
                                {
                                    "type": "interruption",
                                    "gen_id": session.gen_id,
                                },
                            )

                    # 5. turn_complete is informational -- just log and continue
                    if response.server_content and getattr(
                        response.server_content, "turn_complete", False
                    ):
                        logger.info(
                            "[session=%s] Turn complete, continuing to listen",
                            session.session_id,
                        )

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
            pass
        except Exception as exc:
            # Outer: the Gemini session itself died
            logger.error(
                "[session=%s] Gemini session died: %s",
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
