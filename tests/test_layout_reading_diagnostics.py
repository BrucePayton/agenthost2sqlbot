import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.dependencies import get_identity, get_services
from app.dashboard_layout.diagnostics import LayoutDiagnostics
from app.dashboard_layout.routes import recorded_compute, router, solver_revision
from app.dashboard_layout.solver import LayoutProblem


@pytest.mark.asyncio
async def test_reading_capability_is_read_only_and_owner_scoped():
    app = FastAPI()
    app.include_router(router)
    access = SimpleNamespace(require_session_owner=AsyncMock())
    app.dependency_overrides[get_identity] = lambda: "owner"
    app.dependency_overrides[get_services] = lambda: SimpleNamespace(workspace_access=access)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/sessions/s/dashboardLayoutCapabilities")
        assert response.status_code == 200
        assert response.json()["readingContract"] == "local-regions-v1"
        assert response.headers["cache-control"] == "no-store"
        assert not hasattr(app.state, "dashboard_layout_jobs")
        access.require_session_owner.side_effect = HTTPException(403)
        assert (await client.get("/api/sessions/s/dashboardLayoutCapabilities")).status_code == 403


def test_legacy_reading_source_is_explicitly_recorded(tmp_path, monkeypatch):
    problem = LayoutProblem(version="constraint-v1", orders=[["metric"]],
        nodes=[dict(id="metric", kind="metric", shapes=[dict(w=24, h=3)])])
    monkeypatch.setattr("app.dashboard_layout.routes.compute", lambda *args: {
        "version": "constraint-v1", "status": "feasible",
        "placements": [dict(id="metric", x=0, y=0, w=24, h=3, variant=0)],
    })
    store = LayoutDiagnostics(tmp_path)
    result = recorded_compute(problem, time.monotonic() + 1, Event(), store,
                              "run", "session", "call")
    summary = store.get("run")["trace"]["inputSummary"]
    assert summary["orderSourceCounts"] == {"legacy_strict": 1}
    assert summary["requiredBeforeCount"] == 0
    assert result["solverRevision"] == solver_revision()
    assert result["readingContract"] == "local-regions-v1"


def test_reading_decision_is_saved_without_business_content(tmp_path, monkeypatch):
    problem = LayoutProblem(version="constraint-v1", orders=[["metric", "chart"]],
        nodes=[dict(id=id, kind=kind, shapes=[dict(w=24, h=3)])
               for id, kind in [("metric", "metric"), ("chart", "chart")]])
    decision = [["metric", "chart"]]
    monkeypatch.setattr("app.dashboard_layout.routes.compute", lambda *args: {
        "version": "constraint-v1", "status": "feasible", "readingOrders": decision,
        "placements": [],
    })
    store = LayoutDiagnostics(tmp_path)
    result = recorded_compute(problem, time.monotonic() + 1, Event(), store,
                              "run", "session", "call")
    record = store.get("run")
    assert record["result"]["readingOrders"] == decision
    assert record["result"]["solverRevision"] == result["solverRevision"]
    assert record["trace"]["readingContract"] == "local-regions-v1"


def test_derived_source_and_hard_relation_counts_are_distinct(tmp_path, monkeypatch):
    problem = LayoutProblem(version="constraint-v1", orders=[["m1", "chart", "m2"]],
        orderSources=["derived_geometry"], requiredBefore=[("m1", "m2")],
        nodes=[dict(id=id, kind=kind, shapes=[dict(w=24, h=3)])
               for id, kind in [("m1", "metric"), ("chart", "chart"), ("m2", "metric")]])
    monkeypatch.setattr("app.dashboard_layout.routes.compute", lambda *args: {
        "version": "constraint-v1", "status": "feasible",
        "readingOrders": [["m1", "m2", "chart"]], "placements": [],
    })
    store = LayoutDiagnostics(tmp_path)
    recorded_compute(problem, time.monotonic() + 1, Event(), store, "run", "session", "call")
    summary = store.get("run")["trace"]["inputSummary"]
    assert summary["orderSourceCounts"] == {"derived_geometry": 1}
    assert summary["requiredBeforeCount"] == 1
