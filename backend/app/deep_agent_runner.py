"""DeepAgentRunner -- Claude SDK streaming + sentence boundary splitter + tool loop."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, AsyncGenerator, Generator

import anthropic

from app.config import CLAUDE_MODEL, MAX_TOOL_ROUNDS
from app.tools.dispatch import dispatch_tool

if TYPE_CHECKING:
    from app.models import AgentSession, ConversationSession
    from app.registry import AgentRegistry
    from app.tts_service import TTSService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentence-boundary splitter
# ---------------------------------------------------------------------------

ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "vs", "etc", "approx", "dept", "est"}
)

# Matches sentence-ending punctuation followed by whitespace or end-of-string
_SENTENCE_END_RE = re.compile(r"([.?!])\s+")


def split_into_sentences(text: str) -> Generator[str, None, None]:
    """Yield complete sentences from *text*, handling common abbreviations.

    This is the *batch* form used after accumulating tokens.  The streaming
    variant is embedded inside ``DeepAgentRunner.run()``.
    """
    buf = text
    search_start = 0
    while True:
        m = _SENTENCE_END_RE.search(buf, search_start)
        if m is None:
            break
        candidate = buf[: m.start() + 1].strip()
        # Check if the word before the punctuation is an abbreviation
        last_word = (
            candidate.rsplit(None, 1)[-1].rstrip(".?!").lower() if candidate else ""
        )
        if last_word in ABBREVIATIONS:
            # Not a real sentence end; skip past this match and keep scanning
            search_start = m.end()
            continue
        yield candidate
        buf = buf[m.end() :]
        search_start = 0
    # Yield any remaining text as a final (possibly incomplete) sentence
    remainder = buf.strip()
    if remainder:
        yield remainder


# ---------------------------------------------------------------------------
# Claude tool definitions (Anthropic API format)
# ---------------------------------------------------------------------------

# Maps agent name -> list of tool definitions for the Anthropic API
AGENT_TOOL_DEFINITIONS: dict[str, list[dict[str, Any]]] = {
    "ellen": [
        {
            "name": "calendar_read",
            "description": "Read calendar events for a given date.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date to query, e.g. 'today' or '2026-03-28'",
                    }
                },
                "required": [],
            },
        },
        {
            "name": "email_send",
            "description": "Send an email.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
        {
            "name": "task_list",
            "description": "List the user's tasks / to-do items.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    ],
    "shijing": [
        {
            "name": "user_profile_read",
            "description": "Read the user's profile.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
        {
            "name": "user_journey_read",
            "description": "Read user journey / activity analytics.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "days": {
                        "type": "integer",
                        "description": "Lookback window in days",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "risk_score_read",
            "description": "Read the user's risk score.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
    ],
    "eva": [
        {
            "name": "transaction_read",
            "description": "Read transaction history.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "days": {"type": "integer"},
                },
                "required": [],
            },
        },
        {
            "name": "bank_data_read",
            "description": "Read bank account data.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
        {
            "name": "chargeback_read",
            "description": "Read chargeback and dispute data.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
    ],
    "ming": [
        {
            "name": "id_check",
            "description": "Perform an identity verification check.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
        {
            "name": "async_risk_check",
            "description": "Run an asynchronous risk check.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
        {
            "name": "fraud_signal_read",
            "description": "Read fraud detection signals.",
            "input_schema": {
                "type": "object",
                "properties": {"user_id": {"type": "string"}},
                "required": [],
            },
        },
    ],
}


class DeepAgentRunner:
    """Runs a Claude agent with streaming, sentence splitting, and tool use."""

    def __init__(
        self,
        agent_registry: "AgentRegistry",
        tts_service: "TTSService",
    ) -> None:
        self._registry = agent_registry
        self._tts = tts_service
        self._client = anthropic.AsyncAnthropic()  # uses ANTHROPIC_API_KEY env var

    async def run(
        self,
        agent_session: "AgentSession",
        task: str,
        conv_session: "ConversationSession",
        deliver_fn=None,
    ) -> None:
        """Run a Claude agent task with streaming and per-sentence TTS.

        *deliver_fn* is an async callable ``(conv_session, agent_session, pcm) -> None``
        used to deliver or queue audio.  If None, audio is not sent (useful for testing).

        Fix 4: handles tool_use blocks from Claude -- dispatches to stubs and re-invokes.
        """
        entry = self._registry.get(agent_session.agent_name)
        if entry is None:
            raise ValueError(f"Unknown agent: {agent_session.agent_name}")

        system_prompt = entry.system_prompt
        tools = AGENT_TOOL_DEFINITIONS.get(agent_session.agent_name, [])

        # Build message list from conversation history + new task
        messages: list[dict[str, Any]] = list(agent_session.conversation_history)
        messages.append({"role": "user", "content": task})

        full_text_parts: list[str] = []

        for _round in range(MAX_TOOL_ROUNDS):
            # Stream Claude response
            text_buffer = ""
            tool_use_blocks: list[dict[str, Any]] = []
            stop_reason = None

            try:
                async with self._client.messages.stream(
                    model=CLAUDE_MODEL,
                    system=system_prompt,
                    messages=messages,
                    tools=tools if tools else anthropic.NOT_GIVEN,
                    max_tokens=4096,
                ) as stream:
                    async for event in stream:
                        if hasattr(event, "type"):
                            if event.type == "content_block_delta":
                                if hasattr(event.delta, "text"):
                                    token = event.delta.text
                                    text_buffer += token

                                    # Check for sentence boundaries
                                    _search_start = 0
                                    while True:
                                        m = _SENTENCE_END_RE.search(
                                            text_buffer, _search_start
                                        )
                                        if m is None:
                                            break
                                        candidate = text_buffer[: m.start() + 1].strip()
                                        last_word = (
                                            candidate.rsplit(None, 1)[-1]
                                            .rstrip(".?!")
                                            .lower()
                                            if candidate
                                            else ""
                                        )
                                        if last_word in ABBREVIATIONS:
                                            # Not a real sentence end; skip past
                                            _search_start = m.end()
                                            continue
                                        # Valid sentence boundary
                                        sentence = candidate
                                        text_buffer = text_buffer[m.end() :]
                                        _search_start = 0
                                        full_text_parts.append(sentence)
                                        if deliver_fn and sentence:
                                            pcm = await self._tts.synthesize(
                                                sentence, agent_session.agent_name
                                            )
                                            if pcm:
                                                await deliver_fn(
                                                    conv_session, agent_session, pcm
                                                )

                    # Get the final message to check stop reason and tool use
                    final_msg = await stream.get_final_message()
                    stop_reason = final_msg.stop_reason

                    # Collect any tool_use blocks
                    for block in final_msg.content:
                        if block.type == "tool_use":
                            tool_use_blocks.append(
                                {
                                    "type": "tool_use",
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                }
                            )

            except Exception as exc:
                logger.error(
                    "Claude stream error for %s: %s",
                    agent_session.agent_name,
                    exc,
                    exc_info=True,
                )
                raise

            # Flush remaining text buffer as final sentence
            remainder = text_buffer.strip()
            if remainder:
                full_text_parts.append(remainder)
                if deliver_fn:
                    pcm = await self._tts.synthesize(
                        remainder, agent_session.agent_name
                    )
                    if pcm:
                        await deliver_fn(conv_session, agent_session, pcm)

            # Fix 4: Handle tool_use blocks
            if stop_reason == "tool_use" and tool_use_blocks:
                # Append assistant message with tool_use blocks
                messages.append({"role": "assistant", "content": tool_use_blocks})

                # Dispatch each tool and collect results
                tool_results = []
                for tu in tool_use_blocks:
                    result = await dispatch_tool(tu["name"], tu["input"])
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": json.dumps(result),
                        }
                    )
                messages.append({"role": "user", "content": tool_results})
                # Continue the loop to re-invoke Claude with tool results
                continue
            else:
                # stop_reason == "end_turn" or no tool use -- we're done
                break

        # Update conversation history
        full_response = " ".join(full_text_parts)
        agent_session.conversation_history.append({"role": "user", "content": task})
        agent_session.conversation_history.append(
            {"role": "assistant", "content": full_response}
        )
