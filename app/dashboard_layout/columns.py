"""Bounded ordered-column construction for complete, independent page bands."""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Event

from app.dashboard_layout.diagnostics import count
from app.dashboard_layout.solver import COLS, LayoutProblem, Shape, audit

MAX_REGION_NODES = 8
MAX_REGION_HEIGHT = 100


@dataclass(frozen=True)
class Stack:
    area: int
    cost: int
    shapes: tuple[Shape, ...]


def _column_plan(nodes, height_limit, stopped):
    """DP over contiguous business subsequences, admitted sizes and grid width."""
    profiles = {}
    heights = set()
    for width in range(1, COLS + 1):
        if stopped():
            return None
        options = []
        for node in nodes:
            by_height = {}
            for shape in node.shapes:
                if shape.parentVariant or shape.w > width or shape.h > min(height_limit, node.maxH):
                    continue
                previous = by_height.get(shape.h)
                if previous is None or (-shape.w, shape.cost, shape.variant) < (
                        -previous.w, previous.cost, previous.variant):
                    by_height[shape.h] = shape
            options.append(tuple(by_height.values()))
        for start in range(len(nodes)):
            states = {0: Stack(0, 0, ())}
            for end in range(start, len(nodes)):
                if stopped():
                    return None
                following = {}
                for height, stack in states.items():
                    for shape in options[end]:
                        new_height = height + shape.h
                        if new_height > height_limit:
                            continue
                        area = stack.area + shape.w * shape.h
                        cost = stack.cost + shape.cost
                        previous = following.get(new_height)
                        if previous is None or (-area, cost) < (-previous.area, previous.cost):
                            following[new_height] = Stack(area, cost, stack.shapes + (shape,))
                if not following:
                    break
                states = following
                profiles[start, end + 1, width] = states
                heights.update(states)
    count("columns.stack_profiles", len(profiles))
    best, best_score = None, None
    # A column may end earlier, but every candidate is scored against the full
    # 24-column band, including the unused right edge and shorter columns.
    for height in sorted(heights):
        if stopped():
            break
        dp = {(0, 0): (0, 0, ())}
        for start in range(len(nodes)):
            if stopped():
                return best
            prefixes = [(w, p) for (end, w), p in dp.items() if end == start]
            for end in range(start + 1, len(nodes) + 1):
                for width in range(1, COLS + 1):
                    if stopped():
                        return best
                    choices = profiles.get((start, end, width), {})
                    stack = min((s for h, s in choices.items() if h <= height),
                                key=lambda s: (-s.area, s.cost), default=None)
                    if stack is None:
                        continue
                    for used, (area, cost, columns) in prefixes:
                        if used + width > COLS:
                            continue
                        value = (area + stack.area, cost + stack.cost,
                                 columns + ((used, start, stack.shapes),))
                        key = (end, used + width)
                        previous = dp.get(key)
                        if previous is None or (-value[0], value[1]) < (-previous[0], previous[1]):
                            dp[key] = value
        for (end, _), (area, cost, columns) in dp.items():
            if end != len(nodes):
                continue
            actual_height = max(sum(s.h for s in shapes) for _, _, shapes in columns)
            score = (COLS * actual_height - area, cost, actual_height)
            if best_score is not None and score >= best_score:
                continue
            rows = []
            for x, start, shapes in columns:
                y = 0
                for index, shape in enumerate(shapes):
                    rows.append(dict(id=nodes[start + index].id, x=x, y=y,
                                     w=shape.w, h=shape.h, variant=shape.variant))
                    y += shape.h
            best, best_score = rows, score
        if best_score is not None and best_score[0] == 0 and best_score[1] == 0:
            break
    return best


def refine_column_regions(problem: LayoutProblem, placements: list[dict], *,
                          deadline: float, cancelled: Event | None = None) -> list[dict]:
    """Improve whitespace only; retain the incumbent whenever search is inapplicable."""
    count("columns.calls")

    def stopped():
        return time.monotonic() >= deadline or bool(cancelled and cancelled.is_set())

    if stopped() or deadline - time.monotonic() < 0.02:
        count("columns.stopped")
        return placements
    if any(n.container or n.parentId is not None or n.fixedX is not None for n in problem.nodes):
        count("columns.skipped_scope_or_lock")
        return placements
    order = next((o for o in problem.orders if len(o) == len(problem.nodes)), None)
    if order is None:
        count("columns.skipped_order")
        return placements
    nodes = {n.id: n for n in problem.nodes}
    by_id = {r["id"]: r for r in placements}
    ordered = [by_id[id] for id in order]
    suffix_top = [r["y"] for r in ordered] + [10001]
    for index in range(len(order) - 1, -1, -1):
        suffix_top[index] = min(suffix_top[index], suffix_top[index + 1])
    boundaries, bottom = [0], 0
    for index, row in enumerate(ordered):
        bottom = max(bottom, row["y"] + row["h"])
        if bottom <= suffix_top[index + 1]:
            boundaries.append(index + 1)
    windows = []
    for start in boundaries:
        if stopped():
            count("columns.stopped")
            return placements
        for end in boundaries:
            if not 2 <= end - start <= MAX_REGION_NODES:
                continue
            rows = ordered[start:end]
            top = min(r["y"] for r in rows)
            bottom = max(r["y"] + r["h"] for r in rows)
            gap = COLS * (bottom - top) - sum(r["w"] * r["h"] for r in rows)
            if gap:
                windows.append((-gap, start - end, start, end))
    result = placements
    checked = audit(problem, result, include_gap_components=False)
    if not checked["valid"]:
        count("columns.skipped_invalid_incumbent")
        return placements
    current_gap = checked["gapCells"]
    touched = set()
    for _, _, start, end in sorted(windows):
        if stopped():
            count("columns.stopped")
            break
        ids = order[start:end]
        if touched.intersection(ids):
            continue
        current = {r["id"]: r for r in result}
        top = min(current[id]["y"] for id in ids)
        bottom = max(current[id]["y"] + current[id]["h"] for id in ids)
        count("columns.attempted")
        local = _column_plan([nodes[id] for id in ids],
                             min(MAX_REGION_HEIGHT, bottom - top), stopped)
        if cancelled and cancelled.is_set():
            count("columns.cancelled")
            return placements
        if local is None:
            count("columns.no_candidate")
            continue
        local_height = max(r["y"] + r["h"] for r in local)
        delta = top + local_height - bottom
        replacement = {r["id"]: dict(r, y=r["y"] + top) for r in local}
        proposal = [replacement[r["id"]] if r["id"] in replacement else
                    dict(r, y=r["y"] + delta) if r["y"] >= bottom else r for r in result]
        checked = audit(problem, proposal, include_gap_components=False)
        if not checked["valid"]:
            count("columns.rejected_" + checked["reason"])
        elif checked["gapCells"] < current_gap:
            count("columns.accepted")
            count("columns.gap_cells_removed", current_gap - checked["gapCells"])
            current_gap, result = checked["gapCells"], proposal
            touched.update(ids)
        else:
            count("columns.not_improved")
    return result
