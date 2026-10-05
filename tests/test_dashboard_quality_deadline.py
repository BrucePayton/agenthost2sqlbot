import copy
from types import SimpleNamespace

import pytest

from app.dashboard_layout import regional, solver


class StopDuringWork:
    def __init__(self, reason, after=10):
        self.reason = reason
        self.after = after
        self.armed = False
        self.checks = 0
        self.stopped = False

    def tick(self):
        if self.armed:
            self.checks += 1
            self.stopped |= self.checks >= self.after

    def monotonic(self):
        if self.reason == "deadline":
            self.tick()
        return 2.0 if self.stopped and self.reason == "deadline" else 0.0

    def is_set(self):
        if self.reason == "cancelled":
            self.tick()
        return self.stopped and self.reason == "cancelled"


def sparse_problem():
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": str(i), "shapes": [{"w": 1, "h": 100}]}
            for i in range(100)
        ],
        "orders": [[str(i) for i in range(100)]],
    })
    rows = [dict(id=str(i), x=0, y=i * 100, w=1, h=100, variant=0)
            for i in range(100)]
    return problem, rows


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_gap_flood_can_stop_inside_one_large_component(monkeypatch, reason):
    stop = StopDuringWork(reason)
    stop.armed = True
    monkeypatch.setattr(solver, "time", SimpleNamespace(monotonic=stop.monotonic))
    # One late card leaves a large connected component above and beside it.
    rows = [dict(x=0, y=9900, w=1, h=100)]
    with pytest.raises(InterruptedError, match=reason):
        solver._gaps(rows, deadline=1.0, cancelled=stop)
    assert stop.checks == stop.after


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
@pytest.mark.parametrize("exact", [False, True])
def test_quality_stopped_at_entry_never_starts_gap_work(monkeypatch, reason, exact):
    problem, rows = sparse_problem()
    stop = StopDuringWork(reason, after=1)
    stop.armed = True
    monkeypatch.setattr(solver, "time", SimpleNamespace(monotonic=stop.monotonic))

    def forbidden(*args, **kwargs):
        pytest.fail("stopped quality must not enter the gap scan")

    monkeypatch.setattr(solver, "_gaps", forbidden)
    with pytest.raises(InterruptedError, match=reason):
        solver.layout_quality(problem, rows, include_gap_components=exact,
                              deadline=1.0, cancelled=stop)


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
@pytest.mark.parametrize("exact_index", [1, 2])
def test_regional_interrupted_exact_quality_retains_complete_incumbent(
    monkeypatch, reason, exact_index
):
    problem, rows = sparse_problem()
    alternative = [dict(row, x=1) for row in rows]
    result = {
        "status": "feasible",
        "placements": rows,
        "readingOrders": problem.orders,
        "quality": solver.layout_quality(problem, rows),
        "alternatives": [{
            "placements": alternative,
            "readingOrders": problem.orders,
            "quality": solver.layout_quality(problem, alternative),
        }],
    }
    original = copy.deepcopy(result)
    stop = StopDuringWork(reason)
    clock = SimpleNamespace(monotonic=stop.monotonic)
    monkeypatch.setattr(solver, "time", clock)
    monkeypatch.setattr(regional, "time", clock)
    real_gaps = solver._gaps
    scans = []

    def gaps(*args, **kwargs):
        scans.append(kwargs)
        if len(scans) == exact_index:
            stop.armed = True
        return real_gaps(*args, **kwargs)

    monkeypatch.setattr(solver, "_gaps", gaps)
    output = regional.refine_regions(problem, result, deadline=1.0, cancelled=stop)
    diagnostics = output["readingDiagnostics"]
    assert diagnostics["stopReason"] == reason
    assert diagnostics["selectedScoreStatus"] == "baseline_retained_without_rescoring"
    assert diagnostics["selectedScore"] is None
    assert output["placements"] == original["placements"]
    assert output["readingOrders"] == original["readingOrders"]
    assert output["quality"] == original["quality"]
    assert output["alternatives"] == original["alternatives"]
    assert result == original
    assert len(scans) == exact_index
    assert scans[-1] == {"deadline": 1.0, "cancelled": stop}
    assert solver.audit(problem, output["placements"], include_gap_components=False)["valid"]


def test_quality_optional_budget_preserves_exact_results_and_completed(monkeypatch):
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [{"id": "a", "shapes": [{"w": 12, "h": 4}]}],
        "orders": [["a"]],
    })
    rows = [dict(id="a", x=0, y=0, w=12, h=4, variant=0)]
    monkeypatch.setattr(solver, "time", SimpleNamespace(monotonic=lambda: 0.0))
    monkeypatch.setattr(regional, "time", SimpleNamespace(monotonic=lambda: 0.0))
    expected = solver.layout_quality(problem, rows)
    assert expected["gapCells"] == expected["largestGapCells"] == 48
    assert solver._gaps(rows, deadline=1.0) == solver._gaps(rows)
    assert solver.layout_quality(problem, rows, deadline=1.0) == expected
    output = regional.refine_regions(
        problem, dict(status="feasible", placements=rows), deadline=1.0
    )
    assert output["quality"] == expected
    assert output["readingDiagnostics"]["stopReason"] == "completed"
    assert output["readingDiagnostics"]["selectedScoreStatus"] == "evaluated"


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
@pytest.mark.parametrize("phase", ["_alignment_quality", "_shape_cost"])
def test_quality_does_not_return_scores_after_late_interruption(monkeypatch, reason, phase):
    problem, rows = sparse_problem()
    stop = StopDuringWork(reason, after=1)
    monkeypatch.setattr(solver, "time", SimpleNamespace(monotonic=stop.monotonic))
    real = getattr(solver, phase)

    def interrupt(*args, **kwargs):
        value = real(*args, **kwargs)
        stop.armed = True
        return value

    monkeypatch.setattr(solver, phase, interrupt)
    with pytest.raises(solver.QualityInterrupted, match=reason):
        solver.layout_quality(problem, rows, include_gap_components=False,
                              deadline=1.0, cancelled=stop)


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_regional_checks_stop_after_last_exact_candidate_score(monkeypatch, reason):
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [{"id": "a", "shapes": [{"w": 24, "h": 4}]}],
        "orders": [["a"]],
    })
    rows = [dict(id="a", x=0, y=0, w=24, h=4, variant=0)]
    stop = StopDuringWork(reason, after=1)
    clock = SimpleNamespace(monotonic=stop.monotonic)
    monkeypatch.setattr(solver, "time", clock)
    monkeypatch.setattr(regional, "time", clock)
    exact_finished = False
    real_quality = regional.layout_quality
    real_score = regional._quality_score

    def quality(*args, **kwargs):
        nonlocal exact_finished
        value = real_quality(*args, **kwargs)
        exact_finished = kwargs.get("include_gap_components", True)
        return value

    def score(*args, **kwargs):
        value = real_score(*args, **kwargs)
        if exact_finished:
            stop.armed = True
        return value

    monkeypatch.setattr(regional, "layout_quality", quality)
    monkeypatch.setattr(regional, "_quality_score", score)
    output = regional.refine_regions(
        problem, dict(status="feasible", placements=rows),
        deadline=1.0, cancelled=stop,
    )
    assert output["placements"] == rows
    assert "quality" not in output
    assert output["readingDiagnostics"]["stopReason"] == reason
    assert output["readingDiagnostics"]["selectedScore"] is None
    assert output["readingDiagnostics"]["selectedScoreStatus"] == "baseline_retained_without_rescoring"
