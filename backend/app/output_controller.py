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
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from app.codec import AGENT_SPEAKER_IDS, MsgType, SpeakerId, encode_frame

logger = logging.getLogger(__name__)

_AUDIO_CHUNK_SIZE = 3200  # 100ms of 16 kHz 16-bit PCM
_RESPONSE_QUEUE_MAX = 5  # cap to prevent unbounded growth

# Type alias for the TTS callback
TTSFn = Callable[[str, str], Coroutine[Any, Any, bytes | None]]
GenIdFn = Callable[[], int]


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


@dataclass
class PendingResponse:
    """An agent text response waiting to be TTS'd and played."""

    agent_name: str
    spoken_text: str  # post-rephrase text ready for TTS
    raw_text: str  # original agent output (for transcript logging)
    gen_id: int
    enqueued_at: float = field(default_factory=_time.monotonic)
    pcm: bytes | None = None  # eager TTS result
    tts_task: asyncio.Task | None = None  # background TTS task


class OutputController:
    """Serializes all audio output through a single writer task.

    Also manages a response queue for agent text results that are
    TTS'd eagerly but only played when the audio pipeline is idle.
    """

    def __init__(self) -> None:
        self.state: OutputState = OutputState.LISTENING
        self._moderator_queue: list[AudioItem] = []
        self._agent_queue: list[AudioItem] = []
        self._drain_event = asyncio.Event()
        self._drain_task: asyncio.Task | None = None
        self._ws: Any = None  # WebSocket connection, set via start()
        self._frame_seq: int = 0
        self._flushed: bool = False  # set by flush() to abort active send
        # Response queue (text-level, pre-TTS)
        self._response_queue: list[PendingResponse] = []
        self._response_drain_event = asyncio.Event()
        self._response_drain_task: asyncio.Task | None = None
        self._tts_fn: TTSFn | None = None
        self._gen_id_fn: GenIdFn | None = None

    def start(
        self,
        ws: Any,
        tts_fn: TTSFn | None = None,
        gen_id_fn: GenIdFn | None = None,
    ) -> None:
        """Attach to a WebSocket and start drain loops."""
        self._ws = ws
        self._tts_fn = tts_fn
        self._gen_id_fn = gen_id_fn
        self._drain_task = asyncio.create_task(self._drain_loop())
        if tts_fn is not None:
            self._response_drain_task = asyncio.create_task(
                self._response_drain_loop()
            )

    async def stop(self) -> None:
        """Stop all drain loops and cancel in-flight TTS tasks."""
        logger.info(
            "DIAG OutputController: stop() called, state=%s, "
            "mod_q=%d, agent_q=%d, resp_q=%d, drain_task_done=%s",
            self.state.value,
            len(self._moderator_queue),
            len(self._agent_queue),
            len(self._response_queue),
            self._drain_task.done() if self._drain_task else "N/A",
        )
        for task_attr in ("_drain_task", "_response_drain_task"):
            task = getattr(self, task_attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            setattr(self, task_attr, None)
        # Cancel any in-flight TTS tasks
        for resp in self._response_queue:
            if resp.tts_task and not resp.tts_task.done():
                resp.tts_task.cancel()
        self._response_queue.clear()
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

    # ------------------------------------------------------------------
    # Response queue (text-level)
    # ------------------------------------------------------------------

    def _is_stale_gen(self, gen_id: int) -> bool:
        """RFC 1982 staleness check for 8-bit gen_id."""
        if self._gen_id_fn is None:
            return False
        current = self._gen_id_fn() & 0xFF
        diff = (current - gen_id) & 0xFF
        return 0 < diff < 128

    def enqueue_response(
        self,
        agent_name: str,
        spoken_text: str,
        raw_text: str,
        gen_id: int,
    ) -> None:
        """Enqueue an agent text response for TTS-when-idle delivery.

        TTS starts immediately in background (eager). The response drain
        loop plays the result only when the audio pipeline is idle.
        """
        if self._is_stale_gen(gen_id):
            logger.info(
                "Discarding stale response from %s (gen=%d, current=%d)",
                agent_name, gen_id,
                self._gen_id_fn() if self._gen_id_fn else -1,
            )
            return

        # Cap queue size
        if len(self._response_queue) >= _RESPONSE_QUEUE_MAX:
            dropped = self._response_queue.pop(0)
            if dropped.tts_task and not dropped.tts_task.done():
                dropped.tts_task.cancel()
            logger.warning(
                "Response queue overflow: dropped oldest from %s (gen=%d)",
                dropped.agent_name, dropped.gen_id,
            )

        resp = PendingResponse(
            agent_name=agent_name,
            spoken_text=spoken_text,
            raw_text=raw_text,
            gen_id=gen_id,
        )
        # Start eager TTS
        if self._tts_fn is not None:
            resp.tts_task = asyncio.create_task(
                self._eager_tts(resp)
            )

        logger.info(
            "DIAG OutputController: enqueue_response agent=%s, "
            "gen_id=%d, text_len=%d, resp_q=%d",
            agent_name, gen_id, len(spoken_text),
            len(self._response_queue) + 1,
        )
        self._response_queue.append(resp)
        self._response_drain_event.set()

    async def _eager_tts(self, resp: PendingResponse) -> bytes | None:
        """Background TTS synthesis for a pending response."""
        try:
            pcm = await self._tts_fn(resp.spoken_text, resp.agent_name)
            resp.pcm = pcm
            # Signal drain loop in case it's waiting
            self._response_drain_event.set()
            return pcm
        except asyncio.CancelledError:
            return None
        except Exception as exc:
            logger.error(
                "Eager TTS failed for %s: %s", resp.agent_name, exc,
            )
            return None

    def pending_count(self) -> int:
        return (
            len(self._moderator_queue)
            + len(self._agent_queue)
            + len(self._response_queue)
        )

    def _get_next(self) -> AudioItem | None:
        """Return the next item to drain.  Moderator always takes priority."""
        if self._moderator_queue:
            return self._moderator_queue.pop(0)
        if self._agent_queue:
            return self._agent_queue.pop(0)
        return None

    def flush(
        self, gen_id: int | None = None,
    ) -> list[PendingResponse]:
        """Flush queued audio and pending responses (barge-in).

        Returns list of flushed PendingResponse items so the caller
        can log them to the transcript with a flushed marker.
        """
        before_mod = len(self._moderator_queue)
        before_agent = len(self._agent_queue)
        before_resp = len(self._response_queue)
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
        # Flush response queue and cancel in-flight TTS
        flushed_responses = list(self._response_queue)
        for resp in flushed_responses:
            if resp.tts_task and not resp.tts_task.done():
                resp.tts_task.cancel()
            logger.info(
                "INTERRUPT: flushed unspoken response from %s (gen=%d): %.100s",
                resp.agent_name, resp.gen_id, resp.spoken_text,
            )
        self._response_queue.clear()

        # Signal active _send_audio to abort
        self._flushed = True
        items_flushed = (
            (before_mod - len(self._moderator_queue))
            + (before_agent - len(self._agent_queue))
            + before_resp
        )
        logger.info(
            "INTERRUPT: barge-in detected, prev_state=%s, gen_id=%s, "
            "items_flushed=%d, mod_q %d->%d, agent_q %d->%d, resp_q %d->0",
            prev_state.value, gen_id, items_flushed,
            before_mod, len(self._moderator_queue),
            before_agent, len(self._agent_queue),
            before_resp,
        )
        self.state = OutputState.LISTENING
        return flushed_responses

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
                            # Wake response drain loop on idle transition
                            self._response_drain_event.set()
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

    # ------------------------------------------------------------------
    # Response drain loop -- TTS-when-idle
    # ------------------------------------------------------------------

    async def _response_drain_loop(self) -> None:
        """Drain pending text responses: TTS and enqueue audio when idle."""
        try:
            while True:
                await self._response_drain_event.wait()
                self._response_drain_event.clear()

                while self._response_queue:
                    # Only proceed if pipeline is idle
                    if self.state != OutputState.LISTENING:
                        break

                    resp = self._response_queue.pop(0)

                    # Discard if gen_id is now stale
                    if self._is_stale_gen(resp.gen_id):
                        if resp.tts_task and not resp.tts_task.done():
                            resp.tts_task.cancel()
                        logger.info(
                            "Response drain: discarding stale %s (gen=%d)",
                            resp.agent_name, resp.gen_id,
                        )
                        continue

                    # Await eager TTS if not ready yet
                    if resp.pcm is None and resp.tts_task:
                        try:
                            resp.pcm = await resp.tts_task
                        except asyncio.CancelledError:
                            continue

                    # Race condition guard: re-check state after TTS
                    if self.state != OutputState.LISTENING:
                        # Put back at front and wait
                        self._response_queue.insert(0, resp)
                        logger.info(
                            "Response drain: state changed during TTS, "
                            "re-queuing %s", resp.agent_name,
                        )
                        break

                    if resp.pcm:
                        logger.info(
                            "Response drain: playing %s (gen=%d, %.1fs audio)",
                            resp.agent_name, resp.gen_id,
                            len(resp.pcm) / (16000 * 2),
                        )
                        self.enqueue_agent_audio(
                            resp.agent_name, resp.pcm, resp.gen_id
                        )
                        # Wait for audio drain to finish before next response
                        # (the audio drain loop will set state back to LISTENING)
                        break
                    else:
                        logger.warning(
                            "Response drain: no PCM for %s (TTS failed?)",
                            resp.agent_name,
                        )

        except asyncio.CancelledError:
            logger.info("DIAG OutputController: response drain loop cancelled")
        except Exception as exc:
            logger.error(
                "OutputController response drain loop crashed: %s",
                exc, exc_info=True,
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
