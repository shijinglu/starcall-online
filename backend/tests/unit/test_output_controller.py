"""Unit tests for the audio OutputController."""

import asyncio

import pytest

from app.output_controller import AudioItem, OutputController, OutputState


class TestOutputState:
    def test_initial_state_is_listening(self):
        oc = OutputController()
        assert oc.state == OutputState.LISTENING

    def test_states_are_distinct(self):
        assert OutputState.LISTENING != OutputState.MODERATOR_SPEAKING
        assert OutputState.MODERATOR_SPEAKING != OutputState.AGENT_SPEAKING


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_moderator_audio(self):
        oc = OutputController()
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        assert oc.pending_count() == 1

    @pytest.mark.asyncio
    async def test_enqueue_agent_audio(self):
        oc = OutputController()
        oc.enqueue_agent_audio("ellen", b"\x00" * 6400, gen_id=0)
        assert oc.pending_count() == 1

    @pytest.mark.asyncio
    async def test_moderator_has_priority_over_agent(self):
        """Moderator audio should drain before agent audio."""
        oc = OutputController()
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=0)
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        item = oc._get_next()
        assert item is not None
        assert item.speaker == "moderator"


class TestFlush:
    def test_flush_clears_all_queues(self):
        oc = OutputController()
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=0)
        oc.flush()
        assert oc.pending_count() == 0
        assert oc.state == OutputState.LISTENING

    def test_flush_with_gen_id_keeps_current(self):
        oc = OutputController()
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=1)
        oc.enqueue_agent_audio("shijing", b"\x00" * 3200, gen_id=3)
        oc.flush(gen_id=2)
        assert oc.pending_count() == 1  # only shijing (gen_id=3) remains

    def test_flush_resets_state_to_listening(self):
        oc = OutputController()
        oc.state = OutputState.AGENT_SPEAKING
        oc.flush()
        assert oc.state == OutputState.LISTENING


class TestDrainLoop:
    @pytest.mark.asyncio
    async def test_drain_sends_chunks_to_ws(self):
        """Verify the drain loop writes chunked frames to the WS mock."""
        sent_frames: list[bytes] = []

        class MockWS:
            async def send_bytes(self, data: bytes) -> None:
                sent_frames.append(data)

        oc = OutputController()
        oc.start(ws=MockWS())
        # 6400 bytes = 2 chunks of 3200
        oc.enqueue_moderator_audio(b"\x00" * 6400, gen_id=0)
        await asyncio.sleep(0.05)  # let drain loop run
        await oc.stop()
        assert len(sent_frames) == 2

    @pytest.mark.asyncio
    async def test_drain_moderator_before_agent(self):
        """Moderator audio drains before agent audio regardless of enqueue order."""
        speakers: list[int] = []

        class MockWS:
            async def send_bytes(self, data: bytes) -> None:
                # byte 0 = msg_type: 0x02=moderator, 0x03=agent
                speakers.append(data[0])

        oc = OutputController()
        oc.start(ws=MockWS())
        oc.enqueue_agent_audio("ellen", b"\x00" * 3200, gen_id=0)
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        await asyncio.sleep(0.3)
        await oc.stop()
        # Moderator (0x02) should come first, then agent (0x03)
        assert speakers[0] == 0x02
        assert speakers[-1] == 0x03

    @pytest.mark.asyncio
    async def test_state_returns_to_listening_after_drain(self):
        class MockWS:
            async def send_bytes(self, data: bytes) -> None:
                pass

        oc = OutputController()
        oc.start(ws=MockWS())
        oc.enqueue_moderator_audio(b"\x00" * 3200, gen_id=0)
        await asyncio.sleep(0.3)
        assert oc.state == OutputState.LISTENING
        await oc.stop()
