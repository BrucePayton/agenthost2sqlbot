from app.dashboard_layout.incumbent import build_complete_incumbent
from app.dashboard_layout.solver import LayoutProblem, audit


def problem(nodes, orders=()):
    return LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": nodes,
        "orders": list(orders),
        "budgetMs": 1000,
    })


def test_builds_a_complete_incumbent_for_forty_wide_domains():
    nodes = [{
        "id": str(index),
        "shapes": [
            {"w": width, "h": height, "cost": width * height}
            for width in range(7, 24)
            for height in range(5, 18)
        ],
    } for index in range(40)]
    value = problem(nodes, [[str(index) for index in range(40)]])

    first = build_complete_incumbent(value)
    second = build_complete_incumbent(value)

    assert first == second
    assert len(first) == 40
    assert audit(value, first)["valid"]


def test_builds_container_profiles_bottom_up_and_fills_its_single_child():
    value = problem([
        {"id": "group", "container": True, "headerPx": 50, "shapes": [
            {"w": 12, "h": 6, "variant": 1},
        ]},
        {"id": "table", "parentId": "group", "shapes": [
            {"w": 12, "h": 8, "parentVariant": 1, "cost": 20},
            {"w": 24, "h": 8, "parentVariant": 1, "cost": 0},
        ]},
        {"id": "right", "fixedX": 12, "shapes": [{"w": 12, "h": 8}]},
    ], [["group", "right"], ["table"]])

    incumbent = build_complete_incumbent(value)
    by_id = {row["id"]: row for row in incumbent}

    assert by_id["table"]["w"] == 24
    assert by_id["right"]["x"] == 12
    assert by_id["group"]["h"] >= 8
    assert audit(value, incumbent)["valid"]


def test_returns_no_incumbent_when_fixed_geometry_is_impossible():
    value = problem([{
        "id": "impossible",
        "fixedX": 20,
        "shapes": [{"w": 8, "h": 4}],
    }])

    assert build_complete_incumbent(value) == []


def test_uses_a_valid_nested_original_before_greedy_repacking_overflows_frame():
    value = problem([
        {"id": "group", "container": True, "headerPx": 50, "maxH": 11,
         "original": {"x": 0, "y": 0, "w": 24, "h": 11},
         "shapes": [{"w": 24, "h": 11, "variant": 1}]},
        {"id": "a", "parentId": "group",
         "original": {"x": 0, "y": 0, "w": 16, "h": 5},
         "shapes": [{"w": 16, "h": 5, "parentVariant": 1}]},
        {"id": "b", "parentId": "group",
         "original": {"x": 0, "y": 5, "w": 16, "h": 5},
         "shapes": [{"w": 16, "h": 5, "parentVariant": 1}]},
        {"id": "c", "parentId": "group",
         "original": {"x": 16, "y": 0, "w": 8, "h": 10},
         "shapes": [{"w": 8, "h": 10, "parentVariant": 1}]},
    ])

    incumbent = build_complete_incumbent(value)

    assert incumbent
    assert audit(value, incumbent)["valid"]
    assert {row["id"]: (row["x"], row["y"]) for row in incumbent} == {
        "group": (0, 0), "a": (0, 0), "b": (0, 5), "c": (16, 0),
    }


def test_reports_the_container_height_that_rejected_every_scope_profile():
    value = problem([
        {"id": "group", "container": True, "headerPx": 50, "maxH": 11,
         "shapes": [{"w": 24, "h": 11, "variant": 1}]},
        {"id": "a", "parentId": "group",
         "shapes": [{"w": 16, "h": 5, "parentVariant": 1}]},
        {"id": "b", "parentId": "group",
         "shapes": [{"w": 16, "h": 5, "parentVariant": 1}]},
        {"id": "c", "parentId": "group",
         "shapes": [{"w": 8, "h": 10, "parentVariant": 1}]},
    ])
    failures = []

    assert build_complete_incumbent(value, diagnostics=failures) == []
    assert failures == [{
        "nodeId": "group", "scopeId": "root", "reason": "container_height",
        "requiredH": 15, "maxH": 11, "childBottom": 15,
    }]
