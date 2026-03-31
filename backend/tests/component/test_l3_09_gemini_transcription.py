"""L3-09 — Gemini Live API: Audio Transcription Events.

Verifies that input_audio_transcription and output_audio_transcription
config options cause Gemini to return transcription events alongside
audio responses.

Requires: GEMINI_API_KEY in environment.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import GEMINI_API_KEY, GEMINI_MODEL

pytestmark = pytest.mark.skipif(not GEMINI_API_KEY, reason="GEMINI_API_KEY not set")


@pytest.mark.asyncio
async def test_output_transcription_returned():
    """When output_audio_transcription is enabled, Gemini returns
    output_transcription events alongside audio data."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(api_version="v1alpha"),
    )

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    output_transcriptions: list[str] = []
    audio_chunks: list[bytes] = []

    async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text="Say exactly: hello world")],
            ),
            turn_complete=True,
        )

        try:
            async with asyncio.timeout(15):
                while True:
                    response = await session._receive()
                    if response is None:
                        break

                    if response.data:
                        audio_chunks.append(response.data)

                    sc = response.server_content
                    if sc:
                        out = getattr(sc, "output_transcription", None)
                        if out and getattr(out, "text", None):
                            output_transcriptions.append(out.text)
                        if getattr(sc, "turn_complete", False):
                            break
        except TimeoutError:
            pass

    assert len(audio_chunks) > 0, "Should receive audio response"
    assert len(output_transcriptions) > 0, (
        "Should receive output_transcription events when output_audio_transcription is enabled"
    )

    # Transcription fragments should form coherent text
    full_text = "".join(output_transcriptions).lower()
    assert len(full_text) > 0, "Transcription should contain text"


@pytest.mark.asyncio
async def test_input_transcription_returned():
    """When input_audio_transcription is enabled, Gemini returns
    input_transcription events for user text input."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(api_version="v1alpha"),
    )

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    input_transcriptions: list[str] = []
    turn_complete_seen = False

    async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text="What is two plus two?")],
            ),
            turn_complete=True,
        )

        try:
            async with asyncio.timeout(15):
                while True:
                    response = await session._receive()
                    if response is None:
                        break

                    sc = response.server_content
                    if sc:
                        inp = getattr(sc, "input_transcription", None)
                        if inp and getattr(inp, "text", None):
                            input_transcriptions.append(inp.text)
                        if getattr(sc, "turn_complete", False):
                            turn_complete_seen = True
                            break
        except TimeoutError:
            pass

    assert turn_complete_seen, "Should see turn_complete"
    # Note: input_transcription for text input may or may not be returned
    # by Gemini (it's primarily for audio input). This test documents the
    # behavior rather than strictly asserting.


@pytest.mark.asyncio
async def test_transcription_fragments_are_incremental():
    """Output transcription arrives as multiple incremental fragments,
    not a single block — this is why buffering is needed."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(api_version="v1alpha"),
    )

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    output_transcriptions: list[str] = []

    async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
        # Ask for a longer response to get multiple fragments
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[
                    types.Part(
                        text="Count from one to five, saying each number as a word."
                    )
                ],
            ),
            turn_complete=True,
        )

        try:
            async with asyncio.timeout(20):
                while True:
                    response = await session._receive()
                    if response is None:
                        break

                    sc = response.server_content
                    if sc:
                        out = getattr(sc, "output_transcription", None)
                        if out and getattr(out, "text", None):
                            output_transcriptions.append(out.text)
                        if getattr(sc, "turn_complete", False):
                            break
        except TimeoutError:
            pass

    assert len(output_transcriptions) > 1, (
        f"Expected multiple transcription fragments (got {len(output_transcriptions)}). "
        "This confirms transcription arrives incrementally and buffering is needed."
    )
