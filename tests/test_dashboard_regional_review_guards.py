"""Independent, lightweight review guards; no production-profile replay."""

import time
from threading import Event
from types import SimpleNamespace

import pytest

from app.dashboard_layout import regional, solver


@pytest.mark.parametrize("source", [None, "explicit_request", "explicit_saved",
                                   "derived_geometry", "derived_stamp"])
@pytest.mark.parametrize("objective", [None, "organize-v1"])
def test_review_score_field_values_match_selected_tuple(monkeypatch, source, objective):
    rows = [dict(id=id, x=x, y=y, w=w, h=h, variant=0) for id, x, y, w, h in [
        ("a", 0, 0, 6, 3), ("b", 6, 0, 8, 3),
        ("d", 0, 3, 14, 4), ("c", 14, 0, 10, 7),
    ]]
    derived = source in ("derived_geometry", "derived_stamp")
    chosen = [["a", "b", "d", "c"]]
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1", "objective": objective,
        "nodes": [dict(id=r["id"], kind="chart" if r["id"] == "c" else "metric",
                       shapes=[dict(w=r["w"], h=r["h"], cost=7)]) for r in rows],
        "orders": [["a", "c", "b", "d"]] if derived else chosen,
        "orderSources": [source] if source else None,
    })
    assert solver.audit(problem, rows, reading_orders=chosen)["valid"]
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    output = regional.refine_regions(problem, {
        "status": "feasible", "placements": rows, "readingOrders": chosen,
    }, deadline=time.monotonic() + 1)
    diagnostics = output["readingDiagnostics"]
    fields, values = diagnostics["scorePolicy"]["fields"], diagnostics["selectedScore"]
    assert values is not None
    assert len(fields) == len(values) == len(set(fields))
    q = output["quality"]
    expected = dict(regionalEmptyViolation=solver.regional_empty_violation(problem, rows),
                    gapCells=q["gapCells"], largestGapCells=q["largestGapCells"],
                    shapeCost=q["shapeCost"], totalHeight=q["totalHeight"])
    if objective:
        expected["organizeTotalHeight"] = q["totalHeight"]
    if derived:
        aligned, misaligned = regional._region_alignment(rows)
        balance = regional._metric_group_imbalance(problem, rows, chosen)
        expected.update(
            metricRegionGapCells=0,
            regionalMetricRowImbalance=balance,
            regionalMisalignedBlockCount=misaligned,
            qualityRegionalMetricRowImbalance=balance,
            negativeRegionalAlignedBoundaryCount=-aligned,
            metricGroupedHeightExcess=regional._metric_group_height_excess(problem, rows, chosen),
            metricNeighborhoodImbalance=regional._metric_neighbor_imbalance(problem, rows, chosen),
            negativeLocalMetricPairs=-3,
            firstMetricAnchorDisplacement=0, displacedNodeCount=3,
            readingDistance=4, readingSpan=3, metricShelfCount=2,
            crossBandMisalignedBoundaryCount=q["misalignedBlockCount"],
            negativeCrossBandAlignedBoundaryCount=-q["alignedBoundaryCount"],
            crossBandMetricRowImbalance=q["metricRowImbalance"],
        )
    else:
        expected.update(misalignedBlockCount=q["misalignedBlockCount"],
                        metricRowImbalance=q["metricRowImbalance"],
                        negativeAlignedBoundaryCount=-q["alignedBoundaryCount"])
        assert tuple(values) == (
            solver.regional_empty_violation(problem, rows),
            *solver._quality_score(problem, q),
        )
        assert output["readingOrders"] == problem.orders
    assert dict(zip(fields, values)) == expected


def test_review_metric_groups_do_not_cross_scope_or_explicit_orders():
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [dict(id=id, container=True, shapes=[dict(w=24, h=10)])
                  for id in ("p", "q")] + [
            dict(id=id, parentId=parent, kind="metric", shapes=[dict(w=12, h=3)])
            for id, parent in [("a", "p"), ("b", "p"), ("c", "q"), ("d", "q")]]
            + [dict(id="barrier", parentId="p", kind="chart", shapes=[dict(w=24, h=3)])],
        "orders": [["a", "barrier", "b"], ["c", "d"]],
        "orderSources": ["derived_geometry", "explicit_saved"],
    })
    assert regional._derived_metric_groups(problem, problem.orders) == []
    assert regional._derived_metric_groups(problem, [["a", "b", "barrier"], ["c", "d"]]) == [["a", "b"]]


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_review_stop_during_initial_retention_skips_remaining_incumbents(monkeypatch, reason):
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [dict(id="a", shapes=[dict(w=1, h=1)])],
        "orders": [["a"]], "orderSources": ["derived_geometry"],
    })
    plans = [[dict(id="a", x=x, y=0, w=1, h=1, variant=0)] for x in range(9)]
    event = Event()
    now = [0.0]
    monkeypatch.setattr(regional, "time", SimpleNamespace(monotonic=lambda: now[0]))
    real_quality = regional.layout_quality
    calls = []

    def quality(*args, **kwargs):
        calls.append(args[1])
        value = real_quality(*args, **kwargs)
        if reason == "deadline":
            now[0] = 2.0
        else:
            event.set()
        return value

    monkeypatch.setattr(regional, "layout_quality", quality)
    result = regional.refine_regions(problem, {
        "status": "feasible", "placements": plans[0],
        "alternatives": [dict(placements=plan) for plan in plans[1:]],
    }, deadline=1.0, cancelled=event)
    assert result["placements"] == plans[0]
    assert result["readingDiagnostics"]["stopReason"] == reason
    assert len(calls) == 1, "do not rescore eight more alternatives after observing stop"


def test_review_unusable_profile_cannot_change_metric_height_baseline():
    body = {
        "version": "constraint-v1", "orders": [["a", "b"]],
        "orderSources": ["derived_geometry"],
        "nodes": [
            dict(id="a", kind="metric", maxH=4, shapes=[dict(w=24, h=4)]),
            dict(id="b", kind="metric", shapes=[dict(w=24, h=h) for h in (3, 8, 12)]),
        ],
    }
    rows = [dict(id="a", x=0, y=0, w=24, h=4, variant=0),
            dict(id="b", x=0, y=4, w=24, h=8, variant=0)]
    original = solver.LayoutProblem.model_validate(body)
    body["nodes"][0]["shapes"].append(dict(w=24, h=12))
    with_unusable = solver.LayoutProblem.model_validate(body)
    assert solver.audit(original, rows)["valid"]
    assert solver.audit(with_unusable, rows)["valid"]
    expected = regional._metric_group_height_excess(original, rows, original.orders)
    assert expected == 4
    assert regional._metric_group_height_excess(with_unusable, rows, with_unusable.orders) == expected


@pytest.mark.parametrize("objective", [None, "organize-v1"])
def test_review_A_local_grouping_precedes_page_height_at_zero_gap(monkeypatch, objective):
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1", "objective": objective,
        "orders": [["a", "chart", "b"]], "orderSources": ["derived_geometry"],
        "nodes": [dict(id=id, kind="metric", shapes=[dict(w=24, h=2), dict(w=12, h=4)])
                  for id in ("a", "b")]
                  + [dict(id="chart", kind="chart", shapes=[dict(w=24, h=h) for h in (2, 4)])],
    })
    fragmented = [dict(id=id, x=0, y=i*2, w=24, h=2, variant=0)
                  for i, id in enumerate(("a", "chart", "b"))]
    grouped = [dict(id="a", x=0, y=0, w=12, h=4, variant=0),
               dict(id="b", x=12, y=0, w=12, h=4, variant=0),
               dict(id="chart", x=0, y=4, w=24, h=4, variant=0)]
    grouped_orders = [["a", "b", "chart"]]
    for rows, orders in [(fragmented, problem.orders), (grouped, grouped_orders)]:
        assert solver.audit(problem, rows, reading_orders=orders)["valid"]
        assert solver.layout_quality(problem, rows)["gapCells"] == 0
        assert regional._metric_group_holes(problem, rows, orders) == 0
        assert regional._metric_group_imbalance(problem, rows, orders) == 0
        assert regional._metric_neighbor_imbalance(problem, rows, orders) == 0
        assert regional._metric_group_height_excess(problem, rows, orders) == 0
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    output = regional.refine_regions(problem, {
        "status": "feasible", "placements": fragmented, "readingOrders": problem.orders,
        "alternatives": [dict(placements=grouped, readingOrders=grouped_orders)],
    }, deadline=time.monotonic() + 1)
    assert output["readingOrders"] == grouped_orders, output["readingDiagnostics"]


@pytest.mark.parametrize("invalid", [False, True])
def test_review_partial_span_normalization_requires_matching_tracks(invalid):
    rows = [dict(id=str(i), x=(i % 3)*4, y=(i // 3)*4, w=4, h=4)
            for i in range(7)]
    rows.append(dict(id="last", x=4, y=8, w=8, h=4))
    if invalid:
        rows[3]["w"] = 3
    normalized = regional._metric_cell_rows(rows)
    assert normalized[-1]["w"] == (8 if invalid else 4)
    assert rows[-1]["w"] == 8


def test_review_local_metric_pairs_stop_at_original_ten_node_window():
    ids = [str(i) for i in range(11)]
    problem = solver.LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [dict(id=id, kind="metric", shapes=[dict(w=24, h=1)]) for id in ids],
        "orders": [ids], "orderSources": ["derived_geometry"],
    })
    assert regional._local_metric_pairs(problem, problem.orders) == 54
