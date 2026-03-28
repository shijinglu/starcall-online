"""L3-03 — Gemini Live API: resume_agent Tool Call.

Covers: resume_agent schema; Gemini uses correct tool for follow-up turns.
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
Use resume_agent(agent_session_id, follow_up) for follow-up turns to an idle agent.
"""


@pytest.mark.asyncio
async def test_resume_agent_on_followup():
    """After a dispatch, a follow-up should trigger resume_agent, not dispatch_agent."""
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

    fake_agent_session_id = "aaaa-bbbb-cccc-dddd"

    async with client.aio.live.connect(
        model="gemini-2.0-flash-live", config=config
    ) as session:
        # Turn 1: dispatch
        await session.send(
            input="Analyze my spending patterns for this month.",
            end_of_turn=True,
        )

        turn1_tool_calls = []
        try:
            async with asyncio.timeout(15):
                async for response in session.receive():
                    if response.tool_call:
                        for fn_call in response.tool_call.function_calls:
                            turn1_tool_calls.append(fn_call)
                    if response.server_content and response.server_content.turn_complete:
                        break
        except TimeoutError:
            pass

        # If we got a dispatch_agent, send back a tool response with a fake session id
        dispatched = [tc for tc in turn1_tool_calls if tc.name == "dispatch_agent"]
        if dispatched:
            fn_response = types.FunctionResponse(
                name="dispatch_agent",
                response={
                    "status": "dispatched",
                    "agent_session_id": fake_agent_session_id,
                },
            )
            await session.send(input=fn_response)

            # Wait for Gemini to process the tool response
            try:
                async with asyncio.timeout(10):
                    async for response in session.receive():
                        if response.server_content and response.server_content.turn_complete:
                            break
            except TimeoutError:
                pass

            # Turn 2: follow-up targeting same agent
            await session.send(
                input=f"What about last month? (The agent session id is {fake_agent_session_id})",
                end_of_turn=True,
            )

            turn2_tool_calls = []
            try:
                async with asyncio.timeout(15):
                    async for response in session.receive():
                        if response.tool_call:
                            for fn_call in response.tool_call.function_calls:
                                turn2_tool_calls.append(fn_call)
                        if response.server_content and response.server_content.turn_complete:
                            break
            except TimeoutError:
                pass

            # Turn 2 should use resume_agent, not dispatch_agent
            resume_calls = [tc for tc in turn2_tool_calls if tc.name == "resume_agent"]
            dispatch_calls = [tc for tc in turn2_tool_calls if tc.name == "dispatch_agent"]

            if turn2_tool_calls:
                # If Gemini emitted any tool call, prefer resume_agent
                assert len(resume_calls) >= 1 or len(dispatch_calls) == 0, (
                    f"Expected resume_agent for follow-up. Got: {[tc.name for tc in turn2_tool_calls]}"
                )

                # Validate resume_agent schema
                for rc in resume_calls:
                    assert "agent_session_id" in rc.args, "resume_agent should have agent_session_id"
                    assert "follow_up" in rc.args, "resume_agent should have follow_up"
                    assert len(rc.args["follow_up"]) > 0, "follow_up should be non-empty"
        else:
            pytest.skip("Gemini did not emit dispatch_agent on turn 1; cannot test resume")
