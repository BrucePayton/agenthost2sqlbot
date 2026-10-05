import math
import time

import pytest
from pydantic import ValidationError

from app.dashboard_layout.solver import (
    LayoutProblem,
    _objective_score,
    _skyline_seed,
    audit,
    solve,
)


def node(id, shapes, **kw):
    return dict(id=id, shapes=[dict(w=w, h=h, cost=cost) for w, h, cost in shapes], **kw)


def problem(nodes, **kw):
    return LayoutProblem.model_validate(dict(version="constraint-v1", nodes=nodes,
                                            budgetMs=2000, **kw))


def test_measured_alternatives_fill_ten_metric_cards_without_inflation():
    p = problem([node(str(i), [(4, 4, 0), (5, 4, 4), (6, 4, 8)]) for i in range(10)],
                orders=[[str(i) for i in range(10)]])
    result = solve(p)
    assert result["status"] == "feasible"
    assert len(result["placements"]) == 10
    assert audit(p, result["placements"])["valid"]
    assert max(r["y"] + r["h"] for r in result["placements"]) == 8


def test_mixed_height_layout_is_not_restricted_to_equal_height_rows():
    p = problem([node("a", [(12, 4, 0)]), node("b", [(12, 4, 0)]),
                 node("c", [(12, 8, 0)])])
    result = solve(p)
    assert result["status"] == "feasible"
    assert max(r["y"] + r["h"] for r in result["placements"]) == 8
    assert audit(p, result["placements"])["valid"]


@pytest.mark.parametrize("objective", [None, "organize-v1"])
def test_metric_fills_below_a_chart_beside_a_spanning_table(objective):
    nodes = [
        node("line", [(8, 8, 0)], kind="chart"),
        node("table", [(16, 16, 0)], kind="chart"),
        node("metric", [(4, 4, 0), (8, 8, 1200)], kind="metric"),
    ]
    options = {"orders": [["line", "table", "metric"]]}
    if objective:
        options["objective"] = objective
    p = problem(nodes, **options)

    result = solve(p)

    rows = {row["id"]: row for row in result["placements"]}
    assert result["gapCells"] == 0
    assert (rows["line"]["x"], rows["line"]["y"], rows["line"]["w"]) == (0, 0, 8)
    assert (rows["table"]["x"], rows["table"]["y"], rows["table"]["h"]) == (8, 0, 16)
    assert (rows["metric"]["x"], rows["metric"]["y"], rows["metric"]["w"],
            rows["metric"]["h"]) == (0, 8, 8, 8)


@pytest.mark.parametrize("objective", [None, "organize-v1"])
def test_single_ordinary_chart_row_expands_to_twenty_four_columns(objective):
    nodes = [
        node("top-left", [(12, 8, 0)], kind="chart"),
        node("top-right", [(12, 8, 0)], kind="chart"),
        node("bottom", [(18, 8, 0), (24, 8, 50)], kind="chart"),
    ]
    options = {"orders": [["top-left", "top-right", "bottom"]]}
    if objective:
        options["objective"] = objective
    p = problem(nodes, **options)

    result = solve(p)

    bottom = next(row for row in result["placements"] if row["id"] == "bottom")
    assert (bottom["x"], bottom["w"]) == (0, 24)
    assert result["gapCells"] == 0


def test_parent_width_and_children_are_selected_jointly():
    nodes = [dict(id="group", container=True, shapes=[
        dict(w=12, h=6, variant=1), dict(w=24, h=6, variant=2)]),
        node("chart", [(12, 9, 0)])]
    for i in range(4):
        nodes.append(dict(id=f"m{i}", parentId="group", shapes=[
            dict(w=12, h=4, parentVariant=1), dict(w=6, h=4, parentVariant=2)]))
    p = problem(nodes, orders=[["group", "chart"], [f"m{i}" for i in range(4)]])
    result = solve(p)
    assert result["status"] == "feasible"
    by_id = {r["id"]: r for r in result["placements"]}
    assert by_id["group"]["w"] == 12
    assert by_id["group"]["h"] == 9
    assert all(by_id[f"m{i}"]["w"] == 12 for i in range(4))
    assert audit(p, result["placements"])["valid"]


def test_infeasible_candidates_and_budget_exhaustion_are_distinct():
    p = problem([node("rank", [(8, 12, 0)], fixedX=20)])
    result = solve(p)
    assert result["status"] == "infeasible_candidates"
    assert result["profileFailures"] == [{
        "nodeId": "rank", "scopeId": "root", "reason": "no_compatible_shape",
    }]
    assert solve(p, deadline=time.monotonic() - 1)["status"] == "budget_exhausted"


def test_audit_rejects_wrong_shape_overlap_and_missing_nodes_but_not_whitespace():
    p = problem([node("a", [(12, 4, 0)]), node("b", [(12, 4, 0)])])
    good = [dict(id="a", x=0, y=0, w=12, h=4, variant=0),
            dict(id="b", x=12, y=0, w=12, h=4, variant=0)]
    assert audit(p, good)["valid"]
    for bad in [good[:1], [good[0], dict(good[1], x=0)],
                [dict(good[0], w=11), good[1]]]:
        assert not audit(p, bad)["valid"]
    whitespace = [good[0], dict(good[1], y=4)]
    assert audit(p, whitespace)["valid"]
    assert audit(p, whitespace)["gapCells"] > 0


def test_problem_rejects_cycles_duplicate_ids_unknown_fields_and_cross_scope_order():
    cases = [
        [node("a", [(12, 4, 0)]), node("a", [(12, 4, 0)])],
        [node("a", [(12, 4, 0)], parentId="a", container=True)],
        [node("a", [(12, 4, 0)], title="business data is not accepted")],
        [node("a", [(12, 4, 0)], parentId="missing")],
    ]
    for nodes in cases:
        with pytest.raises(ValidationError):
            problem(nodes)
    with pytest.raises(ValidationError):
        problem([node("a", [(12, 6, 0)], container=True),
                 node("b", [(24, 4, 0)], parentId="a")], orders=[["a", "b"]])


@pytest.mark.parametrize("count", [40, 200])
def test_bounded_large_metric_board(count):
    p = problem([node(str(i), [(3, 4, 0), (4, 4, 4), (6, 4, 12)]) for i in range(count)],
                orders=[[str(i) for i in range(count)]])
    started = time.monotonic()
    r = solve(p)
    assert r["status"] == "feasible"
    assert audit(p, r["placements"])["valid"]
    assert time.monotonic() - started < 3


def test_fixed_container_height_and_alignment_columns_are_hard_constraints():
    p = problem([dict(id="group", container=True, maxH=6, fixedX=12,
                     shapes=[dict(w=12, h=6)]),
                 node("child", [(24, 10, 0)], parentId="group")])
    assert solve(p)["status"] == "infeasible_candidates"


def test_mixed_height_seed_fills_below_a_small_card_without_reordering():
    p = problem([node("chart", [(16, 12, 0)]), node("metric", [(6, 4, 0)]),
                 node("detail", [(8, 8, 0)])], orders=[["chart", "metric", "detail"]])
    seed = _skyline_seed(p, time.monotonic()+1)
    assert seed is not None
    assert audit(p, seed)["valid"]
    assert audit(p, seed)["gapCells"] == 8
    assert max(r["y"]+r["h"] for r in seed) == 12


def test_skyline_seed_accepts_column_stacks_for_interleaved_metrics_and_charts():
    metric = [(4, 4, 0)]
    chart = [
        (width, height, abs(width - 12) * 8 + abs(height - 8) * 4)
        for width in range(8, 25)
        for height in (8, 9, 10, 11, 12, 16)
    ]
    nodes = [
        node("metric-1", metric),
        node("metric-2", metric),
        node("metric-3", metric),
        node("line", chart),
        node("metric-4", metric),
        node("pie", chart),
    ]
    p = problem(nodes, orders=[[value["id"] for value in nodes]])

    seed = _skyline_seed(p, time.monotonic() + 1)

    assert seed is not None
    assert audit(p, seed)["valid"]
    by_id = {row["id"]: row for row in seed}
    # The third metric remains semantically before the line chart even though
    # the compact column traversal places it on a lower visual row.
    assert by_id["metric-3"]["y"] > by_id["line"]["y"]
    height = max(row["y"] + row["h"] for row in seed)
    assert audit(p, seed)["gapCells"] / (24 * height) <= 0.15


def test_solver_uses_mixed_height_incumbent_when_bounded_search_prunes_it():
    p = problem([node("0", [(12, 12, 0), (4, 4, 0), (12, 8, 0)]),
                 node("1", [(6, 4, 0), (8, 8, 0)]),
                 node("2", [(4, 4, 0), (6, 12, 0)]),
                 node("3", [(10, 14, 0), (12, 8, 0), (16, 12, 0)])],
                orders=[["0", "1", "2", "3"]])
    seed = _skyline_seed(p, time.monotonic()+1)
    assert seed is not None
    assert audit(p, seed)["valid"]

    result = solve(p)

    assert result["status"] == "feasible"
    assert audit(p, result["placements"])["valid"]


def test_incumbent_score_does_not_reward_filling_space_with_larger_cards():
    p = problem([node("a", [(12, 4, 0), (12, 8, 20)]), node("b", [(12, 4, 0), (12, 8, 20)])])
    compact = [dict(id=id, x=x, y=0, w=12, h=4, variant=0) for id, x in (("a", 0), ("b", 12))]
    inflated = [dict(r, h=8) for r in compact]
    assert audit(p, compact)["valid"] and audit(p, inflated)["valid"]
    assert _objective_score(p, compact) < _objective_score(p, inflated)


def test_forty_card_wide_candidate_domain_keeps_a_complete_anytime_solution():
    nodes = [node(str(index), [
        (width, height, width * height)
        for width in range(7, 24)
        for height in range(5, 18)
    ]) for index in range(40)]
    p = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": nodes,
        "orders": [[str(index) for index in range(40)]],
        "budgetMs": 7000,
    })

    started = time.monotonic()
    result = solve(p)

    assert result["status"] == "feasible"
    assert result["method"] == "cp_sat_anytime"
    assert len(result["placements"]) == 40
    assert audit(p, result["placements"])["valid"]
    assert time.monotonic() - started < 7


def test_forty_interleaved_cards_use_a_batched_incumbent_with_bounded_whitespace():
    metric = [(4, 4, 0)]
    chart = [
        (width, height, abs(width - 12) * 8 + abs(height - 8) * 4)
        for width in range(8, 25)
        for height in (8, 9, 10, 11, 12, 16)
    ]
    nodes = [
        node(str(index), chart if index % 6 in (3, 5) else metric)
        for index in range(40)
    ]
    p = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": nodes,
        "orders": [[str(index) for index in range(40)]],
        "budgetMs": 1500,
    })

    result = solve(p)

    assert result["status"] == "feasible"
    assert result["seedMethod"] == "batched_cp_sat"
    assert 5 <= result["batchCount"] <= 7
    assert result["batchSizesTried"] == [6, 7, 8]
    assert audit(p, result["placements"])["valid"]
    height = max(row["y"] + row["h"] for row in result["placements"])
    assert result["gapCells"] / (24 * height) <= 0.15


def test_short_optimization_budget_returns_the_constructed_incumbent():
    nodes = [node(str(index), [
        (width, height, width * height)
        for width in range(7, 24)
        for height in range(5, 18)
    ]) for index in range(40)]
    p = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": nodes,
        "orders": [[str(index) for index in range(40)]],
        "budgetMs": 50,
    })

    result = solve(p)

    assert result["status"] == "feasible"
    assert result["method"] == "cp_sat_anytime"
    assert result["optimal"] is False
    assert result["searchPruned"] is True
    assert audit(p, result["placements"])["valid"]


@pytest.mark.parametrize("overview", [False, True])
def test_forty_mixed_cards_geometry_regression(overview):
    # Type/order pattern from the failed board; dimensions are synthetic, not
    # browser-measured content or acceptance of the user's screenshot samples.
    types = [2001, 2001, 2001, 2001, 2001, 4001, 2001, 4001, 4001, 4001,
             4001, 4001, 4001, 4001, 4001, 4001, 5001, 4001, 5001, 4001,
             4001, 11001, 4001, 4001, 11001, 4001, 11001, 11001, 11001, 1001,
             2001, 2001, 2001, 2001, 4001, 4001, 4001, 4001, 4001, 11001]
    nodes = []
    for i, kind in enumerate(types):
        if kind == 2001:
            shapes = [(w, 4, (w-4)*100+4) for w in (4, 5, 6)]
        elif kind == 11001:
            shapes = [(w, max(12, math.ceil(((w*1594/24-10)*1.15+10)/40)),
                       round((w*1594/24-10)/8)+(200 if w > 8 else 0)) for w in (6, 8, 10, 12)]
        elif kind == 1001:
            shapes = [(w, h, (24-w)*30+h) for w in (24, 18, 12) for h in (8, 12)]
        else:
            shapes = [(w, h, max(0, w*h-96)*2+(15 if w == 8 else 0))
                      for w in (8, 12, 16, 18, 24) for h in (6, 8, 9, 10, 11, 12, 16)]
        nodes.append(node(str(i), shapes))
    order = list(range(len(types)))
    if overview:
        priority = {2001: 0, 4001: 1, 5001: 2, 11001: 3, 1001: 4}
        order.sort(key=lambda i: priority[types[i]])
    p = LayoutProblem(version="constraint-v1", nodes=nodes,
                      orders=[[str(i) for i in order]], budgetMs=15000)
    start = time.monotonic()
    result = solve(p)
    assert result["status"] == "feasible"
    assert audit(p, result["placements"])["valid"]
    assert time.monotonic()-start < 5
