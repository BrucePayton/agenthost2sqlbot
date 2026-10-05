import copy
import json
import time
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError

from app.dashboard_layout.diagnostics import capture
from app.dashboard_layout.incumbent import build_complete_incumbent
from app.dashboard_layout.solver import LayoutProblem, audit, solve


def problem_for(case, source="derived_geometry", **overrides):
    def node(id, kind, sizes):
        return {"id": id, "kind": kind, "shapes": [{"w": w, "h": h} for w, h in sizes]}

    if case == "048":
        nodes = [node(f"m{i}", "metric", [(3, 3)]) for i in range(1, 7)]
        nodes.append(node("chart", "chart", [(18, 9)]))
        order = ["m1", "chart", *[f"m{i}" for i in range(2, 7)]]
    elif case == "050":
        nodes = [node(f"m{i}", "metric", [(6, 3)]) for i in range(1, 5)]
        nodes += [node("rank", "rank", [(6, 15)]), node("chart", "chart", [(18, 15)])]
        order = ["m1", "rank", "chart", "m2", "m3", "m4"]
    else:
        nodes = [node("rank", "rank", [(6, 12)])]
        nodes += [node(f"m{i}", "metric", [(6, 3)]) for i in range(1, 7)]
        nodes += [
            node("m7", "metric", [(12, 3)]),
            node("chart", "chart", [(6, 12)]),
            node("next", "chart", [(24, 4)]),
        ]
        order = [n["id"] for n in nodes]
    body = {
        "version": "constraint-v1",
        "nodes": nodes,
        "orders": [order],
        "budgetMs": 5000,
    }
    if source is not None:
        body["orderSources"] = [source]
    body.update(overrides)
    return LayoutProblem.model_validate(body)


def refine(problem, rows=None, **kwargs):
    from app.dashboard_layout.regional import refine_regions

    rows = rows or build_complete_incumbent(problem)
    result = {"version": problem.version, "status": "feasible", "placements": rows}
    return refine_regions(
        problem, result, deadline=kwargs.pop("deadline", time.monotonic() + 3), **kwargs
    )


def test_wire_accepts_json_pairs_and_preserves_sources():
    problem = problem_for("050", requiredBefore=[["m1", "m2"], ["rank", "chart"]])
    assert problem.requiredBefore == [("m1", "m2"), ("rank", "chart")]
    assert LayoutProblem.model_validate_json(problem.model_dump_json()) == problem
    assert problem_for("050", None).orderSources is None


@pytest.mark.parametrize(
    "patch",
    [
        {"orderSources": []},
        {"orderSources": ["unknown"]},
        {"requiredBefore": [["absent", "m1"]]},
        {"requiredBefore": [["m1", "m1"]]},
        {"requiredBefore": [["m1", "m2"], ["m2", "m1"]]},
        {"requiredBefore": [["m1", "m2"], ["m1", "m2"]]},
        {"requiredBefore": [["chart", "rank"]]},
    ],
)
def test_invalid_order_contract_is_rejected(patch):
    with pytest.raises(ValidationError):
        problem_for("050", **patch)


def test_required_before_cannot_cross_parent_scope():
    body = problem_for("050").model_dump()
    body["nodes"].append(
        {"id": "group", "container": True, "shapes": [{"w": 24, "h": 30}]}
    )
    body["nodes"][0]["parentId"] = "group"
    body.update(
        orders=[["m1"], ["rank", "chart", "m2", "m3", "m4", "group"]],
        orderSources=["derived_geometry"] * 2,
        requiredBefore=[["m1", "rank"]],
    )
    with pytest.raises(ValidationError):
        LayoutProblem.model_validate(body)


@pytest.mark.parametrize("case", ["048", "050", "047"])
def test_nested_regions_are_generated_with_selected_reading_orders(case):
    problem = problem_for(case)
    before = problem.model_dump()
    with capture() as counters:
        result = refine(problem)
    assert result["status"] == "feasible"
    assert audit(problem, result["placements"], reading_orders=result["readingOrders"])[
        "valid"
    ]
    assert result["gapCells"] == 0
    assert problem.model_dump() == before
    assert counters["regional.accepted"] >= 1
    by_id = {r["id"]: r for r in result["placements"]}
    if case == "048":
        assert [by_id[f"m{i}"]["x"] for i in range(1, 7)] == [0, 3, 0, 3, 0, 3]
        assert by_id["chart"]["x"] == 6 and by_id["chart"]["y"] == 0
    elif case == "050":
        assert [by_id[f"m{i}"]["x"] for i in range(1, 5)] == [0, 6, 12, 18]
        assert all(by_id[f"m{i}"]["y"] == 0 for i in range(1, 5))
        assert by_id["rank"]["y"] == by_id["chart"]["y"] == 3
    else:
        assert by_id["rank"]["x"] == 0 and by_id["chart"]["x"] == 18
        assert by_id["rank"]["y"] == by_id["chart"]["y"] == 0
        assert by_id["m7"]["x"] == 6 and by_id["m7"]["y"] == 9
        assert by_id["next"]["y"] == 12
    for alternative in result["alternatives"]:
        assert audit(
            problem,
            alternative["placements"],
            reading_orders=alternative["readingOrders"],
        )["valid"]


@pytest.mark.parametrize("source", [None, "explicit_request", "explicit_saved"])
@pytest.mark.parametrize("case", ["048", "050"])
def test_explicit_and_legacy_orders_cannot_be_replaced(source, case):
    problem = problem_for(case, source)
    result = refine(problem)
    assert result["readingOrders"] == problem.orders
    assert audit(problem, result["placements"])["valid"]
    rows = {row["id"]: row for row in result["placements"]}
    if case == "048":
        assert result["gapCells"] > 0
    else:
        assert not all(rows[f"m{i}"]["y"] == 0 for i in range(1, 5))


def test_required_relation_blocks_metric_grouping_without_dropping_complete_plan():
    problem = problem_for("050", requiredBefore=[["rank", "m2"]])
    with capture() as counters:
        result = refine(problem)
    order = result["readingOrders"][0]
    assert order.index("rank") < order.index("m2")
    assert len(result["placements"]) == len(problem.nodes)
    assert counters["regional.rejected.required_before"] > 0


def test_selected_order_audit_checks_identity_stability_and_ten_node_locality():
    from app.dashboard_layout.reading import reading_order_error

    problem = problem_for("050")
    assert (
        reading_order_error(problem, [["m1", "m2", "m3", "m4", "rank", "chart"]])
        is None
    )
    assert reading_order_error(problem, [["m2", "m1", "m3", "m4", "rank", "chart"]])
    assert reading_order_error(problem, [["m1", "m2", "m3", "m4", "chart", "rank"]])
    assert reading_order_error(problem, [["m1"] * 6])
    body = problem.model_dump()
    body["nodes"] += [{"id": f"c{i}", "shapes": [{"w": 24, "h": 3}]} for i in range(10)]
    body["orders"] = [
        ["m1", *[f"c{i}" for i in range(10)], "m2", "m3", "m4", "rank", "chart"]
    ]
    long = LayoutProblem.model_validate(body)
    moved = [["m1", "m2", *[f"c{i}" for i in range(10)], "m3", "m4", "rank", "chart"]]
    assert reading_order_error(long, moved) == "order_locality"


@pytest.mark.parametrize("window", [9, 10, 11])
def test_window_limit_is_against_request_not_accumulated_edits(window):
    from app.dashboard_layout.reading import reading_order_error

    body = {
        "version": "constraint-v1",
        "nodes": [
            {
                "id": f"n{i}",
                "kind": "metric" if i == window - 1 else "chart",
                "shapes": [{"w": 24, "h": 3}],
            }
            for i in range(window)
        ],
        "orders": [[f"n{i}" for i in range(window)]],
        "orderSources": ["derived_geometry"],
    }
    problem = LayoutProblem.model_validate(body)
    chosen = [[f"n{window - 1}", *problem.orders[0][:-1]]]
    assert reading_order_error(problem, chosen) == (
        None if window <= 10 else "order_locality"
    )
    if window == 11:
        intermediate = [
            [problem.orders[0][0], f"n{window - 1}", *problem.orders[0][1:-1]]
        ]
        assert reading_order_error(problem, intermediate) is None
        # A second individually short edit is still illegal against the request.
        copied = problem.model_copy(update={"orders": intermediate})
        assert reading_order_error(copied, chosen) is None
        assert reading_order_error(problem, chosen) == "order_locality"


@pytest.mark.parametrize("objective", [None, "organize-v1"])
def test_explicit_edge_can_override_derived_cross_kind_sequence(objective):
    problem = problem_for("050", objective=objective, requiredBefore=[["m2", "rank"]])
    result = solve(problem)
    assert result["status"] == "feasible"
    assert audit(problem, result["placements"], reading_orders=result["readingOrders"])[
        "valid"
    ]
    assert result["readingOrders"][0].index("m2") < result["readingOrders"][0].index(
        "rank"
    )


def test_batched_solver_filters_relations_to_its_local_nodes():
    from app.dashboard_layout.cp_sat import _batched_cp_sat_incumbent

    body = problem_for("047").model_dump()
    body["requiredBefore"] = [["rank", "next"]]
    problem = LayoutProblem.model_validate(body)
    rows, _ = _batched_cp_sat_incumbent(problem, deadline=time.monotonic() + 2)
    assert rows and audit(problem, rows)["valid"]


def test_region_preserves_nested_scope_variant_and_complete_suffix():
    body = problem_for("048").model_dump()
    for node in body["nodes"]:
        node["parentId"] = "parent"
        for shape in node["shapes"]:
            shape["parentVariant"] = 7
    body["nodes"] += [
        {
            "id": "parent",
            "container": True,
            "shapes": [{"w": 24, "h": 30, "variant": 7}],
        },
        {"id": "suffix", "shapes": [{"w": 24, "h": 4}]},
    ]
    body["orders"].append(["parent", "suffix"])
    body["orderSources"].append("explicit_saved")
    problem = LayoutProblem.model_validate(body)
    before = build_complete_incumbent(problem)
    result = refine(problem, before)
    assert audit(problem, result["placements"], reading_orders=result["readingOrders"])[
        "valid"
    ]
    rows = {row["id"]: row for row in result["placements"]}
    assert rows["chart"]["x"] == 6
    assert rows["parent"] == next(r for r in before if r["id"] == "parent")
    assert rows["suffix"] == next(r for r in before if r["id"] == "suffix")


def test_mixed_profiles_keep_regular_metric_grid_with_admitted_six_eight_ten_widths():
    body = problem_for("047").model_dump()
    for node in body["nodes"]:
        if node["kind"] == "metric":
            node["shapes"] = [
                {"w": w, "h": h, "cost": w * h - 16}
                for w in (4, 6, 8, 10, 12)
                for h in (4, 5, 6, 8, 10, 12)
                if w * h <= 48
            ]
        elif node["id"] == "rank":
            node["shapes"] = [{"w": 6, "h": 16}]
        elif node["id"] == "chart":
            node["shapes"] = [{"w": 10, "h": 16}]
    problem = LayoutProblem.model_validate(body)
    result = refine(problem)
    rows = {r["id"]: r for r in result["placements"]}
    assert [(rows[f"m{i}"]["x"], rows[f"m{i}"]["y"]) for i in range(1, 7)] == [
        (6, 0),
        (10, 0),
        (6, 4),
        (10, 4),
        (6, 8),
        (10, 8),
    ]
    assert (rows["m7"]["x"], rows["m7"]["y"], rows["m7"]["w"]) == (6, 12, 8)
    assert rows["rank"]["y"] == rows["chart"]["y"] == 0
    assert rows["chart"]["x"] == 14
    assert result["gapCells"] == 0


def test_independent_two_metrics_rank_and_two_chart_shape_structure():
    # Shape-only analogue of 049; no assertion about its incomplete real mapping.
    body = {
        "version": "constraint-v1",
        "nodes": [
            {"id": id, "kind": kind, "shapes": [{"w": w, "h": h}]}
            for id, kind, w, h in [
                ("a", "metric", 6, 3),
                ("b", "metric", 6, 3),
                ("c", "rank", 12, 9),
                ("d", "chart", 12, 6),
                ("e", "chart", 12, 6),
            ]
        ],
        "orders": [["a", "c", "d", "b", "e"]],
        "orderSources": ["derived_stamp"],
    }
    result = refine(LayoutProblem.model_validate(body))
    rows = {r["id"]: r for r in result["placements"]}
    assert [(rows[id]["x"], rows[id]["y"]) for id in "abcde"] == [
        (0, 0),
        (6, 0),
        (0, 3),
        (12, 0),
        (12, 6),
    ]


def test_smaller_closed_window_is_not_lost_to_larger_suffix_window():
    body = problem_for("050").model_dump()
    body["nodes"] += [
        {"id": f"tail{i}", "kind": "chart", "shapes": [{"w": 24, "h": 4}]}
        for i in range(5)
    ]
    body["orders"][0].extend(f"tail{i}" for i in range(5))
    problem = LayoutProblem.model_validate(body)
    result = refine(problem, deadline=time.monotonic() + 0.4)
    rows = {r["id"]: r for r in result["placements"]}
    assert all(rows[f"m{i}"]["y"] == 0 for i in range(1, 5))
    assert rows["rank"]["y"] == rows["chart"]["y"] == 3
    assert [(rows[f"tail{i}"]["x"], rows[f"tail{i}"]["y"]) for i in range(5)] == [
        (0, 18 + i * 4) for i in range(5)
    ]
    assert len(rows) == len(problem.nodes)


def test_reading_diagnostics_are_bounded_and_identify_window_outcomes():
    problem = problem_for("050", requiredBefore=[["rank", "m2"]])
    result = refine(problem)
    diagnostics = result["readingDiagnostics"]
    assert diagnostics["version"] == "reading-v1"
    assert 0 < len(diagnostics["windows"]) <= 64
    assert isinstance(diagnostics["truncated"], bool)
    ids = {node.id for node in problem.nodes}
    rejected = 0
    for window in diagnostics["windows"]:
        assert set(window) <= {
            "scopeId",
            "nodeIds",
            "inputSequence",
            "chosenSequence",
            "startMs",
            "elapsedMs",
            "remainingMs",
            "generated",
            "accepted",
            "rejected",
            "stopReason",
            "bestQualityChange",
            "bestScoreChange",
            "prunedProfiles",
            "repairNodeIds",
            "repairChosenSequence",
        }
        assert 2 <= len(window["nodeIds"]) <= 10
        assert set(window["nodeIds"]) <= ids
        assert set(window["chosenSequence"]) == set(window["inputSequence"])
        assert all(
            0 <= window[k] <= 10000000 for k in ("startMs", "elapsedMs", "remainingMs")
        )
        assert window["generated"] >= window["accepted"]
        rejected += window["rejected"].get("required_before", 0)
    assert rejected > 0


@pytest.mark.parametrize("objective", [None, "organize-v1"])
@pytest.mark.parametrize(
    "source",
    [None, "explicit_request", "explicit_saved", "derived_stamp", "derived_geometry"],
)
def test_reading_diagnostics_include_versioned_final_score_and_window_balance(
    objective, source
):
    from app.dashboard_layout.solver import _quality_score, regional_empty_violation

    problem = problem_for("050", source=source, objective=objective)
    result = refine(problem)
    diagnostics = result["readingDiagnostics"]
    policy = diagnostics["scorePolicy"]
    derived = source in ("derived_stamp", "derived_geometry")
    assert policy["version"] == (
        "regional-reading-v14" if derived else "layout-quality-v1"
    )
    assert policy["comparison"] == "lexicographic_min"
    assert policy["windowGapBasis"] == "scope_gap_upper_bound"
    assert policy["selectedGapBasis"] == "connected_components"
    assert diagnostics["selectedScoreStatus"] == "evaluated"
    assert diagnostics["scoreValuesClamped"] is False
    assert len(policy["fields"]) == len(diagnostics["selectedScore"])
    assert len(set(policy["fields"])) == len(policy["fields"])
    assert all(
        type(value) is int and -10000000 <= value <= 10000000
        for value in diagnostics["selectedScore"]
    )
    score = dict(zip(policy["fields"], diagnostics["selectedScore"]))
    assert score["gapCells"] == result["quality"]["gapCells"]
    assert score["crossBandMetricRowImbalance" if derived else "metricRowImbalance"] == result["quality"]["metricRowImbalance"]
    assert (
        score["negativeCrossBandAlignedBoundaryCount" if derived else "negativeAlignedBoundaryCount"]
        == -result["quality"]["alignedBoundaryCount"]
    )
    base_score = _quality_score(problem, result["quality"])
    selected_score = diagnostics["selectedScore"]
    if derived:
        assert score["crossBandMisalignedBoundaryCount"] == result["quality"]["misalignedBlockCount"]
        assert score["regionalMisalignedBlockCount"] <= result["quality"]["misalignedBlockCount"]
        assert score["shapeCost"] == result["quality"]["shapeCost"]
        assert score["totalHeight"] == result["quality"]["totalHeight"]
    else:
        assert tuple(selected_score) == (
            regional_empty_violation(problem, result["placements"]),
            *base_score,
        )
    for window in diagnostics["windows"]:
        for quality in window["bestQualityChange"].values():
            assert {
                "metricRowImbalance",
                "misalignedBlockCount",
                "alignedBoundaryCount",
            } <= quality.keys()
        for values in window["bestScoreChange"].values():
            assert len(values) == len(policy["fields"])
            assert all(
                type(value) is int and abs(value) <= 10000000 for value in values
            )


def test_expired_pass_reports_deadline_without_discarding_incumbent():
    problem = problem_for("050")
    result = refine(problem, deadline=time.monotonic() - 1)
    assert result["readingDiagnostics"]["stopReason"] == "deadline"
    assert len(result["placements"]) == len(problem.nodes)


def test_reading_score_logging_clamps_values_without_changing_quality():
    problem = LayoutProblem.model_validate(
        {
            "version": "constraint-v1",
            "nodes": [
                {"id": str(i), "shapes": [{"w": 24, "h": 1, "cost": 100000}]}
                for i in range(101)
            ],
            "orders": [[str(i) for i in range(101)]],
        }
    )
    result = refine(problem)
    diagnostics = result["readingDiagnostics"]
    score = dict(
        zip(diagnostics["scorePolicy"]["fields"], diagnostics["selectedScore"])
    )
    assert result["quality"]["shapeCost"] == 10100000
    assert score["shapeCost"] == 10000000
    assert diagnostics["scoreValuesClamped"] is True


@pytest.mark.parametrize("stop", ["deadline", "cancelled"])
def test_stopped_pass_skips_quality_work(monkeypatch, stop):
    from app.dashboard_layout import regional

    problem = problem_for("050")
    rows = build_complete_incumbent(problem)

    def forbidden(*args, **kwargs):
        raise AssertionError("stopped pass must not compute quality")

    monkeypatch.setattr(regional, "layout_quality", forbidden)
    event = Event()
    if stop == "cancelled":
        event.set()
    result = refine(
        problem,
        rows,
        cancelled=event,
        deadline=time.monotonic() + (-1 if stop == "deadline" else 1),
    )
    assert result["placements"] == rows
    diagnostics = result["readingDiagnostics"]
    assert diagnostics["selectedScore"] is None
    assert diagnostics["selectedScoreStatus"] == "baseline_retained_without_rescoring"


def test_same_geometry_replaces_inferior_reading_witness():
    from app.dashboard_layout.regional import refine_regions

    problem = LayoutProblem.model_validate(
        {
            "version": "constraint-v1",
            "nodes": [
                {"id": "a", "kind": "metric", "shapes": [{"w": 6, "h": 3}]},
                {"id": "b", "kind": "chart", "shapes": [{"w": 18, "h": 6}]},
                {"id": "c", "kind": "metric", "shapes": [{"w": 6, "h": 3}]},
            ],
            "orders": [["a", "b", "c"]],
            "orderSources": ["derived_geometry"],
        }
    )
    rows = [
        {"id": id, "x": x, "y": y, "w": w, "h": h, "variant": 0}
        for id, x, y, w, h in [("a", 0, 0, 6, 3), ("b", 6, 0, 18, 6), ("c", 0, 3, 6, 3)]
    ]
    result = refine_regions(
        problem,
        {
            "status": "feasible",
            "placements": rows,
            "readingOrders": problem.orders,
            "alternatives": [{"placements": rows, "readingOrders": [["a", "c", "b"]]}],
        },
        deadline=time.monotonic() + 1,
    )
    assert result["readingOrders"] == [["a", "c", "b"]]


@pytest.mark.parametrize(
    "status", ["cancelled", "budget_exhausted", "infeasible_candidates"]
)
def test_seed_stop_reason_is_not_reported_as_infeasible(monkeypatch, status):
    from app.dashboard_layout import reading

    event = Event()

    def seed(problem, *, deadline, cancelled):
        assert cancelled is event and deadline > time.monotonic()
        if status == "cancelled":
            event.set()
        elif status == "budget_exhausted":
            monkeypatch.setattr(time, "monotonic", lambda: deadline + 1)

    monkeypatch.setattr(reading, "seed_reading_orders", seed)
    assert solve(problem_for("050"), cancelled=event)["status"] == status


def test_replay_rejects_partial_metric_profile_coverage():
    profiles = json.loads(
        (
            Path(__file__).parent / "fixtures/layout_regional_preparation_profiles.json"
        ).read_text()
    )
    with pytest.raises(ValueError, match="incomplete metric profile coverage"):
        replay_problem("048", profiles=profiles)


@pytest.mark.parametrize("case", ["047", "048", "050"])
def test_all_metric_replay_preserves_every_node_and_historical_provenance(case):
    import hashlib

    directory = Path(__file__).parent / "fixtures"
    raw = (directory / f"layout_case{case}_geometry.json").read_bytes()
    archived = json.loads(raw)
    profiles = json.loads(
        (directory / "layout_regional_all_metric_preparation_profiles.json").read_text()
    )
    ranks = json.loads((directory / "layout_regional_rank_preparation_profiles.json").read_text())
    original = copy.deepcopy(archived["problem"])
    for node in original["nodes"]:
        node["shapes"] = archived["shapeSets"][node.pop("shapeSet")]
    original = LayoutProblem.model_validate(original)
    replay = replay_problem(case)
    assert len(replay.nodes) == len(original.nodes) == 40
    assert profiles["liveDomMeasured"] is False
    assert profiles["businessConfigRecovered"] is False
    assert (
        profiles["evidenceType"]
        == "production_candidate_generation_from_historical_solver_shape_profiles"
    )
    assert (
        profiles["sources"][case]["sourceFixtureSha256"]
        == hashlib.sha256(raw).hexdigest()
    )
    assert len(profiles["cases"][case]) == 10
    for before, after in zip(original.nodes, replay.nodes):
        assert before.model_dump(exclude={"shapes"}) == after.model_dump(
            exclude={"shapes"}
        )
        if before.kind == "rank" and case in ranks["cases"]:
            expected = ranks["shapeSets"][ranks["cases"][case][before.id]]
            assert [[s.w, s.h, s.cost, s.variant, s.parentVariant] for s in after.shapes] == expected
        elif before.kind != "metric":
            assert before == after
        else:
            expected = profiles["shapeSets"][profiles["cases"][case][before.id]]
            assert [
                [s.w, s.h, s.cost, s.variant, s.parentVariant] for s in after.shapes
            ] == expected
    assert replay.orders == original.orders
    assert replay.requiredBefore == original.requiredBefore
    assert replay.objective == original.objective


@pytest.mark.parametrize("case", ["047", "048", "050"])
def test_replay_merges_all_rank_production_profiles_without_changing_nodes(case):
    directory = Path(__file__).parent / "fixtures"
    ranks = json.loads((directory / "layout_regional_rank_preparation_profiles.json").read_text())
    replay = replay_problem(case)
    rank_nodes = [node for node in replay.nodes if node.kind == "rank"]
    assert {n.id for n in rank_nodes} == set(ranks["cases"][case])
    assert len(rank_nodes) == 9 and len(replay.nodes) == 40
    assert ranks["liveDomMeasured"] is False
    for node in rank_nodes:
        assert [[s.w, s.h, s.cost, s.variant, s.parentVariant] for s in node.shapes] == ranks["shapeSets"][ranks["cases"][case][node.id]]
        assert {(s.w, s.h) for s in node.shapes} == {(w, h) for w in (6, 7, 8) for h in range(15, 21)}


@pytest.mark.parametrize("nested", [False, True])
def test_derived_alignment_allows_complete_bands_and_nested_t_junctions(monkeypatch, nested):
    from app.dashboard_layout import regional

    top = [dict(id=f"m{i}", x=6*i, y=0, w=6, h=3, variant=0) for i in range(4)]
    rows = top + [dict(id="rank", x=0, y=3, w=6, h=15, variant=0),
                  dict(id="chart", x=6, y=3, w=18, h=15, variant=0)]
    if nested:
        rows = [dict(id="rank", x=0, y=0, w=6, h=12, variant=0),
                dict(id="chart", x=12, y=0, w=12, h=12, variant=0)] + [
                    dict(id=f"m{i}", x=6, y=3*i, w=6, h=3, variant=0) for i in range(4)]
        rows.sort(key=lambda row: (row["x"], row["y"]))
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1", "budgetMs": 3000,
        "nodes": [dict(id=r["id"], kind="metric" if r["id"].startswith("m") else "chart",
                       shapes=[dict(w=r["w"], h=r["h"])]) for r in rows],
        "orders": [[r["id"] for r in rows]], "orderSources": ["derived_geometry"],
    })
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = refine(problem, rows)
    d = result["readingDiagnostics"]
    score = dict(zip(d["scorePolicy"]["fields"], d["selectedScore"]))
    assert score["regionalMisalignedBlockCount"] == 0
    if not nested:
        assert result["quality"]["misalignedBlockCount"] == 2
        assert score["crossBandMisalignedBoundaryCount"] == 2


def test_region_alignment_still_counts_exposed_boundary_changes():
    from app.dashboard_layout.regional import _region_alignment

    assert _region_alignment([
        dict(x=0, y=0, w=12, h=3), dict(x=0, y=3, w=6, h=3)
    ]) == (0, 2)


def test_metric_neighbor_consistency_is_local_derived_and_size_only():
    from app.dashboard_layout.regional import _metric_neighbor_imbalance

    problem = problem_for("050")
    rows = [{"id": n.id, "w": 6, "h": 3} for n in problem.nodes]
    assert _metric_neighbor_imbalance(problem, rows) == 0
    isolated = [dict(r, w=4, h=4) if r["id"] == "m1" else r for r in rows]
    assert _metric_neighbor_imbalance(problem, isolated) == 0
    changed = [dict(r, w=4, h=4) if r["id"] == "m2" else r for r in rows]
    assert _metric_neighbor_imbalance(problem, changed) > 0
    assert _metric_neighbor_imbalance(problem.model_copy(update={"orderSources": None}), changed) == 0
    assert _metric_neighbor_imbalance(problem.model_copy(update={"orderSources": ["explicit_request"]}), changed) == 0
    body = problem.model_dump()
    def node(id, kind, sizes):
        return dict(id=id, kind=kind, shapes=[dict(w=w, h=h) for w, h in sizes])
    body["nodes"] = [node("a", "metric", [(6, 3)]), *[
        node(f"c{i}", "chart", [(24, 3)]) for i in range(9)
    ], node("b", "metric", [(4, 4)])]
    body["orders"] = [[n["id"] for n in body["nodes"]]]
    distant = LayoutProblem.model_validate(body)
    assert _metric_neighbor_imbalance(distant, [
        {"id": n.id, "w": n.shapes[0].w, "h": n.shapes[0].h} for n in distant.nodes
    ]) == 0


def test_uniformly_tall_metric_groups_do_not_hide_height_excess():
    from app.dashboard_layout.regional import _metric_group_height_excess

    problem = problem_for("050")
    rows = [{"id": n.id, "w": 6, "h": 3, "variant": 0} for n in problem.nodes]
    orders = [["m1", "m2", "m3", "m4", "rank", "chart"]]
    assert _metric_group_height_excess(problem, rows, orders) == 0
    tall = [dict(row, h=6) if row["id"].startswith("m") else row for row in rows]
    assert _metric_group_height_excess(problem, tall, orders) == 12
    assert _metric_group_height_excess(problem.model_copy(update={"orderSources": None}), tall, orders) == 0
    body = problem.model_dump()
    for node in body["nodes"]:
        if node["kind"] == "metric":
            node["shapes"] = [dict(w=6, h=h) for h in ([4, 6] if node["id"] == "m1" else [3, 4, 6])]
    mixed = LayoutProblem.model_validate(body)
    balanced = [dict(row, h=4) if row["id"].startswith("m") else row for row in rows]
    assert _metric_group_height_excess(mixed, balanced, orders) == 0


def test_metric_balance_does_not_join_an_isolated_metric_across_a_complete_band():
    from app.dashboard_layout.regional import _metric_group_imbalance
    from app.dashboard_layout.solver import layout_quality

    rows = [dict(id="a", x=0, y=0, w=6, h=4, variant=0),
            dict(id="b", x=6, y=0, w=6, h=4, variant=0),
            dict(id="chart", x=12, y=0, w=12, h=4, variant=0),
            dict(id="single", x=0, y=4, w=3, h=8, variant=0),
            dict(id="tail", x=3, y=4, w=21, h=8, variant=0)]
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1", "budgetMs": 3000,
        "nodes": [dict(id=r["id"], kind="chart" if r["id"] in ("chart", "tail") else "metric",
                       shapes=[dict(w=r["w"], h=r["h"])]) for r in rows],
        "orders": [[r["id"] for r in rows]], "orderSources": ["derived_geometry"],
    })
    assert layout_quality(problem, rows)["metricRowImbalance"] == 7
    assert _metric_group_imbalance(problem, rows, problem.orders) == 0


def test_unchanged_metric_run_cohesion_includes_all_its_bands():
    from app.dashboard_layout.regional import _metric_group_holes

    body = problem_for("050").model_dump()
    body["nodes"] = [dict(id=f"m{i}", kind="metric", shapes=[dict(w=6, h=4)]) for i in range(6)]
    body["orders"] = [[n["id"] for n in body["nodes"]]]
    problem = LayoutProblem.model_validate(body)
    fragmented = [dict(id=f"m{i}", x=(i*6 if i<4 else 0), y=(0 if i<4 else (i-3)*4), w=6, h=4) for i in range(6)]
    compact = [dict(id=f"m{i}", x=(i%2)*6, y=(i//2)*4, w=6, h=4) for i in range(6)]
    assert _metric_group_holes(problem, fragmented, problem.orders) == 144
    assert _metric_group_holes(problem, compact, problem.orders) == 0


def test_complete_region_height_precedes_metric_shelf_count(monkeypatch):
    from app.dashboard_layout import regional

    ids = [f"m{i}" for i in range(6)]
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1", "budgetMs": 3000,
        "nodes": [dict(id=id, kind="metric", shapes=[dict(w=6, h=4), dict(w=8, h=4)]) for id in ids]
            + [dict(id="chart", kind="chart", shapes=[dict(w=12, h=12), dict(w=24, h=8)])],
        "orders": [ids + ["chart"]], "orderSources": ["derived_geometry"],
    })
    compact = [dict(id=id, x=(i % 2)*6, y=(i // 2)*4, w=6, h=4, variant=0) for i, id in enumerate(ids)]
    compact += [dict(id="chart", x=12, y=0, w=12, h=12, variant=0)]
    shelves = [dict(id=id, x=(i % 3)*8, y=(i // 3)*4, w=8, h=4, variant=0) for i, id in enumerate(ids)]
    shelves += [dict(id="chart", x=0, y=8, w=24, h=8, variant=0)]
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = regional.refine_regions(problem, dict(status="feasible", placements=shelves,
        alternatives=[dict(placements=compact)]), deadline=time.monotonic()+1)
    assert result["quality"]["totalHeight"] == 12
    assert result["placements"] == compact


def test_bounded_region_pairs_cover_both_frontiers_before_repetition():
    from app.dashboard_layout.regional import _region_pairs

    left, right = list(range(12)), list(range(24))
    pairs = list(_region_pairs(left, right, 24))
    assert len(pairs) == len(set(pairs)) == 24
    assert {a for a, _ in pairs} == set(left)
    assert {b for _, b in pairs} == set(right)
    assert (2, 4) in list(_region_pairs(left, right, 96))
    complete = list(_region_pairs(left, right, 1000))
    assert len(complete) == len(set(complete)) == 12 * 24
    assert list(_region_pairs([], right, 24)) == []


def replay_problem(case, *, profiles=None, historical_partial=False):
    """Full coverage by default; partial mode only reproduces frozen old regressions."""
    directory = Path(__file__).parent / "fixtures"
    fixture = json.loads((directory / f"layout_case{case}_geometry.json").read_text())
    if profiles is None:
        filename = (
            "layout_regional_preparation_profiles.json"
            if historical_partial
            else "layout_regional_all_metric_preparation_profiles.json"
        )
        profiles = json.loads((directory / filename).read_text())
    body = copy.deepcopy(fixture["problem"])
    replacements = profiles["cases"].get(case, {})
    metrics = {node["id"] for node in body["nodes"] if node["kind"] == "metric"}
    if not historical_partial and set(replacements) != metrics:
        raise ValueError("incomplete metric profile coverage")
    ranks = json.loads((directory / "layout_regional_rank_preparation_profiles.json").read_text())
    rank_replacements = {} if historical_partial else ranks["cases"].get(case, {})
    if rank_replacements and set(rank_replacements) != {n["id"] for n in body["nodes"] if n["kind"] == "rank"}:
        raise ValueError("incomplete rank profile coverage")
    for node in body["nodes"]:
        node["shapes"] = fixture["shapeSets"][node.pop("shapeSet")]
        index = replacements.get(node["id"])
        if index is not None:
            node["shapes"] = [
                dict(zip(("w", "h", "cost", "variant", "parentVariant"), values))
                for values in profiles["shapeSets"][index]
            ]
        if node["id"] in rank_replacements:
            node["shapes"] = [
                dict(zip(("w", "h", "cost", "variant", "parentVariant"), values))
                for values in ranks["shapeSets"][rank_replacements[node["id"]]]
            ]
    body["orderSources"] = ["derived_geometry"] * len(body["orders"])
    body["budgetMs"] = 12000
    return LayoutProblem.model_validate(body)


@pytest.mark.parametrize("budget", [3000, 6000, 12000])
def test_production_profile_replay_050_preserves_order_and_tiles_first_region(budget):
    """Accept aligned real-domain layouts without prescribing a four-metric shelf."""
    from app.dashboard_layout.reading import MAX_READING_WINDOW

    problem = replay_problem("050").model_copy(update={"budgetMs": budget})
    before = problem.model_dump()
    result = solve(problem)
    assert problem.model_dump() == before
    assert result["status"] == "feasible"
    placements = result["placements"]
    rows = {row["id"]: row for row in placements}
    assert len(placements) == len(rows) == len(problem.nodes) == 40
    assert set(rows) == {node.id for node in problem.nodes}
    report = audit(problem, placements, reading_orders=result["readingOrders"])
    assert report["valid"], report
    metric_ids = {node.id for node in problem.nodes if node.kind == "metric"}
    assert len(metric_ids) == 10
    assert [[id for id in order if id in metric_ids] for order in result["readingOrders"]] == [
        [id for id in order if id in metric_ids] for order in problem.orders
    ]
    # Check the first bounded reading window, including whole successor cards.
    first_ids = problem.orders[0][:MAX_READING_WINDOW]
    bottom = max(rows[id]["y"] + rows[id]["h"] for id in first_ids)
    while True:
        extended = max(row["y"] + row["h"] for row in placements if row["y"] < bottom)
        if extended == bottom:
            break
        bottom = extended
    first_region = [row for row in placements if row["y"] < bottom]
    occupied = {(x, y) for row in first_region
                for x in range(row["x"], row["x"] + row["w"])
                for y in range(row["y"], row["y"] + row["h"])}
    missing = {(x, y) for x in range(24) for y in range(bottom)} - occupied
    print(json.dumps({"case": "050", "budgetMs": budget, "gapCells": report["gapCells"],
                      "firstRegionBottom": bottom, "firstRegionMissingCells": len(missing),
                      "firstRegionPlacements": first_region}, sort_keys=True))
    assert not missing, f"first region has uncovered cells: {sorted(missing)[:12]}"


def test_production_profile_replay_048_preserves_order_and_tiles_first_region():
    """Accept aligned real-domain layouts without prescribing a six-metric grid."""
    from app.dashboard_layout.reading import MAX_READING_WINDOW

    problem = replay_problem("048")
    before = problem.model_dump()
    result = solve(problem)
    assert problem.model_dump() == before
    assert result["status"] == "feasible"
    placements = result["placements"]
    rows = {row["id"]: row for row in placements}
    assert len(placements) == len(rows) == len(problem.nodes) == 40
    assert set(rows) == {node.id for node in problem.nodes}
    report = audit(problem, placements, reading_orders=result["readingOrders"])
    assert report["valid"], report
    metric_ids = {node.id for node in problem.nodes if node.kind == "metric"}
    assert len(metric_ids) == 10
    assert [[id for id in order if id in metric_ids] for order in result["readingOrders"]] == [
        [id for id in order if id in metric_ids] for order in problem.orders
    ]
    # Check the first bounded reading window, including whole successor cards.
    first_ids = problem.orders[0][:MAX_READING_WINDOW]
    bottom = max(rows[id]["y"] + rows[id]["h"] for id in first_ids)
    while True:
        extended = max(row["y"] + row["h"] for row in placements if row["y"] < bottom)
        if extended == bottom:
            break
        bottom = extended
    first_region = [row for row in placements if row["y"] < bottom]
    occupied = {(x, y) for row in first_region
                for x in range(row["x"], row["x"] + row["w"])
                for y in range(row["y"], row["y"] + row["h"])}
    missing = {(x, y) for x in range(24) for y in range(bottom)} - occupied
    print(json.dumps({"case": "048", "budgetMs": problem.budgetMs, "gapCells": report["gapCells"],
                      "firstRegionBottom": bottom, "firstRegionMissingCells": len(missing),
                      "firstRegionPlacements": first_region}, sort_keys=True))
    assert not missing, f"first region has uncovered cells: {sorted(missing)[:12]}"


@pytest.mark.parametrize("reverse", [False, True])
def test_alignment_precedes_derived_reading_displacement(monkeypatch, reverse):
    from app.dashboard_layout import regional

    problem = replay_problem("050", historical_partial=True)
    evidence = json.loads(
        (
            Path(__file__).parent / "fixtures/layout_regional_score_witnesses.json"
        ).read_text()
    )
    candidates = [evidence["fragmented"], evidence["grouped"]]
    if reverse:
        candidates.reverse()
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = regional.refine_regions(
        problem,
        {"status": "feasible", **candidates[0], "alternatives": candidates},
        deadline=time.monotonic() + 2,
    )
    assert result["placements"] == evidence["grouped"]["placements"]
    assert result["quality"]["misalignedBlockCount"] < 32


def test_size_variants_do_not_evict_distinct_metric_grouping(monkeypatch):
    from app.dashboard_layout import regional

    problem = replay_problem("048", historical_partial=True)
    evidence = json.loads(
        (
            Path(__file__).parent / "fixtures/layout_regional_diversity_witnesses.json"
        ).read_text()
    )

    def expand(plan):
        return {
            "placements": [
                dict(zip(("id", "x", "y", "w", "h", "variant"), row))
                for row in plan["rows"]
            ],
            "readingOrders": plan["orders"],
        }

    grouped = expand(evidence["grouped"])
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = regional.refine_regions(
        problem,
        {
            "status": "feasible",
            **grouped,
            "alternatives": [expand(plan) for plan in evidence["fragmented"]],
        },
        deadline=time.monotonic() + 2,
    )
    assert len(result["alternatives"]) <= 8
    assert any(a["placements"] == grouped["placements"] for a in result["alternatives"])


def test_common_metric_band_is_generated_before_expensive_width_search(monkeypatch):
    from itertools import count
    from types import SimpleNamespace

    from app.dashboard_layout import regions

    problem = replay_problem("050", historical_partial=True)
    ids = problem.orders[0][:6]
    nodes = {node.id: node for node in problem.nodes}
    ordered = [nodes[id] for id in ids if nodes[id].kind == "metric"]
    ordered += [nodes[id] for id in ids if nodes[id].kind != "metric"]
    ordered = [
        node.model_copy(update={"id": f"renamed-{i}"}) for i, node in enumerate(ordered)
    ]
    ticks = count()
    monkeypatch.setattr(regions, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    plans = regions.region_plans(ordered, 2500, ordered_metrics=True)
    assert any(
        all(
            row["y"] == 0 and row["x"] == i * 6 and row["w"] == 6
            for i, row in enumerate(plan[:4])
        )
        and plan[4]["y"] == plan[5]["y"] == plan[0]["h"]
        and plan[4]["h"] == plan[5]["h"] == 15
        for plan in plans
    )


def test_common_metric_grid_matches_chart_height_before_width_search(monkeypatch):
    from itertools import count
    from types import SimpleNamespace

    from app.dashboard_layout import regions

    problem = replay_problem("048", historical_partial=True)
    ids = problem.orders[0][:7]
    nodes = {node.id: node for node in problem.nodes}
    ordered = [nodes[id] for id in ids if nodes[id].kind == "metric"]
    ordered += [nodes[id] for id in ids if nodes[id].kind != "metric"]
    ordered = [
        node.model_copy(update={"id": f"renamed-{i}"}) for i, node in enumerate(ordered)
    ]
    ticks = count()
    monkeypatch.setattr(regions, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    plans = regions.region_plans(ordered, 2500, ordered_metrics=True)
    assert any(
        [row["y"] for row in plan[:6]] == [0, 0, 3, 3, 6, 6]
        and [row["x"] for row in plan[:6]] == [0, 6, 0, 6, 0, 6]
        and plan[6]["x"] == 9
        and plan[6]["y"] == 0
        and plan[6]["h"] == 10
        for plan in plans
    )


@pytest.mark.parametrize("case", ["048", "050"])
def test_common_regions_are_seeded_before_nonuniform_profile_expansion(monkeypatch, case):
    from itertools import count
    from types import SimpleNamespace
    from app.dashboard_layout import regions

    problem = replay_problem(case)
    by_id = {n.id: n for n in problem.nodes}
    ids = problem.orders[0][:(7 if case == "048" else 6)]
    ordered = [by_id[id] for id in ids if by_id[id].kind == "metric"]
    ordered += [by_id[id] for id in ids if by_id[id].kind != "metric"]
    ordered = [node.model_copy(update={"id": f"node-{i}"}) for i, node in enumerate(ordered)]
    ticks = count()
    monkeypatch.setattr(regions, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    plans = regions.region_plans(ordered, 1200, ordered_metrics=True)
    expected = ([(i % 2 * 6, i // 2 * 4, 6, 4) for i in range(6)] if case == "048"
                else [(i * 6, 0, 6, 3) for i in range(4)])
    assert any([(r["x"], r["y"], r["w"], r["h"]) for r in plan[:len(expected)]] == expected for plan in plans)


def test_region_frontier_keeps_uniform_grid_and_column_boundary():
    from app.dashboard_layout.regions import region_plans

    problem = replay_problem("048")
    nodes = {n.id: n for n in problem.nodes}
    ids = problem.orders[0][:7]
    ordered = [nodes[id] for id in ids if nodes[id].kind == "metric"]
    ordered += [nodes[id] for id in ids if nodes[id].kind != "metric"]
    plans = region_plans(ordered, time.monotonic() + 1, ordered_metrics=True)
    assert any(
        [(r["x"], r["y"], r["w"], r["h"]) for r in plan[:6]]
        == [(i % 2 * 6, i // 2 * 4, 6, 4) for i in range(6)]
        and (plan[6]["x"], plan[6]["y"], plan[6]["w"], plan[6]["h"]) == (12, 0, 12, 12)
        for plan in plans
    )

    problem = replay_problem("050")
    nodes = {n.id: n for n in problem.nodes}
    ordered = [nodes[id] for id in problem.orders[0][6:10]]
    plans = region_plans(ordered, time.monotonic() + 1, ordered_metrics=True)
    assert any(
        [(r["x"], r["y"], r["w"], r["h"]) for r in plan[:3]]
        == [(0, i * 3, 6, 3) for i in range(3)]
        and (plan[3]["x"], plan[3]["y"], plan[3]["w"], plan[3]["h"]) == (6, 0, 18, 9)
        for plan in plans
    )


@pytest.mark.parametrize("stop", ["deadline", "cancelled"])
def test_stopped_refinement_retains_complete_candidate(stop):
    problem = problem_for("050")
    rows = build_complete_incumbent(problem)
    cancelled = Event()
    if stop == "cancelled":
        cancelled.set()
    result = refine(
        problem,
        rows,
        cancelled=cancelled,
        deadline=time.monotonic() + (-1 if stop == "deadline" else 1),
    )
    assert result["placements"] == rows
    assert result["readingOrders"] == problem.orders


@pytest.mark.parametrize("case", ["048", "050", "047"])
@pytest.mark.parametrize("objective", [None, "organize-v1"])
def test_public_solve_uses_common_regional_pass(case, objective):
    problem = problem_for(case, objective=objective)
    result = solve(problem)
    assert result["status"] == "feasible"
    assert result["gapCells"] == 0
    assert audit(problem, result["placements"], reading_orders=result["readingOrders"])[
        "valid"
    ]


@pytest.mark.parametrize("case", ["044", "045", "047", "048", "050"])
def test_recorded_geometry_remains_admitted_and_complete(case):
    fixture = json.loads(
        (
            Path(__file__).parent / f"fixtures/layout_case{case}_geometry.json"
        ).read_text()
    )
    body = copy.deepcopy(fixture["problem"])
    for node in body["nodes"]:
        node["shapes"] = fixture["shapeSets"][node.pop("shapeSet")]
    # No provenance or sizes are invented for historical replay.
    problem = LayoutProblem.model_validate(body)
    result = refine(problem, fixture["placements"], deadline=time.monotonic() + 0.5)
    assert result["readingOrders"] == problem.orders
    assert audit(problem, result["placements"], reading_orders=result["readingOrders"])[
        "valid"
    ]
    assert len(result["placements"]) == len(problem.nodes)
