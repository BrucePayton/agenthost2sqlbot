"""Synthetic geometry regressions, not acceptance of real dashboard screenshots."""
import time
from threading import Event

from app.dashboard_layout.solver import LayoutProblem, audit, solve


def board(nodes, orders=(), budget=3000):
    return LayoutProblem(version="constraint-v1", nodes=nodes,
                         orders=list(orders), budgetMs=budget)


def card(id, w, h, **kw):
    return dict(id=id, shapes=[dict(w=w, h=h)], **kw)


def test_default_solver_uses_anytime_mathematical_optimization():
    p = board([card("metric", 8, 4), card("chart", 16, 8), card("detail", 8, 4)],
              [["metric", "chart", "detail"]])
    result = solve(p)
    assert result["status"] == "feasible"
    assert audit(p, result["placements"])["valid"]
    assert result["method"] == "cp_sat_anytime"


def test_forty_grouped_nodes_keep_conditional_shapes():
    nodes, orders = [], [[f"group{i}" for i in range(8)]]
    for i in range(8):
        parent = f"group{i}"
        nodes.append(dict(id=parent, container=True, shapes=[
            dict(w=6, h=6, variant=1), dict(w=12, h=6, variant=2)]))
        children = [f"{parent}-metric{j}" for j in range(4)]
        orders.append(children)
        nodes.extend(dict(id=id, parentId=parent, shapes=[
            dict(w=12, h=4, parentVariant=1), dict(w=6, h=4, parentVariant=2)])
            for id in children)
    p = board(nodes, orders)
    start = time.monotonic()
    result = solve(p)
    assert result["status"] == "feasible"
    assert audit(p, result["placements"])["valid"]
    assert len(result["placements"]) == 40
    assert time.monotonic() - start < 4


def test_fixed_columns_can_stack_without_global_search():
    p = board([card("left", 12, 8, fixedX=0),
               card("top", 12, 4, fixedX=12), card("bottom", 12, 4, fixedX=12)],
              [["left", "top", "bottom"]])
    result = solve(p)
    assert result["status"] == "feasible"
    assert audit(p, result["placements"])["valid"]


def test_cancelled_search_never_returns_a_saveable_incumbent():
    cancelled = Event()
    cancelled.set()
    result = solve(board([card("full", 24, 4)]), cancelled=cancelled)
    assert result["status"] == "cancelled"
    assert "placements" not in result


def test_impossible_single_card_has_a_geometric_reason():
    p = board([card("rank", 8, 12, fixedX=20)])
    result = solve(p)
    assert result["status"] == "infeasible_candidates"
    assert result["reason"] == "no_valid_scope_profile"
    assert result["widgetIds"] == ["rank"]


def test_elapsed_budget_is_checked_before_optimization():
    result = solve(board([card("full", 24, 4)]), deadline=time.monotonic() - 1)
    assert result["status"] == "budget_exhausted"
    assert "placements" not in result


def test_solver_returns_ranked_distinct_alternatives():
    p = board([
        dict(id="a", shapes=[dict(w=8, h=4), dict(w=12, h=4)]),
        dict(id="b", shapes=[dict(w=12, h=4), dict(w=16, h=4)]),
        dict(id="c", shapes=[dict(w=8, h=4), dict(w=12, h=4)]),
        dict(id="d", shapes=[dict(w=12, h=4), dict(w=16, h=4)]),
    ])

    result = solve(p)

    assert result["status"] == "feasible"
    assert result["placements"] == result["alternatives"][0]["placements"]
    assert 1 < len(result["alternatives"]) <= 8
    keys = [candidate["planKey"] for candidate in result["alternatives"]]
    assert len(keys) == len(set(keys))
    scores = [candidate["quality"]["gapCells"] for candidate in result["alternatives"]]
    assert scores == sorted(scores)


def test_budget_exhaustion_keeps_an_audited_incumbent():
    p = board([
        dict(id=str(index), shapes=[
            dict(w=4, h=4), dict(w=6, h=4), dict(w=8, h=4),
        ])
        for index in range(100)
    ], [list(map(str, range(100)))], budget=1)

    result = solve(p)

    assert result["status"] == "feasible"
    assert result["searchPruned"] is True
    assert audit(p, result["placements"])["valid"]


def test_budget_exhaustion_keeps_grouped_linear_incumbent():
    nodes, orders = [], [["group-a", "chart"]]
    nodes.append(dict(id="group-a", container=True, shapes=[
        dict(w=12, h=6, variant=1), dict(w=24, h=6, variant=2)
    ]))
    orders.append(["metric-a", "metric-b", "metric-c"])
    nodes.extend([
        dict(id="metric-a", parentId="group-a", shapes=[
            dict(w=12, h=4, parentVariant=1), dict(w=8, h=4, parentVariant=2)
        ]),
        dict(id="metric-b", parentId="group-a", shapes=[
            dict(w=12, h=4, parentVariant=1), dict(w=8, h=4, parentVariant=2)
        ]),
        dict(id="metric-c", parentId="group-a", shapes=[
            dict(w=12, h=4, parentVariant=1), dict(w=8, h=4, parentVariant=2)
        ]),
        dict(id="chart", shapes=[dict(w=12, h=8), dict(w=24, h=8)])
    ])
    p = board(nodes, orders, budget=1)

    result = solve(p)

    assert result["status"] == "feasible"
    assert result["searchPruned"] is True
    assert audit(p, result["placements"])["valid"]
    assert len(result["placements"]) == len(nodes)
