from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.dependencies import get_identity, get_services
from app.dashboard_layout.routes import router


@pytest.mark.asyncio
async def test_run_is_durable_queryable_and_scoped(tmp_path):
    app = FastAPI()
    app.include_router(router)
    access = SimpleNamespace(require_session_owner=AsyncMock())
    services = SimpleNamespace(settings=SimpleNamespace(app_data_dir=tmp_path),
        workspace_access=access,
        deferred_frontend_tools=SimpleNamespace(pending_layout_call=AsyncMock(return_value=True)),
        frontend_tool_bridges=SimpleNamespace(active_for_thread=lambda _: None))
    app.dependency_overrides[get_identity] = lambda: "owner"
    app.dependency_overrides[get_services] = lambda: services
    body = dict(toolCallId="call", problem=dict(version="constraint-v1", budgetMs=1000,
        nodes=[dict(id="a", shapes=[dict(w=24, h=4)])]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = (await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)).json()
        run_id = result["layoutRunId"]
        url = f"/api/sessions/s/dashboardLayoutRuns/{run_id}"
        detail = await client.get(url)
        assert detail.headers["cache-control"] == "no-store"
        record = detail.json()
        assert record["problem"] == body["problem"] or record["problem"]["nodes"][0]["id"] == "a"
        assert record["result"]["status"] == "feasible"
        assert record["layoutAttempt"] == 1
        assert record["trace"]["counters"]
        assert record["frontendOutcome"] is None
        assert (await client.get(url.replace("/s/", "/other/"))).status_code == 404
        outcome = dict(toolCallId="call", status="error", persisted=False,
            executionMode="remote", changes=[dict(id="a", x=0, y=0, w=24, h=4)],
            changesTruncated=False, errorCode="PERSISTENCE_OUTCOME_UNKNOWN",
            firstIssueCode="PERSISTENCE_OUTCOME_UNKNOWN", writeDispatched=True,
            persistenceStage="receipt_validation", persistenceReason="receipt_incomplete")
        assert (await client.post(url + "/outcome", json=outcome)).status_code == 200
        saved_outcome = (await client.get(url)).json()["frontendOutcome"]
        assert saved_outcome["persisted"] is False
        assert saved_outcome["errorCode"] == "PERSISTENCE_OUTCOME_UNKNOWN"
        assert saved_outcome["firstIssueCode"] == "PERSISTENCE_OUTCOME_UNKNOWN"
        assert saved_outcome["writeDispatched"] is True
        assert saved_outcome["committed"] is None
        assert saved_outcome["persistenceStage"] == "receipt_validation"
        assert saved_outcome["persistenceReason"] == "receipt_incomplete"
        assert (await client.post(url + "/outcome", json=dict(outcome, toolCallId="wrong"))).status_code == 404
        assert (await client.post(url + "/outcome", json=dict(outcome, title="secret"))).status_code == 422
        for field, value in [
            ("errorCode", "x" * 65),
            ("errorCode", "PRIVATE_CUSTOMER_ACME"),
            ("firstIssueCode", "contains private text"),
            ("firstIssueCode", "PRIVATE_CUSTOMER_ACME"),
            ("writeDispatched", "true"),
            ("persistenceStage", "database_private_stage"),
            ("persistenceReason", "secret backend response"),
        ]:
            assert (await client.post(url + "/outcome", json=dict(outcome, **{field: value}))).status_code == 422
        assert (await client.post(url + "/outcome", json=dict(
            outcome, status="success", persisted=True))).status_code == 422
        clean_success = {"toolCallId": "call", "status": "success", "persisted": True}
        assert (await client.post(url + "/outcome", json=dict(
            clean_success, firstIssueCode="LAYOUT_OVERLAP"))).status_code == 422
        assert (await client.post(url + "/outcome", json=dict(
            clean_success, writeDispatched=False))).status_code == 422
        assert (await client.post(url + "/outcome", json=dict(
            outcome, persistenceStage="backend_response",
            persistenceReason="response_unconfirmed"))).status_code == 200
        preflight = dict(outcome, errorCode="EXECUTION_FAILED",
            firstIssueCode="GROUPING_INVALID_PROPOSAL", writeDispatched=False,
            persistenceStage=None, persistenceReason=None,
            preflightStage="proposal_validation",
            preflightError="GROUPING_INVALID_PROPOSAL")
        assert (await client.post(url + "/outcome", json=preflight)).status_code == 200
        saved_preflight = (await client.get(url)).json()["frontendOutcome"]
        assert saved_preflight["preflightStage"] == "proposal_validation"
        assert saved_preflight["preflightError"] == "GROUPING_INVALID_PROPOSAL"
        pending_preflight = dict(
            preflight,
            firstIssueCode="GROUPING_PENDING_WRITES_FAILED",
            preflightStage="pending_writes",
            preflightError="GROUPING_PENDING_WRITES_FAILED",
        )
        assert (await client.post(
            url + "/outcome", json=pending_preflight
        )).status_code == 200
        assert (await client.post(url + "/outcome", json=dict(
            preflight, writeDispatched=True))).status_code == 422
        assert (await client.post(url + "/outcome", json=dict(
            preflight, preflightStage=None))).status_code == 422
        committed = dict(outcome, errorCode="EXECUTION_FAILED",
            firstIssueCode="GROUPING_COMMITTED_CONFLICT", committed=True,
            persistenceReason="local_state_conflict")
        assert (await client.post(url + "/outcome", json=committed)).status_code == 200
        assert (await client.get(url)).json()["frontendOutcome"]["committed"] is True
        assert (await client.post(url + "/outcome", json=dict(
            committed, committed=False))).status_code == 422
        access.require_session_owner.side_effect = HTTPException(403)
        assert (await client.get(url)).status_code == 403
    assert list(tmp_path.rglob("*.sqlite3"))


@pytest.mark.asyncio
async def test_diagnostic_storage_failure_does_not_break_solve(tmp_path):
    # A file where the data directory should be forces a real storage error.
    blocked = tmp_path / "blocked"
    blocked.write_text("x")
    app = FastAPI()
    app.include_router(router)
    services = SimpleNamespace(settings=SimpleNamespace(app_data_dir=blocked),
        workspace_access=SimpleNamespace(require_session_owner=AsyncMock()),
        deferred_frontend_tools=SimpleNamespace(pending_layout_call=AsyncMock(return_value=True)),
        frontend_tool_bridges=SimpleNamespace(active_for_thread=lambda _: None))
    app.dependency_overrides[get_identity] = lambda: "owner"
    app.dependency_overrides[get_services] = lambda: services
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/sessions/s/dashboardLayoutSolve", json=dict(
            toolCallId="call", problem=dict(version="constraint-v1", budgetMs=1000,
            nodes=[dict(id="a", shapes=[dict(w=24, h=4)])])))
    assert response.status_code == 200
    assert response.json()["status"] == "feasible"
    assert response.json()["diagnosticsStored"] is False


def test_retention_and_process_restart(tmp_path, monkeypatch):
    import time

    from app.dashboard_layout.diagnostics import LayoutDiagnostics
    store = LayoutDiagnostics(tmp_path)
    monkeypatch.setattr("app.dashboard_layout.diagnostics.MAX_RUNS", 2)
    for i in range(3):
        assert store.save(dict(layoutRunId=str(i), sessionId="s", toolCallId="c",
            createdAt=time.time() + i, frontendOutcome=None))
    restarted = LayoutDiagnostics(tmp_path)
    assert restarted.get("0") is None
    assert restarted.get("2")["toolCallId"] == "c"
    assert len(restarted.list_runs("s", "c", 10)) == 2
    monkeypatch.setattr("app.dashboard_layout.diagnostics.RETENTION_SECONDS", -100)
    assert restarted.get("2") is None


def test_trace_does_not_change_solver_output_and_explains_rejections():
    from app.dashboard_layout.diagnostics import capture
    from app.dashboard_layout.solver import LayoutProblem, rank_candidates
    problem = LayoutProblem(version="constraint-v1", nodes=[dict(id="a", shapes=[dict(w=24, h=4)])])
    good = [dict(id="a", x=0, y=0, w=24, h=4, variant=0)]
    bad = [dict(id="a", x=0, y=0, w=24, h=5, variant=0)]
    baseline = rank_candidates(problem, [good, bad])
    with capture() as counters:
        observed = rank_candidates(problem, [good, bad])
    assert observed == baseline
    assert counters["ranking.rejected.shape"] > 0


@pytest.mark.asyncio
async def test_inspector_read_requires_operator_auth(tmp_path):
    import time

    from pydantic import SecretStr

    from app.dashboard_layout.diagnostics import LayoutDiagnostics
    from app.inspector.routes import router as inspector_router
    app = FastAPI()
    app.include_router(inspector_router)
    app.state.services = SimpleNamespace(settings=SimpleNamespace(app_data_dir=tmp_path,
        session_inspector_username="operator", session_inspector_password=SecretStr("secret")))
    run_id = "00000000-0000-4000-8000-000000000001"
    LayoutDiagnostics(tmp_path).save(dict(layoutRunId=run_id, sessionId="s", toolCallId="c",
        createdAt=time.time(), state="finished"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/inspector/dashboardLayoutRuns/{run_id}"
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, auth=("operator", "bad"))).status_code == 401
        response = await client.get(url, auth=("operator", "secret"))
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        listing = await client.get("/api/inspector/dashboardLayoutRuns?sessionId=s", auth=("operator", "secret"))
        assert listing.json()["items"][0]["layoutRunId"] == run_id


@pytest.mark.asyncio
async def test_compute_error_keeps_trace_id(tmp_path, monkeypatch):
    import time
    from threading import Event

    from app.dashboard_layout.diagnostics import LayoutDiagnostics
    from app.dashboard_layout.routes import recorded_compute
    from app.dashboard_layout.solver import LayoutProblem
    def fail(*args):
        raise RuntimeError("secret token")
    monkeypatch.setattr("app.dashboard_layout.routes.compute", fail)
    store = LayoutDiagnostics(tmp_path)
    result = recorded_compute(LayoutProblem(version="constraint-v1", nodes=[dict(id="a", shapes=[dict(w=24, h=4)])]),
        time.monotonic() + 10, Event(), store, "run", "s", "c")
    assert result["layoutRunId"] == "run"
    assert result["status"] == "compute_error"
    assert "secret token" not in str(store.get("run"))
