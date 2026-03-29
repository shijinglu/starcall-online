"""L2-12 — Agent List Endpoint.

Covers: GET /api/v1/agents; Agent Registry content.
Requires: Backend running on localhost:8000.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.getenv("TEST_BACKEND_URL", "http://localhost:8000")

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 and start the backend to run integration tests",
)


@pytest.fixture
def http_client():
    return httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)


EXPECTED_AGENTS = {"ellen", "shijing", "eva", "ming"}


@pytest.mark.asyncio
async def test_agents_endpoint_lists_all_agents(http_client):
    """GET /agents returns all 4 agents with expected fields."""
    resp = await http_client.get("/api/v1/agents")
    assert resp.status_code == 200

    body = resp.json()
    assert "agents" in body
    agents = body["agents"]

    agent_names = {a["name"] for a in agents}
    assert (
        agent_names == EXPECTED_AGENTS
    ), f"Expected {EXPECTED_AGENTS}, got {agent_names}"

    for agent in agents:
        assert "voice_id" in agent, f"Agent {agent['name']} missing voice_id"
        assert "tool_set" in agent, f"Agent {agent['name']} missing tool_set"
        assert len(agent["tool_set"]) > 0, f"Agent {agent['name']} has empty tool_set"
        assert "speaker_id" in agent, f"Agent {agent['name']} missing speaker_id"
        assert "description" in agent, f"Agent {agent['name']} missing description"


@pytest.mark.asyncio
async def test_agents_have_distinct_voices(http_client):
    """All 4 agents should have distinct voice IDs."""
    resp = await http_client.get("/api/v1/agents")
    agents = resp.json()["agents"]

    voice_ids = [a["voice_id"] for a in agents]
    assert len(set(voice_ids)) == 4, f"Voice IDs should be unique: {voice_ids}"


@pytest.mark.asyncio
async def test_agents_have_distinct_speaker_ids(http_client):
    """All 4 agents should have distinct speaker IDs."""
    resp = await http_client.get("/api/v1/agents")
    agents = resp.json()["agents"]

    speaker_ids = [a["speaker_id"] for a in agents]
    assert len(set(speaker_ids)) == 4, f"Speaker IDs should be unique: {speaker_ids}"
