"""Rephrase agent output into natural spoken language via Gemini."""

from __future__ import annotations

import logging

from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

_REPHRASE_MODEL = "gemini-2.5-flash"

_MAX_SPOKEN_CHARS = 500  # ~30s of TTS audio at typical speaking rate

_SYSTEM_PROMPT = """\
You are a text-to-speech preprocessor. Rewrite the following text so it sounds \
natural when read aloud by a TTS engine.

Rules:
- HARD LIMIT: Your output MUST be {max_chars} characters or fewer. This is \
roughly 30 seconds of spoken audio. Ruthlessly prioritize — lead with the most \
important findings, drop boilerplate, and cut follow-up offers like "would you \
like me to…".
- PRESERVE KEY content — keep critical facts, numbers, and conclusions but \
summarize supporting detail.
- Use short, clear sentences in a conversational speaking style.
- Prefer common spoken words over formal or technical phrasing.
- Spell out abbreviations and avoid symbols when possible.
""".format(max_chars=_MAX_SPOKEN_CHARS)

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
            if len(rephrased) > _MAX_SPOKEN_CHARS:
                # Hard-truncate at last sentence boundary within limit
                truncated = rephrased[:_MAX_SPOKEN_CHARS]
                last_period = truncated.rfind(".")
                if last_period > _MAX_SPOKEN_CHARS // 2:
                    truncated = truncated[: last_period + 1]
                logger.warning(
                    "TTS rephrase exceeded %d char cap (%d chars), "
                    "truncated to %d chars",
                    _MAX_SPOKEN_CHARS, len(rephrased), len(truncated),
                )
                rephrased = truncated
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
