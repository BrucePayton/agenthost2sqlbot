"""Bounded compositions of consecutive content, independent of widget type."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.dashboard_layout.diagnostics import count
from app.dashboard_layout.solver import COLS

REGION_CHOICES = 4


@dataclass(frozen=True)
class _Region:
    """A rectangular reading region with local coordinates and occupied area."""

    width: int
    height: int
    area: int
    cost: int
    rows: tuple[dict, ...]


def region_plans(
    nodes,
    deadline,
    cancelled=None,
    *,
    ordered_metrics=False,
    diagnostics=None,
    cache=None,
    cohesive_groups=(),
):
    """Compose prefix then suffix, horizontally or vertically, with bounded memoization."""
    memo = {} if cache is None else cache
    uniform_only = ordered_metrics
    domain_keys = [
        (node.id, tuple(id(shape) for shape in node.shapes)) for node in nodes
    ]
    cohesive_groups = tuple(tuple(group) for group in cohesive_groups)

    def cohesion(region):
        by_id = {row["id"]: row for row in region.rows}
        holes = 0
        for group in cohesive_groups:
            if all(id in by_id for id in group):
                rows = [by_id[id] for id in group]
                width = max(r["x"] + r["w"] for r in rows) - min(r["x"] for r in rows)
                height = max(r["y"] + r["h"] for r in rows) - min(r["y"] for r in rows)
                holes += width * height - sum(r["w"] * r["h"] for r in rows)
        return holes

    minimum_widths = [
        min((s.w for s in n.shapes if s.h <= n.maxH), default=COLS) for n in nodes
    ]
    widths = {s.w for node in nodes for s in node.shapes}
    widths.update(COLS - w for w in list(widths) if w < COLS)
    metric_positions = {
        node.id: i for i, node in enumerate(nodes) if node.kind == "metric"
    }
    metric_groups = {}
    group = -1
    for i, node in enumerate(nodes):
        if node.kind == "metric":
            if i == 0 or nodes[i - 1].kind != "metric":
                group += 1
            metric_groups[node.id] = group

    def metric_profile(region):
        ids = [r["id"] for r in region.rows if r["id"] in metric_groups]
        if not ordered_metrics or not ids:
            return ()
        first_group = min(metric_groups[id] for id in ids)
        shelves = {}
        left, right = region.width, 0
        for row in region.rows:
            if metric_groups.get(row["id"]) == first_group:
                shelves[row["y"]] = shelves.get(row["y"], 0) + 1
                left = min(left, row["x"])
                right = max(right, row["x"] + row["w"])
        return (
            int(right - left == region.width),
            left,
            right,
            *(shelves[y] for y in sorted(shelves)),
        )

    def metric_balance(region):
        metrics = [row for row in region.rows if row["id"] in metric_positions]
        return sum(
            abs(a["w"] - b["w"]) + abs(a["h"] - b["h"])
            for i, a in enumerate(metrics) for b in metrics[i + 1:]
            if a["y"] == b["y"] or (a["x"] == b["x"] and
                (a["y"] + a["h"] == b["y"] or b["y"] + b["h"] == a["y"]))
        ) if ordered_metrics else 0

    def metric_inversions(region):
        if not ordered_metrics:
            return 0
        positions = [
            metric_positions[row["id"]]
            for row in sorted(region.rows, key=lambda row: (row["y"], row["x"]))
            if row["id"] in metric_positions
        ]
        return sum(a > b for i, a in enumerate(positions) for b in positions[i + 1 :])

    def expired():
        stopped = time.monotonic() >= deadline or cancelled and cancelled.is_set()
        if stopped and diagnostics is not None:
            diagnostics["deadline"] = True
        return stopped

    def keep(regions, limit=REGION_CHOICES):
        count("regions.considered", len(regions))
        balances = {id(region): metric_balance(region) for region in regions}
        best = {}
        for region in regions:
            key = (region.height, region.area, metric_profile(region))
            previous = best.get(key)
            if previous is None or (
                cohesion(region),
                metric_inversions(region),
                balances[id(region)],
                region.cost,
            ) < (cohesion(previous), metric_inversions(previous), balances[id(previous)], previous.cost):
                best[key] = region
        count("regions.deduplicated", len(regions) - len(best))
        if ordered_metrics and limit == REGION_CHOICES:
            limit = 12
        count("regions.trimmed", max(0, len(best) - limit))
        if diagnostics is not None:
            diagnostics["prunedProfiles"] = min(
                10000000,
                diagnostics.get("prunedProfiles", 0) + max(0, len(best) - limit),
            )
        ranked = sorted(
            best.values(),
            key=lambda r: (r.width * r.height - r.area, cohesion(r), balances[id(r)], r.height, r.cost),
        )
        if ordered_metrics:
            diverse = {}
            for region in ranked:
                profile = metric_profile(region)
                # Retain topology first, then boundary alternatives within it.
                diverse.setdefault(profile[:1] + profile[3:], region)
            chosen = list(diverse.values())[:limit]
            return (
                chosen + [r for r in ranked if r not in chosen][: limit - len(chosen)]
            )
        return ranked[:limit]

    def join(first, second, horizontal):
        dx, dy = (first.width, 0) if horizontal else (0, first.height)
        return _Region(
            first.width + second.width if horizontal else first.width,
            max(first.height, second.height)
            if horizontal
            else first.height + second.height,
            first.area + second.area,
            first.cost + second.cost,
            first.rows
            + tuple(dict(r, x=r["x"] + dx, y=r["y"] + dy) for r in second.rows),
        )

    def grid_widths(start, stop, width, columns):
        full_stop = start + (stop - start) // columns * columns
        choices = [
            set.intersection(
                *(
                    {shape.w for shape in nodes[i].shapes if shape.h <= nodes[i].maxH}
                    for i in range(start + column, full_stop, columns)
                )
            )
            for column in range(columns)
        ]
        found = []

        def visit(column, remaining, widths):
            if expired() or len(found) >= 64:
                return
            if column == columns:
                if not remaining:
                    found.append(widths)
                return
            for w in sorted(
                choices[column], key=lambda w: (abs(w * columns - width), w)
            ):
                if w <= remaining - (columns - column - 1):
                    visit(column + 1, remaining - w, widths + [w])

        visit(0, width, [])
        return found

    def one_grid(start, stop, width, widths):
        columns = len(widths)
        states = [_Region(width, 0, 0, 0, ())]
        for first in range(start, stop, columns):
            if expired():
                return []
            row_nodes = nodes[first : min(first + columns, stop)]
            row_widths = widths[: len(row_nodes) - 1] + [
                sum(widths[len(row_nodes) - 1 :])
            ]
            choices = []
            for i, node in enumerate(row_nodes):
                by_height = {}
                for shape in node.shapes:
                    if shape.w == row_widths[i] and shape.h <= node.maxH:
                        old = by_height.get(shape.h)
                        if old is None or (shape.cost, shape.variant) < (
                            old.cost,
                            old.variant,
                        ):
                            by_height[shape.h] = shape
                choices.append(by_height)
            heights = set.intersection(*(set(choice) for choice in choices))
            row_profiles = []
            for height in sorted(heights):
                x = 0
                rows = []
                cost = 0
                for node, choice in zip(row_nodes, choices):
                    shape = choice[height]
                    rows.append(
                        {
                            "id": node.id,
                            "x": x,
                            "y": 0,
                            "w": shape.w,
                            "h": height,
                            "variant": shape.variant,
                        }
                    )
                    x += shape.w
                    cost += shape.cost
                row_profiles.append(
                    _Region(width, height, width * height, cost, tuple(rows))
                )
            # Preserve height diversity until the complete grid is formed.
            best = {}
            for above in states:
                for below in row_profiles:
                    joined = join(above, below, False)
                    key = (joined.height, height)
                    old = best.get(key)
                    if old is None or (metric_balance(joined), joined.cost) < (metric_balance(old), old.cost):
                        best[key] = joined
            states = sorted(best.values(), key=lambda r: (r.height, metric_balance(r), r.cost))[:100]
            if not states:
                break
        return states

    def metric_grids(start, stop, width):
        """Shared row/grid profiles with admitted, consistent column widths."""
        profiles = []
        if uniform_only:
            for columns in range(1, min(stop - start, width) + 1):
                if expired():
                    break
                if width % columns:
                    continue
                cell_width = width // columns
                choices = []
                for i in range(start, stop):
                    card_width = cell_width * (columns - (i - start) % columns) if i == stop - 1 else cell_width
                    heights = {}
                    for shape in nodes[i].shapes:
                        if shape.w == card_width and shape.h <= nodes[i].maxH:
                            previous = heights.get(shape.h)
                            if previous is None or (shape.cost, shape.variant) < (previous.cost, previous.variant):
                                heights[shape.h] = shape
                    choices.append(heights)
                common = set.intersection(*(set(heights) for heights in choices))
                for height in sorted(common):
                    rows = tuple(dict(
                        id=nodes[i].id,
                        x=((i - start) % columns) * cell_width,
                        y=((i - start) // columns) * height,
                        w=choices[i - start][height].w, h=height,
                        variant=choices[i - start][height].variant,
                    ) for i in range(start, stop))
                    total_height = ((stop - start + columns - 1) // columns) * height
                    profiles.append(_Region(width, total_height, width * total_height,
                        sum(heights[height].cost for heights in choices), rows))
            return keep(profiles, 100)
        for columns in range(1, min(stop - start, width) + 1):
            for widths in grid_widths(start, stop, width, columns):
                profiles.extend(one_grid(start, stop, width, widths))
                if expired():
                    return keep(profiles, 100)
        count("regions.metric_grid_profiles", len(profiles))
        return keep(profiles, 100)

    def build(start, stop, width):
        if width < max(minimum_widths[start:stop]):
            return []
        included = {node.id for node in nodes[start:stop]}
        local_groups = tuple(
            group for group in cohesive_groups if set(group) <= included
        )
        key = (tuple(domain_keys[start:stop]), width, ordered_metrics, local_groups, uniform_only)
        if key in memo:
            count("regions.cache_hits")
            return memo[key]
        if expired():
            return []
        if stop - start == 1:
            node = nodes[start]
            result = keep(
                [
                    _Region(
                        width,
                        s.h,
                        width * s.h,
                        s.cost,
                        (
                            {
                                "id": node.id,
                                "x": 0,
                                "y": 0,
                                "w": width,
                                "h": s.h,
                                "variant": s.variant,
                            },
                        ),
                    )
                    for s in node.shapes
                    if s.w == width and s.h <= node.maxH
                ],
                100 if ordered_metrics else REGION_CHOICES,
            )
            if len(memo) < 2048:
                memo[key] = result
            return result
        if ordered_metrics and all(node.kind == "metric" for node in nodes[start:stop]):
            result = metric_grids(start, stop, width)
            if not expired() and len(memo) < 2048:
                memo[key] = result
            return result
        result = []
        splits = sorted(range(start + 1, stop), key=lambda i: abs(2 * i - start - stop))
        if ordered_metrics:
            splits = [
                i
                for i in splits
                if not (nodes[i - 1].kind == nodes[i].kind == "metric")
            ]
        # Widths present in readable candidates are most promising. Remaining
        # grid widths permit nested regions made from smaller side-by-side cards.
        cuts = sorted(
            range(1, width), key=lambda w: (w not in widths, abs(width - 2 * w), w)
        )
        for split in splits:
            # Complete horizontal bands need no width search. Generate them
            # before exploring nested columns so a deadline retains a band.
            if ordered_metrics:
                above = build(start, split, width)
                below = build(split, stop, width)
                result = keep(
                    result + [join(a, b, False) for a in above for b in below]
                )
            for cut in cuts:
                if expired():
                    return result
                left = build(start, split, cut)
                if not left:
                    continue
                right = build(split, stop, width - cut)
                result = keep(result + [join(a, b, True) for a in left for b in right])
            if expired():
                break
            if not ordered_metrics:
                above = build(start, split, width)
                below = build(split, stop, width)
                result = keep(
                    result + [join(a, b, False) for a in above for b in below]
                )
        if not expired() and len(memo) < 2048:
            memo[key] = result
        return result

    result = build(0, len(nodes), COLS)
    if ordered_metrics and not expired():
        uniform_only = False
        result = keep(result + build(0, len(nodes), COLS))
    return [list(r.rows) for r in result]
