"""Browser callback adaptation must use the same strict envelope as Host routes."""

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from app.dashboard_layout.routes import READING_CONTRACT, SolveRequest, solver_revision
from app.dashboard_layout.solver import audit
from tests.browser.test_organize_pipeline import MemoryDiagnostics, compute_request, solve_request


def _payload():
    """Minimal geometry plus a transport-only preparation diagnostic."""
    return {"version": "constraint-v1", "objective": "organize-v1",
            "nodes": [{"id": "metric", "kind": "metric", "shapes": [{"w": 4, "h": 3}]}],
            "orders": [["metric"]], "orderSources": ["derived_geometry"],
            "requiredBefore": [], "budgetMs": 3000,
            "preparationDiagnostics": {"version": "preparation-v1", "events": [],
                                       "elapsedMs": 1, "truncated": False}}


def test_adapter_moves_only_preparation_diagnostics_into_route_envelope():
    """Retain diagnostic evidence without changing the browser's captured payload."""
    value = _payload()
    before = copy.deepcopy(value)
    request = solve_request(value)
    assert isinstance(request, SolveRequest)
    assert value == before
    assert request.preparationDiagnostics == before["preparationDiagnostics"]
    assert request.problem.orderSources == ["derived_geometry"]
    assert request.problem.objective == "organize-v1"
    assert request.problem.nodes[0].shapes[0].w == 4


def test_adapter_accepts_requests_without_optional_diagnostics():
    """Diagnostics remain optional as in the production route."""
    value = _payload()
    del value["preparationDiagnostics"]
    assert solve_request(value).preparationDiagnostics is None


@pytest.mark.parametrize("location", ["problem", "node"])
def test_adapter_rejects_unknown_fields_instead_of_silently_filtering(location):
    """Schema drift must surface at the test boundary, never disappear in an allowlist."""
    value = _payload()
    target = value if location == "problem" else value["nodes"][0]
    target["unknownGeometryField"] = True
    with pytest.raises(ValidationError) as failure:
        solve_request(value)
    assert any(error["type"] == "extra_forbidden" and
               error["loc"][-1] == "unknownGeometryField" for error in failure.value.errors())


def test_adapter_emits_real_route_identity_for_a_derived_reorder():
    """The actual frontend validator must accept the envelope and reject its legacy form."""
    value = _payload()
    value.update(objective=None, nodes=[
        dict(id=id, kind=kind, shapes=[dict(w=w, h=h)])
        for id, kind, w, h in [("m1", "metric", 12, 4), ("chart", "chart", 24, 8),
                               ("m2", "metric", 12, 4)]
    ], orders=[["m1", "chart", "m2"]], requiredBefore=[["m2", "chart"]])
    request = solve_request(value)
    store = MemoryDiagnostics()
    result = compute_request(request, store)
    assert result.get("readingContract") == READING_CONTRACT == "local-regions-v1"
    assert result.get("solverRevision") == solver_revision()
    assert result["diagnosticsStored"] is True
    record = store.records[result["layoutRunId"]]
    assert record["trace"]["readingContract"] == READING_CONTRACT
    assert record["trace"]["inputSummary"]["orderSourceCounts"] == {"derived_geometry": 1}
    assert record["preparationDiagnostics"] == request.preparationDiagnostics
    assert result["status"] == "feasible"
    assert result["readingOrders"] == [["m1", "m2", "chart"]] != request.problem.orders
    assert audit(request.problem, result["placements"], reading_orders=result["readingOrders"])["valid"]
    webapp = Path(os.environ.get("DAVINCI_ROOT", Path(__file__).resolve().parents[2] / "davinci")) / "webapp"
    # Only unrelated card identity is shimmed, as in the frontend geometry tests.
    script = r"""
const fs = require('fs'), assert = require('assert'), Module = require('module')
const originalLoad = Module._load
Module._load = function(id, ...args) {
  if (id.endsWith('/cardUid')) return { withEnsuredCardUid: widget => widget }
  return originalLoad.call(this, id, ...args)
}
const { validateSolvedLayoutCandidates } = require(process.cwd() +
  '/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardConstraintLayout')
const { problem, result } = JSON.parse(fs.readFileSync(0, 'utf8'))
const widgets = problem.nodes.map((node, order) => ({ id: node.id, name: node.id,
  type: 'chart', x: 0, y: order * 8, w: node.shapes[0].w, h: node.shapes[0].h, order,
  config: { chartType: node.kind === 'metric' ? 2001 : 4001, rootLayoutMode: 'rows' } }))
const accepted = validateSolvedLayoutCandidates(widgets, problem, result, 1440)
assert(accepted.candidates.length > 0)
assert.equal(accepted.readingContract, result.readingContract)
assert.equal(accepted.solverRevision, result.solverRevision)
assert(accepted.candidates.some(c => JSON.stringify(c.readingOrders) === JSON.stringify(result.readingOrders)))
const legacy = { ...result }; delete legacy.readingContract
assert.throws(() => validateSolvedLayoutCandidates(widgets, problem, legacy, 1440), /ORDER/)
assert.throws(() => validateSolvedLayoutCandidates(widgets,
  { ...problem, orderSources: ['explicit_request'] }, result, 1440), /ORDER/)
process.stdout.write(JSON.stringify({ accepted: true, legacyRejected: true, explicitOrderRetained: true }))
"""
    checked = subprocess.run(["node", "-r", "ts-node/register/transpile-only", "-r",
                              "tsconfig-paths/register", "-e", script], cwd=webapp,
                             env={**os.environ, "TS_NODE_COMPILER_OPTIONS": '{"module":"commonjs"}'},
                             input=json.dumps({"problem": request.problem.model_dump(exclude_none=True),
                                               "result": result}), text=True, capture_output=True,
                             timeout=30, check=False)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout) == {"accepted": True, "legacyRejected": True,
                                        "explicitOrderRetained": True}
