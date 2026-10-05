import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.dependencies import get_identity, get_services
from app.dashboard_layout.routes import router


@pytest.mark.asyncio
async def test_solver_requires_session_owner_and_pending_layout_call():
    app = FastAPI()
    app.include_router(router)
    access = SimpleNamespace(require_session_owner=AsyncMock())
    store = SimpleNamespace(pending_layout_call=AsyncMock(return_value=False))
    services = SimpleNamespace(workspace_access=access, deferred_frontend_tools=store,
        frontend_tool_bridges=SimpleNamespace(active_for_thread=lambda _: None))
    app.dependency_overrides[get_identity] = lambda: "owner"
    app.dependency_overrides[get_services] = lambda: services
    body = dict(toolCallId="call", problem=dict(version="constraint-v1", budgetMs=1000,
        nodes=[dict(id="a", shapes=[dict(w=24, h=4)])]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)).status_code == 409
        access.require_session_owner.assert_awaited_with("owner", "s")
        store.pending_layout_call.return_value = True
        response = await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)
        assert response.status_code == 200
        assert response.json()["status"] == "feasible"
        assert response.json()["placements"]
        assert response.json()["alternatives"][0]["planKey"]
        assert response.json()["candidateCount"] <= 8
        assert (await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)).status_code == 409
        body["layoutAttempt"] = 2
        retry = await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)
        assert retry.status_code == 200
        assert retry.json()["layoutAttempt"] == 2
        body["layoutAttempt"] = 3
        assert (await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)).status_code == 422
        body["layoutAttempt"] = 1
        body["toolCallId"] = "new-call"
        app.state.dashboard_layout_jobs.active = 2
        assert (await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)).status_code == 429
        assert ("s", "new-call") not in app.state.dashboard_layout_jobs.claimed
        app.state.dashboard_layout_jobs.active = 0
        access.require_session_owner.side_effect = HTTPException(403, "Not session owner")
        assert (await client.post("/api/sessions/other/dashboardLayoutSolve", json=body)).status_code == 403
        access.require_session_owner.side_effect = None
        body["problem"]["nodes"][0]["title"] = "not geometry"
        assert (await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)).status_code == 422


@pytest.mark.asyncio
async def test_layout_compute_does_not_block_the_event_loop(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.get("/ping")(lambda: {"ok": True})
    access = SimpleNamespace(require_session_owner=AsyncMock())
    store = SimpleNamespace(pending_layout_call=AsyncMock(return_value=True))
    services = SimpleNamespace(workspace_access=access, deferred_frontend_tools=store,
        frontend_tool_bridges=SimpleNamespace(active_for_thread=lambda _: None))
    app.dependency_overrides[get_identity] = lambda: "owner"
    app.dependency_overrides[get_services] = lambda: services

    def slow_compute(problem, deadline, cancelled):
        time.sleep(0.15)
        return {
            "version": "constraint-v1", "status": "feasible", "placements": [],
            "alternatives": [], "candidateCount": 0,
        }

    monkeypatch.setattr("app.dashboard_layout.routes.compute", slow_compute)
    body = dict(toolCallId="call", problem=dict(version="constraint-v1", budgetMs=1000,
        nodes=[dict(id=str(index), shapes=[dict(w=4, h=4)]) for index in range(200)]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        layout_task = asyncio.create_task(
            client.post("/api/sessions/s/dashboardLayoutSolve", json=body)
        )
        await asyncio.sleep(0.02)
        started = time.monotonic()
        ping = await client.get("/ping")
        elapsed = time.monotonic() - started
        response = await layout_task

    assert ping.json() == {"ok": True}
    assert elapsed < 0.08
    assert response.status_code == 200
