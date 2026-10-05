import time
from threading import Event

import pytest
from pydantic import ValidationError

from app.dashboard_layout import reading
from app.dashboard_layout.solver import LayoutProblem, audit, solve


def problem_for(order=None, *, required=None, source="derived_geometry", containers=()):
    order = order or ["m0", "m1", "m2", "c0", "c1"]
    body = {
        "version": "constraint-v1",
        "nodes": [{"id": id, "kind": "metric" if id.startswith("m") else "chart",
                   "container": id in containers, "shapes": [{"w": 24, "h": 4}]}
                  for id in order],
        "orders": [order],
        "requiredBefore": required if required is not None else [["c0", "m1"], ["c1", "m2"]],
    }
    if source is not None:
        body["orderSources"] = [source]
    return LayoutProblem.model_validate(body)


def test_greedy_trap_has_a_local_seed_and_valid_full_width_geometry():
    problem = problem_for()
    witness = [["m0", "c0", "c1", "m1", "m2"]]
    rows = [{"id": id, "x": 0, "y": i * 4, "w": 24, "h": 4, "variant": 0}
            for i, id in enumerate(witness[0])]
    assert audit(problem, rows, reading_orders=witness)["valid"]
    before = problem.model_dump()
    chosen = reading.seed_reading_orders(problem)
    assert chosen is not None
    assert reading.reading_order_error(problem, chosen) is None
    assert chosen[0][0] == "m0"
    assert problem.model_dump() == before


def test_solve_does_not_declare_the_greedy_trap_infeasible():
    problem = problem_for()
    result = solve(problem, deadline=time.monotonic() + 2)
    assert result["status"] == "feasible"
    assert audit(problem, result["placements"], reading_orders=result["readingOrders"])["valid"]


@pytest.mark.parametrize("order", [
    ["m0", "m1", "m2", "c0", "c1"],
    ["m0", "c0", "m1", "c1", "m2"],
    ["c0", "m0", "c1", "m1", "m2"],
    ["m0", "c0", "m1", "c1", "m2", "c2"],
])
def test_seed_can_reach_every_small_disjoint_window_witness(order):
    problem = problem_for(order, required=[])
    nodes = {node.id: node for node in problem.nodes}

    # Enumerate only the permitted window grammar, not arbitrary permutations.
    def legal_suffixes(start):
        if start == len(order):
            return {()}
        return {tuple(window) + tail
                for end in range(start + 1, min(len(order), start + 10) + 1)
                for window in reading.metric_orders(order[start:end], nodes)
                for tail in legal_suffixes(end)}

    for witness in legal_suffixes(0):
        constrained = LayoutProblem.model_validate({**problem.model_dump(),
            "requiredBefore": list(zip(witness, witness[1:]))})
        assert reading.seed_reading_orders(constrained) == [list(witness)]


@pytest.mark.parametrize("length", [10, 11])
def test_window_limit_remains_against_the_original_request(length):
    order = [*[f"c{i}" for i in range(length - 1)], "m0"]
    problem = problem_for(order, required=[["m0", "c0"]])
    chosen = reading.seed_reading_orders(problem)
    if length == 10:
        assert chosen == [["m0", *order[:-1]]]
        assert reading.reading_order_error(problem, chosen) is None
    else:
        assert chosen is None


def test_scopes_are_solved_independently_and_keep_explicit_order():
    body = problem_for().model_dump()
    body["nodes"].extend([
        {"id": "group", "container": True, "shapes": [{"w": 24, "h": 40}]},
        {"id": "other", "shapes": [{"w": 24, "h": 4}]},
    ])
    for node in body["nodes"][:5]:
        node["parentId"] = "group"
    body["orders"].append(["group", "other"])
    body["orderSources"].append("explicit_saved")
    body["requiredBefore"].append(("group", "other"))
    problem = LayoutProblem.model_validate(body)
    chosen = reading.seed_reading_orders(problem)
    assert chosen is not None
    assert chosen[1] == ["group", "other"]
    assert reading.reading_order_error(problem, chosen) is None
    body["requiredBefore"].append(("group", "m0"))
    with pytest.raises(ValidationError, match="scope"):
        LayoutProblem.model_validate(body)


@pytest.mark.parametrize("source", [None, "explicit_request", "explicit_saved", "derived_stamp"])
def test_valid_seed_is_unchanged_and_strict_sources_never_reorder(source):
    problem = problem_for(required=[["m0", "c0"]], source=source)
    assert reading.seed_reading_orders(problem) == problem.orders
    if source != "derived_stamp":
        invalid = problem.model_copy(update={"requiredBefore": [("c0", "m0")]})
        assert reading.seed_reading_orders(invalid) is None


def test_container_blocks_cannot_be_crossed_to_satisfy_an_edge():
    problem = problem_for(["m0", "group", "m1"], required=[["m1", "group"]], containers=["group"])
    assert reading.seed_reading_orders(problem) is None


def test_cycles_still_fail_validation_and_cannot_produce_a_seed():
    with pytest.raises(ValidationError, match="cyclic"):
        problem_for(required=[["m0", "c0"], ["c0", "m0"]])
    invalid = problem_for().model_copy(update={"requiredBefore": [("m0", "c0"), ("c0", "m0")]})
    assert reading.seed_reading_orders(invalid) is None


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_stopped_search_does_not_enumerate_candidates(reason, monkeypatch):
    event = Event()
    if reason == "cancelled":
        event.set()
    monkeypatch.setattr(reading, "metric_orders", lambda *_: pytest.fail("enumerated after stop"))
    assert reading.seed_reading_orders(problem_for(),
        deadline=time.monotonic() - 1 if reason == "deadline" else None, cancelled=event) is None


@pytest.mark.parametrize("reason", ["deadline", "cancelled"])
def test_search_stops_between_candidates_and_discards_late_seed(reason, monkeypatch):
    event = Event()
    clock = [0.0]
    calls = []
    original = reading.metric_orders

    def stopping_orders(ids, nodes):
        calls.append(list(ids))
        for candidate in original(ids, nodes):
            if reason == "cancelled":
                event.set()
            else:
                clock[0] = 2.0
            yield candidate

    monkeypatch.setattr(reading, "metric_orders", stopping_orders)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    assert reading.seed_reading_orders(problem_for(), deadline=1.0, cancelled=event) is None
    assert len(calls) == 1


def test_two_hundred_nodes_use_bounded_windows_not_permutations(monkeypatch):
    order = []
    edges = []
    for block in range(40):
        ids = [f"m{block}-0", f"m{block}-1", f"m{block}-2", f"c{block}-0", f"c{block}-1"]
        order.extend(ids)
        edges.extend([[ids[3], ids[1]], [ids[4], ids[2]]])
    problem = problem_for(order, required=edges)
    sizes = []
    original = reading.metric_orders

    def counted_orders(ids, nodes):
        sizes.append(len(ids))
        yield from original(ids, nodes)

    monkeypatch.setattr(reading, "metric_orders", counted_orders)
    chosen = reading.seed_reading_orders(problem)
    assert chosen is not None
    assert reading.reading_order_error(problem, chosen) is None
    assert max(sizes) <= 10
    # Includes the final independent order audit as well as seed construction.
    assert len(sizes) <= 3 * len(order) * reading.MAX_READING_WINDOW
