"""Case047 geometry acceptance from archived production profiles, not live DOM.

The confirmed business sequence is documented in Davinci case-047.json.  Keep
the complete forty-node replay and its domains; do not turn this into a nine-card
synthetic solver input or require one particular set of column widths.
"""

import json
import time

import pytest

from app.dashboard_layout.regions import region_plans
from app.dashboard_layout.solver import audit, solve
from tests.test_dashboard_regional_contract import replay_problem


RANK = "3448"
METRICS = ("3432", "3428", "3429", "3431", "3433", "3458", "3430")
CHART = "3434"
TARGET = (RANK, *METRICS, CHART)


def _problem():
    """Load all production-profile domains without changing order or budgets."""
    problem = replay_problem("047")
    assert len(problem.nodes) == len({node.id for node in problem.nodes}) == 40
    assert tuple(problem.orders[0][:9]) == TARGET
    assert sum(node.kind == "metric" for node in problem.nodes) == 10
    ranks = [node for node in problem.nodes if node.kind == "rank"]
    assert len(ranks) == 9 and all(len(node.shapes) > 1 for node in ranks)
    assert all(node.parentId is None and not node.container for node in problem.nodes)
    return problem


def _assert_target_structure(placements):
    """Require three aligned regions and ordered 2+2+2+1 metrics, not fixed widths."""
    by_id = {row["id"]: row for row in placements}
    rank, chart = by_id[RANK], by_id[CHART]
    metrics = [by_id[id] for id in METRICS]
    assert (rank["x"], rank["y"], chart["y"]) == (0, 0, 0), (
        "rank and chart must flank the metric region from the same top", rank, chart
    )
    assert chart["x"] + chart["w"] == 24
    assert rank["h"] == chart["h"], "rank and chart must share the bottom"
    left_edge, right_edge = rank["x"] + rank["w"], chart["x"]
    assert left_edge < right_edge
    split = metrics[0]["x"] + metrics[0]["w"]
    assert left_edge < split < right_edge
    bottom = 0
    for index in (0, 2, 4):
        left, right = metrics[index:index + 2]
        assert left["x"] == left_edge and right["x"] == split
        assert left["x"] + left["w"] == split
        assert right["x"] + right["w"] == right_edge
        assert left["y"] == right["y"] == bottom
        assert left["h"] == right["h"]
        bottom += left["h"]
    last = metrics[-1]
    assert last["x"] == left_edge and last["x"] + last["w"] == right_edge
    assert last["y"] == bottom, "the seventh metric spans both columns below row three"
    assert last["y"] + last["h"] == rank["y"] + rank["h"]
    assert tuple(row["id"] for row in sorted(metrics, key=lambda row: (row["y"], row["x"]))) == METRICS
    assert sum(by_id[id]["w"] * by_id[id]["h"] for id in TARGET) == 24 * rank["h"]
    return rank["h"]


def _domain_witness(problem):
    """Pick a known feasible geometry from real domains and retain every tail node."""
    nodes = {node.id: node for node in problem.nodes}

    def row(id, x, y, w, h):
        shape = next((shape for shape in nodes[id].shapes
                      if (shape.w, shape.h, shape.parentVariant) == (w, h, 0)), None)
        assert shape is not None, f"{id}: {w}x{h} is missing from production domain"
        return dict(id=id, x=x, y=y, w=w, h=h, variant=shape.variant)

    # This is a feasibility witness only; the structural assertions allow other widths.
    rows = [row(RANK, 0, 0, 6, 16)]
    rows.extend(row(id, 6 + index % 2 * 5, index // 2 * 4, 5, 4)
                for index, id in enumerate(METRICS[:6]))
    rows.extend([row(METRICS[-1], 6, 12, 10, 4), row(CHART, 16, 0, 8, 16)])
    bottom = 16
    for id in problem.orders[0][9:]:
        node = nodes[id]
        shape = min((shape for shape in node.shapes
                     if shape.parentVariant == 0 and shape.h <= node.maxH),
                    key=lambda shape: (shape.w * shape.h, shape.w, shape.h))
        rows.append(row(id, node.fixedX or 0, bottom, shape.w, shape.h))
        bottom += shape.h
    return rows


def test_case047_expected_structure_is_legal_in_complete_production_domains():
    """Prove the target needs no invented shapes, metric reorder or omitted cards."""
    problem = _problem()
    rows = _domain_witness(problem)
    assert len(rows) == 40
    report = audit(problem, rows, reading_orders=problem.orders)
    assert report["valid"], report
    assert _assert_target_structure(rows) == 16


def test_case047_region_frontier_contains_the_expected_structure():
    """Separate local candidate availability from full-page candidate selection."""
    problem = _problem()
    nodes = {node.id: node for node in problem.nodes}
    plans = region_plans([nodes[id] for id in TARGET], time.monotonic() + 2,
                         ordered_metrics=True)
    for plan in plans:
        try:
            _assert_target_structure(plan)
        except AssertionError:
            continue
        return
    assert False, f"No rank-left / 2+2+2+1 metrics / chart-right structure in {len(plans)} plans"


def _assert_principled_layout(problem, rows, reading_orders):
    """Audit hard constraints and occupied cells without prescribing any topology."""
    assert len(rows) == len({row["id"] for row in rows}) == 40
    assert {row["id"] for row in rows} == {node.id for node in problem.nodes}
    report = audit(problem, rows, reading_orders=reading_orders)
    assert report["valid"], report
    selected_metrics = [id for order in reading_orders for id in order if id in METRICS]
    assert tuple(selected_metrics) == METRICS
    by_id = {row["id"]: row for row in rows}
    bottom = max(by_id[id]["y"] + by_id[id]["h"] for id in TARGET)
    # Include whole successor cards crossing the boundary, not an arbitrary crop
    # that could hide their trailing holes or require the nine targets to align.
    while True:
        extended = max(row["y"] + row["h"] for row in rows if row["y"] < bottom)
        if extended == bottom:
            break
        bottom = extended
    first_region = [row for row in rows if row["y"] < bottom]
    # Count occupied cells independently; global gap totals can hide the local failure.
    occupied = {(x, y) for row in first_region
                for x in range(row["x"], row["x"] + row["w"])
                for y in range(row["y"], row["y"] + row["h"])}
    missing = {(x, y) for x in range(24) for y in range(bottom)} - occupied
    assert not missing, f"first region has uncovered cells: {sorted(missing)[:12]}"


def _successor_chart_witness(problem):
    """Use two admitted 8x8 charts in place of the optional single 8x16 chart."""
    rows = _domain_witness(problem)
    by_id = {row["id"]: row for row in rows}
    by_id[CHART].update(h=8)
    by_id["3435"].update(x=16, y=8, w=8, h=8, variant=0)
    return rows


def test_case047_acceptance_allows_successor_charts_to_complete_the_first_region():
    """The former red layout is acceptable when the following chart fills its space."""
    problem = _problem()
    _assert_principled_layout(problem, _successor_chart_witness(problem), problem.orders)


@pytest.mark.parametrize("defect", ["hole", "bounds", "shape", "order"])
def test_case047_principled_acceptance_still_rejects_invalid_layouts(defect):
    """Do not trade the obsolete same-card-bottom rule for weaker safety checks."""
    problem = _problem()
    rows = _successor_chart_witness(problem)
    by_id = {row["id"]: row for row in rows}
    if defect == "hole":
        by_id["3435"]["y"] += 1
        # A legal one-cell move must fail occupancy, not shape/order validation.
        assert audit(problem, rows, reading_orders=problem.orders)["valid"]
    elif defect == "bounds":
        by_id[RANK]["x"] = -1
    elif defect == "shape":
        by_id[RANK]["w"] = 5
    else:
        a, b = (by_id[id] for id in METRICS[:2])
        a["x"], b["x"] = b["x"], a["x"]
    with pytest.raises(AssertionError, match="uncovered cells" if defect == "hole" else defect):
        _assert_principled_layout(problem, rows, problem.orders)


def test_case047_full_page_is_legal_ordered_and_has_an_aligned_hole_free_first_region():
    """Accept public solve by layout principles, not the optional three-card template."""
    problem = _problem()
    before = problem.model_dump()
    result = solve(problem)
    assert problem.model_dump() == before
    assert result["status"] == "feasible"
    rows = result["placements"]
    bottom = max(row["y"] + row["h"] for row in rows if row["id"] in TARGET)
    print(json.dumps({"gapCells": result.get("gapCells"),
                      "firstRegionPlacements": [row for row in rows if row["y"] < bottom]}, sort_keys=True))
    _assert_principled_layout(problem, rows, result["readingOrders"])
