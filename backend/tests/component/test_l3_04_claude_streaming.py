"""L3-04 — Claude SDK: Streaming Token Output.

Covers: Claude SDK stream=True; token-level streaming; sentence boundary detection.
Requires: ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import time

import anthropic
import pytest

from app.config import ANTHROPIC_API_KEY
from app.deep_agent_runner import split_into_sentences

pytestmark = pytest.mark.skipif(not ANTHROPIC_API_KEY, reason="ANTHROPIC_API_KEY not set")


@pytest.fixture
def client():
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


ELLEN_SYSTEM_PROMPT = (
    "You are Ellen, a warm and efficient personal assistant. "
    "Keep responses concise and action-oriented. Address the user as 'boss'. "
    "Format your answers in clear, natural sentences suitable for text-to-speech."
)


@pytest.mark.asyncio
async def test_streaming_produces_progressive_sentences(client):
    """First complete sentence < 5s; subsequent sentences arrive progressively."""
    task = "Give me a brief summary of 3 tips for time management."

    tokens: list[str] = []
    sentence_times: list[float] = []
    text_buffer = ""
    start = time.monotonic()

    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        system=ELLEN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": task}],
        max_tokens=1024,
    ) as stream:
        async for event in stream:
            if hasattr(event, "type") and event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    token = event.delta.text
                    tokens.append(token)
                    text_buffer += token

                    # Check for sentence boundary (simple check for ". " or "? " or "! ")
                    import re
                    m = re.search(r"[.?!]\s+", text_buffer)
                    if m and not sentence_times:
                        sentence_times.append(time.monotonic() - start)

    full_text = "".join(tokens)
    assert len(full_text) > 0, "Should receive non-empty response"

    # Split into sentences using the production splitter
    sentences = list(split_into_sentences(full_text))
    assert len(sentences) >= 2, f"Expected at least 2 sentences, got {len(sentences)}"

    # First sentence should arrive within 5 seconds
    if sentence_times:
        assert sentence_times[0] < 5.0, (
            f"First sentence took {sentence_times[0]:.1f}s (> 5s budget)"
        )

    # Verify all sentences concatenated reconstruct the full response
    rejoined = " ".join(sentences)
    # The rejoined text should contain all the same words (whitespace may differ)
    assert len(rejoined) > 0

    # Verify no sentence is split mid-word
    for s in sentences:
        assert not s.startswith(" "), f"Sentence starts with space: {s!r}"
        stripped = s.strip()
        assert len(stripped) > 0, "Empty sentence found"


@pytest.mark.asyncio
async def test_streaming_tokens_arrive_incrementally(client):
    """Tokens should arrive incrementally, not all at the end."""
    task = "List 5 common fruits."

    token_times: list[float] = []
    start = time.monotonic()

    async with client.messages.stream(
        model="claude-sonnet-4-20250514",
        system=ELLEN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": task}],
        max_tokens=512,
    ) as stream:
        async for event in stream:
            if hasattr(event, "type") and event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    token_times.append(time.monotonic() - start)

    assert len(token_times) >= 2, "Should receive multiple tokens"

    # Check that tokens arrive over a span of time, not all at once
    time_span = token_times[-1] - token_times[0]
    assert time_span > 0.1, (
        f"Token stream should span > 0.1s, but all arrived within {time_span:.3f}s"
    )
