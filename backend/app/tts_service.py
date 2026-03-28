"""Google Cloud TTS service -- per-sentence synthesis with retry."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.registry import AgentRegistry

logger = logging.getLogger(__name__)


class TTSService:
    """Synthesise text to LINEAR16 PCM (16 kHz) via Google Cloud TTS.

    Called per-sentence, not per-full-response, to minimise time-to-first-audio.
    Includes one automatic retry on transient failure (Fix 5).
    """

    def __init__(self, agent_registry: "AgentRegistry") -> None:
        self._registry = agent_registry
        self._client = None  # lazy init

    def _get_client(self):
        """Lazily create the TTS client (avoids import-time gRPC channel)."""
        if self._client is None:
            try:
                from google.cloud import texttospeech

                self._client = texttospeech.TextToSpeechAsyncClient()
            except Exception as exc:
                logger.error("Failed to create TTS client: %s", exc)
                raise
        return self._client

    async def synthesize(self, text: str, agent_name: str) -> bytes:
        """Synthesize *text* using the voice assigned to *agent_name*.

        Returns raw PCM bytes (LINEAR16, 16 kHz).
        On failure after 2 attempts returns empty bytes so the caller can
        skip the sentence gracefully instead of crashing (Fix 5).
        """
        entry = self._registry.get(agent_name)
        if entry is None:
            logger.error("Unknown agent for TTS: %s", agent_name)
            return b""

        voice_id = entry.voice_id

        for attempt in range(2):
            try:
                from google.cloud import texttospeech

                client = self._get_client()
                response = await client.synthesize_speech(
                    input=texttospeech.SynthesisInput(text=text),
                    voice=texttospeech.VoiceSelectionParams(
                        language_code="en-US",
                        name=voice_id,
                    ),
                    audio_config=texttospeech.AudioConfig(
                        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                        sample_rate_hertz=16000,
                    ),
                )
                return response.audio_content  # raw PCM bytes
            except Exception as exc:
                if attempt == 0:
                    logger.warning(
                        "TTS attempt 1 failed (%s): %s, retrying in 0.5s",
                        agent_name,
                        exc,
                    )
                    await asyncio.sleep(0.5)
                else:
                    logger.error(
                        "TTS failed after 2 attempts (%s): %s",
                        agent_name,
                        exc,
                    )
                    return b""  # empty audio; sentence is skipped gracefully
        return b""  # unreachable, but keeps mypy happy
