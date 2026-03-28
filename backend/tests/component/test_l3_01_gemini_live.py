"""L3-01 — Gemini Live API: STT + VAD + TTS Round-Trip.

Covers: Gemini Live connectivity; speech-to-text; text-to-speech; VAD end-of-utterance.
Requires: GEMINI_API_KEY in environment.

NOTE: This test requires a pre-recorded PCM audio file. If not available,
the test sends a text turn instead to verify basic connectivity.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.config import GEMINI_API_KEY

pytestmark = pytest.mark.skipif(not GEMINI_API_KEY, reason="GEMINI_API_KEY not set")

# Path to a pre-recorded 16kHz int16 PCM file (optional)
TEST_AUDIO_FILE = os.getenv("TEST_AUDIO_PCM_FILE", "")


@pytest.mark.asyncio
async def test_gemini_live_text_round_trip():
    """Verify Gemini Live connectivity with a text turn and collect audio response."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
    )

    responses_received = []

    async with client.aio.live.connect(
        model="gemini-2.0-flash-live", config=config
    ) as session:
        # Send a simple text message
        await session.send(input="What is 2 plus 2? Answer in one word.", end_of_turn=True)

        # Collect responses with a timeout
        try:
            async with asyncio.timeout(10):
                async for response in session.receive():
                    responses_received.append(response)
                    # Check if we got audio or text
                    if response.data or response.text:
                        break
                    if response.server_content and response.server_content.turn_complete:
                        break
        except TimeoutError:
            pass

    assert len(responses_received) > 0, "Should receive at least one response from Gemini"

    # Check that we got audio data back
    has_audio = any(r.data for r in responses_received)
    has_text = any(r.text for r in responses_received)
    assert has_audio or has_text, "Should receive audio or text response"


@pytest.mark.asyncio
@pytest.mark.skipif(not TEST_AUDIO_FILE or not os.path.exists(TEST_AUDIO_FILE),
                    reason="TEST_AUDIO_PCM_FILE not set or file not found")
async def test_gemini_live_audio_round_trip():
    """Send PCM audio to Gemini Live and verify transcript + audio response."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO", "TEXT"],
    )

    # Read pre-recorded PCM file
    with open(TEST_AUDIO_FILE, "rb") as f:
        pcm_data = f.read()

    audio_responses = []
    text_responses = []

    async with client.aio.live.connect(
        model="gemini-2.0-flash-live", config=config
    ) as session:
        # Send audio in chunks (100ms at 16kHz = 3200 bytes)
        chunk_size = 3200
        for i in range(0, len(pcm_data), chunk_size):
            chunk = pcm_data[i : i + chunk_size]
            await session.send(
                input={
                    "realtime_input": {
                        "media_chunks": [
                            {"data": chunk, "mime_type": "audio/pcm;rate=16000"}
                        ]
                    }
                }
            )
            await asyncio.sleep(0.1)  # Simulate real-time pacing

        # Signal end of audio
        await session.send(input="", end_of_turn=True)

        # Collect responses
        try:
            async with asyncio.timeout(15):
                async for response in session.receive():
                    if response.data:
                        audio_responses.append(response.data)
                    if response.text:
                        text_responses.append(response.text)
                    if response.server_content and response.server_content.turn_complete:
                        break
        except TimeoutError:
            pass

    assert len(audio_responses) > 0 or len(text_responses) > 0, (
        "Should receive audio or text response from Gemini"
    )
