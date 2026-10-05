"""Bounded organizer regressions; these do not replace real visual acceptance."""
import time
from threading import Event

import pytest

from app.dashboard_layout.solver import LayoutProblem, audit, solve


def problem(nodes):
    return LayoutProblem(version="constraint-v1", objective="organize-v1",
                         nodes=nodes, orders=[[n["id"] for n in nodes]], budgetMs=2000)


def metric(i):
    return dict(id=str(i), kind="metric", shapes=[dict(w=w, h=2) for w in (6, 12)])


@pytest.mark.parametrize("count,height,columns", [(2, 4, 1), (3, 6, 1), (4, 4, 2), (5, 6, 2)])
def test_metric_stack_rules(count, height, columns):
    p = problem([*[metric(i) for i in range(count)],
                 dict(id="chart", kind="chart", shapes=[dict(w=12 if columns == 2 else 18, h=height)])])
    result = solve(p)
    assert result["status"] == "feasible"
    assert audit(p, result["placements"])["valid"]
    rows = {r["id"]: r for r in result["placements"]}
    assert result["gapCells"] == 0
    assert max(r["y"] + r["h"] for r in rows.values()) == height
    assert all(rows[str(i)]["h"] == 2 for i in range(count))
    if count == 5:
        assert rows["4"]["w"] == 12


def test_large_board_does_not_enter_global_cp_sat(monkeypatch):
    import app.dashboard_layout.solver as module
    monkeypatch.setattr(module, "_solve_cp_sat", lambda *a, **kw: pytest.fail("global search"))
    p = problem([dict(id=str(i), shapes=[dict(w=6, h=2)]) for i in range(200)])
    result = solve(p)
    assert result["status"] == "feasible"
    assert len(result["placements"]) == 200
    assert audit(p, result["placements"])["valid"]


def test_realistic_forty_card_domains_finish_as_zero_gap_batches():
    nodes = []
    for index in range(40):
        if index % 4 == 3:
            shapes = [
                {"w": width, "h": height,
                 "cost": abs(width - 12) * 8 + abs(height - 8) * 4}
                for width in (8, 10, 12, 14, 16, 18, 20, 24)
                for height in (8, 10, 12, 16)
            ]
            kind = "chart"
            original = {"x": 0, "y": index * 8, "w": 12, "h": 8}
        else:
            shapes = [
                {"w": width, "h": height,
                 "cost": (width * height - 16) * 25 + height}
                for width in (4, 6, 8) for height in (4, 8)
            ]
            kind = "metric"
            original = {"x": 0, "y": index * 4, "w": 4, "h": 4}
        nodes.append({"id": str(index), "kind": kind,
                      "shapes": shapes, "original": original})
    started = time.monotonic()

    result = solve(problem(nodes))

    assert result["status"] == "feasible"
    assert result["method"] == "bounded_scope_batches"
    assert len(result["placements"]) == 40
    assert result["gapCells"] == 0
    assert audit(problem(nodes), result["placements"])["valid"]
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("count", [1, 2, 3])
def test_isolated_metrics_retain_original_geometry(count):
    nodes = [dict(id=str(i), kind="metric", original=dict(x=i * 8, y=3, w=8, h=4),
                  shapes=[dict(w=8, h=4), dict(w=6, h=2)]) for i in range(count)]
    p = problem(nodes)
    result = solve(p)
    for row in result["placements"]:
        assert {k: row[k] for k in ("x", "y", "w", "h")} == nodes[int(row["id"])]["original"]


def test_tiny_budget_returns_complete_plan():
    p = problem([dict(id=str(i), shapes=[dict(w=6, h=2)]) for i in range(200)])
    p.budgetMs = 1
    started = time.monotonic()
    result = solve(p)
    assert audit(p, result["placements"])["valid"]
    assert time.monotonic() - started < 1


def test_chart_first_retains_direction():
    p = problem([dict(id="chart", kind="chart", shapes=[dict(w=18, h=4)]), metric(0), metric(1)])
    result = solve(p)
    rows = {r["id"]: r for r in result["placements"]}
    assert rows["chart"]["x"] == 0
    assert rows["0"]["x"] == rows["1"]["x"] == 18
    assert result["gapCells"] == 0


def test_multiple_short_charts_stack_beside_metrics():
    p = problem([*[metric(i) for i in range(4)],
                 *[dict(id=f"c{i}", kind="chart", shapes=[dict(w=18, h=4)]) for i in range(2)]])
    result = solve(p)
    assert result["gapCells"] == 0
    assert audit(p, result["placements"])["valid"]


def test_grouped_batches_keep_parent_variants_and_containment():
    nodes = [dict(id="group", container=True, shapes=[dict(w=24, h=30, variant=7)])]
    children = [dict(**metric(i), parentId="group") for i in range(5)]
    children.append(dict(id="chart", kind="chart", parentId="group", shapes=[dict(w=12, h=6)]))
    for node in children:
        node["shapes"] = [dict(**s, parentVariant=7) for s in node["shapes"]]
    p = LayoutProblem(version="constraint-v1", objective="organize-v1", nodes=nodes+children,
                      orders=[["group"], [n["id"] for n in children]])
    result = solve(p)
    assert audit(p, result["placements"])["valid"]
    assert result["gapCells"] == 0


def test_cancel_during_batch_never_returns_saveable_plan(monkeypatch):
    import app.dashboard_layout.organize as module
    cancelled = Event()
    def cancel(nodes):
        cancelled.set()
        return []
    monkeypatch.setattr(module, "_stack_plans", cancel)
    result = solve(problem([metric(0), metric(1), metric(2), metric(3)]), cancelled=cancelled)
    assert result["status"] == "cancelled"
    assert "placements" not in result


def test_unreadable_original_is_not_restored():
    node = metric(0)
    node["original"] = dict(x=0, y=0, w=1, h=1)
    p = problem([node])
    result = solve(p)
    assert audit(p, result["placements"])["valid"]
    assert result["placements"][0]["w"] != 1


def test_readable_short_chart_precedes_tall_single_column():
    p = problem([*[metric(i) for i in range(4)],
                 dict(id="chart", kind="chart", shapes=[dict(w=12, h=4), dict(w=18, h=8)])])
    result = solve(p)
    assert max(r["y"]+r["h"] for r in result["placements"]) == 4


def test_wide_table_moves_below_even_metric_rows():
    p = problem([*[metric(i) for i in range(6)],
                 dict(id="table", kind="chart", shapes=[dict(w=24, h=8)])])
    result = solve(p)
    assert audit(p, result["placements"])["valid"]
    assert result["gapCells"] == 0


def test_two_metrics_expand_evenly_above_a_full_width_table():
    metrics = [
        {"id": f"m{i}", "kind": "metric",
         "shapes": [{"w": w, "h": 4} for w in (4, 6, 8, 12)]}
        for i in range(2)
    ]
    table = {"id": "table", "kind": "chart", "shapes": [{"w": 24, "h": 8}]}

    result = solve(problem([*metrics, table]))

    assert result["status"] == "feasible"
    assert result["gapCells"] == 0
    rows = {row["id"]: row for row in result["placements"]}
    assert (rows["m0"]["x"], rows["m0"]["w"]) == (0, 12)
    assert (rows["m1"]["x"], rows["m1"]["w"]) == (12, 12)
    assert (rows["table"]["x"], rows["table"]["w"]) == (0, 24)


def test_two_metrics_expand_evenly_beside_a_tall_table():
    metrics = [
        {"id": f"m{i}", "kind": "metric",
         "shapes": [{"w": 4, "h": h} for h in (4, 8)]}
        for i in range(2)
    ]
    table = {"id": "table", "kind": "chart", "shapes": [{"w": 20, "h": 16}]}

    result = solve(problem([*metrics, table]))

    assert result["status"] == "feasible"
    assert result["gapCells"] == 0
    rows = {row["id"]: row for row in result["placements"]}
    assert (rows["m0"]["x"], rows["m0"]["y"], rows["m0"]["h"]) == (0, 0, 8)
    assert (rows["m1"]["x"], rows["m1"]["y"], rows["m1"]["h"]) == (0, 8, 8)
    assert (rows["table"]["x"], rows["table"]["h"]) == (4, 16)


@pytest.mark.parametrize("kinds", [
    ["metric", "chart", "chart", "metric"], ["other"] * 4,
])
def test_contiguous_mixed_columns_are_generated_not_only_accepted(kinds):
    nodes = [dict(id=str(i), kind=kind, shapes=[dict(w=w, h=h)])
             for i, (kind, (w, h)) in enumerate(zip(kinds, [(8, 2), (8, 6), (16, 6), (16, 2)]))]
    p = problem(nodes)
    result = solve(p)
    assert result["gapCells"] == 0
    assert audit(p, result["placements"])["valid"]
    rows = {r["id"]: r for r in result["placements"]}
    assert rows["1"]["y"] > rows["2"]["y"]


def test_nested_mixed_regions_keep_natural_order():
    # Left: two small cards side by side above a wide card. Right: one tall card.
    p = problem([dict(id=str(i), shapes=[dict(w=w, h=h)])
                 for i, (w, h) in enumerate([(4, 2), (4, 2), (8, 6), (16, 8)])])
    result = solve(p)
    assert result["gapCells"] == 0
    assert max(r["y"]+r["h"] for r in result["placements"]) == 8


def test_batch_boundary_does_not_split_a_useful_mixed_region():
    nodes = [dict(id=f"header{i}", shapes=[dict(w=24, h=2)]) for i in range(11)]
    nodes += [dict(id=str(i), shapes=[dict(w=w, h=h)])
              for i, (w, h) in enumerate([(8, 2), (8, 6), (16, 6), (16, 2)])]
    p = problem(nodes)
    result = solve(p)
    assert result["gapCells"] == 0
    assert audit(p, result["placements"])["valid"]
    assert max(r["y"]+r["h"] for r in result["placements"]) == 30


@pytest.mark.parametrize("left_count,right_count,width", [(1, 3, 6), (2, 2, 8), (3, 1, 12), (3, 3, 16)])
def test_regional_reading_generalizes_across_counts_and_widths(left_count, right_count, width):
    sizes = [(width, right_count*2)] * left_count + [(24-width, left_count*2)] * right_count
    p = problem([dict(id=str(i), shapes=[dict(w=w, h=h)]) for i, (w, h) in enumerate(sizes)])
    result = solve(p)
    assert result["gapCells"] == 0
    assert audit(p, result["placements"])["valid"]


def test_reverse_content_is_still_rejected():
    p = problem([dict(id=str(i), shapes=[dict(w=12, h=4)]) for i in range(4)])
    placements = [dict(id=str(i), x=(3-i) % 2 * 12, y=(3-i) // 2 * 4,
                       w=12, h=4, variant=0) for i in range(4)]
    assert audit(p, placements)["reason"] == "order"


def test_full_width_cards_cannot_fake_compaction_by_making_a_taller_page():
    nodes = [dict(id=f"m{i}", kind="metric", shapes=[dict(w=4, h=4), dict(w=24, h=4)]) for i in range(2)]
    nodes += [dict(id=f"c{i}", kind="chart", shapes=[dict(w=w, h=8) for w in (8, 12, 24)]) for i in range(2)]
    nodes += [dict(id="table", kind="chart", shapes=[dict(w=24, h=8)])]
    result = solve(problem(nodes))
    assert result["gapCells"] == 0
    assert max(r["y"]+r["h"] for r in result["placements"]) == 16
