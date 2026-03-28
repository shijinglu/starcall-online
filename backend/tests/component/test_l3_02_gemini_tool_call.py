"""L3-02 — Gemini Live API: Tool Call Emission.

Covers: dispatch_agent tool call schema; Gemini produces correct function call.
Requires: GEMINI_API_KEY in environment.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import GEMINI_API_KEY
from app.gemini_proxy import DISPATCH_AGENT_TOOL, RESUME_AGENT_TOOL

pytestmark = pytest.mark.skipif(not GEMINI_API_KEY, reason="GEMINI_API_KEY not set")

SYSTEM_PROMPT = """\
You are a fast AI moderator for a voice-first assistant system.
For complex analytical tasks, delegate to the appropriate deep-thinking agent using dispatch_agent.
For follow-up questions to an existing agent session, use resume_agent.

Available agents:
- ellen: Personal assistant — calendar, email, tasks
- shijing: User risk analyst — user profile and journey
- eva: Financial analyst — transactions and bank data
- ming: Fraud investigator — ID checks and async risk

Use dispatch_agent(name, task) for first contact.
Use resume_agent(agent_session_id, follow_up) for follow-up turns.
"""


@pytest.mark.asyncio
async def test_dispatch_agent_tool_call():
    """Gemini should emit dispatch_agent when asked a complex analytical question."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    config = types.LiveConnectConfig(
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_PROMPT)]
        ),
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(**DISPATCH_AGENT_TOOL),
                    types.FunctionDeclaration(**RESUME_AGENT_TOOL),
                ]
            )
        ],
        response_modalities=["TEXT"],
    )

    tool_calls = []

    async with client.aio.live.connect(
        model="gemini-2.0-flash-live", config=config
    ) as session:
        await session.send(
            input="Analyze my spending patterns for this month and give me a risk summary.",
            end_of_turn=True,
        )

        try:
            async with asyncio.timeout(15):
                async for response in session.receive():
                    if response.tool_call:
                        for fn_call in response.tool_call.function_calls:
                            tool_calls.append(fn_call)
                    if response.server_content and response.server_content.turn_complete:
                        break
        except TimeoutError:
            pass

    # Should have at least one dispatch_agent call
    dispatch_calls = [tc for tc in tool_calls if tc.name == "dispatch_agent"]
    assert len(dispatch_calls) >= 1, (
        f"Expected dispatch_agent call, got: {[tc.name for tc in tool_calls]}"
    )

    # Validate schema
    for dc in dispatch_calls:
        assert "name" in dc.args, "dispatch_agent should have 'name' field"
        assert "task" in dc.args, "dispatch_agent should have 'task' field"
        assert dc.args["name"] in {"ellen", "shijing", "eva", "ming"}, (
            f"Agent name should be valid, got: {dc.args['name']}"
        )
        assert len(dc.args["task"]) > 0, "Task should be non-empty"


@pytest.mark.asyncio
async def test_no_hallucinated_tool_names():
    """Gemini should not emit tool calls with names not in the declared tools."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    config = types.LiveConnectConfig(
        system_instruction=types.Content(
            parts=[types.Part(text=SYSTEM_PROMPT)]
        ),
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(**DISPATCH_AGENT_TOOL),
                    types.FunctionDeclaration(**RESUME_AGENT_TOOL),
                ]
            )
        ],
        response_modalities=["TEXT"],
    )

    tool_calls = []

    async with client.aio.live.connect(
        model="gemini-2.0-flash-live", config=config
    ) as session:
        await session.send(
            input="Check my fraud signals and verify my identity.",
            end_of_turn=True,
        )

        try:
            async with asyncio.timeout(15):
                async for response in session.receive():
                    if response.tool_call:
                        for fn_call in response.tool_call.function_calls:
                            tool_calls.append(fn_call)
                    if response.server_content and response.server_content.turn_complete:
                        break
        except TimeoutError:
            pass

    valid_names = {"dispatch_agent", "resume_agent"}
    for tc in tool_calls:
        assert tc.name in valid_names, (
            f"Hallucinated tool name: {tc.name}. Only {valid_names} are valid."
        )
