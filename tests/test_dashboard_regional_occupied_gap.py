"""Small audited score regressions, independent of screenshot templates/replays."""

import time

import pytest

from app.dashboard_layout import regional
from app.dashboard_layout.solver import LayoutProblem, audit


def row(id, x, y, w, h):
    return dict(id=id, x=x, y=y, w=w, h=h, variant=0)


def problem_for(rows, order, *, objective=None, sizes=None):
    return LayoutProblem.model_validate({
        "version": "constraint-v1",
        "objective": objective,
        "orderSources": ["derived_geometry"],
        "orders": [order],
        "nodes": [dict(
            id=r["id"], kind="metric" if r["id"] in ("a", "b") else "chart",
            shapes=[dict(w=w, h=h) for w, h in
                    (sizes[r["id"]] if sizes else [(r["w"], r["h"])])],
        ) for r in rows],
    })


def real_gap(rows):
    """Independent occupancy oracle for these tiny, single-scope fixtures."""
    bottom = max(r["y"] + r["h"] for r in rows)
    occupied = {(x, y) for r in rows
                for x in range(r["x"], r["x"] + r["w"])
                for y in range(r["y"], r["y"] + r["h"])}
    return 24 * bottom - len(occupied)


def evaluate(monkeypatch, problem, candidates):
    for candidate in candidates:
        report = audit(problem, candidate["placements"],
                       reading_orders=candidate["readingOrders"])
        assert report["valid"], report
        assert report["gapCells"] == real_gap(candidate["placements"])
    # Isolate production audit/score/selection from candidate generation.
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = regional.refine_regions(problem, {
        "status": "feasible", **candidates[0], "alternatives": candidates[1:],
    }, deadline=time.monotonic() + 1)
    diagnostics = result["readingDiagnostics"]
    assert diagnostics["selectedScoreStatus"] == "evaluated"
    fields = diagnostics["scorePolicy"]["fields"]
    assert len(fields) == len(diagnostics["selectedScore"])
    return result, dict(zip(fields, diagnostics["selectedScore"]))


def filled_metric_corner():
    rows = [row("a", 0, 0, 6, 6), row("b", 6, 0, 18, 3),
            row("c", 6, 3, 18, 3)]
    return problem_for(rows, ["a", "b", "c"]), rows


def test_metric_group_holes_exclude_chart_occupying_the_group_box():
    problem, rows = filled_metric_corner()
    assert audit(problem, rows)["valid"]
    assert real_gap(rows) == 0
    # c occupies all 54 cells missing from the metric-only rectangle.
    assert regional._metric_group_holes(problem, rows, problem.orders) == 0


def test_group_and_geoband_do_not_double_count_occupied_chart_as_108_gap(monkeypatch):
    problem, rows = filled_metric_corner()
    result, score = evaluate(monkeypatch, problem, [
        dict(placements=rows, readingOrders=problem.orders),
    ])
    assert result["quality"]["gapCells"] == real_gap(rows) == 0
    assert score["regionalMisalignedBlockCount"] == 0
    assert score["metricRegionGapCells"] == 0


def test_geoband_holes_exclude_chart_between_separate_metric_runs(monkeypatch):
    rows = [row("a", 0, 0, 6, 3), row("c", 6, 0, 12, 3),
            row("b", 18, 0, 6, 3)]
    problem = problem_for(rows, ["a", "c", "b"])
    assert regional._metric_group_holes(problem, rows, problem.orders) == 0
    _, score = evaluate(monkeypatch, problem, [
        dict(placements=rows, readingOrders=problem.orders),
    ])
    assert real_gap(rows) == 0
    assert score["metricRegionGapCells"] == 0


def test_changed_region_holes_exclude_unchanged_metric_filling_the_box(monkeypatch):
    rows = [row("c", 0, 0, 24, 3), row("a", 0, 3, 12, 3),
            row("b", 12, 3, 12, 3)]
    problem = problem_for(rows, ["a", "c", "b"])
    chosen = [["c", "a", "b"]]
    assert regional._metric_group_holes(problem, rows, chosen) == 0
    _, score = evaluate(monkeypatch, problem, [dict(placements=rows, readingOrders=chosen)])
    # The edited permutation c,a excludes b, but b occupies its entire 36-cell hole.
    assert real_gap(rows) == 0
    assert score["metricRegionGapCells"] == 0


@pytest.mark.parametrize("objective", [None, "organize-v1"])
@pytest.mark.parametrize("reverse", [False, True])
def test_real_external_alignment_beats_cohesion_at_equal_actual_gap(
    monkeypatch, objective, reverse
):
    aligned = [row("a", 0, 0, 6, 3), row("c", 6, 0, 6, 3),
               row("b", 12, 0, 6, 3), row("d", 0, 3, 18, 3)]
    stepped = [row("a", 0, 0, 12, 3), row("b", 12, 0, 12, 3),
               row("c", 0, 3, 24, 3), row("d", 0, 6, 12, 3)]
    problem = problem_for(aligned, ["a", "c", "b", "d"], objective=objective, sizes={
        "a": [(6, 3), (12, 3)], "b": [(6, 3), (12, 3)],
        "c": [(6, 3), (24, 3)], "d": [(12, 3), (18, 3)],
    })
    good = dict(placements=aligned, readingOrders=problem.orders)
    worse = dict(placements=stepped, readingOrders=[["a", "b", "c", "d"]])
    _, good_score = evaluate(monkeypatch, problem, [good])
    _, worse_score = evaluate(monkeypatch, problem, [worse])
    assert real_gap(aligned) == real_gap(stepped) == 36
    assert good_score["largestGapCells"] == worse_score["largestGapCells"] == 36
    assert good_score["shapeCost"] == worse_score["shapeCost"] == 0
    assert good_score["regionalMetricRowImbalance"] == worse_score["regionalMetricRowImbalance"] == 0
    assert good_score["regionalMisalignedBlockCount"] == 0
    assert worse_score["regionalMisalignedBlockCount"] == 1
    assert good_score["totalHeight"] == 6
    assert worse_score["totalHeight"] == 9
    candidates = [worse, good] if reverse else [good, worse]
    result, _ = evaluate(monkeypatch, problem, candidates)
    assert result["placements"] == aligned
    fields = result["readingDiagnostics"]["scorePolicy"]["fields"]
    assert fields.index("regionalMisalignedBlockCount") < fields.index("metricRegionGapCells")
    assert fields.index("regionalMisalignedBlockCount") < fields.index("regionalMetricRowImbalance")
    assert fields.index("negativeLocalMetricPairs") < fields.index("totalHeight")
