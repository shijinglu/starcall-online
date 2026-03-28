"""L2-01 — Session Lifecycle REST API.

Covers: POST /sessions, GET /health, DELETE /sessions/{id}; token issuance and invalidation.
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


@pytest.mark.asyncio
async def test_health_endpoint(http_client):
    """GET /health returns 200 with expected fields."""
    resp = await http_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "active_sessions" in body


@pytest.mark.asyncio
async def test_create_session(http_client):
    """POST /sessions returns session_id and auth_token."""
    resp = await http_client.post("/api/v1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert "auth_token" in body
    assert body["session_id"].startswith("s-")
    # auth_token should be a UUID
    assert len(body["auth_token"]) == 36  # UUID format


@pytest.mark.asyncio
async def test_delete_session(http_client):
    """DELETE /sessions/{id} returns 200; second DELETE returns 404."""
    # Create
    create_resp = await http_client.post("/api/v1/sessions")
    session_id = create_resp.json()["session_id"]

    # Delete
    del_resp = await http_client.delete(f"/api/v1/sessions/{session_id}")
    assert del_resp.status_code == 200

    # Second delete should return 404
    del2_resp = await http_client.delete(f"/api/v1/sessions/{session_id}")
    assert del2_resp.status_code == 404


@pytest.mark.asyncio
async def test_session_count_increments(http_client):
    """Creating a session should increment active_sessions count."""
    # Get baseline
    h1 = await http_client.get("/api/v1/health")
    count_before = h1.json()["active_sessions"]

    # Create session
    create_resp = await http_client.post("/api/v1/sessions")
    session_id = create_resp.json()["session_id"]

    # Check count
    h2 = await http_client.get("/api/v1/health")
    count_after = h2.json()["active_sessions"]
    assert count_after == count_before + 1

    # Cleanup
    await http_client.delete(f"/api/v1/sessions/{session_id}")
