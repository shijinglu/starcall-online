"""L3-07 — Google Cloud TTS: Per-Sentence Synthesis.

Covers: TTS API call with LINEAR16 at 16 kHz; per-sentence latency; audio format correctness.
Requires: GOOGLE_APPLICATION_CREDENTIALS pointing to a valid service account JSON.
"""

from __future__ import annotations

import os
import struct
import time

import pytest

# Skip entire module if TTS credentials are not configured
pytestmark = pytest.mark.skipif(
    not os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").startswith("/path/to"),
    reason="GOOGLE_APPLICATION_CREDENTIALS not configured with a real service account",
)

VOICE_IDS = {
    "ellen": "en-US-Journey-F",
    "shijing": "en-US-Journey-D",
    "eva": "en-US-Journey-O",
    "ming": "en-US-Neural2-D",
}

SAMPLE_SENTENCES = [
    "The risk score is high.",
    "You should review your recent transactions carefully before proceeding.",
    "Done.",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name,voice_id", list(VOICE_IDS.items()))
async def test_tts_produces_valid_pcm(agent_name, voice_id):
    """Each voice ID produces non-empty LINEAR16 PCM at 16 kHz."""
    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechAsyncClient()

    for sentence in SAMPLE_SENTENCES:
        response = await client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=sentence),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name=voice_id,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
            ),
        )

        pcm = response.audio_content
        assert len(pcm) > 0, f"Empty audio for {agent_name}: {sentence!r}"

        # LINEAR16 = int16 LE samples, 16 kHz
        # Audio duration ~ len(pcm) / (16000 * 2) seconds
        duration = len(pcm) / (16000 * 2)
        assert duration > 0.1, f"Audio too short ({duration:.2f}s) for: {sentence!r}"


@pytest.mark.asyncio
async def test_first_sentence_latency():
    """First sentence should synthesize in < 1.5s (latency budget)."""
    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechAsyncClient()

    start = time.monotonic()
    response = await client.synthesize_speech(
        input=texttospeech.SynthesisInput(text="The risk score is high."),
        voice=texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name="en-US-Journey-F",
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
        ),
    )
    elapsed = time.monotonic() - start

    assert len(response.audio_content) > 0
    assert elapsed < 1.5, f"First sentence TTS took {elapsed:.2f}s (> 1.5s budget)"


@pytest.mark.asyncio
async def test_audio_duration_proportional_to_length():
    """Longer sentences should produce proportionally longer audio."""
    from google.cloud import texttospeech

    client = texttospeech.TextToSpeechAsyncClient()
    durations = []

    for sentence in SAMPLE_SENTENCES:
        response = await client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=sentence),
            voice=texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name="en-US-Journey-F",
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
            ),
        )
        duration = len(response.audio_content) / (16000 * 2)
        durations.append(duration)

    # "Done." should be shorter than the long sentence
    assert durations[2] < durations[1], (
        f"Short sentence ({durations[2]:.2f}s) should be shorter than "
        f"long sentence ({durations[1]:.2f}s)"
    )
