"""Replay the UAT geometry without session credentials or business datasets."""
import json
import time
from pathlib import Path

import pytest

from app.dashboard_layout.regional import (
    _prioritized_window_requests,
    refine_regions,
)
from app.dashboard_layout.solver import (
    LayoutProblem,
    _row_seed,
    audit,
    layout_quality,
    preserves_reading_order,
    rank_candidates,
    solve,
)


def test_sparse_tail_windows_run_before_dense_prefix_windows():
    nodes = [
        {"id": f"m{i}", "kind": "metric", "shapes": [{"w": 6, "h": 2}]}
        for i in range(8)
    ] + [
        {"id": "tail-metric-a", "kind": "metric", "shapes": [{"w": 9, "h": 2}]},
        {"id": "tail-chart", "kind": "chart", "shapes": [{"w": 24, "h": 8}]},
        {"id": "tail-metric-b", "kind": "metric", "shapes": [{"w": 3, "h": 2}]},
        {"id": "tail-chart-b", "kind": "chart", "shapes": [{"w": 21, "h": 2}]},
    ]
    problem = LayoutProblem(
        version="constraint-v1", nodes=nodes,
        orders=[[node["id"] for node in nodes]],
        orderSources=["derived_geometry"],
    )
    placements = [
        {"id": f"m{i}", "x": i % 4 * 6, "y": i // 4 * 2,
         "w": 6, "h": 2, "variant": 0}
        for i in range(8)
    ] + [
        {"id": "tail-metric-a", "x": 0, "y": 4, "w": 9, "h": 2, "variant": 0},
        {"id": "tail-chart", "x": 0, "y": 6, "w": 24, "h": 8, "variant": 0},
        {"id": "tail-metric-b", "x": 0, "y": 14, "w": 3, "h": 2, "variant": 0},
        {"id": "tail-chart-b", "x": 3, "y": 14, "w": 21, "h": 2, "variant": 0},
    ]

    requests = _prioritized_window_requests(problem, placements)

    assert requests[0][:3] == (0, 8, 11)
    assert requests[0][3] == 30
    assert any(start == 0 for _, start, _, _ in requests)


def test_sparse_tail_is_repaired_instead_of_returned_as_the_final_candidate():
    prefix = [
        {"id": f"m{i}", "kind": "metric", "shapes": [{"w": 6, "h": 2}]}
        for i in range(8)
    ]
    tail = [
        {"id": "a", "kind": "metric", "shapes": [{"w": 9, "h": 2}, {"w": 3, "h": 6}]},
        {"id": "b", "kind": "chart", "shapes": [
            {"w": 24, "h": 8}, {"w": 21, "h": 8}, {"w": 13, "h": 12}]},
        {"id": "c", "kind": "metric", "shapes": [
            {"w": 3, "h": 2}, {"w": 3, "h": 6}, {"w": 8, "h": 2}]},
        {"id": "d", "kind": "chart", "shapes": [
            {"w": 21, "h": 8}, {"w": 18, "h": 8},
            {"w": 8, "h": 8}, {"w": 13, "h": 12}]},
        {"id": "e", "kind": "metric", "shapes": [{"w": 3, "h": 6}, {"w": 8, "h": 2}]},
    ]
    problem = LayoutProblem(
        version="constraint-v1", nodes=prefix + tail,
        orders=[[node["id"] for node in prefix + tail]],
        orderSources=["derived_geometry"],
    )
    placements = [
        {"id": f"m{i}", "x": i % 4 * 6, "y": i // 4 * 2,
         "w": 6, "h": 2, "variant": 0}
        for i in range(8)
    ] + [
        {"id": "a", "x": 0, "y": 4, "w": 9, "h": 2, "variant": 0},
        {"id": "b", "x": 0, "y": 6, "w": 24, "h": 8, "variant": 0},
        {"id": "c", "x": 0, "y": 14, "w": 3, "h": 2, "variant": 0},
        {"id": "d", "x": 3, "y": 14, "w": 21, "h": 8, "variant": 0},
        {"id": "e", "x": 0, "y": 16, "w": 3, "h": 6, "variant": 0},
    ]
    quality = layout_quality(problem, placements)
    assert quality["gapCells"] == quality["largestGapCells"] == 30

    result = refine_regions(problem, {
        "status": "feasible", "placements": placements,
        "alternatives": [{"placements": placements, "quality": quality}],
    }, deadline=time.monotonic() + 1.2)

    assert result["quality"]["gapCells"] == 6
    assert result["quality"]["largestGapCells"] == 6
    assert result["readingDiagnostics"]["windows"][0]["nodeIds"] == ["a", "b", "c"]


def captured():
    data = json.loads((Path(__file__).parent / "fixtures/layout/uat_mixed_reading.json").read_text())
    return LayoutProblem.model_validate(data["problem"]), data["placements"], data["tail"]


def stacked(rows, ids):
    changes = {
        ids[0]: {"x": 0, "y": 66, "w": 6, "h": 6},
        ids[1]: {"x": 0, "y": 72, "w": 6, "h": 6},
        ids[2]: {"x": 6, "y": 66, "w": 18, "h": 12},
    }
    return [dict(row, **changes.get(row["id"], {})) for row in rows]


def test_captured_tail_can_stack_without_changing_previous_business_regions():
    problem, rows, ids = captured()
    desired = stacked(rows, ids)
    assert audit(problem, desired)["valid"]
    assert layout_quality(problem, desired)["gapCells"] == 38
    assert [r for r in desired if r["id"] not in ids] == [r for r in rows if r["id"] not in ids]


def test_candidate_finishing_repairs_captured_metric_table_region():
    problem, rows, ids = captured()
    before = json.dumps(rows)
    best = rank_candidates(problem, [rows])[0]
    tail = {r["id"]: r for r in best.placements if r["id"] in ids}
    a, b, table = (tail[id] for id in ids)
    assert (a["x"], a["w"], a["h"]) == (b["x"], b["w"], b["h"])
    assert a["y"] + a["h"] == b["y"]
    assert a["y"] == table["y"]
    assert b["y"] + b["h"] == table["y"] + table["h"]
    assert a["w"] + table["w"] == 24
    assert best.quality["gapCells"] < layout_quality(problem, rows)["gapCells"]
    assert audit(problem, list(best.placements))["valid"]
    assert json.dumps(rows) == before


def test_unequal_metric_heights_are_not_reported_as_balanced():
    problem, rows, _ = captured()
    assert layout_quality(problem, rows)["metricRowImbalance"] > 0


def test_metric_finishing_can_fold_a_separate_metric_row_beside_the_table():
    problem, rows, ids = captured()
    changes = {ids[0]: {"x": 0, "y": 66, "w": 6, "h": 4},
               ids[1]: {"x": 6, "y": 66, "w": 6, "h": 4},
               ids[2]: {"x": 0, "y": 70, "w": 24, "h": 12}}
    rows = [dict(r, **changes.get(r["id"], {})) for r in rows]
    best = rank_candidates(problem, [rows])[0]
    tail = [r for r in best.placements if r["id"] in ids]
    assert max(r["y"] + r["h"] for r in tail) == 78
    assert tail[0]["x"] == tail[1]["x"]
    assert tail[0]["h"] == tail[1]["h"] == 6


def test_full_captured_solver_keeps_metric_peers_equal_and_fills_the_final_region():
    problem, _, ids = captured()
    result = solve(problem)
    assert audit(problem, result["placements"])["valid"]
    tail = {r["id"]: r for r in result["placements"] if r["id"] in ids}
    a, b, table = (tail[id] for id in ids)
    assert a["x"] == b["x"]
    assert a["w"] == b["w"]
    assert a["h"] == b["h"]
    assert b["y"] == a["y"] + a["h"]
    # A complete re-solve may also pair the metrics with the preceding chart
    # and put the table full-width below. Neither valid reading is hard-coded.
    bottom = max(r["y"] + r["h"] for r in result["placements"])
    occupied = sum(r["w"] * max(0, min(bottom, r["y"] + r["h"]) - max(a["y"], r["y"]))
                   for r in result["placements"])
    assert occupied == 24 * (bottom - a["y"])
    if table["y"] == a["y"]:
        assert table["x"] == a["x"] + a["w"]
        assert table["y"] + table["h"] == b["y"] + b["h"]
    else:
        assert table["y"] == b["y"] + b["h"]
        assert (table["x"], table["w"]) == (0, 24)


@pytest.mark.parametrize("width,height", [(6, 4), (8, 6), (12, 8)])
def test_independent_bands_may_use_different_reading_modes(width, height):
    rows = [
        {"id": "a", "x": 0, "y": 0, "w": 12, "h": 4},
        {"id": "b", "x": 12, "y": 0, "w": 12, "h": 8},
        {"id": "c", "x": 0, "y": 4, "w": 12, "h": 4},
        {"id": "d", "x": 0, "y": 8, "w": width, "h": height},
        {"id": "e", "x": 0, "y": 8+height, "w": width, "h": height},
        {"id": "f", "x": width, "y": 8, "w": 24-width, "h": 2*height},
    ]
    by_id = {r["id"]: r for r in rows}
    assert preserves_reading_order(list("abcdef"), by_id)
    assert not preserves_reading_order(list("abcedf"), by_id)
    assert not preserves_reading_order(list("defabc"), by_id)


def test_reading_modes_cannot_switch_through_a_spanning_card():
    rows = [
        {"id": "a", "x": 0, "y": 0, "w": 12, "h": 4},
        {"id": "b", "x": 12, "y": 0, "w": 12, "h": 12},
        {"id": "c", "x": 0, "y": 4, "w": 6, "h": 4},
        {"id": "d", "x": 0, "y": 8, "w": 6, "h": 4},
        {"id": "e", "x": 6, "y": 4, "w": 6, "h": 8},
    ]
    assert not preserves_reading_order(list("abcde"), {r["id"]: r for r in rows})


def test_cp_sat_and_audit_agree_on_mixed_independent_bands():
    specs = [("a", 12, 4, 0), ("b", 12, 8, 12), ("c", 12, 4, 0),
             ("d", 6, 6, 0), ("e", 6, 6, 0), ("f", 18, 12, 6)]
    problem = LayoutProblem(version="constraint-v1", budgetMs=2000,
        nodes=[{"id": id, "kind": "metric" if id in ("d", "e") else "chart",
                    "fixedX": x, "shapes": [{"w": w, "h": h}]} for id, w, h, x in specs],
        orders=[list("abcdef")])
    result = solve(problem)
    assert result["status"] == "feasible"
    assert audit(problem, result["placements"])["valid"]
    assert result["gapCells"] == 0
    assert max(r["y"]+r["h"] for r in result["placements"]) == 20


def test_row_seed_includes_two_small_cards_before_a_spanning_chart():
    problem = LayoutProblem(version="constraint-v1", nodes=[
        {"id": "a", "kind": "metric", "shapes": [{"w": 6, "h": 6}]},
        {"id": "b", "kind": "metric", "shapes": [{"w": 6, "h": 6}]},
        {"id": "c", "kind": "chart", "shapes": [{"w": 18, "h": 12}]},
    ], orders=[["a", "b", "c"]])
    seed = _row_seed(problem, time.monotonic()+1)
    assert seed is not None
    assert audit(problem, seed)["valid"]
    assert layout_quality(problem, seed)["gapCells"] == 0


def test_row_seed_does_not_drop_a_valid_stacked_suffix_due_to_a_sparse_prefix():
    problem = LayoutProblem(version="constraint-v1", nodes=[
        {"id": "chart", "kind": "chart", "shapes": [{"w": 16, "h": 15}]},
        {"id": "rank", "kind": "rank", "shapes": [{"w": 6, "h": 15}]},
        {"id": "a", "kind": "metric", "shapes": [{"w": 6, "h": 6}]},
        {"id": "b", "kind": "metric", "shapes": [{"w": 6, "h": 6}]},
        {"id": "c", "kind": "chart", "shapes": [{"w": 18, "h": 12}]},
    ], orders=[["chart", "rank", "a", "b", "c"]])
    seed = _row_seed(problem, time.monotonic()+1)
    assert seed is not None
    assert audit(problem, seed)["valid"]
    assert layout_quality(problem, seed)["gapCells"] == 30


@pytest.mark.parametrize("restriction", ["fixed", "missing-shape", "separate-order"])
def test_metric_finishing_never_bypasses_shape_position_or_scope_limits(restriction):
    problem, rows, ids = captured()
    if restriction == "fixed":
        next(n for n in problem.nodes if n.id == ids[1]).fixedX = 4
    elif restriction == "missing-shape":
        for n in problem.nodes:
            if n.id in ids[:2]:
                row = next(r for r in rows if r["id"] == n.id)
                n.shapes = [s for s in n.shapes if (s.w, s.h) == (row["w"], row["h"])]
    else:
        # Separate order lists do not authorize combining unrelated sequences.
        problem.orders = [problem.orders[0][:-3], ids[:1], ids[1:]]
    best = rank_candidates(problem, [rows])[0]
    assert audit(problem, list(best.placements))["valid"]
    assert [r for r in best.placements if r["id"] in ids] == [r for r in rows if r["id"] in ids]


@pytest.mark.parametrize("chart_first", [False, True])
def test_region_collapse_moves_following_band_without_reordering(chart_first):
    metrics = [{"id": id, "kind": "metric", "shapes": [{"w": 6, "h": 4}, {"w": 6, "h": 6}]}
               for id in ("a", "b")]
    chart = {"id": "table", "kind": "chart", "shapes": [{"w": 24, "h": 12}, {"w": 18, "h": 12}]}
    nodes = ([chart, *metrics] if chart_first else [*metrics, chart]) + [
        {"id": "next", "kind": "chart", "shapes": [{"w": 24, "h": 8}]}]
    problem = LayoutProblem(version="constraint-v1", nodes=nodes,
                            orders=[[n["id"] for n in nodes]])
    rows = [{"id": "a", "x": 0, "y": 12 if chart_first else 0, "w": 6, "h": 4, "variant": 0},
            {"id": "b", "x": 6, "y": 12 if chart_first else 0, "w": 6, "h": 4, "variant": 0},
            {"id": "table", "x": 0, "y": 0 if chart_first else 4, "w": 24, "h": 12, "variant": 0},
            {"id": "next", "x": 0, "y": 16, "w": 24, "h": 8, "variant": 0}]
    best = rank_candidates(problem, [rows])[0]
    by_id = {r["id"]: r for r in best.placements}
    assert best.quality["gapCells"] == 0
    assert by_id["next"]["y"] == 12
    assert by_id["a"]["x"] == by_id["b"]["x"] == (18 if chart_first else 0)
    assert by_id["a"]["y"] == 0 and by_id["b"]["y"] == 6
    assert audit(problem, list(best.placements))["valid"]


def test_odd_height_stack_tries_both_admitted_height_assignments():
    problem = LayoutProblem(version="constraint-v1", nodes=[
        {"id": "a", "kind": "metric", "shapes": [{"w": 6, "h": 4}]},
        {"id": "b", "kind": "metric", "shapes": [{"w": 6, "h": 5}]},
        {"id": "c", "kind": "chart", "shapes": [{"w": 12, "h": 9}, {"w": 18, "h": 9}]},
    ], orders=[["a", "b", "c"]])
    rows = [{"id": id, "x": x, "y": 0, "w": w, "h": h, "variant": 0}
            for id, x, w, h in [("a", 0, 6, 4), ("b", 6, 6, 5), ("c", 12, 12, 9)]]
    best = rank_candidates(problem, [rows])[0]
    assert best.quality["gapCells"] == 0
    assert [(r["y"], r["h"]) for r in best.placements[:2]] == [(0, 4), (4, 5)]


def test_organize_finishing_uses_the_same_height_priority_as_final_ranking():
    problem = LayoutProblem(version="constraint-v1", objective="organize-v1", nodes=[
        {"id": id, "kind": "metric", "shapes": [
            {"w": 6, "h": 4, "cost": 100}, {"w": 6, "h": 6}]} for id in ("a", "b")
    ] + [{"id": "c", "kind": "chart", "shapes": [
        {"w": 24, "h": 12}, {"w": 18, "h": 12}, {"w": 18, "h": 8, "cost": 100}]}],
        orders=[["a", "b", "c"]])
    rows = [{"id": "a", "x": 0, "y": 0, "w": 6, "h": 4, "variant": 0},
            {"id": "b", "x": 6, "y": 0, "w": 6, "h": 4, "variant": 0},
            {"id": "c", "x": 0, "y": 4, "w": 24, "h": 12, "variant": 0}]
    best = rank_candidates(problem, [rows])[0]
    assert best.quality["gapCells"] == 0
    assert best.quality["totalHeight"] == 8


def test_large_finishing_search_does_not_override_an_expired_solve_budget():
    nodes = []
    for i in range(24):
        nodes.extend({"id": f"{i}-{j}", "kind": "metric", "shapes": [
            {"w": 6, "h": h, "cost": 0 if h == 50 else 100} for h in range(1, 51)
        ]} for j in range(2))
        nodes.append({"id": f"{i}-c", "kind": "chart", "shapes": [
            {"w": 24, "h": 100}, *[{"w": 18, "h": h, "cost": 100} for h in range(2, 101)]
        ]})
    problem = LayoutProblem(version="constraint-v1", nodes=nodes,
                            orders=[[n["id"] for n in nodes]], budgetMs=1)
    started = time.monotonic()
    result = solve(problem)
    assert time.monotonic() - started < 3
    assert result["status"] == "feasible"
    assert audit(problem, result["placements"])["valid"]


def captured_metric_row_rank():
    data = json.loads((Path(__file__).parent / "fixtures/layout/uat_metric_row_rank.json").read_text())
    return LayoutProblem.model_validate(data["problem"]), data["original"]


def test_case037_expected_structure_preserves_order_but_lacks_metric_shapes():
    problem, rows = captured_metric_row_rank()
    desired = [dict(r) for r in rows]
    x = 0
    for row, width in zip(desired[:4], (4, 5, 5, 4)):
        row.update(x=x, w=width)
        x += width
    desired[4].update(x=0, y=3, w=18, h=12)
    desired[5].update(x=18, y=0, w=6, h=15)
    assert preserves_reading_order(problem.orders[0], {r["id"]: r for r in desired})
    assert all([(s.w, s.h) for s in node.shapes] == [(5, 3)] for node in problem.nodes[:4])
    assert audit(problem, desired) == {"valid": False, "reason": "shape"}


def test_case037_current_shapes_already_allow_a_lower_gap_chart_rank_pair():
    problem, rows = captured_metric_row_rank()
    better = [dict(r) for r in rows]
    better[4].update(w=18, h=15)
    better[5].update(x=18)
    assert audit(problem, better)["valid"]
    assert layout_quality(problem, rows)["gapCells"] == 186
    assert layout_quality(problem, better)["gapCells"] == 12
    # This verifies feasibility/ranking, not generation of the missing proposal.
    assert rank_candidates(problem, [rows, better])[0].quality["gapCells"] == 12
