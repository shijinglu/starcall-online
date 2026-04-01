"""Single-writer audio output controller.

All audio destined for the iOS client (moderator TTS, agent TTS) flows
through this controller.  A single drain task serializes writes to the
WebSocket, preventing races and interleaved audio.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time as _time
from dataclasses import dataclass
from typing import Any

from app.codec import AGENT_SPEAKER_IDS, MsgType, SpeakerId, encode_frame

logger = logging.getLogger(__name__)

_AUDIO_CHUNK_SIZE = 3200  # 100ms of 16 kHz 16-bit PCM


class OutputState(enum.Enum):
    LISTENING = "listening"
    MODERATOR_SPEAKING = "moderator_speaking"
    AGENT_SPEAKING = "agent_speaking"


@dataclass
class AudioItem:
    """A unit of audio to be written to the WebSocket."""

    speaker: str  # "moderator" or agent name (e.g., "ellen")
    pcm: bytes  # raw PCM audio
    gen_id: int  # generation counter for zombie prevention

    @property
    def priority(self) -> int:
        """Lower number = higher priority. Moderator always wins."""
        return 0 if self.speaker == "moderator" else 1


class OutputController:
    """Serializes all audio output through a single writer task."""

    def __init__(self) -> None:
        self.state: OutputState = OutputState.LISTENING
        self._moderator_queue: list[AudioItem] = []
        self._agent_queue: list[AudioItem] = []
        self._drain_event = asyncio.Event()
        self._drain_task: asyncio.Task | None = None
        self._ws: Any = None  # WebSocket connection, set via start()
        self._frame_seq: int = 0
        self._flushed: bool = False  # set by flush() to abort active send

    def start(self, ws: Any) -> None:
        """Attach to a WebSocket and start the drain loop."""
        self._ws = ws
        self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        """Stop the drain loop."""
        logger.info(
            "DIAG OutputController: stop() called, state=%s, "
            "mod_q=%d, agent_q=%d, drain_task_done=%s",
            self.state.value,
            len(self._moderator_queue),
            len(self._agent_queue),
            self._drain_task.done() if self._drain_task else "N/A",
        )
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
        self._drain_task = None
        self._ws = None

    def enqueue_moderator_audio(self, pcm: bytes, gen_id: int) -> None:
        """Enqueue moderator (Gemini) audio for output."""
        logger.info(
            "DIAG OutputController: enqueue MODERATOR pcm=%d bytes (%.1fs), "
            "gen_id=%d, state=%s, mod_q=%d, agent_q=%d",
            len(pcm), len(pcm) / (16000 * 2), gen_id, self.state.value,
            len(self._moderator_queue), len(self._agent_queue),
        )
        self._moderator_queue.append(AudioItem("moderator", pcm, gen_id))
        self._drain_event.set()

    def enqueue_agent_audio(self, agent_name: str, pcm: bytes, gen_id: int) -> None:
        """Enqueue agent TTS audio for output."""
        logger.info(
            "DIAG OutputController: enqueue AGENT=%s pcm=%d bytes (%.1fs), "
            "gen_id=%d, state=%s, mod_q=%d, agent_q=%d",
            agent_name, len(pcm), len(pcm) / (16000 * 2), gen_id,
            self.state.value, len(self._moderator_queue), len(self._agent_queue),
        )
        self._agent_queue.append(AudioItem(agent_name, pcm, gen_id))
        self._drain_event.set()

    def pending_count(self) -> int:
        return len(self._moderator_queue) + len(self._agent_queue)

    def _get_next(self) -> AudioItem | None:
        """Return the next item to drain.  Moderator always takes priority."""
        if self._moderator_queue:
            return self._moderator_queue.pop(0)
        if self._agent_queue:
            return self._agent_queue.pop(0)
        return None

    def flush(self, gen_id: int | None = None) -> None:
        """Flush queued audio (barge-in).  If gen_id given, only flush stale items."""
        before_mod = len(self._moderator_queue)
        before_agent = len(self._agent_queue)
        prev_state = self.state
        if gen_id is None:
            self._moderator_queue.clear()
            self._agent_queue.clear()
        else:
            self._moderator_queue = [
                i for i in self._moderator_queue if i.gen_id >= gen_id
            ]
            self._agent_queue = [
                i for i in self._agent_queue if i.gen_id >= gen_id
            ]
        # Signal active _send_audio to abort
        self._flushed = True
        items_flushed = (before_mod - len(self._moderator_queue)) + (
            before_agent - len(self._agent_queue)
        )
        logger.info(
            "INTERRUPT: barge-in detected, prev_state=%s, gen_id=%s, "
            "items_flushed=%d, mod_q %d->%d, agent_q %d->%d",
            prev_state.value, gen_id, items_flushed,
            before_mod, len(self._moderator_queue),
            before_agent, len(self._agent_queue),
        )
        self.state = OutputState.LISTENING

    # ------------------------------------------------------------------
    # Drain loop -- the single writer
    # ------------------------------------------------------------------

    async def _drain_loop(self) -> None:
        """Single async task that owns all WS audio writes."""
        try:
            while True:
                await self._drain_event.wait()
                self._drain_event.clear()

                while True:
                    item = self._get_next()
                    if item is None:
                        if self.state != OutputState.LISTENING:
                            self.state = OutputState.LISTENING
                            await self._emit_playback_state("listening")
                        break

                    # Update state and notify client
                    prev_state = self.state
                    if item.speaker == "moderator":
                        self.state = OutputState.MODERATOR_SPEAKING
                    else:
                        self.state = OutputState.AGENT_SPEAKING
                        await self._emit_playback_state(
                            "agent_speaking", agent_name=item.speaker
                        )

                    logger.info(
                        "DIAG OutputController: draining item speaker=%s, "
                        "pcm=%d bytes (%.1fs), gen_id=%d, "
                        "state %s->%s, remaining mod=%d agent=%d",
                        item.speaker,
                        len(item.pcm),
                        len(item.pcm) / (16000 * 2),
                        item.gen_id,
                        prev_state.value,
                        self.state.value,
                        len(self._moderator_queue),
                        len(self._agent_queue),
                    )

                    # Chunk and send
                    self._flushed = False
                    await self._send_audio(item)

                    # Notify client when agent finishes speaking
                    if item.speaker != "moderator" and not self._flushed:
                        await self._emit_playback_state(
                            "agent_done", agent_name=item.speaker
                        )

        except asyncio.CancelledError:
            logger.info("DIAG OutputController: drain loop cancelled")
        except Exception as exc:
            logger.error(
                "OutputController drain loop crashed: %s", exc, exc_info=True
            )

    async def _emit_playback_state(
        self, state: str, agent_name: str | None = None
    ) -> None:
        """Send a playback_state JSON message to the client."""
        if self._ws is None:
            return
        payload: dict[str, Any] = {"type": "playback_state", "state": state}
        if agent_name:
            payload["agent_name"] = agent_name
        try:
            await self._ws.send_json(payload)
        except Exception:
            logger.debug("Failed to send playback_state (WS closed?)")

    async def _send_audio(self, item: AudioItem) -> None:
        """Chunk PCM and write frames to the WebSocket."""
        if self._ws is None:
            logger.warning(
                "DIAG OutputController._send_audio: ws is None, "
                "dropping %s audio (%d bytes)",
                item.speaker, len(item.pcm),
            )
            return

        if item.speaker == "moderator":
            msg_type = MsgType.AUDIO_RESPONSE
            speaker_id = SpeakerId.MODERATOR
        else:
            msg_type = MsgType.AGENT_AUDIO
            speaker_id = AGENT_SPEAKER_IDS.get(item.speaker, 0)

        t0 = _time.monotonic()
        chunks_sent = 0
        try:
            for offset in range(0, len(item.pcm), _AUDIO_CHUNK_SIZE):
                # Check if flush was requested mid-send
                if self._flushed:
                    elapsed_audio = (chunks_sent * _AUDIO_CHUNK_SIZE) / (16000 * 2)
                    total_audio = len(item.pcm) / (16000 * 2)
                    logger.info(
                        "INTERRUPT: playback aborted for speaker=%s, "
                        "played=%.1fs of %.1fs (%.0f%%), chunks_sent=%d",
                        item.speaker, elapsed_audio, total_audio,
                        (elapsed_audio / total_audio * 100) if total_audio > 0 else 0,
                        chunks_sent,
                    )
                    return

                chunk = item.pcm[offset : offset + _AUDIO_CHUNK_SIZE]
                frame = encode_frame(
                    msg_type,
                    speaker_id,
                    item.gen_id & 0xFF,
                    self._frame_seq & 0xFF,
                    chunk,
                )
                await self._ws.send_bytes(frame)
                self._frame_seq += 1
                chunks_sent += 1
        except Exception as exc:
            logger.warning(
                "DIAG OutputController: WS write failed after %d chunks "
                "(%.1fs): %s",
                chunks_sent, _time.monotonic() - t0, exc,
            )
        else:
            elapsed = _time.monotonic() - t0
            logger.info(
                "DIAG OutputController._send_audio: speaker=%s, "
                "%d chunks sent in %.3fs (audio_duration=%.1fs, "
                "send_speed=%.0fx realtime)",
                item.speaker,
                chunks_sent,
                elapsed,
                len(item.pcm) / (16000 * 2),
                (len(item.pcm) / (16000 * 2)) / elapsed if elapsed > 0 else 0,
            )
