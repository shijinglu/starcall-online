"""Rephrase agent output into natural spoken language via Gemini."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_REPHRASE_MODEL = "gemini-2.5-flash"

_SYSTEM_PROMPT = """\
You are a text-to-speech preprocessor. Rewrite the following text so it sounds \
natural when read aloud by a TTS engine.

Rules:
- PRESERVE KEY content — do no drop any facts, numbers, or important details.
- Your output should be roughly the SAME LENGTH as the input, just reformatted for speech.
- Use short, clear sentences in a conversational speaking style.
- Prefer common spoken words over formal or technical phrasing.
- Spell out abbreviations and avoid symbols when possible.
"""

# Lazy-initialised client
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


async def rephrase_for_tts(text: str) -> str:
    """Rephrase *text* for natural TTS delivery.

    Returns the original text unchanged on any error so the pipeline
    never blocks on a rephraser failure.
    """
    if not text or not GEMINI_API_KEY:
        return text

    try:
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=_REPHRASE_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=10240,
            ),
        )
        rephrased = response.text
        if rephrased:
            logger.info(
                "TTS rephrase: %d chars -> %d chars, "
                "input_preview=%.100s, output_full=%s",
                len(text),
                len(rephrased),
                text,
                rephrased,
            )
            return rephrased
        logger.warning("TTS rephrase returned empty; using original text")
        return text
    except Exception:
        logger.exception("TTS rephrase failed; using original text")
        return text
