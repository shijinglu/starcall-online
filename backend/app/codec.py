"""Binary wire protocol: 4-byte header + raw PCM.

Frame layout
------------
Byte 0: msg_type   -- identifies the frame kind
Byte 1: speaker_id -- who is speaking
Byte 2: gen_id     -- generation counter (zombie-audio prevention)
Byte 3: frame_seq  -- monotonic sequence within a generation (wraps 0-255)
Bytes 4+: raw PCM  -- 16 kHz, int16, little-endian
"""

from __future__ import annotations

import struct

HEADER_FMT = ">BBBB"  # 4 unsigned bytes, big-endian
HEADER_SIZE = 4


# ---------- msg_type constants ----------
class MsgType:
    AUDIO_CHUNK = 0x01
    AUDIO_RESPONSE = 0x02
    AGENT_AUDIO = 0x03


# ---------- speaker_id constants ----------
class SpeakerId:
    MODERATOR = 0x00
    ELLEN = 0x01
    SHIJING = 0x02
    EVA = 0x03
    MING = 0x04


AGENT_SPEAKER_IDS: dict[str, int] = {
    "ellen": SpeakerId.ELLEN,
    "shijing": SpeakerId.SHIJING,
    "eva": SpeakerId.EVA,
    "ming": SpeakerId.MING,
}


def encode_frame(
    msg_type: int,
    speaker_id: int,
    gen_id: int,
    frame_seq: int,
    pcm: bytes,
) -> bytes:
    """Encode a binary audio frame with a 4-byte header."""
    header = struct.pack(HEADER_FMT, msg_type, speaker_id, gen_id & 0xFF, frame_seq & 0xFF)
    return header + pcm


def decode_frame(data: bytes) -> tuple[int, int, int, int, bytes]:
    """Decode a binary audio frame.

    Returns (msg_type, speaker_id, gen_id, frame_seq, pcm_bytes).
    Raises ValueError if data is shorter than the header.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Frame too short: {len(data)} bytes")
    msg_type, speaker_id, gen_id, frame_seq = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    return msg_type, speaker_id, gen_id, frame_seq, data[HEADER_SIZE:]


def gen_id_is_stale(frame_gen: int, current_gen: int) -> bool:
    """Return True if *frame_gen* is in the 'past' half of the circular space.

    Uses RFC 1982 modular arithmetic so the 0/255 wrap boundary works correctly.
    """
    diff = (current_gen - frame_gen) & 0xFF
    return diff > 0 and diff < 128
