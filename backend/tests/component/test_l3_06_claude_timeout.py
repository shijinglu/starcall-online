"""L3-06 — Claude SDK: 30-Second Timeout Behavior.

Covers: Timeout propagation; task cancellation without side effects.
Requires: ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import asyncio
import time

import anthropic
import pytest

from app.config import ANTHROPIC_API_KEY

pytestmark = pytest.mark.skipif(
    not ANTHROPIC_API_KEY, reason="ANTHROPIC_API_KEY not set"
)


@pytest.fixture
def client():
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


@pytest.mark.asyncio
async def test_timeout_raises_at_approximately_30s(client):
    """asyncio.wait_for wrapping a Claude call should timeout at ~30s."""
    # Use a very short timeout (3s) to avoid actually waiting 30s in tests
    # This validates the mechanism works, not the exact 30s boundary
    timeout_seconds = 3

    prompt = (
        "Write a very detailed, comprehensive essay about the complete history of "
        "mathematics from ancient Babylon to modern times. Cover every major "
        "mathematician, their contributions, and the evolution of mathematical thought. "
        "Include at least 50 paragraphs."
    )

    partial_text_parts: list[str] = []
    start = time.monotonic()

    with pytest.raises(asyncio.TimeoutError):
        async with client.messages.stream(
            model="claude-sonnet-4-20250514",
            system="You are a verbose writer. Always write extremely long responses.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
        ) as stream:

            async def collect_tokens():
                async for event in stream:
                    if hasattr(event, "type") and event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            partial_text_parts.append(event.delta.text)

            await asyncio.wait_for(collect_tokens(), timeout=timeout_seconds)

    elapsed = time.monotonic() - start

    # Should have timed out near the timeout value
    assert (
        elapsed >= timeout_seconds - 0.5
    ), f"Timeout fired too early: {elapsed:.1f}s vs expected ~{timeout_seconds}s"
    assert (
        elapsed < timeout_seconds + 5.0
    ), f"Timeout fired too late: {elapsed:.1f}s vs expected ~{timeout_seconds}s"

    # Partial text received before timeout should be non-empty
    partial_text = "".join(partial_text_parts)
    assert len(partial_text) > 0, "Should have received some text before timeout"


@pytest.mark.asyncio
async def test_no_dangling_connections_after_cancel(client):
    """After cancellation, no SDK connections should remain."""
    # This test verifies that we can make another call after cancellation
    prompt = "Write a very long essay about every country in the world."

    try:
        async with client.messages.stream(
            model="claude-sonnet-4-20250514",
            system="Write extremely long responses.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
        ) as stream:

            async def collect():
                async for event in stream:
                    if hasattr(event, "type") and event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            break  # break after first token

            await asyncio.wait_for(collect(), timeout=5)
    except (asyncio.TimeoutError, Exception):
        pass

    # Verify we can still make a new request (no dangling connections)
    response = await client.messages.create(
        model="claude-sonnet-4-20250514",
        system="Be brief.",
        messages=[{"role": "user", "content": "Say hello."}],
        max_tokens=64,
    )
    assert len(response.content[0].text) > 0, "Should work after prior cancellation"
