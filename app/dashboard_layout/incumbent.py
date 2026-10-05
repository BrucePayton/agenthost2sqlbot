"""Deterministic complete layout used to warm-start mathematical optimization."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.dashboard_layout.diagnostics import count
from app.dashboard_layout.solver import COLS, LayoutProblem, Node, audit


@dataclass(frozen=True)
class _Profile:
    w: int
    h: int
    variant: int
    cost: int
    descendants: tuple[dict, ...] = ()


def _depths(problem: LayoutProblem) -> dict[str, int]:
    by_id = {node.id: node for node in problem.nodes}
    depths: dict[str, int] = {}
    for node in problem.nodes:
        depth = 0
        parent_id = node.parentId
        while parent_id is not None:
            depth += 1
            parent_id = by_id[parent_id].parentId
        depths[node.id] = depth
    return depths


def _scope_order(problem: LayoutProblem, nodes: list[Node]) -> list[Node]:
    by_id = {node.id: node for node in nodes}
    exact = next((order for order in problem.orders
                  if len(order) == len(nodes) and set(order) == set(by_id)), None)
    ids = exact or [node.id for node in nodes]
    return [by_id[node_id] for node_id in ids]


def _required_container_height(node: Node, child_plan: list[dict],
                               direct_child_ids: set[str]) -> int:
    bottom = max((row["y"] + row["h"] for row in child_plan
                  if row["id"] in direct_child_ids), default=0)
    step, gap = (34, 4) if node.parentId else (40, 10)
    needed = bottom * 34 + 12 + node.headerPx + gap
    return max(1, (needed + step - 1) // step)


def _profile_key(profile: _Profile, remaining: int) -> tuple:
    exact_fill = profile.w == remaining
    return (not exact_fill, profile.cost, profile.w * profile.h,
            profile.h, -profile.w, profile.variant)


def _place_scope(nodes: list[Node], profiles: dict[str, list[_Profile]]) -> list[dict]:
    placements: list[dict] = []
    x = 0
    y = 0
    row_height = 0
    for node in nodes:
        choices = [profile for profile in profiles[node.id]
                   if node.fixedX is None or node.fixedX + profile.w <= COLS]
        if not choices:
            return []
        desired_x = node.fixedX if node.fixedX is not None else x
        fitting = [profile for profile in choices if desired_x >= x and
                   desired_x + profile.w <= COLS]
        if not fitting:
            y += row_height
            x = 0
            row_height = 0
            desired_x = node.fixedX or 0
            fitting = [profile for profile in choices if desired_x + profile.w <= COLS]
        if not fitting:
            return []
        profile = min(fitting, key=lambda item: _profile_key(item, COLS - desired_x))
        placements.append({
            "id": node.id,
            "x": desired_x,
            "y": y,
            "w": profile.w,
            "h": profile.h,
            "variant": profile.variant,
        })
        placements.extend(dict(row) for row in profile.descendants)
        x = desired_x + profile.w
        row_height = max(row_height, profile.h)
        if x == COLS:
            y += row_height
            x = 0
            row_height = 0
    return placements


def _original_incumbent(problem: LayoutProblem) -> list[dict]:
    """Recover a complete legal frontend witness without changing its geometry."""
    depths = _depths(problem)
    variants: dict[str, int] = {}
    rows: list[dict] = []
    for node in sorted(problem.nodes, key=lambda item: depths[item.id]):
        original = node.original
        if original is None:
            return []
        parent_variant = variants[node.parentId] if node.parentId else 0
        shapes = [shape for shape in node.shapes
                  if shape.parentVariant == parent_variant
                  and shape.w == original.w
                  and (shape.h <= original.h if node.container else shape.h == original.h)]
        if not shapes:
            return []
        shape = min(shapes, key=lambda item: (item.cost, item.variant))
        variants[node.id] = shape.variant
        rows.append({"id": node.id, **original.model_dump(), "variant": shape.variant})
    return rows if audit(problem, rows)["valid"] else []


def build_complete_incumbent(
    problem: LayoutProblem, *, diagnostics: list[dict] | None = None
) -> list[dict]:
    """Build one legal full solution before spending time on optimization."""
    original = _original_incumbent(problem)
    if original:
        count("incumbent.original.valid")
        return original
    if all(node.original is not None for node in problem.nodes):
        count("incumbent.original.invalid")
    scopes: dict[str | None, list[Node]] = defaultdict(list)
    for node in problem.nodes:
        scopes[node.parentId].append(node)
    depths = _depths(problem)
    container_ids = [node.id for node in problem.nodes if node.container]
    scope_ids: list[str | None] = sorted(
        container_ids, key=lambda node_id: depths[node_id], reverse=True
    ) + [None]
    variants = defaultdict(set)
    variants[None].add(0)
    for node in problem.nodes:
        if node.container:
            variants[node.id].update(shape.variant for shape in node.shapes)

    plans: dict[tuple[str | None, int], list[dict]] = {}
    for parent_id in scope_ids:
        direct = scopes[parent_id]
        if not direct:
            continue
        ordered = _scope_order(problem, direct)
        for parent_variant in sorted(variants[parent_id]):
            choices: dict[str, list[_Profile]] = defaultdict(list)
            possible = True
            for node in ordered:
                direct_child_ids = {child.id for child in scopes[node.id]}
                height_rejections: list[tuple[int, int]] = []
                for shape in node.shapes:
                    if shape.parentVariant != parent_variant or shape.h > node.maxH:
                        continue
                    if node.fixedX is not None and node.fixedX + shape.w > COLS:
                        continue
                    descendants: list[dict] = []
                    height = shape.h
                    if node.container and direct_child_ids:
                        child = plans.get((node.id, shape.variant))
                        if child is None or not child:
                            continue
                        descendants = child
                        child_bottom = max(row["y"] + row["h"] for row in child
                                           if row["id"] in direct_child_ids)
                        required_height = _required_container_height(
                            node, child, direct_child_ids
                        )
                        height = max(height, required_height)
                        if height > node.maxH:
                            height_rejections.append((height, child_bottom))
                    if height <= node.maxH:
                        choices[node.id].append(_Profile(
                            shape.w, height, shape.variant, shape.cost,
                            tuple(dict(row) for row in descendants),
                        ))
                if not choices[node.id]:
                    if diagnostics is not None:
                        if height_rejections:
                            required, child_bottom = min(height_rejections)
                            diagnostics.append({
                                "nodeId": node.id,
                                "scopeId": parent_id or "root",
                                "reason": "container_height",
                                "requiredH": required,
                                "maxH": node.maxH,
                                "childBottom": child_bottom,
                            })
                        else:
                            diagnostics.append({
                                "nodeId": node.id,
                                "scopeId": parent_id or "root",
                                "reason": "no_compatible_shape",
                            })
                    possible = False
                    break
            plans[(parent_id, parent_variant)] = (
                _place_scope(ordered, choices) if possible else []
            )

    incumbent = plans.get((None, 0), [])
    return incumbent if incumbent and audit(problem, incumbent)["valid"] else []
