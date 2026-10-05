from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from threading import Event
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dashboard_layout.diagnostics import count

VERSION = "constraint-v1"
COLS = 24
MAX_GAP = 8
MAX_BUDGET_MS = 120000


class GeometryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Shape(GeometryModel):
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=100)
    cost: int = Field(default=0, ge=0, le=100000)
    variant: int = Field(default=0, ge=0, le=10000)
    parentVariant: int = Field(default=0, ge=0, le=10000)


class OriginalGeometry(GeometryModel):
    x: int = Field(ge=0, le=23)
    y: int = Field(ge=0, le=10000)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=100)


class Node(GeometryModel):
    id: str = Field(min_length=1, max_length=128)
    parentId: str | None = Field(default=None, max_length=128)
    shapes: list[Shape] = Field(min_length=1, max_length=512)
    container: bool = False
    headerPx: int = Field(default=50, ge=0, le=100)
    maxH: int = Field(default=100, ge=1, le=100)
    fixedX: int | None = Field(default=None, ge=0, le=23)
    kind: Literal["metric", "chart", "rank", "other"] = "other"
    original: OriginalGeometry | None = None


class LayoutProblem(GeometryModel):
    version: Literal["constraint-v1"]
    objective: Literal["organize-v1"] | None = None
    nodes: list[Node] = Field(min_length=1, max_length=200)
    orders: list[list[str]] = Field(default_factory=list, max_length=200)
    orderSources: list[Literal["explicit_request", "explicit_saved", "derived_stamp", "derived_geometry"]] | None = Field(default=None, max_length=200)
    requiredBefore: list[tuple[str, str]] = Field(default_factory=list, max_length=4000)
    budgetMs: int = Field(default=MAX_BUDGET_MS, ge=1, le=MAX_BUDGET_MS)

    @field_validator("requiredBefore", mode="before")
    @classmethod
    def json_pairs(cls, value):
        # HTTP JSON arrays represent tuples; retain strict validation of members.
        if isinstance(value, list):
            return [tuple(pair) if isinstance(pair, list) else pair for pair in value]
        return value

    @model_validator(mode="after")
    def validate_graph(self):
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes) or sum(len(n.shapes) for n in self.nodes) > 12000:
            raise ValueError("duplicate ids or excessive candidate count")
        for node in self.nodes:
            parent = by_id.get(node.parentId)
            if node.parentId is not None and (parent is None or not parent.container):
                raise ValueError("missing/non-container parent")
            ancestors = {node.id}
            while parent:
                if parent.id in ancestors or len(ancestors) > 4:
                    raise ValueError("cycle or unsupported depth")
                ancestors.add(parent.id)
                parent = by_id.get(parent.parentId)
            variants = {s.variant for s in by_id[node.parentId].shapes} if node.parentId else {0}
            if any(s.parentVariant not in variants for s in node.shapes):
                raise ValueError("unknown parent width variant")
        ordered = set()
        for order in self.orders:
            if not order or len(order) > 200 or any(i not in by_id or i in ordered for i in order):
                raise ValueError("invalid order")
            if len(set(order)) != len(order) or len({by_id[i].parentId for i in order}) != 1:
                raise ValueError("duplicate/cross-scope order")
            ordered.update(order)
        from app.dashboard_layout.reading import validate_order_contract
        validate_order_contract(self)
        return self


class QualityInterrupted(InterruptedError):
    """Quality evaluation stopped without producing a complete measurement."""


def _check_quality_budget(deadline: float | None, cancelled: Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise QualityInterrupted("cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise QualityInterrupted("deadline")


def _gaps(rects, *, deadline: float | None = None, cancelled: Event | None = None):
    """Connected empty cells, excluding padding and space below the last card."""
    _check_quality_budget(deadline, cancelled)
    bottom = max((r["y"] + r["h"] for r in rects), default=0)
    rows = [0] * bottom
    for r in rects:
        _check_quality_budget(deadline, cancelled)
        mask = ((1 << r["w"]) - 1) << r["x"]
        for y in range(r["y"], r["y"] + r["h"]):
            if y % 256 == 0:
                _check_quality_budget(deadline, cancelled)
            rows[y] |= mask
    components = []
    for y in range(bottom):
        _check_quality_budget(deadline, cancelled)
        for x in range(COLS):
            if rows[y] & (1 << x):
                continue
            cells = [(x, y)]
            rows[y] |= 1 << x
            for index, (cx, cy) in enumerate(cells):
                # A single sparse component can cover the entire page.
                if index % 256 == 0:
                    _check_quality_budget(deadline, cancelled)
                for nx, ny in ((cx-1, cy), (cx+1, cy), (cx, cy-1), (cx, cy+1)):
                    if 0 <= nx < COLS and 0 <= ny < bottom and not rows[ny] & (1 << nx):
                        rows[ny] |= 1 << nx
                        cells.append((nx, ny))
            components.append(cells)
    _check_quality_budget(deadline, cancelled)
    return components


def precedes(first: dict, second: dict) -> bool:
    """Preserve order across either a horizontal row or a vertical stack."""
    return (first["x"] + first["w"] <= second["x"] or
            first["y"] + first["h"] <= second["y"])


def preserves_reading_order(order: list[str], by_id: dict[str, dict]) -> bool:
    """Allow each complete horizontal band its own coherent reading traversal."""
    if not order:
        return True
    rows = [by_id[id] for id in order]
    suffix_top = [row["y"] for row in rows]
    for i in range(len(rows)-2, -1, -1):
        suffix_top[i] = min(suffix_top[i], suffix_top[i+1])
    start, bottom = 0, 0
    for i, row in enumerate(rows):
        bottom = max(bottom, row["y"] + row["h"])
        if i+1 < len(rows) and bottom > suffix_top[i+1]:
            continue
        pairs = [(rows[a], rows[b]) for a in range(start, i+1)
                 for b in range(a+1, i+1)]
        row_major = all(a["y"] < b["y"] or
                        (a["y"] == b["y"] and a["x"] + a["w"] <= b["x"])
                        for a, b in pairs)
        if not row_major and not all(precedes(a, b) for a, b in pairs):
            return False
        start = i+1
    return True


def audit(problem: LayoutProblem, placements: list[dict], *,
          include_gap_components: bool = True, reading_orders: list[list[str]] | None = None) -> dict:
    """Audit hard identity, hierarchy, order and geometry constraints only."""
    if reading_orders is not None:
        from app.dashboard_layout.reading import reading_order_error
        reason = reading_order_error(problem, reading_orders)
        if reason:
            return {"valid": False, "reason": reason}
        problem = problem.model_copy(update={"orders": reading_orders})
    by_id = {r.get("id"): r for r in placements}
    if len(by_id) != len(placements) or set(by_id) != {n.id for n in problem.nodes}:
        return {"valid": False, "reason": "identity"}
    scopes = defaultdict(list)
    for node in problem.nodes:
        r = by_id[node.id]
        if any(type(r.get(k)) is not int for k in ("x", "y", "w", "h", "variant")):
            return {"valid": False, "reason": "integer"}
        if not (r["x"] >= 0 and r["y"] >= 0 and r["x"] + r["w"] <= COLS
                and r["y"] + r["h"] <= 10000 and 1 <= r["h"] <= node.maxH
                and (node.fixedX is None or r["x"] == node.fixedX)):
            return {"valid": False, "reason": "bounds"}
        pv = by_id[node.parentId]["variant"] if node.parentId else 0
        if not any(s.w == r["w"] and s.variant == r["variant"] and s.parentVariant == pv
                   and (s.h <= r["h"] if node.container else s.h == r["h"]) for s in node.shapes):
            return {"valid": False, "reason": "shape"}
        scopes[node.parentId].append(r)
    for order in problem.orders:
        if not preserves_reading_order(order, by_id):
            return {"valid": False, "reason": "order"}
    positions = {id: (i, j) for i, order in enumerate(problem.orders) for j, id in enumerate(order)}
    for first, second in problem.requiredBefore:
        a, b = positions.get(first), positions.get(second)
        if (a is not None and b is not None and a[0] == b[0] and a[1] >= b[1]
                or not preserves_reading_order([first, second], by_id)):
            return {"valid": False, "reason": "required_before"}
    nodes = {n.id: n for n in problem.nodes}
    large = []
    total_gap = 0
    for parent_id, rects in scopes.items():
        for i, a in enumerate(rects):
            if any(a["x"] < b["x"]+b["w"] and b["x"] < a["x"]+a["w"]
                   and a["y"] < b["y"]+b["h"] and b["y"] < a["y"]+a["h"] for b in rects[i+1:]):
                return {"valid": False, "reason": "overlap"}
        if parent_id:
            parent = nodes[parent_id]
            step, gap = (34, 4) if parent.parentId else (40, 10)
            needed = max(r["y"]+r["h"] for r in rects)*34 + 12 + parent.headerPx
            if by_id[parent_id]["h"]*step-gap < needed:
                return {"valid": False, "reason": "containment"}
        if not include_gap_components:
            total_gap += COLS * max((r["y"] + r["h"] for r in rects), default=0) - sum(
                r["w"] * r["h"] for r in rects)
            continue
        for cells in _gaps(rects):
            total_gap += len(cells)
            if len(cells) > MAX_GAP:
                large.append((parent_id, cells[:MAX_GAP+1]))
    return {"valid": True, "reason": None,
            "large": large, "gapCells": total_gap}


@dataclass(frozen=True)
class RankedCandidate:
    """One hard-valid layout with deterministic quality metadata."""
    plan_key: str
    placements: tuple[dict, ...]
    quality: dict[str, int]
    score: tuple


def plan_key(problem: LayoutProblem, placements: list[dict]) -> str:
    """Return a stable geometry-only key; no dashboard business data is hashed."""
    parents = {node.id: node.parentId for node in problem.nodes}
    normalized = [
        [
            row["id"], parents.get(row["id"]), row["x"], row["y"],
            row["w"], row["h"], row["variant"],
        ]
        for row in sorted(placements, key=lambda item: item["id"])
    ]
    payload = json.dumps([problem.version, normalized], ensure_ascii=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _shape_cost(problem: LayoutProblem, placements: list[dict]) -> int:
    by_id = {row["id"]: row for row in placements}
    total = 0
    for node in problem.nodes:
        row = by_id[node.id]
        parent_variant = by_id[node.parentId]["variant"] if node.parentId else 0
        total += min(
            shape.cost for shape in node.shapes
            if shape.w == row["w"] and shape.variant == row["variant"]
            and shape.parentVariant == parent_variant
            and (shape.h <= row["h"] if node.container else shape.h == row["h"])
        )
    return total


def _fill_isolated_chart_rows(
    problem: LayoutProblem, placements: list[dict]
) -> list[dict]:
    """Use an admitted full-width shape when a normal chart owns its whole band."""
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: dict(row) for row in placements}
    scopes = defaultdict(list)
    for row in by_id.values():
        scopes[nodes[row["id"]].parentId].append(row)
    changed = False
    for node in problem.nodes:
        if node.kind != "chart" or node.container or node.fixedX not in (None, 0):
            continue
        row = by_id[node.id]
        siblings = scopes[node.parentId]
        if any(other["id"] != node.id and
               row["y"] < other["y"] + other["h"] and
               other["y"] < row["y"] + row["h"] for other in siblings):
            continue
        parent_variant = by_id[node.parentId]["variant"] if node.parentId else 0
        full = next((shape for shape in node.shapes
                     if shape.w == COLS and shape.h == row["h"] and
                     shape.variant == row["variant"] and
                     shape.parentVariant == parent_variant), None)
        if full is None or row["x"] == 0 and row["w"] == COLS:
            continue
        row.update(x=0, w=COLS)
        changed = True
    result = list(by_id.values())
    return result if changed and audit(problem, result)["valid"] else placements


def _balance_metric_rows(
    problem: LayoutProblem, placements: list[dict]
) -> list[dict]:
    """Evenly fill a metric shelf up to the surrounding structural boundaries."""
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: dict(row) for row in placements}
    scopes = defaultdict(list)
    rows = defaultdict(list)
    for row in by_id.values():
        node = nodes[row["id"]]
        scopes[node.parentId].append(row)
        if node.kind == "metric" and not node.container and node.fixedX is None:
            rows[(node.parentId, row["y"], row["h"])].append(row)
    changed = False
    for (parent_id, top, height), metrics in rows.items():
        if len(metrics) < 2:
            continue
        metrics.sort(key=lambda row: row["x"])
        metric_ids = {row["id"] for row in metrics}
        blockers = [row for row in scopes[parent_id]
                    if row["id"] not in metric_ids and
                    top < row["y"] + row["h"] and row["y"] < top + height]
        left = max([0, *(row["x"] + row["w"] for row in blockers
                         if row["x"] + row["w"] <= metrics[0]["x"])])
        right = min([COLS, *(row["x"] for row in blockers
                            if row["x"] >= metrics[-1]["x"] + metrics[-1]["w"])])
        span = right - left
        narrow, remainder = divmod(span, len(metrics))
        if narrow < 1:
            continue
        widths = [narrow + (1 if index < remainder else 0)
                  for index in range(len(metrics))]
        parent_variant = by_id[parent_id]["variant"] if parent_id else 0
        selected = []
        for row, width in zip(metrics, widths):
            shape = next((shape for shape in nodes[row["id"]].shapes
                          if shape.w == width and shape.h == row["h"] and
                          shape.parentVariant == parent_variant), None)
            if shape is None:
                selected = []
                break
            selected.append(shape)
        if not selected:
            continue
        x = left
        for row, shape in zip(metrics, selected):
            if (row["x"], row["w"], row["variant"]) != (x, shape.w, shape.variant):
                row.update(x=x, w=shape.w, variant=shape.variant)
                changed = True
            x += shape.w
    result = list(by_id.values())
    return result if changed and audit(problem, result)["valid"] else placements


def _balance_metric_stacks(problem: LayoutProblem, placements: list[dict],
                           *, deadline: float) -> list[dict]:
    """Refit consecutive metric peers beside a chart within their existing region."""
    nodes = {node.id: node for node in problem.nodes}
    current = placements
    for order in problem.orders:
        for i in range(len(order)-2):
            if time.monotonic() >= deadline:
                return current
            ids = order[i:i+3]
            group = [nodes[id] for id in ids]
            if [n.kind for n in group] not in (["metric", "metric", "chart"],
                                              ["chart", "metric", "metric"]):
                continue
            if any(n.container for n in group):
                continue
            by_id = {r["id"]: r for r in current}
            rows = [by_id[id] for id in ids]
            left = min(r["x"] for r in rows)
            right = max(r["x"] + r["w"] for r in rows)
            top = min(r["y"] for r in rows)
            bottom = max(r["y"] + r["h"] for r in rows)
            if any(r["id"] not in ids and nodes[r["id"]].parentId == group[0].parentId
                   and r["x"] < right and r["x"] + r["w"] > left
                   and r["y"] < bottom and r["y"] + r["h"] > top for r in current):
                continue
            metrics = [n for n in group if n.kind == "metric"]
            chart = next(n for n in group if n.kind == "chart")
            chart_first = group[0] is chart
            parent_variant = by_id[group[0].parentId]["variant"] if group[0].parentId else 0
            best = current
            best_score = None
            sizes = sorted({(right-left-s.w, s.h) for s in chart.shapes
                            if s.h <= bottom-top and
                            (s.h == bottom-top or (left == 0 and right == COLS))})
            assignments = [(width, height, heights) for width, height in sizes
                           for heights in sorted({((height+1)//2, height//2),
                                                  (height//2, (height+1)//2)})]
            for width, height, heights in assignments:
                if time.monotonic() >= deadline:
                    break
                if width < 1 or width >= right-left:
                    continue
                dimensions = [(width, h) for h in heights] + [(right-left-width, height)]
                shapes = []
                for node, (w, h) in zip([*metrics, chart], dimensions):
                    matching = [s for s in node.shapes if s.w == w and s.h == h
                                and s.variant == by_id[node.id]["variant"]
                                and s.parentVariant == parent_variant and h <= node.maxH]
                    if not matching:
                        break
                    shapes.append(min(matching, key=lambda s: s.cost))
                if len(shapes) != 3:
                    continue
                mx = right-width if chart_first else left
                cx = left if chart_first else left+width
                updates = {}
                y = top
                for node, shape in zip(metrics, shapes[:2]):
                    updates[node.id] = dict(by_id[node.id], x=mx, y=y, w=shape.w, h=shape.h)
                    y += shape.h
                updates[chart.id] = dict(by_id[chart.id], x=cx, y=top,
                                        w=shapes[2].w, h=shapes[2].h)
                if height < bottom-top:
                    for row in current:
                        if nodes[row["id"]].parentId == group[0].parentId and row["y"] >= bottom:
                            updates[row["id"]] = dict(row, y=row["y"]-(bottom-top-height))
                candidate = [updates.get(r["id"], r) for r in current]
                if audit(problem, candidate)["valid"]:
                    # Retain the original and use the final ranking objective.
                    if best_score is None:
                        current_quality = layout_quality(problem, current)
                        best_score = (
                            current_quality["regionalEmptyViolation"],
                            *_quality_score(problem, current_quality),
                        )
                    candidate_quality = layout_quality(problem, candidate)
                    score = (
                        candidate_quality["regionalEmptyViolation"],
                        *_quality_score(problem, candidate_quality),
                    )
                    if score < best_score:
                        best, best_score = candidate, score
            if best is not current:
                count("finishing.metric_stacks")
                current = best
    return current


def _ranking_boundary_variants(problem: LayoutProblem, placements: list[dict]):
    """Match a ranking's right edge to an adjacent row using admitted shapes."""
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: row for row in placements}
    scopes = defaultdict(list)
    for row in placements:
        scopes[nodes[row["id"]].parentId].append(row)
    for node in problem.nodes:
        if node.kind != "rank" or node.container:
            continue
        rank = by_id[node.id]
        edge = rank["x"] + rank["w"]
        bottom = rank["y"] + rank["h"]
        siblings = scopes[node.parentId]
        peers = sorted((row for row in siblings
                        if row["x"] == edge and row["y"] >= rank["y"] and
                        row["y"] + row["h"] <= bottom), key=lambda row: row["y"])
        if not peers or peers[0]["y"] != rank["y"] or peers[-1]["y"] + peers[-1]["h"] != bottom:
            continue
        if any(left["y"] + left["h"] != right["y"]
               for left, right in pairwise(peers)):
            continue
        if any(nodes[row["id"]].container or nodes[row["id"]].fixedX is not None for row in peers):
            continue
        targets = sorted({boundary for row in siblings
                          if row["y"] + row["h"] == rank["y"] or row["y"] == bottom
                          for boundary in (row["x"], row["x"] + row["w"])
                          if edge < boundary < COLS})
        parent_variant = by_id[node.parentId]["variant"] if node.parentId else 0
        for target in targets:
            changes = {}
            for row in [rank, *peers]:
                x = row["x"] if row is rank else target
                width = target - x if row is rank else row["x"] + row["w"] - target
                shape = next((shape for shape in nodes[row["id"]].shapes
                              if shape.w == width and shape.h == row["h"] and
                              shape.variant == row["variant"] and
                              shape.parentVariant == parent_variant), None)
                if shape is None:
                    break
                changes[row["id"]] = dict(row, x=x, w=width)
            else:
                candidate = [changes.get(row["id"], row) for row in placements]
                if audit(problem, candidate)["valid"]:
                    yield candidate


def _alignment_quality(rects: list[dict]) -> tuple[int, int]:
    """Count shared and unmatched internal vertical edges across row boundaries."""
    if len(rects) < 2:
        return 0, 0
    boundaries = sorted({row["y"] for row in rects} |
                        {row["y"] + row["h"] for row in rects})
    aligned = 0
    misaligned = 0
    for y in boundaries[1:-1]:
        above = {
            edge for row in rects if row["y"] < y <= row["y"] + row["h"]
            for edge in (row["x"], row["x"] + row["w"])
            if edge not in (0, COLS)
        }
        below = {
            edge for row in rects if row["y"] <= y < row["y"] + row["h"]
            for edge in (row["x"], row["x"] + row["w"])
            if edge not in (0, COLS)
        }
        aligned += len(above & below)
        misaligned += len(above ^ below)
    return aligned, misaligned


def layout_quality(problem: LayoutProblem, placements: list[dict], *,
                   include_gap_components: bool = True,
                   deadline: float | None = None,
                   cancelled: Event | None = None) -> dict[str, int]:
    """Measure quality, raising QualityInterrupted rather than returning partial scores."""
    _check_quality_budget(deadline, cancelled)
    nodes = {node.id: node for node in problem.nodes}
    scopes = defaultdict(list)
    for row in placements:
        scopes[nodes[row["id"]].parentId].append(row)

    gap_cells = 0
    largest_gap = 0
    aligned = 0
    misaligned = 0
    metric_row_imbalance = 0
    regional_empty_violation_count = 0
    for rects in scopes.values():
        _check_quality_budget(deadline, cancelled)
        if include_gap_components:
            components = _gaps(rects, deadline=deadline, cancelled=cancelled)
            sizes = [len(component) for component in components]
            regional_empty_violation_count += int(any(
                len(component) * 20 > COLS *
                (max(y for _, y in component) - min(y for _, y in component) + 1) * 3
                for component in components
            ))
        else:
            sizes = [COLS * max((r["y"]+r["h"] for r in rects), default=0)
                     - sum(r["w"]*r["h"] for r in rects)]
        gap_cells += sum(sizes)
        largest_gap = max([largest_gap, *sizes])
        scope_aligned, scope_misaligned = _alignment_quality(rects)
        _check_quality_budget(deadline, cancelled)
        aligned += scope_aligned
        misaligned += scope_misaligned
        metrics = [r for r in rects if nodes[r["id"]].kind == "metric"]
        for i, row in enumerate(metrics):
            _check_quality_budget(deadline, cancelled)
            for other in metrics[i+1:]:
                if row["y"] == other["y"] or (row["x"] == other["x"] and
                        (row["y"] + row["h"] == other["y"] or
                         other["y"] + other["h"] == row["y"])):
                    metric_row_imbalance += (abs(row["w"] - other["w"]) +
                                             abs(row["h"] - other["h"]))

    root_rows = scopes.get(None, [])
    total_height = max((row["y"] + row["h"] for row in root_rows), default=0)
    shape_cost = _shape_cost(problem, placements)
    _check_quality_budget(deadline, cancelled)
    return {
        "gapCells": gap_cells,
        "largestGapCells": largest_gap,
        "misalignedBlockCount": misaligned,
        "alignedBoundaryCount": aligned,
        "metricRowImbalance": metric_row_imbalance,
        "regionalEmptyViolation": regional_empty_violation_count,
        "totalHeight": total_height,
        "shapeCost": shape_cost,
    }


def regional_empty_violation(problem: LayoutProblem, placements: list[dict], *,
                             deadline: float | None = None,
                             cancelled: Event | None = None) -> int:
    """Return one when a connected gap exceeds 15% of its vertical region."""
    return int(layout_quality(
        problem, placements, deadline=deadline, cancelled=cancelled
    )["regionalEmptyViolation"] > 0)


def _quality_score(problem: LayoutProblem, quality: dict[str, int]) -> tuple:
    return (
        quality["gapCells"], quality["largestGapCells"],
        # Organize compares footprint before rewarding fewer internal edges.
        *((quality["totalHeight"],) if problem.objective == "organize-v1" else ()),
        quality["misalignedBlockCount"], quality["metricRowImbalance"],
        -quality["alignedBoundaryCount"], quality["shapeCost"], quality["totalHeight"],
    )


def rank_candidates(problem: LayoutProblem, plans: list[list[dict]],
                    limit: int = 8, *, deadline: float | None = None) -> list[RankedCandidate]:
    """Deduplicate and rank hard-valid plans; quality findings never reject them."""
    ranked = {}
    count("ranking.calls")
    count("ranking.supplied", len(plans))

    def retain(plan):
        checked = audit(problem, plan)
        if not checked["valid"]:
            count("ranking.rejected." + checked["reason"])
            return
        key = plan_key(problem, plan)
        if key in ranked:
            count("ranking.duplicates")
            return
        quality = layout_quality(problem, plan)
        ranked[key] = RankedCandidate(
            plan_key=key, placements=tuple(dict(row) for row in plan),
            quality=quality,
            score=(quality["regionalEmptyViolation"],
                   *_quality_score(problem, quality), key),
        )

    for supplied in plans:
        chart_filled = _fill_isolated_chart_rows(problem, supplied)
        balanced = _balance_metric_rows(problem, chart_filled)
        variants = [supplied, chart_filled, _balance_metric_rows(problem, supplied), balanced]
        variants.extend(_ranking_boundary_variants(problem, balanced))
        for plan in variants:
            retain(plan)
    # Spend the shared finishing budget on the strongest incumbents first.
    finishing_deadline = min(deadline if deadline is not None else float("inf"),
                             time.monotonic() + 0.15)
    for candidate in sorted(ranked.values(), key=lambda item: item.score):
        if time.monotonic() >= finishing_deadline:
            break
        retain(_balance_metric_stacks(problem, list(candidate.placements),
                                      deadline=finishing_deadline))
    count("ranking.valid", len(ranked))
    count("ranking.trimmed", max(0, len(ranked) - max(0, limit)))
    return sorted(ranked.values(), key=lambda candidate: candidate.score)[:max(0, limit)]


def _objective_score(problem: LayoutProblem, placements: list[dict]) -> int:
    by_id = {r["id"]: r for r in placements}
    scopes = defaultdict(list)
    score = 0
    for n in problem.nodes:
        r = by_id[n.id]
        pv = by_id[n.parentId]["variant"] if n.parentId else 0
        score += r["y"] + min(s.cost for s in n.shapes if s.w == r["w"]
            and s.variant == r["variant"] and s.parentVariant == pv
            and (s.h <= r["h"] if n.container else s.h == r["h"]))
        if n.container:
            score += r["w"]*r["h"]
        scopes[n.parentId].append(r)
    for rects in scopes.values():
        bottom = max(r["y"]+r["h"] for r in rects)
        score += (COLS*bottom-sum(r["w"]*r["h"] for r in rects))*1000+bottom*10
    return score


def _row_seed(problem: LayoutProblem, deadline: float) -> list[dict] | None:
    """A fast incumbent, not a restriction on the joint solver's search space."""
    if any(n.container or n.parentId or n.fixedX is not None for n in problem.nodes):
        return None
    by_id = {n.id: n for n in problem.nodes}
    order = next((o for o in problem.orders if len(o) == len(by_id)), list(by_id))
    nodes = [by_id[i] for i in order]
    suffix = {len(nodes): (0, [])}
    for i in range(len(nodes)-1, -1, -1):
        if time.monotonic() >= deadline:
            return None
        states = {(0, 0): (0, [])}
        best = None
        for j in range(i, min(len(nodes), i+24)):
            next_states = {}
            for (width, height), (cost, shapes) in states.items():
                for s in nodes[j].shapes:
                    if width+s.w > COLS or (height and height != s.h) or s.h > nodes[j].maxH:
                        continue
                    key = (width+s.w, s.h)
                    value = (cost+s.cost, shapes+[s])
                    if key not in next_states or value[0] < next_states[key][0]:
                        next_states[key] = value
            states = next_states
            if j+1 in suffix:
                for (width, height), (cost, shapes) in states.items():
                    total = cost + (COLS-width)*height*1000 + height*10 + suffix[j+1][0]
                    if best is None or total < best[0]:
                        x = 0
                        block = []
                        for n, s in zip(nodes[i:j+1], shapes):
                            block.append(dict(id=n.id, x=x, y=0, w=s.w, h=s.h, variant=s.variant))
                            x += s.w
                        best = (total, [(block, height)] + suffix[j+1][1])
        # Two small cards may stack alongside a taller chart. These are only
        # incumbent candidates: arbitrary non-slicing layouts remain in CP-SAT.
        if i+3 in suffix:
            a, b, c = nodes[i:i+3]
            third = {(s.w, s.h): s for s in sorted(c.shapes, key=lambda s: -s.cost)
                     if s.h <= c.maxH}
            for sa in a.shapes:
                for sb in b.shapes:
                    if time.monotonic() >= deadline:
                        return None
                    if sa.h > a.maxH or sb.h > b.maxH:
                        continue
                    options = []
                    sc = third.get((COLS-sa.w, sa.h+sb.h))
                    if sc and sa.w == sb.w:
                        options.append((sc, [(0, 0), (0, sa.h), (sa.w, 0)], sc.h, 0))
                    sc = third.get((COLS-sa.w, sa.h-sb.h))
                    if sc and sb.w <= sc.w and (sc.w-sb.w)*sb.h <= MAX_GAP:
                        options.append((sc, [(0, 0), (sa.w, 0), (sa.w, sb.h)], sa.h,
                                        (sc.w-sb.w)*sb.h))
                    sc = third.get((sa.w, sb.h-sa.h))
                    if sc and sa.w+sb.w == COLS:
                        options.append((sc, [(0, 0), (sa.w, 0), (0, sa.h)], sb.h, 0))
                    for sc, positions, height, gap in options:
                        total = sa.cost+sb.cost+sc.cost+height*10+gap*1000+suffix[i+3][0]
                        if best is None or total < best[0]:
                            block = [dict(id=n.id, x=x, y=y, w=s.w, h=s.h, variant=s.variant)
                                     for n, s, (x, y) in zip((a, b, c), (sa, sb, sc), positions)]
                            best = (total, [(block, height)] + suffix[i+3][1])
        if best is not None:
            suffix[i] = best
    if 0 not in suffix:
        return None
    placements = []
    y = 0
    for block, height in suffix[0][1]:
        placements.extend(dict(r, y=r["y"]+y) for r in block)
        y += height
    return placements if audit(problem, placements)["valid"] else None


def _skyline_seed(problem: LayoutProblem, deadline: float) -> list[dict] | None:
    """Bounded mixed-height incumbent; never constrains subsequent CP-SAT search."""
    if any(n.container or n.parentId or n.fixedX is not None for n in problem.nodes):
        return None
    by_id = {n.id: n for n in problem.nodes}
    order = next((o for o in problem.orders if len(o) == len(by_id)), list(by_id))
    # skyline, occupied area, sealed holes, cost, placements
    states = [((0,)*COLS, 0, 0, 0, [])]
    for id in order:
        candidates = {}
        for skyline, area, holes, cost, placed in states:
            shapes = list(by_id[id].shapes)
            if len(shapes) > 48:
                representatives = {}
                for shape in sorted(shapes, key=lambda item: (
                        item.cost, item.w * item.h, item.h, item.w))[:16]:
                    representatives[(shape.w, shape.h, shape.variant,
                                     shape.parentVariant)] = shape
                for field in ("w", "h"):
                    values = sorted({getattr(shape, field) for shape in shapes})
                    for value in values:
                        matching = [shape for shape in shapes
                                    if getattr(shape, field) == value]
                        shape = min(matching, key=lambda item: (
                            item.cost, item.w * item.h, item.h, item.w))
                        representatives[(shape.w, shape.h, shape.variant,
                                         shape.parentVariant)] = shape
                shapes = list(representatives.values())
            for s in shapes:
                if s.h > by_id[id].maxH:
                    continue
                edges = [i for i in range(1, COLS) if skyline[i] != skyline[i-1]]
                positions = {0, COLS-s.w, *edges, *(i-s.w for i in edges)}
                for x in sorted(x for x in positions if 0 <= x <= COLS-s.w):
                    if time.monotonic() >= deadline:
                        return None
                    y = max(skyline[x:x+s.w])
                    candidate = dict(id=id, x=x, y=y, w=s.w, h=s.h,
                                     variant=s.variant)
                    if any(not precedes(previous, candidate) for previous in placed):
                        continue
                    sealed = holes + sum(y-v for v in skyline[x:x+s.w])
                    updated = skyline[:x] + (y+s.h,)*s.w + skyline[x+s.w:]
                    occupied = area+s.w*s.h
                    total_cost = cost+s.cost
                    score = (COLS*max(updated)-occupied)*1000 + max(updated)*10 + total_cost
                    key = (updated, y, x, sealed)
                    if key not in candidates or score < candidates[key][0]:
                        candidates[key] = (
                            score,
                            (updated, occupied, sealed, total_cost,
                             placed + [candidate]),
                        )
        states = [v[1] for v in sorted(candidates.values(), key=lambda v: v[0])[:64]]
        if not states:
            return None
    complete = [state[4] for state in states
                if audit(problem, state[4])["valid"]]
    ranked = rank_candidates(problem, complete, 1, deadline=deadline)
    return list(ranked[0].placements) if ranked else None


def solve(problem: LayoutProblem, *, deadline: float | None = None, cancelled: Event | None = None) -> dict:
    """Solve geometry mathematically while retaining a complete incumbent."""
    started = time.monotonic()
    count("solve.organize" if problem.objective == "organize-v1" else "solve.compact")
    deadline = min(deadline or float("inf"), started + problem.budgetMs / 1000)
    if cancelled and cancelled.is_set():
        return dict(version=VERSION, status="cancelled", elapsedMs=0)
    if started >= deadline:
        return dict(version=VERSION, status="budget_exhausted", elapsedMs=0)
    if problem.objective == "organize-v1":
        from app.dashboard_layout.organize import organize
        engine = organize
    else:
        engine = _solve_cp_sat
    from app.dashboard_layout.regional import refine_regions
    from app.dashboard_layout.reading import DERIVED_SOURCES, seed_reading_orders
    chosen = seed_reading_orders(problem, deadline=deadline, cancelled=cancelled)
    if chosen is None:
        if cancelled is not None and cancelled.is_set():
            status = "cancelled"
        elif time.monotonic() >= deadline:
            status = "budget_exhausted"
        else:
            status = "infeasible_candidates"
            count("regional.rejected.order_locality")
        return dict(version=VERSION, status=status,
                    elapsedMs=int((time.monotonic() - started) * 1000))
    selected = problem.model_copy(update={"orders": chosen})
    derived = any(source in DERIVED_SOURCES for source in problem.orderSources or [])
    reserve = min(3.0 if derived else 0.3, max(0, deadline - started) * 0.6)
    engine_deadline = min(deadline - reserve, started + 3.5) if derived else deadline - reserve
    result = engine(selected, deadline=engine_deadline, cancelled=cancelled)
    if result.get("status") == "feasible":
        result["readingOrders"] = chosen
        for alternative in result.get("alternatives", []):
            alternative["readingOrders"] = chosen
        result = refine_regions(problem, result, deadline=min(deadline, time.monotonic() + reserve),
                                cancelled=cancelled)
    if cancelled is not None and cancelled.is_set():
        result = dict(version=VERSION, status="cancelled")
    result["elapsedMs"] = int((time.monotonic() - started) * 1000)
    return result


# Import OR-Tools when the service module is loaded. Its native extension can
# take seconds to initialize on a cold process and must not consume one
# dashboard request's optimization budget.
from app.dashboard_layout.cp_sat import solve_cp_sat as _solve_cp_sat
