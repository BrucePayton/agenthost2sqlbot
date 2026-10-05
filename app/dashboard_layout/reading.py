"""Request-bound reading orders and bounded stable metric extraction."""
import time
from collections import defaultdict
from threading import Event

MAX_READING_WINDOW = 10
DERIVED_SOURCES = frozenset({"derived_stamp", "derived_geometry"})


def order_source(problem, index):
    return problem.orderSources[index] if problem.orderSources is not None else "legacy"


def metric_orders(ids, nodes):
    """Keep both subsequences stable; move one metric block within this window."""
    yield list(ids)
    if any(nodes[id].container for id in ids):
        return
    metrics = [id for id in ids if nodes[id].kind == "metric"]
    others = [id for id in ids if nodes[id].kind != "metric"]
    if not metrics or not others:
        return
    first = ids.index(metrics[0])
    for index in sorted(range(len(others) + 1), key=lambda i: (abs(i - first), i)):
        candidate = others[:index] + metrics + others[index:]
        if candidate != list(ids):
            yield candidate


def validate_order_contract(problem):
    if problem.orderSources is not None and len(problem.orderSources) != len(problem.orders):
        raise ValueError("orderSources must match orders")
    nodes = {node.id: node for node in problem.nodes}
    edges = set()
    for first, second in problem.requiredBefore:
        if (first not in nodes or second not in nodes or first == second
                or nodes[first].parentId != nodes[second].parentId):
            raise ValueError("invalid requiredBefore scope or identity")
        if (first, second) in edges:
            raise ValueError("duplicate requiredBefore")
        edges.add((first, second))
    for index, order in enumerate(problem.orders):
        chains = [order]
        if order_source(problem, index) in DERIVED_SOURCES:
            chains = [[id for id in order if nodes[id].kind == "metric"],
                      [id for id in order if nodes[id].kind != "metric"]]
        for chain in chains:
            edges.update(zip(chain, chain[1:]))
    outgoing = defaultdict(set)
    incoming = dict.fromkeys(nodes, 0)
    for first, second in edges:
        outgoing[first].add(second)
        incoming[second] += 1
    ready = [id for id, degree in incoming.items() if degree == 0]
    visited = 0
    while ready:
        first = ready.pop()
        visited += 1
        for second in outgoing[first]:
            incoming[second] -= 1
            if incoming[second] == 0:
                ready.append(second)
    if visited != len(nodes):
        raise ValueError("cyclic reading constraints")


def reading_order_error(problem, chosen):
    """Recognize disjoint <=10-node edits against the original, never edit history."""
    if not isinstance(chosen, list) or len(chosen) != len(problem.orders):
        return "order_identity"
    nodes = {node.id: node for node in problem.nodes}
    positions = {}
    for index, (original, selected) in enumerate(zip(problem.orders, chosen)):
        if (not isinstance(selected, list) or any(not isinstance(id, str) for id in selected)
                or len(selected) != len(original) or set(selected) != set(original)):
            return "order_identity"
        positions.update({id: (index, position) for position, id in enumerate(selected)})
        if selected == original:
            continue
        if order_source(problem, index) not in DERIVED_SOURCES:
            return "order_explicit"
        if ([id for id in selected if nodes[id].kind == "metric"] !=
                [id for id in original if nodes[id].kind == "metric"] or
                [id for id in selected if nodes[id].kind != "metric"] !=
                [id for id in original if nodes[id].kind != "metric"]):
            return "order_stability"
        reachable = {0}
        for start in range(len(original)):
            if start not in reachable:
                continue
            if original[start] == selected[start]:
                reachable.add(start + 1)
            for end in range(start + 2, min(len(original), start + MAX_READING_WINDOW) + 1):
                window = original[start:end]
                if (set(window) == set(selected[start:end])
                        and len({nodes[id].parentId for id in window}) == 1
                        and selected[start:end] in metric_orders(window, nodes)):
                    reachable.add(end)
        if len(original) not in reachable:
            return "order_locality"
    for first, second in problem.requiredBefore:
        if first in positions and second in positions:
            a, b = positions[first], positions[second]
            if a[0] == b[0] and a[1] >= b[1]:
                return "required_before"
    return None


def seed_reading_orders(problem, *, deadline: float | None = None, cancelled: Event | None = None):
    """Find a legal disjoint-window seed; None also signals an expired/cancelled search."""
    def stopped():
        return ((cancelled is not None and cancelled.is_set())
                or (deadline is not None and time.monotonic() >= deadline))

    if stopped():
        return None
    nodes = {node.id: node for node in problem.nodes}
    incoming = defaultdict(set)
    for first, second in problem.requiredBefore:
        incoming[second].add(first)
    chosen = []
    for index, order in enumerate(problem.orders):
        if stopped():
            return None
        if order_source(problem, index) not in DERIVED_SOURCES:
            chosen.append(list(order))
            continue
        positions = {id: i for i, id in enumerate(order)}
        # At each boundary the prefix membership is fixed, independent of its
        # traversal. One valid suffix suffices; shorter windows preserve more anchors.
        suffixes = {len(order): None}
        for start in range(len(order) - 1, -1, -1):
            if stopped():
                return None
            for end in range(start + 1, min(len(order), start + MAX_READING_WINDOW) + 1):
                if end not in suffixes:
                    continue
                for candidate in metric_orders(order[start:end], nodes):
                    if stopped():
                        return None
                    local = {id: start + i for i, id in enumerate(candidate)}
                    if all(positions.get(before, len(order)) < start
                           or local.get(before, end) < local[id]
                           for id in candidate for before in incoming[id]):
                        suffixes[start] = (end, candidate)
                        break
                if start in suffixes:
                    break
        if 0 not in suffixes:
            return None
        selected = []
        start = 0
        while start < len(order):
            if stopped():
                return None
            start, candidate = suffixes[start]
            selected.extend(candidate)
        chosen.append(selected)
    if stopped() or reading_order_error(problem, chosen) is not None or stopped():
        return None
    return chosen


def changed_regions(problem, chosen):
    """Smallest edited permutation intervals, including an adjacent metric anchor."""
    nodes = {node.id: node for node in problem.nodes}
    regions = []
    for original, selected in zip(problem.orders, chosen):
        start = 0
        while start < len(original):
            if original[start] == selected[start]:
                start += 1
                continue
            end = start + 1
            while end < len(original) and set(original[start:end]) != set(selected[start:end]):
                end += 1
            left = start
            while (left > 0 and end-left < MAX_READING_WINDOW
                   and nodes[selected[left]].kind == nodes[selected[left-1]].kind == "metric"):
                left -= 1
            regions.append(selected[left:end])
            start = end
    return regions
