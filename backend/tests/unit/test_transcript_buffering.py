"""Unit tests for transcript buffering in GeminiLiveProxy.

Verifies that word-level transcription fragments are accumulated into
complete sentences, sent as partials (is_final=False), and flushed as
finals (is_final=True) on turn boundaries.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.gemini_proxy import GeminiLiveProxy
from app.models import ConversationSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proxy(captured: list[dict]) -> GeminiLiveProxy:
    """Create a GeminiLiveProxy with a mock send_json that captures calls."""

    async def _capture_json(session, payload):
        captured.append(payload)

    proxy = GeminiLiveProxy(
        agent_registry=SimpleNamespace(entries=[]),
        agent_task_manager=SimpleNamespace(),
        session_manager=SimpleNamespace(),
        send_json_fn=_capture_json,
    )
    return proxy


def _make_session(sid: str = "test-session") -> ConversationSession:
    session = ConversationSession()
    # Override generated ID for deterministic tests
    object.__setattr__(session, "session_id", sid)
    return session


def _make_response(
    input_text: str | None = None,
    output_text: str | None = None,
    turn_complete: bool = False,
    interrupted: bool = False,
    text: str | None = None,
    data: bytes | None = None,
    tool_call=None,
):
    """Build a fake Gemini Live response object."""
    inp_transcription = None
    if input_text is not None:
        inp_transcription = SimpleNamespace(text=input_text)

    out_transcription = None
    if output_text is not None:
        out_transcription = SimpleNamespace(text=output_text)

    server_content = SimpleNamespace(
        input_transcription=inp_transcription,
        output_transcription=out_transcription,
        turn_complete=turn_complete,
        interrupted=interrupted,
    )
    return SimpleNamespace(
        server_content=server_content,
        text=text,
        data=data,
        tool_call=tool_call,
        session_resumption_update=None,
    )


async def _feed_responses(proxy, session, responses):
    """Simulate the response receive loop by feeding fake responses."""
    # We can't call _response_receive_loop directly (it reads from gemini_session),
    # so we replicate the core logic inline. This tests the buffering behavior
    # which is what we care about.
    for response in responses:
        sid = session.session_id
        sc = response.server_content

        if sc:
            inp = getattr(sc, "input_transcription", None)
            if inp and getattr(inp, "text", None):
                proxy._user_transcript_buf[sid] = (
                    proxy._user_transcript_buf.get(sid, "") + inp.text
                )
                if proxy.send_json:
                    await proxy.send_json(
                        session,
                        {
                            "type": "transcript",
                            "speaker": "user",
                            "text": proxy._user_transcript_buf[sid],
                            "is_final": False,
                        },
                    )
            out = getattr(sc, "output_transcription", None)
            if out and getattr(out, "text", None):
                proxy._moderator_transcript_buf[sid] = (
                    proxy._moderator_transcript_buf.get(sid, "") + out.text
                )
                if proxy.send_json:
                    await proxy.send_json(
                        session,
                        {
                            "type": "transcript",
                            "speaker": "moderator",
                            "text": proxy._moderator_transcript_buf[sid],
                            "is_final": False,
                        },
                    )

        if sc and getattr(sc, "interrupted", False):
            await proxy._flush_transcript_bufs(session)

        if sc and getattr(sc, "turn_complete", False):
            await proxy._flush_transcript_bufs(session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUserTranscriptBuffering:
    """User (input) transcript fragments are accumulated into one line."""

    @pytest.mark.asyncio
    async def test_fragments_sent_as_partials(self):
        """Each fragment sends accumulated text with is_final=False."""
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(input_text="Hello "),
                _make_response(input_text="world"),
            ],
        )

        assert len(captured) == 2
        # First partial: just the first word
        assert captured[0] == {
            "type": "transcript",
            "speaker": "user",
            "text": "Hello ",
            "is_final": False,
        }
        # Second partial: accumulated
        assert captured[1] == {
            "type": "transcript",
            "speaker": "user",
            "text": "Hello world",
            "is_final": False,
        }

    @pytest.mark.asyncio
    async def test_turn_complete_sends_final(self):
        """turn_complete flushes buffer with is_final=True."""
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(input_text="How "),
                _make_response(input_text="are you?"),
                _make_response(turn_complete=True),
            ],
        )

        # 2 partials + 1 final
        assert len(captured) == 3
        final = captured[-1]
        assert final["speaker"] == "user"
        assert final["text"] == "How are you?"
        assert final["is_final"] is True

    @pytest.mark.asyncio
    async def test_buffer_cleared_after_flush(self):
        """After flush, next fragment starts a fresh buffer."""
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(input_text="First sentence."),
                _make_response(turn_complete=True),
                _make_response(input_text="Second sentence."),
            ],
        )

        # partial + final + partial for new sentence
        assert len(captured) == 3
        assert captured[2]["text"] == "Second sentence."
        assert captured[2]["is_final"] is False


class TestModeratorTranscriptBuffering:
    """Moderator (output) transcript fragments are accumulated."""

    @pytest.mark.asyncio
    async def test_output_fragments_accumulated(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(output_text="Let me "),
                _make_response(output_text="check "),
                _make_response(output_text="with Ming."),
                _make_response(turn_complete=True),
            ],
        )

        # 3 partials + 1 final
        assert len(captured) == 4
        partials = [m for m in captured if not m["is_final"]]
        assert partials[-1]["text"] == "Let me check with Ming."
        assert partials[-1]["speaker"] == "moderator"

        final = captured[-1]
        assert final["text"] == "Let me check with Ming."
        assert final["is_final"] is True
        assert final["speaker"] == "moderator"


class TestInterruptionFlush:
    """Interruption events also flush transcript buffers."""

    @pytest.mark.asyncio
    async def test_interrupted_flushes_user_buffer(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(input_text="Actually never"),
                _make_response(interrupted=True),
            ],
        )

        finals = [m for m in captured if m["is_final"]]
        assert len(finals) == 1
        assert finals[0]["text"] == "Actually never"
        assert finals[0]["speaker"] == "user"

    @pytest.mark.asyncio
    async def test_interrupted_flushes_moderator_buffer(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(output_text="I was saying"),
                _make_response(interrupted=True),
            ],
        )

        finals = [m for m in captured if m["is_final"]]
        assert len(finals) == 1
        assert finals[0]["text"] == "I was saying"
        assert finals[0]["speaker"] == "moderator"


class TestMixedSpeakers:
    """User and moderator transcripts don't interfere with each other."""

    @pytest.mark.asyncio
    async def test_independent_buffers(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [
                _make_response(input_text="Hello "),
                _make_response(output_text="Hi "),
                _make_response(input_text="there"),
                _make_response(output_text="there"),
                _make_response(turn_complete=True),
            ],
        )

        user_msgs = [m for m in captured if m["speaker"] == "user"]
        mod_msgs = [m for m in captured if m["speaker"] == "moderator"]

        # User: 2 partials + 1 final
        user_finals = [m for m in user_msgs if m["is_final"]]
        assert len(user_finals) == 1
        assert user_finals[0]["text"] == "Hello there"

        # Moderator: 2 partials + 1 final
        mod_finals = [m for m in mod_msgs if m["is_final"]]
        assert len(mod_finals) == 1
        assert mod_finals[0]["text"] == "Hi there"


class TestEmptyBufferFlush:
    """Flushing empty buffers should not emit any messages."""

    @pytest.mark.asyncio
    async def test_no_messages_on_empty_flush(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await _feed_responses(
            proxy,
            session,
            [_make_response(turn_complete=True)],
        )

        assert len(captured) == 0


class TestFlushTranscriptBufsDirectly:
    """Test the _flush_transcript_bufs method directly."""

    @pytest.mark.asyncio
    async def test_flush_sends_final_and_clears(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()
        sid = session.session_id

        proxy._user_transcript_buf[sid] = "buffered user text"
        proxy._moderator_transcript_buf[sid] = "buffered mod text"

        await proxy._flush_transcript_bufs(session)

        assert len(captured) == 2
        assert captured[0] == {
            "type": "transcript",
            "speaker": "user",
            "text": "buffered user text",
            "is_final": True,
        }
        assert captured[1] == {
            "type": "transcript",
            "speaker": "moderator",
            "text": "buffered mod text",
            "is_final": True,
        }

        # Buffers cleared
        assert sid not in proxy._user_transcript_buf
        assert sid not in proxy._moderator_transcript_buf

    @pytest.mark.asyncio
    async def test_flush_empty_is_noop(self):
        captured: list[dict] = []
        proxy = _make_proxy(captured)
        session = _make_session()

        await proxy._flush_transcript_bufs(session)

        assert len(captured) == 0
