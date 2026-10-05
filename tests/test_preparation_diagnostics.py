import copy
import json
import sys
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.api.dependencies import get_identity, get_services
from app.dashboard_layout import routes
from app.dashboard_layout.diagnostics import LayoutDiagnostics, count
from app.dashboard_layout.solver import LayoutProblem


@pytest.fixture
def problem():
    return LayoutProblem(version="constraint-v1", nodes=[
        {"id": "parent", "container": True, "shapes": [{"w": 24, "h": 10, "variant": 7}]},
        {"id": "a", "parentId": "parent", "kind": "metric", "original": {"x": 0, "y": 0, "w": 6, "h": 4},
             "shapes": [{"w": 6, "h": 4, "parentVariant": 7}]},
    ], orders=[["a"]])


def event(**overrides):
    return dict({"nodeId": "a", "phase": "measurement", "reason": "measured", "inputCount": 2,
        "outputCount": 1, "sizes": [{"w": 6, "h": 4}], "truncated": False, "remainingMs": 100.5,
        "scopeWidth": 1600.5, "parentVariant": 7, "original": {"x": 0, "y": 0, "w": 6, "h": 4},
        "explicit": {"w": 6}}, **overrides)


def metadata(**overrides):
    return dict({"version": "preparation-v1", "elapsedMs": 12.5, "truncated": False,
        "events": [event()]}, **overrides)


def run_record(tmp_path, monkeypatch, problem, *args):
    def compute(geometry, deadline, cancelled):
        assert geometry is problem
        assert "preparationDiagnostics" not in geometry.model_dump()
        count("ranking.valid")
        return {"version": geometry.version, "status": "feasible", "quality": {"gapCells": 0},
                    "_diagnosticCounters": {"worker.calls": 1}}

    monkeypatch.setattr(routes, "compute", compute)
    store = LayoutDiagnostics(tmp_path)
    result = routes.recorded_compute(problem, time.monotonic() + 10, Event(), store,
                                     "run", "session", "call", *args)
    assert result["layoutRunId"] == "run"
    assert result["diagnosticsStored"] is True
    assert "_diagnosticCounters" not in result
    return store.get("run", "session")


def test_missing_metadata_keeps_legacy_compute_signature(tmp_path, monkeypatch, problem):
    record = run_record(tmp_path, monkeypatch, problem)
    assert record["preparationDiagnostics"] is None
    assert record["preparationDiagnosticsStatus"] == "missing"
    assert record["trace"]["counters"] == {"ranking.valid": 1, "worker.calls": 1}
    assert record["result"]["quality"] == {"gapCells": 0}
    assert record["solverRevision"]


def test_sidecar_sanitizes_privacy_and_keeps_same_run(tmp_path, monkeypatch, problem):
    raw = metadata(title="secret", clientRevision="secret", events=[event(
        title="secret", error="secret", sizes=[{"w": 6, "h": 4, "text": "secret"}],
        original={"x": 0, "y": 0, "w": 6, "h": 4, "sql": "secret"}, explicit={"w": 6, "text": "secret"})])
    before = copy.deepcopy(raw)
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert raw == before
    assert record["preparationDiagnostics"] == metadata()
    assert record["preparationDiagnosticsStatus"] == "captured"
    assert (record["layoutRunId"], record["sessionId"], record["toolCallId"]) == ("run", "session", "call")
    assert "secret" not in json.dumps(record)
    assert "preparationDiagnostics" not in record["problem"]
    store = LayoutDiagnostics(tmp_path)
    assert store.outcome("run", "session", {"toolCallId": "call", "status": "success", "persisted": True})
    assert store.get("run")["preparationDiagnostics"] == metadata()


@pytest.mark.parametrize("raw", [False, 1, "secret", [], {},
    metadata(version="secret"), metadata(events={"secret": 1})])
def test_invalid_envelope_never_reaches_compute(tmp_path, monkeypatch, problem, raw):
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert record["preparationDiagnosticsStatus"] == "invalid"
    assert record["preparationDiagnostics"] is None
    assert record["result"]["status"] == "feasible"


def test_bad_events_and_numbers_are_dropped_best_effort(tmp_path, monkeypatch, problem):
    raw = metadata(events=[event(), event(nodeId="unknown"), event(nodeId="x" * 257),
        event(nodeId=[]), event(phase="secret"), event(reason={}), "secret",
        event(inputCount=True, outputCount=1.5, remainingMs=float("inf"), scopeWidth=0,
              parentVariant="secret", sizes=[{"w": -1, "h": 4}, {"w": 1, "h": 10001}],
              original={"x": -1, "y": 0, "w": 6, "h": 4}, explicit={"w": False, "h": float("nan")}),
        event(inputCount=10000001, outputCount=-1, remainingMs=-1, scopeWidth=10001,
              parentVariant=8),
        event(inputCount=10000000, outputCount=0, remainingMs=10000000,
              scopeWidth=10000, sizes=[{"w": 0.5, "h": 10000}], explicit={"h": 2.5})])
    record = run_record(tmp_path, monkeypatch, problem, raw)
    clean = record["preparationDiagnostics"]
    assert record["preparationDiagnosticsStatus"] == "invalid"
    assert len(clean["events"]) == 5
    assert clean["events"][0] == event()
    assert "reason" not in clean["events"][1]
    bad = clean["events"][2]
    assert set(bad) == {"nodeId", "phase", "reason", "sizes", "truncated"}
    assert bad["sizes"] == []
    assert not ({"inputCount", "outputCount", "remainingMs", "scopeWidth", "parentVariant"}
                & clean["events"][3].keys())
    assert clean["events"][4]["inputCount"] == 10000000
    assert clean["events"][4]["explicit"] == {"h": 2.5}
    assert "secret" not in json.dumps(record, allow_nan=False)


@pytest.mark.parametrize("patch", [{"elapsedMs": -1}, {"elapsedMs": float("nan")},
    {"elapsedMs": 10000001}, {"elapsedMs": True}, {"truncated": "secret"}])
def test_bad_envelope_fields_keep_valid_events(tmp_path, monkeypatch, problem, patch):
    record = run_record(tmp_path, monkeypatch, problem, metadata(**patch))
    assert record["preparationDiagnosticsStatus"] == "invalid"
    assert record["preparationDiagnostics"]["events"] == [event()]
    json.dumps(record, allow_nan=False)


def test_caps_events_and_per_event_and_total_sizes(tmp_path, monkeypatch, problem):
    raw = metadata(events=[event(sizes=[{"w": 6, "h": 4}] * 40)] * 650)
    record = run_record(tmp_path, monkeypatch, problem, raw)
    clean = record["preparationDiagnostics"]
    assert record["preparationDiagnosticsStatus"] == "truncated"
    assert clean["truncated"] is True
    assert len(clean["events"]) == 600
    assert all(len(item["sizes"]) <= 32 for item in clean["events"])
    assert sum(len(item["sizes"]) for item in clean["events"]) == 4096
    assert all(item["truncated"] for item in clean["events"])
    assert len(json.dumps(record)) < 2 * 1024 * 1024


@pytest.mark.parametrize("raw", [metadata(truncated=True), metadata(events=[event(truncated=True)])])
def test_client_truncation_is_preserved(tmp_path, monkeypatch, problem, raw):
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert record["preparationDiagnosticsStatus"] == "truncated"
    assert record["preparationDiagnostics"]["truncated"] is True


def test_parent_variant_is_request_bound(tmp_path, monkeypatch, problem):
    record = run_record(tmp_path, monkeypatch, problem, metadata(events=[
        event(nodeId="parent", parentVariant=0), event(parentVariant=7),
        event(nodeId="parent", parentVariant=7), event(parentVariant="7"),
        event(parentVariant=True), event(parentVariant=-1), event(parentVariant=10001)]))
    events = record["preparationDiagnostics"]["events"]
    assert events[0]["parentVariant"] == 0
    assert events[1]["parentVariant"] == 7
    assert all("parentVariant" not in item for item in events[2:])


def test_preset_enums_are_optional_and_allowlisted(tmp_path, monkeypatch, problem):
    for mode in ("compact", "organize", "align", "reorder"):
        for sizing in ("content", "preserve", "auto"):
            record = run_record(tmp_path, monkeypatch, problem, metadata(mode=mode, sizing=sizing))
            assert record["preparationDiagnostics"] == metadata(mode=mode, sizing=sizing)
    record = run_record(tmp_path, monkeypatch, problem, metadata(mode="secret", sizing=["secret"]))
    assert "mode" not in record["preparationDiagnostics"]
    assert "sizing" not in record["preparationDiagnostics"]
    record = run_record(tmp_path, monkeypatch, problem, metadata(sizing="unsupported"))
    assert "sizing" not in record["preparationDiagnostics"]


def test_all_phases_reasons_and_final_admitted_counts(tmp_path, monkeypatch, problem):
    phases = ["original", "observation", "measurement", "optional_fallback", "filter_explicit",
        "filter_growth", "filter_readable", "filter_organize", "final", "rank_fallback",
        "safety_minimum", "verification_filter"]
    reasons = ["measured", "observed", "fallback", "unsupported_measurement", "content_not_ready",
        "no_data", "query_capacity", "content_overflow", "budget_exhausted", "measurement_timeout",
        "measurement_error", "invalid_sizes", "preserve", "explicit", "filtered", "unchanged",
        "static_profile", "isolated_preserve", "observation_error", "observation_timeout"]
    events = [event(phase=phase, reason=reason, inputCount=40, outputCount=35,
                    sizes=[{"w": 6, "h": 4}] * 32, truncated=True)
              for phase in phases for reason in reasons]
    record = run_record(tmp_path, monkeypatch, problem, metadata(events=events))
    saved = record["preparationDiagnostics"]["events"]
    assert len(saved) == len(events)
    assert {item["phase"] for item in saved} == set(phases)
    assert {item["reason"] for item in saved} == set(reasons)
    assert all(item["inputCount"] == 40 and item["outputCount"] == 35
               for item in saved if item["phase"] == "final")


def test_final_event_without_reason_is_preserved(tmp_path, monkeypatch, problem):
    final = event(phase="final", inputCount=40, outputCount=35)
    del final["reason"]
    raw = metadata(events=[final])
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert record["preparationDiagnosticsStatus"] == "captured"
    assert record["preparationDiagnostics"] == raw
    assert "reason" not in record["preparationDiagnostics"]["events"][0]


@pytest.mark.parametrize("reason", [None, 1, {}, [], "secret"])
def test_invalid_present_reason_is_removed_without_losing_event(tmp_path, monkeypatch, problem, reason):
    record = run_record(tmp_path, monkeypatch, problem, metadata(events=[event(reason=reason)]))
    expected = event()
    del expected["reason"]
    assert record["preparationDiagnosticsStatus"] == "invalid"
    assert record["preparationDiagnostics"]["events"] == [expected]
    assert "secret" not in json.dumps(record)


@pytest.mark.parametrize("field, maximum", [
    ("minimumPixels", 100000), ("minimumGrid", 10000), ("growthFloor", 10000),
])
def test_threshold_pairs_are_bounded_and_private(tmp_path, monkeypatch, problem, field, maximum):
    raw = metadata(events=[event(**{field: {"w": 12.5, "h": maximum, "text": "secret",
                                          "nested": {"sql": "secret"}}})])
    before = copy.deepcopy(raw)
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert record["preparationDiagnosticsStatus"] == "captured"
    assert record["preparationDiagnostics"]["events"][0][field] == {"w": 12.5, "h": maximum}
    assert raw == before
    assert "secret" not in json.dumps(record)


@pytest.mark.parametrize("field, maximum", [
    ("minimumPixels", 100000), ("minimumGrid", 10000), ("growthFloor", 10000),
])
def test_malformed_threshold_pairs_preserve_other_evidence(tmp_path, monkeypatch, problem, field, maximum):
    bad_pairs = [None, [], "secret", {}, {"w": 1}, {"h": 1}]
    for value in (True, False, "1", -1, 0, maximum + 0.5, float("nan"), float("inf")):
        bad_pairs.extend([{"w": value, "h": 1}, {"w": 1, "h": value}])
    raw = metadata(events=[event(**{field: pair}) for pair in bad_pairs])
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert record["preparationDiagnosticsStatus"] == "invalid"
    assert record["preparationDiagnostics"]["events"] == [event()] * len(bad_pairs)
    assert record["result"]["status"] == "feasible"
    assert "secret" not in json.dumps(record, allow_nan=False)


def test_threshold_and_verification_events_leave_problem_authoritative(tmp_path, monkeypatch, problem):
    geometry = problem.model_dump(mode="json")
    thresholds = {"minimumPixels": {"w": 1600.25, "h": 100000},
                  "minimumGrid": {"w": 8, "h": 6}, "growthFloor": {"w": 10, "h": 8}}
    raw = metadata(events=[
        event(phase="safety_minimum", reason="filtered", **thresholds),
        event(phase="verification_filter", reason="content_overflow", inputCount=40,
              outputCount=35, sizes=[{"w": 10, "h": 8}] * 40, **thresholds),
    ])
    record = run_record(tmp_path, monkeypatch, problem, raw)
    assert record["preparationDiagnosticsStatus"] == "truncated"
    saved = record["preparationDiagnostics"]["events"]
    assert saved[0] == raw["events"][0]
    assert saved[1]["phase"] == "verification_filter"
    assert saved[1]["reason"] == "content_overflow"
    assert saved[1]["outputCount"] == 35
    assert len(saved[1]["sizes"]) == 32
    assert all(saved[1][key] == value for key, value in thresholds.items())
    assert record["problem"] == geometry == problem.model_dump(mode="json")


def test_recorded_compute_adds_bounded_timing_and_input_summary(tmp_path, monkeypatch, problem):
    record = run_record(tmp_path, monkeypatch, problem)
    summary = record["trace"]["inputSummary"]
    assert summary == {"nodeCount": 2, "shapeCount": 2, "rootCount": 1, "containerCount": 1,
        "scopeCount": 2, "orderCount": 1, "orderedNodeCount": 1, "originalCount": 1,
        "kindCounts": {"metric": 1, "chart": 0, "rank": 0, "other": 1},
        "budgetMs": 120000, "objective": None}
    timings = record["trace"]["stageTimingsMs"]
    assert set(timings) == {"preparation", "initialSave", "compute", "totalBeforeFinalSave"}
    assert all(type(value) in (int, float) and 0 <= value <= 10000000 for value in timings.values())
    assert timings["totalBeforeFinalSave"] >= timings["compute"]


@pytest.mark.parametrize("source", ["quality", "alternatives"])
def test_existing_result_quality_is_persisted_without_derived_score(tmp_path, monkeypatch, problem, source):
    quality = {"gapCells": 1, "totalHeight": 7}
    result = {"version": problem.version, "status": "feasible"}
    if source == "quality":
        result["quality"] = quality
        result["alternatives"] = [{"quality": dict(quality, gapCells=99)}]
    else:
        result["alternatives"] = [{"quality": quality}, {"quality": dict(quality, gapCells=99)}]
    before = copy.deepcopy(result)
    monkeypatch.setattr(routes, "compute", lambda *args: result)

    store = LayoutDiagnostics(tmp_path)
    response = routes.recorded_compute(problem, time.monotonic() + 10, Event(), store, "run", "s", "c")
    record = store.get("run")
    assert "finalScore" not in record["trace"]
    assert record["result"] == before == result
    assert response == dict(before, layoutRunId="run", diagnosticsStored=True)


def test_error_keeps_sanitized_sidecar_and_bounded_timings(tmp_path, monkeypatch, problem):
    clock = [0.0]

    def fail(*args):
        clock[0] = 1e10
        raise ValueError("secret")

    monkeypatch.setattr(routes, "compute", fail)
    monkeypatch.setattr(routes.time, "monotonic", lambda: clock[0])
    store = LayoutDiagnostics(tmp_path)
    result = routes.recorded_compute(problem, 10, Event(), store, "run", "s", "c", metadata())
    record = store.get("run")
    assert result["status"] == "compute_error"
    assert record["preparationDiagnostics"] == metadata()
    assert record["preparationDiagnosticsStatus"] == "captured"
    assert record["trace"]["stageTimingsMs"]["compute"] == 10000000
    assert record["trace"]["stageTimingsMs"]["totalBeforeFinalSave"] == 10000000
    assert "secret" not in json.dumps(record)


def test_worker_subprocess_receives_only_geometry(tmp_path, monkeypatch):
    monkeypatch.setenv("DAVINCI_LAYOUT_PYTHON", sys.executable)
    problem = LayoutProblem(version="constraint-v1", budgetMs=1000,
        nodes=[{"id": "a", "shapes": [{"w": 24, "h": 4}]}])
    store = LayoutDiagnostics(tmp_path)
    raw = metadata(events=[event(parentVariant=0)])
    result = routes.recorded_compute(problem, time.monotonic() + 10, Event(), store,
                                    "worker-run", "s", "c", raw)
    record = store.get("worker-run")
    # The real worker validates a strict LayoutProblem, rejecting any sidecar leak.
    assert result["status"] == "feasible"
    assert record["preparationDiagnostics"] == raw
    assert record["trace"]["counters"]["solve.compact"] == 1
    assert "preparationDiagnostics" not in record["problem"]


@pytest.mark.parametrize("patch", [{"inputCount": "2"}, {"outputCount": {}},
    {"sizes": None}, {"sizes": [False, None, {}, {"w": True, "h": 1}]},
    {"original": []}, {"explicit": "secret"}, {"parentVariant": []},
    {"scopeWidth": True}, {"remainingMs": 10000001}, {"truncated": "secret"}])
def test_malformed_optional_event_fields_are_tolerated(tmp_path, monkeypatch, problem, patch):
    record = run_record(tmp_path, monkeypatch, problem, metadata(events=[event(**patch)]))
    assert record["preparationDiagnosticsStatus"] == "invalid"
    assert record["preparationDiagnostics"]["events"][0]["nodeId"] == "a"
    assert "secret" not in json.dumps(record)


def test_exact_event_and_size_limits_do_not_mark_truncated(tmp_path, monkeypatch, problem):
    events = [event(sizes=[{"w": 6, "h": 4}] * 32) for _ in range(128)]
    events += [event(sizes=[]) for _ in range(472)]
    record = run_record(tmp_path, monkeypatch, problem, metadata(events=events))
    assert record["preparationDiagnosticsStatus"] == "captured"
    assert record["preparationDiagnostics"] == metadata(events=events)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, status", [(None, "missing"), ("secret", "invalid"),
    (["secret"], "invalid"), (metadata(events=[event(parentVariant=0)]), "captured"),
    (metadata(events=[None, event(parentVariant=0)]), "invalid"),
    (metadata(truncated=True, events=[event(parentVariant=0)]), "truncated")])
async def test_http_accepts_raw_sidecar_and_compute_only_gets_geometry(tmp_path, monkeypatch, raw, status):
    app = FastAPI()
    app.include_router(routes.router)
    services = SimpleNamespace(settings=SimpleNamespace(app_data_dir=tmp_path),
        workspace_access=SimpleNamespace(require_session_owner=AsyncMock()),
        deferred_frontend_tools=SimpleNamespace(pending_layout_call=AsyncMock(return_value=True)),
        frontend_tool_bridges=SimpleNamespace(active_for_thread=lambda _: None))
    app.dependency_overrides[get_identity] = lambda: "owner"
    app.dependency_overrides[get_services] = lambda: services
    seen = []

    def compute(geometry, deadline, cancelled):
        seen.append(geometry.model_dump())
        return {"version": geometry.version, "status": "feasible"}

    monkeypatch.setattr(routes, "compute", compute)
    body = {"toolCallId": "call", "problem": {"version": "constraint-v1",
        "nodes": [{"id": "a", "shapes": [{"w": 6, "h": 4}]}]}, "preparationDiagnostics": raw}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/sessions/s/dashboardLayoutSolve", json=body)
        assert response.status_code == 200
        result = response.json()
        detail = (await client.get(f"/api/sessions/s/dashboardLayoutRuns/{result['layoutRunId']}")).json()
    assert len(seen) == 1
    assert "preparationDiagnostics" not in seen[0]
    assert detail["layoutRunId"] == result["layoutRunId"]
    assert detail["preparationDiagnosticsStatus"] == status
    assert "secret" not in json.dumps(detail)
