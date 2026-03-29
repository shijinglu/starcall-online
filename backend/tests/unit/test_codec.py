"""Unit tests for the binary wire protocol codec (Phase 0 / T-U-01)."""

import pytest

from app.codec import (
    HEADER_SIZE,
    MsgType,
    SpeakerId,
    decode_frame,
    encode_frame,
    gen_id_is_stale,
)


class TestEncodeDecodeRoundTrip:
    """Encode then decode should return the original values for all field ranges."""

    def test_basic_round_trip(self):
        pcm = b"\x01\x02\x03\x04"
        frame = encode_frame(MsgType.AUDIO_CHUNK, SpeakerId.MODERATOR, 0, 0, pcm)
        msg_type, speaker_id, gen_id, frame_seq, out_pcm = decode_frame(frame)
        assert msg_type == MsgType.AUDIO_CHUNK
        assert speaker_id == SpeakerId.MODERATOR
        assert gen_id == 0
        assert frame_seq == 0
        assert out_pcm == pcm

    def test_all_msg_types(self):
        for mt in (MsgType.AUDIO_CHUNK, MsgType.AUDIO_RESPONSE, MsgType.AGENT_AUDIO):
            frame = encode_frame(mt, 0, 0, 0, b"")
            assert decode_frame(frame)[0] == mt

    def test_all_speaker_ids(self):
        for sid in (
            SpeakerId.MODERATOR,
            SpeakerId.ELLEN,
            SpeakerId.SHIJING,
            SpeakerId.EVA,
            SpeakerId.MING,
        ):
            frame = encode_frame(MsgType.AUDIO_CHUNK, sid, 0, 0, b"x")
            assert decode_frame(frame)[1] == sid

    def test_gen_id_full_range(self):
        for g in range(256):
            frame = encode_frame(MsgType.AUDIO_CHUNK, 0, g, 0, b"")
            assert decode_frame(frame)[2] == g

    def test_frame_seq_full_range(self):
        for s in range(256):
            frame = encode_frame(MsgType.AUDIO_CHUNK, 0, 0, s, b"")
            assert decode_frame(frame)[3] == s

    def test_gen_id_wraps_at_256(self):
        frame = encode_frame(MsgType.AUDIO_CHUNK, 0, 256, 0, b"")
        assert decode_frame(frame)[2] == 0

    def test_frame_seq_wraps_at_256(self):
        frame = encode_frame(MsgType.AUDIO_CHUNK, 0, 0, 256, b"")
        assert decode_frame(frame)[3] == 0

    def test_large_pcm_payload(self):
        pcm = b"\xff" * 3200  # 100 ms at 16kHz
        frame = encode_frame(MsgType.AGENT_AUDIO, SpeakerId.ELLEN, 42, 99, pcm)
        _, _, _, _, out_pcm = decode_frame(frame)
        assert out_pcm == pcm

    def test_empty_pcm_payload(self):
        frame = encode_frame(MsgType.AUDIO_RESPONSE, 0, 0, 0, b"")
        assert decode_frame(frame)[4] == b""

    def test_header_size_is_4(self):
        assert HEADER_SIZE == 4


class TestDecodeErrors:
    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            decode_frame(b"\x01\x02")

    def test_zero_bytes_raises(self):
        with pytest.raises(ValueError):
            decode_frame(b"")

    def test_three_bytes_raises(self):
        with pytest.raises(ValueError):
            decode_frame(b"\x01\x02\x03")


class TestGenIdStaleness:
    """RFC 1982 modular arithmetic staleness check."""

    def test_same_gen_is_not_stale(self):
        assert gen_id_is_stale(5, 5) is False

    def test_older_gen_is_stale(self):
        assert gen_id_is_stale(3, 5) is True

    def test_newer_gen_is_not_stale(self):
        assert gen_id_is_stale(5, 3) is False

    def test_wrap_around_stale(self):
        # current=2, frame=254: diff = (2-254)&0xFF = 4 -> stale
        assert gen_id_is_stale(254, 2) is True

    def test_wrap_around_not_stale(self):
        # current=254, frame=2: diff = (254-2)&0xFF = 252 -> not stale (>128)
        assert gen_id_is_stale(2, 254) is False

    def test_half_window_boundary(self):
        # diff = 127 -> stale
        assert gen_id_is_stale(0, 127) is True
        # diff = 128 -> NOT stale (exactly at boundary, not < 128)
        assert gen_id_is_stale(0, 128) is False
