import time
import json
from pathlib import Path
from threading import Event

import pytest

from app.dashboard_layout.diagnostics import capture
from app.dashboard_layout.solver import LayoutProblem, audit


def example(growth=False, suffix=True):
    nodes = [{"id": f"m{i}", "kind": "metric", "shapes":
              [{"w": w, "h": 3}] + ([{"w": 5, "h": h} for h in (3, 4)] if growth else [])}
             for i, w in enumerate((4, 5, 5, 4))]
    nodes += [
        {"id": "rank", "kind": "rank", "shapes": [{"w": 6, "h": 15}]},
        {"id": "chart", "kind": "chart", "shapes": [
            {"w": 24, "h": 8}, {"w": 13, "h": 15}]},
    ]
    rows = [dict(id=f"m{i}", x=x, y=0, w=w, h=3, variant=0)
            for i, (x, w) in enumerate(zip((0, 4, 9, 14), (4, 5, 5, 4)))]
    rows += [dict(id="rank", x=18, y=0, w=6, h=15, variant=0),
             dict(id="chart", x=0, y=15, w=24, h=8, variant=0)]
    if suffix:
        for i in range(34):
            nodes.append({"id": f"tail{i}", "shapes": [{"w": 24, "h": 4}]})
            rows.append(dict(id=f"tail{i}", x=0, y=23 + 4*i, w=24, h=4, variant=0))
    return LayoutProblem.model_validate({"version": "constraint-v1", "nodes": nodes,
                                         "orders": [[n["id"] for n in nodes]]}), rows


@pytest.mark.parametrize("growth,expected_gap", [(False, 21), (True, 0)])
def test_reflows_metric_column_rank_chart_and_keeps_complete_suffix(growth, expected_gap):
    from app.dashboard_layout.columns import refine_column_regions
    problem, rows = example(growth)
    with capture() as trace:
        result = refine_column_regions(problem, rows, deadline=time.monotonic() + 2)
    checked = audit(problem, result)
    assert checked["valid"]
    assert checked["gapCells"] == expected_gap
    by_id = {r["id"]: r for r in result}
    assert [by_id[f"m{i}"]["x"] for i in range(4)] == [0] * 4
    assert by_id["rank"]["x"] == 5
    assert by_id["chart"]["x"] == 11
    assert by_id["chart"]["h"] == 15
    assert by_id["tail0"]["y"] == 15
    assert trace["columns.accepted"] >= 1


@pytest.mark.parametrize("cancelled,expired", [(True, False), (False, True)])
def test_stopped_search_preserves_valid_input(cancelled, expired):
    from app.dashboard_layout.columns import refine_column_regions
    problem, rows = example()
    stop = Event()
    if cancelled:
        stop.set()
    assert refine_column_regions(problem, rows, deadline=time.monotonic() + (-1 if expired else 1),
                                 cancelled=stop) == rows


def test_never_overrides_explicit_x_lock():
    from app.dashboard_layout.columns import refine_column_regions
    problem, rows = example()
    problem.nodes[4].fixedX = 18
    result = refine_column_regions(problem, rows, deadline=time.monotonic() + 1)
    assert audit(problem, result)["valid"]
    assert next(r for r in result if r["id"] == "rank")["x"] == 18


def test_existing_zero_gap_plan_is_unchanged():
    from app.dashboard_layout.columns import refine_column_regions
    problem = LayoutProblem.model_validate({"version": "constraint-v1", "nodes": [
        {"id": "a", "shapes": [{"w": 12, "h": 8}]},
        {"id": "b", "shapes": [{"w": 12, "h": 8}]},
    ], "orders": [["a", "b"]]})
    rows = [dict(id=id, x=x, y=0, w=12, h=8, variant=0) for id, x in (("a", 0), ("b", 12))]
    assert refine_column_regions(problem, rows, deadline=time.monotonic() + 1) == rows


def recorded_case():
    fixture = json.loads((Path(__file__).parent / "fixtures/layout_case044_geometry.json").read_text())
    data = fixture["problem"]
    data["nodes"] = [dict({k: v for k, v in node.items() if k != "shapeSet"},
                          shapes=fixture["shapeSets"][node["shapeSet"]]) for node in data["nodes"]]
    return LayoutProblem.model_validate(data), fixture["placements"]


def test_recorded_case044_improves_without_inventing_missing_metric_sizes():
    from app.dashboard_layout.columns import refine_column_regions
    problem, rows = recorded_case()
    assert audit(problem, rows)["gapCells"] == 292
    with capture() as trace:
        result = refine_column_regions(problem, rows, deadline=time.monotonic() + 1.5)
    checked = audit(problem, result)
    assert checked["valid"]
    assert checked["gapCells"] <= 97
    by_id = {r["id"]: r for r in result}
    assert [by_id[id]["x"] for id in problem.orders[0][:4]] == [0] * 4
    assert trace["columns.gap_cells_removed"] >= 195


def test_solver_routes_large_candidates_through_region_refinement(monkeypatch):
    from app.dashboard_layout import cp_sat, incumbent
    problem, rows = example()
    monkeypatch.setattr(incumbent, "build_complete_incumbent", lambda _: rows)
    monkeypatch.setattr(cp_sat, "_batched_cp_sat_incumbent", lambda *a, **kw: (rows, 7))
    result = cp_sat.solve_cp_sat(problem, deadline=time.monotonic() + 2)
    assert result["gapCells"] == 21
    assert result["seedMethod"] == "ordered_column_regions"


def test_prunes_shapes_above_node_maximum_before_dominance():
    from app.dashboard_layout.columns import refine_column_regions
    problem = LayoutProblem.model_validate({"version": "constraint-v1", "nodes": [
        {"id": "a", "maxH": 2, "shapes": [{"w": 12, "h": 2}, {"w": 12, "h": 3}]},
        {"id": "b", "shapes": [{"w": 12, "h": 3}]},
    ], "orders": [["a", "b"]]})
    rows = [dict(id="a", x=0, y=0, w=12, h=2, variant=0),
            dict(id="b", x=0, y=2, w=12, h=3, variant=0)]
    result = refine_column_regions(problem, rows, deadline=time.monotonic() + 1)
    assert audit(problem, result)["gapCells"] == 12


def test_cancellation_during_refinement_is_not_reported_as_success(monkeypatch):
    from app.dashboard_layout import cp_sat, columns, incumbent
    problem, rows = example()
    stop = Event()
    monkeypatch.setattr(incumbent, "build_complete_incumbent", lambda _: rows)
    monkeypatch.setattr(cp_sat, "_batched_cp_sat_incumbent", lambda *a, **kw: (rows, 7))

    def cancel(*args, **kwargs):
        stop.set()
        return rows

    monkeypatch.setattr(columns, "refine_column_regions", cancel)
    assert cp_sat.solve_cp_sat(problem, deadline=time.monotonic() + 2, cancelled=stop)["status"] == "cancelled"


def test_refinement_audit_keeps_hard_checks_without_expensive_empty_cell_flood(monkeypatch):
    from app.dashboard_layout import solver
    from app.dashboard_layout.columns import refine_column_regions
    problem, rows = example()
    expected = audit(problem, rows)["gapCells"]

    def unexpected_flood(*args):
        raise AssertionError("Refinement only needs the gap count, not connected empty cells")

    monkeypatch.setattr(solver, "_gaps", unexpected_flood)
    assert audit(problem, rows, include_gap_components=False)["gapCells"] == expected
    improved = refine_column_regions(problem, rows, deadline=time.monotonic() + 1)
    assert audit(problem, improved, include_gap_components=False)["gapCells"] == 21
    overlapping = [dict(r) for r in rows]
    overlapping[1]["x"] = 0
    unordered = problem.model_copy(update={"orders": []})
    assert audit(unordered, overlapping, include_gap_components=False)["reason"] == "overlap"
