"""Request-bound, complete candidates from bounded mixed reading regions."""

from __future__ import annotations

import time

from app.dashboard_layout.diagnostics import count
from app.dashboard_layout.reading import (
    DERIVED_SOURCES,
    MAX_READING_WINDOW,
    changed_regions,
    metric_orders,
    order_source,
    reading_order_error,
)
from app.dashboard_layout.regions import region_plans
from app.dashboard_layout.solver import (
    QualityInterrupted,
    _gaps,
    _quality_score,
    audit,
    layout_quality,
    plan_key,
)


def _prioritized_window_requests(problem, placements):
    """Visit windows beside concentrated gaps before already-dense regions."""
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: row for row in placements}
    scopes = {}
    for row in placements:
        scopes.setdefault(nodes[row["id"]].parentId, []).append(row)
    components = {
        scope: [
            (min(x for x, _ in cells), max(x for x, _ in cells) + 1,
             min(y for _, y in cells), max(y for _, y in cells) + 1, len(cells),
             len(cells) / (24 * (max(y for _, y in cells) - min(y for _, y in cells) + 1)))
            for cells in _gaps(rows)
        ]
        for scope, rows in scopes.items()
    }
    ranked = []
    for order_index, order in enumerate(problem.orders):
        for start in range(len(order)):
            for end in range(start + 2, min(len(order), start + MAX_READING_WINDOW) + 1):
                ids = order[start:end]
                scope = nodes[ids[0]].parentId
                if (any(nodes[id].parentId != scope or nodes[id].container for id in ids)
                        or sum(nodes[id].kind == "metric" for id in ids) < 2):
                    continue
                top = min(by_id[id]["y"] for id in ids)
                bottom = max(by_id[id]["y"] + by_id[id]["h"] for id in ids)
                gap = max(((ratio, area, left, right, gap_top, gap_bottom)
                           for left, right, gap_top, gap_bottom, area, ratio
                           in components.get(scope, [])
                           if gap_top < bottom and gap_bottom > top),
                          default=(0, 0, 0, 0, 0, 0))
                _, area, left, right, gap_top, gap_bottom = gap
                lateral = [
                    index for index, id in enumerate(order)
                    if nodes[id].parentId == scope
                    and by_id[id]["y"] < gap_bottom
                    and by_id[id]["y"] + by_id[id]["h"] > gap_top
                    and (by_id[id]["x"] + by_id[id]["w"] == left
                         or by_id[id]["x"] == right)
                ]
                vertical = [
                    index for index, id in enumerate(order)
                    if nodes[id].parentId == scope
                    and by_id[id]["x"] < right
                    and by_id[id]["x"] + by_id[id]["w"] > left
                    and (by_id[id]["y"] + by_id[id]["h"] == gap_top
                         or by_id[id]["y"] == gap_bottom)
                ]
                anchors = lateral or vertical
                distance = min((abs(start - anchor) for anchor in anchors), default=0)
                ranked.append(((-gap[0], -area, distance, end - start, start, end),
                               (order_index, start, end, area)))
    return [request for _, request in sorted(ranked)]


def _region_pairs(plans, repairs, limit):
    """Cover both frontiers before spending the quota on their cross product."""
    if not plans or not repairs:
        return
    seen = set()
    for k in range(max(len(plans), len(repairs))):
        i, j = k % len(plans), k % len(repairs)
        if len(seen) >= limit:
            return
        seen.add((i, j))
        yield plans[i], repairs[j]
    for total in range(len(plans) + len(repairs) - 1):
        for i in range(len(plans)):
            j = total - i
            if not 0 <= j < len(repairs) or (i, j) in seen:
                continue
            if len(seen) >= limit:
                return
            seen.add((i, j))
            yield plans[i], repairs[j]


def _region_alignment(rects):
    """Compare occupied silhouettes, not internal subdivisions of a filled region."""
    def edges(intervals):
        merged = []
        for left, right in sorted(intervals):
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        return {x for span in merged for x in span if x not in (0, 24)}

    aligned = misaligned = 0
    boundaries = sorted({r["y"] for r in rects} | {r["y"] + r["h"] for r in rects})
    for y in boundaries[1:-1]:
        above = edges((r["x"], r["x"] + r["w"]) for r in rects if r["y"] < y <= r["y"] + r["h"])
        below = edges((r["x"], r["x"] + r["w"]) for r in rects if r["y"] <= y < r["y"] + r["h"])
        aligned += len(above & below)
        misaligned += len(above ^ below)
    return aligned, misaligned


def _derived_metric_groups(problem, orders, *, include_singletons=False):
    nodes = {node.id: node for node in problem.nodes}
    groups = []
    for index, order in enumerate(orders):
        if order_source(problem, index) not in DERIVED_SOURCES:
            continue
        groups.append([])
        for id in order:
            node = nodes[id]
            if node.kind != "metric" or node.container:
                groups.append([])
                continue
            if groups[-1] and nodes[groups[-1][-1]].parentId != node.parentId:
                groups.append([])
            groups[-1].append(id)
    return [group for group in groups if len(group) >= (1 if include_singletons else 2)]


def _unoccupied_region_cells(rects, scope_rows):
    left, top = min(r["x"] for r in rects), min(r["y"] for r in rects)
    right = max(r["x"] + r["w"] for r in rects)
    bottom = max(r["y"] + r["h"] for r in rects)
    # Audited sibling rectangles never overlap; clip all occupants to this region.
    occupied = sum(
        max(0, min(right, r["x"] + r["w"]) - max(left, r["x"]))
        * max(0, min(bottom, r["y"] + r["h"]) - max(top, r["y"]))
        for r in scope_rows
    )
    return (right - left) * (bottom - top) - occupied


def _metric_group_holes(problem, rows, orders):
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: row for row in rows}
    holes = 0
    for group in _derived_metric_groups(problem, orders):
        rects = [by_id[id] for id in group]
        siblings = [r for r in rows if nodes[r["id"]].parentId == nodes[group[0]].parentId]
        holes += _unoccupied_region_cells(rects, siblings)
    return holes


def _metric_cell_rows(rows):
    """Measure a regular grid's last spanning card by its underlying cell width."""
    if any("x" not in row or "y" not in row for row in rows):
        return rows
    shelves = {}
    for row in rows:
        shelves.setdefault(row["y"], []).append(row)
    bands = [sorted(shelves[y], key=lambda row: row["x"]) for y in sorted(shelves)]
    if len(bands) < 2 or len(bands[0]) < 2 or len(bands[-1]) >= len(bands[0]):
        return rows
    tracks = [(row["x"], row["w"]) for row in bands[0]]
    if any(x + w != tracks[i + 1][0] for i, (x, w) in enumerate(tracks[:-1])):
        return rows
    for index, band in enumerate(bands[:-1]):
        if [(row["x"], row["w"]) for row in band] != tracks:
            return rows
        if {row["y"] + row["h"] for row in band} != {bands[index + 1][0]["y"]}:
            return rows
    tail = bands[-1]
    if len({row["h"] for row in tail}) != 1:
        return rows
    if [(row["x"], row["w"]) for row in tail[:-1]] != tracks[:len(tail) - 1]:
        return rows
    last, column = tail[-1], len(tail) - 1
    if last["x"] != tracks[column][0] or last["w"] != sum(w for _, w in tracks[column:]):
        return rows
    return [dict(row, w=tracks[column][1]) if row["id"] == last["id"] else row for row in rows]


def _local_metric_pairs(problem, orders):
    positions = {id: index for order in problem.orders for index, id in enumerate(order)}
    return sum(
        1
        for group in _derived_metric_groups(problem, orders)
        for index, id in enumerate(group)
        for other in group[index + 1:]
        if abs(positions[id] - positions[other]) < MAX_READING_WINDOW
    )


def _metric_group_imbalance(problem, rows, orders):
    by_id = {row["id"]: row for row in rows}
    imbalance = 0
    for group in _derived_metric_groups(problem, orders):
        metrics = _metric_cell_rows([by_id[id] for id in group])
        for i, a in enumerate(metrics):
            for b in metrics[i + 1:]:
                if a["y"] == b["y"] or (a["x"] == b["x"] and
                    (a["y"] + a["h"] == b["y"] or b["y"] + b["h"] == a["y"])):
                    imbalance += abs(a["w"] - b["w"]) + abs(a["h"] - b["h"])
    return imbalance


def _metric_neighbor_imbalance(problem, rows, orders=None):
    """Compare nearby metric sizes without joining scopes or explicit sequences."""
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: row for row in rows}
    for group in _derived_metric_groups(problem, orders or problem.orders):
        by_id.update({row["id"]: row for row in _metric_cell_rows([by_id[id] for id in group])})
    imbalance = 0
    seen = set()
    grouped = {
        id: index
        for index, group in enumerate(_derived_metric_groups(problem, orders or problem.orders))
        for id in group
    }
    for order_index, order in enumerate(problem.orders):
        if order_source(problem, order_index) not in DERIVED_SOURCES:
            continue
        for i, id in enumerate(order):
            node = nodes[id]
            if id not in grouped:
                continue
            for other_id in order[i + 1:i + MAX_READING_WINDOW]:
                other = nodes[other_id]
                if other.parentId != node.parentId or other.container:
                    break
                pair = tuple(sorted((id, other_id)))
                if grouped.get(other_id) == grouped[id] and pair not in seen:
                    seen.add(pair)
                    a, b = by_id[id], by_id[other_id]
                    imbalance += abs(a["w"] - b["w"]) + abs(a["h"] - b["h"])
    return imbalance


def _metric_group_height_excess(problem, rows, orders):
    nodes = {node.id: node for node in problem.nodes}
    by_id = {row["id"]: row for row in rows}
    excess = 0
    for group in _derived_metric_groups(problem, orders, include_singletons=True):
        heights = []
        for id in group:
            node, row = nodes[id], by_id[id]
            pv = by_id[node.parentId]["variant"] if node.parentId else 0
            heights.append({s.h for s in node.shapes if s.w == row["w"] and s.h <= node.maxH
                            and s.variant == row["variant"] and s.parentVariant == pv} or {row["h"]})
        common = set.intersection(*heights)
        minimum = min(common) if common else max(min(values) for values in heights)
        excess += sum(max(0, by_id[id]["h"] - minimum) for id in group)
    return excess


def refine_regions(problem, result, *, deadline, cancelled=None):
    """Retain a complete incumbent throughout; never reinterpret explicit orders."""
    nodes = {node.id: node for node in problem.nodes}
    for index in range(len(problem.orders)):
        count("order.source." + order_source(problem, index))
    retained = {}
    started = time.monotonic()
    diagnostics = {
        "version": "reading-v1",
        "truncated": False,
        "windows": [],
        "stopReason": "completed",
    }
    active_window = None
    profile_cache = {}
    derived = any(source in DERIVED_SOURCES for source in problem.orderSources or [])
    quality_fields = [
        "gapCells",
        "largestGapCells",
        *(["organizeTotalHeight"] if problem.objective == "organize-v1" and not derived else []),
        "regionalMisalignedBlockCount" if derived else "misalignedBlockCount",
        "qualityRegionalMetricRowImbalance" if derived else "metricRowImbalance",
        "negativeRegionalAlignedBoundaryCount" if derived else "negativeAlignedBoundaryCount",
        "shapeCost",
        "totalHeight",
    ]
    regional_fields = (
        [
            "metricRegionGapCells",
            "regionalMetricRowImbalance",
            "firstMetricAnchorDisplacement",
            "displacedNodeCount",
            "readingDistance",
            "readingSpan",
            "metricShelfCount",
        ]
        if derived
        else []
    )
    diagnostics.update(
        scorePolicy={
            "version": "regional-reading-v14" if derived else "layout-quality-v1",
            "fields": (
                ["regionalEmptyViolation"]
                + quality_fields[:2]
                + [quality_fields[2], quality_fields[4]]
                + regional_fields[:2]
                + [quality_fields[3]]
                + ["metricGroupedHeightExcess", "metricNeighborhoodImbalance"]
                + ["negativeLocalMetricPairs"]
                + (["organizeTotalHeight"] if problem.objective == "organize-v1" else [])
                + [quality_fields[-1], quality_fields[-2]]
                + regional_fields[2:]
                + ["crossBandMisalignedBoundaryCount", "negativeCrossBandAlignedBoundaryCount", "crossBandMetricRowImbalance"]
                if derived else ["regionalEmptyViolation"] + quality_fields
            ),
            "comparison": "lexicographic_min",
            "windowGapBasis": "scope_gap_upper_bound",
            "selectedGapBasis": "connected_components",
            "metricRegionGapBasis": "sum_of_scope_clipped_region_gaps_may_overlap",
        },
        selectedScore=None,
        selectedScoreStatus="not_evaluated",
        scoreValuesClamped=False,
    )
    request_deadline = deadline
    baseline_orders = result.get(
        "readingOrders", [list(order) for order in problem.orders]
    )
    fallback = dict(
        result, readingOrders=baseline_orders, readingDiagnostics=diagnostics
    )
    fallback["alternatives"] = [
        dict(a, readingOrders=a.get("readingOrders", baseline_orders))
        for a in result.get("alternatives", [])[:8]
    ]

    def request_stopped():
        return (
            time.monotonic() >= request_deadline
            or cancelled is not None
            and cancelled.is_set()
        )

    def stopped_result():
        diagnostics["stopReason"] = (
            "cancelled" if cancelled is not None and cancelled.is_set() else "deadline"
        )
        count("regional." + diagnostics["stopReason"])
        diagnostics["selectedScoreStatus"] = "baseline_retained_without_rescoring"
        return fallback

    if request_stopped():
        return stopped_result()
    # Search may consume its slice, but exact scoring must still fit the request.
    deadline -= min(0.2, max(0, deadline - started) * 0.15)

    def milliseconds(value):
        return min(10000000, max(0, int(value * 1000)))

    def quality_summary(quality):
        return {
            key: min(10000000, max(0, quality[key]))
            for key in (
                "gapCells",
                "totalHeight",
                "shapeCost",
                "metricRowImbalance",
                "misalignedBlockCount",
                "alignedBoundaryCount",
            )
        }

    def bounded_score(values):
        bounded = [min(10000000, max(-10000000, int(value))) for value in values]
        if any(value != safe for value, safe in zip(values, bounded)):
            diagnostics["scoreValuesClamped"] = True
        return bounded

    def score(quality, rows, orders):
        if not derived:
            return _quality_score(problem, quality)
        holes = _metric_group_holes(problem, rows, orders)
        anchor = metric_rows = displaced = distance = span = 0
        scopes = {}
        for row in rows:
            scopes.setdefault(nodes[row["id"]].parentId, []).append(row)
        # Local regions count actual empty cells, including all sibling occupants.
        for rects in scopes.values():
            bands = []
            bottom = -1
            for row in sorted(rects, key=lambda r: (r["y"], r["x"])):
                if row["y"] >= bottom:
                    bands.append([])
                bands[-1].append(row)
                bottom = max(bottom, row["y"] + row["h"])
            for band in bands:
                metrics = [r for r in band if nodes[r["id"]].kind == "metric"]
                if not metrics:
                    continue
                metric_rows += len({r["y"] for r in metrics})
                holes += _unoccupied_region_cells(metrics, rects)
        for original, chosen in zip(problem.orders, orders):
            positions = {id: i for i, id in enumerate(original)}
            moved = [i for i, id in enumerate(chosen) if positions[id] != i]
            displaced += len(moved)
            distance += sum(abs(positions[id] - i) for i, id in enumerate(chosen))
            span += max(moved) - min(moved) + 1 if moved else 0
            first_metric = next(
                (id for id in original if nodes[id].kind == "metric"), None
            )
            if first_metric is not None:
                anchor += abs(chosen.index(first_metric) - positions[first_metric])
        by_id = {row["id"]: row for row in rows}
        for group in changed_regions(problem, orders):
            rects = [by_id[id] for id in group]
            holes += _unoccupied_region_cells(rects, scopes[nodes[group[0]].parentId])
        regional_aligned = regional_misaligned = 0
        for rects in scopes.values():
            aligned, misaligned = _region_alignment(rects)
            regional_aligned += aligned
            regional_misaligned += misaligned
        regional_balance = _metric_group_imbalance(problem, rows, orders)
        base = _quality_score(problem, dict(
            quality, misalignedBlockCount=regional_misaligned,
            alignedBoundaryCount=regional_aligned,
            metricRowImbalance=regional_balance,
        ))
        if problem.objective == "organize-v1":
            base = base[:2] + base[3:]
        return (
            base[:2]
            + (base[2], base[4])
            + (holes, regional_balance)
            + (base[3],)
            + (_metric_group_height_excess(problem, rows, orders), _metric_neighbor_imbalance(problem, rows, orders))
            + (-_local_metric_pairs(problem, orders),)
            + ((quality["totalHeight"],) if problem.objective == "organize-v1" else ())
            + (quality["totalHeight"], quality["shapeCost"], anchor, displaced, distance, span, metric_rows)
            + (quality["misalignedBlockCount"], -quality["alignedBoundaryCount"], quality["metricRowImbalance"])
        )

    def stopped():
        return (
            time.monotonic() >= deadline or cancelled is not None and cancelled.is_set()
        )

    def grouping_profile(orders):
        profile = []
        for order in orders:
            runs = []
            length = 0
            for id in order:
                if nodes[id].kind == "metric":
                    length += 1
                elif length:
                    runs.append(length)
                    length = 0
            if length:
                runs.append(length)
            profile.append(tuple(runs))
        return tuple(profile)

    def retain(rows, orders, *, regional=False):
        checked = audit(
            problem, rows, reading_orders=orders, include_gap_components=False
        )
        if not checked["valid"]:
            count("regional.rejected." + checked["reason"])
            if active_window is not None:
                reasons = active_window["rejected"]
                reasons[checked["reason"]] = reasons.get(checked["reason"], 0) + 1
            return None
        key = plan_key(problem, rows)
        if key in retained:
            previous = retained[key]
            quality = previous[1]["quality"]
            witness_score = score(quality, rows, orders)
            if witness_score >= previous[0]:
                return previous
            count("regional.witness_replaced")
        else:
            selected = problem.model_copy(update={"orders": orders})
            quality = layout_quality(selected, rows, include_gap_components=False)
        candidate = {
            "placements": rows,
            "readingOrders": orders,
            "quality": quality,
            "planKey": key,
        }
        retained[key] = (score(quality, rows, orders), candidate)
        if regional:
            count("regional.accepted")
            if active_window is not None:
                active_window["accepted"] += 1
        # Keep ranking and response size bounded independently of search volume.
        if len(retained) > 8:
            ranked_keys = sorted(retained, key=lambda k: retained[k][0])
            distinct = {}
            if derived:
                for candidate_key in ranked_keys:
                    profile = grouping_profile(retained[candidate_key][1]["readingOrders"])
                    distinct.setdefault(profile, candidate_key)
            preferred = list(distinct.values())[:8]
            preferred += [k for k in ranked_keys if k not in preferred][:8-len(preferred)]
            for candidate_key in ranked_keys:
                if candidate_key not in preferred:
                    del retained[candidate_key]
        return retained.get(key)

    if result.get("placements"):
        retain(result["placements"], baseline_orders)
    for alternative in result.get("alternatives", [])[:8]:
        if request_stopped():
            return stopped_result()
        retain(
            alternative["placements"], alternative.get("readingOrders", baseline_orders)
        )
    if request_stopped():
        return stopped_result()
    if not retained:
        return dict(result, readingDiagnostics=diagnostics)
    current = min(retained.values(), key=lambda item: item[0])
    seeds = list(retained.values())
    initial_key = current[1]["planKey"]
    windows = 0
    window_priority = {
        request[:3]: index
        for index, request in enumerate(
            _prioritized_window_requests(problem, current[1]["placements"])
        )
    }
    for order_index, original in enumerate(problem.orders):
        starts = sorted(range(len(original)), key=lambda start: min(
            (window_priority.get((order_index, start, end), 1000000)
             for end in range(start + 2,
                              min(len(original), start + MAX_READING_WINDOW) + 1)),
            default=1000000,
        ))
        for start in starts:
            if stopped() or windows >= 64:
                break
            # Windows are always indexed against the request, not the last edit.
            ends = sorted(
                range(start + 2, min(len(original), start + MAX_READING_WINDOW) + 1),
                key=lambda end: window_priority.get((order_index, start, end), 1000000),
            )
            for end in ends:
                if stopped() or windows >= 64:
                    break
                ids = original[start:end]
                scope = nodes[ids[0]].parentId
                if any(
                    nodes[id].parentId != scope or nodes[id].container for id in ids
                ):
                    continue
                if sum(nodes[id].kind == "metric" for id in ids) < 2:
                    continue
                window_started = time.monotonic()
                # Share the remaining slice across this anchor's boundaries;
                # insertion variants of a short window must not starve longer ones.
                boundaries_left = min(len(original), start + MAX_READING_WINDOW) - end + 1
                window_deadline = min(
                    deadline,
                    window_started + min(1.0, (deadline - window_started) / boundaries_left),
                )
                window_slice = window_deadline - window_started
                repair_cutoff = window_started + window_slice * 0.3
                profile_cutoff = window_started + window_slice * 0.65
                active_window = {
                    "scopeId": scope,
                    "nodeIds": list(ids),
                    "inputSequence": list(ids),
                    "chosenSequence": list(ids),
                    "startMs": milliseconds(window_started - started),
                    "elapsedMs": 0,
                    "remainingMs": milliseconds(deadline - window_started),
                    "generated": 0,
                    "accepted": 0,
                    "rejected": {},
                    "prunedProfiles": 0,
                    "stopReason": "completed",
                    "bestQualityChange": {
                        "before": quality_summary(current[1]["quality"]),
                        "after": quality_summary(current[1]["quality"]),
                    },
                    "bestScoreChange": {
                        "before": bounded_score((
                            current[1]["quality"].get("regionalEmptyViolation", 0),
                            *current[0],
                        )),
                        "after": bounded_score((
                            current[1]["quality"].get("regionalEmptyViolation", 0),
                            *current[0],
                        )),
                    },
                }
                if len(diagnostics["windows"]) < 64:
                    diagnostics["windows"].append(active_window)
                else:
                    diagnostics["truncated"] = True
                base = None
                repair_ids = []
                repair_plans = []
                # A lower-ranked incumbent can expose a useful closed boundary
                # hidden by the current winner. Keep those complete seeds alive.
                for candidate in sorted(
                    [*retained.values(), *seeds], key=lambda item: item[0]
                ):
                    orders = candidate[1]["readingOrders"]
                    if set(orders[order_index][start:end]) != set(ids):
                        continue
                    by_id = {row["id"]: row for row in candidate[1]["placements"]}
                    top = min(by_id[id]["y"] for id in ids)
                    bottom = max(by_id[id]["y"] + by_id[id]["h"] for id in ids)

                    def overlaps_outside(
                        included, floor, *, scope=scope, by_id=by_id, top=top
                    ):
                        return any(
                            node.parentId == scope
                            and node.id not in included
                            and by_id[node.id]["y"] < floor
                            and by_id[node.id]["y"] + by_id[node.id]["h"] > top
                            for node in problem.nodes
                        )

                    if not overlaps_outside(ids, bottom):
                        base = candidate
                        break
                    # Split a crossing band into two independent bounded regions.
                    # Each repaired suffix is another original <=10-node window.
                    repair_deadline = min(repair_cutoff, time.monotonic() + 0.3)
                    best_repair_gap = None
                    for stop in range(
                        end + 1, min(len(original), end + MAX_READING_WINDOW) + 1
                    ):
                        if time.monotonic() >= repair_deadline or stopped():
                            break
                        tail = original[end:stop]
                        if any(
                            nodes[id].container or nodes[id].parentId != scope
                            for id in tail
                        ) or set(orders[order_index][end:stop]) != set(tail):
                            break
                        if any(by_id[id]["y"] < top for id in tail):
                            break
                        floor = max(
                            bottom, *(by_id[id]["y"] + by_id[id]["h"] for id in tail)
                        )
                        if overlaps_outside(ids + tail, floor):
                            continue
                        pv = by_id[scope]["variant"] if scope else 0
                        tail_nodes = [
                            nodes[id].model_copy(
                                update={
                                    "shapes": [
                                        s
                                        for s in nodes[id].shapes
                                        if s.parentVariant == pv
                                    ]
                                }
                            )
                            for id in orders[order_index][end:stop]
                        ]
                        plans = region_plans(
                            tail_nodes,
                            min(repair_deadline, time.monotonic() + 0.15),
                            cancelled,
                            ordered_metrics=True,
                            cache=profile_cache,
                        )
                        if plans:
                            gap = min(
                                24 * max(r["y"] + r["h"] for r in plan)
                                - sum(r["w"] * r["h"] for r in plan)
                                for plan in plans
                            )
                            if best_repair_gap is None or gap < best_repair_gap:
                                best_repair_gap = gap
                                base, repair_ids, repair_plans, bottom = (
                                    candidate,
                                    tail,
                                    plans,
                                    floor,
                                )
                                active_window["repairNodeIds"] = list(tail)
                            if gap == 0:
                                break
                    if base is not None:
                        break
                if base is None:
                    active_window["stopReason"] = "window_overlap"
                    active_window["rejected"]["window_overlap"] = 1
                    active_window["elapsedMs"] = milliseconds(
                        time.monotonic() - window_started
                    )
                    count("regional.rejected.window_overlap")
                    continue
                windows += 1
                count("regional.windows")
                repair_options = [(plan, list(orders[order_index][end:end + len(repair_ids)]))
                                  for plan in repair_plans]
                if repair_ids and order_source(problem, order_index) in DERIVED_SOURCES:
                    repair_deadline = min(repair_cutoff, time.monotonic() + 0.3)
                    for repair_sequence in metric_orders(repair_ids, nodes):
                        if repair_sequence == orders[order_index][end:end + len(repair_ids)]:
                            continue
                        if stopped() or time.monotonic() >= repair_deadline:
                            break
                        repair_orders = [list(order) for order in orders]
                        repair_orders[order_index][end:end + len(repair_ids)] = repair_sequence
                        if reading_order_error(problem, repair_orders):
                            continue
                        pv = by_id[scope]["variant"] if scope else 0
                        repair_nodes = [nodes[id].model_copy(update={"shapes": [
                            shape for shape in nodes[id].shapes if shape.parentVariant == pv
                        ]}) for id in repair_sequence]
                        variants = region_plans(
                            repair_nodes, min(repair_deadline, time.monotonic() + 0.1),
                            cancelled, ordered_metrics=True, cache=profile_cache,
                        )
                        repair_options.extend((plan, repair_sequence) for plan in variants)
                sequences = (
                    list(metric_orders(ids, nodes))
                    if order_source(problem, order_index) in DERIVED_SOURCES
                    else [ids]
                )
                # Keep an already contiguous group at its original anchor.
                metrics = [i for i, id in enumerate(ids) if nodes[id].kind == "metric"]
                if len(sequences) > 1 and metrics != list(
                    range(metrics[0], metrics[-1] + 1)
                ):
                    sequences = sequences[1:] + sequences[:1]
                if order_source(problem, order_index) in DERIVED_SOURCES:
                    prefixes = []
                    other_prefixes = []
                    for stop in range(3, len(ids)):
                        if sum(nodes[id].kind == "metric" for id in ids[:stop]) < 2:
                            continue
                        first_choice = True
                        for prefix in metric_orders(ids[:stop], nodes):
                            sequence = prefix + ids[stop:]
                            if (
                                sequence != ids
                                and sequence not in prefixes + other_prefixes
                            ):
                                (prefixes if first_choice else other_prefixes).append(
                                    sequence
                                )
                                first_choice = False
                    prefixes += other_prefixes
                    sequences = sequences + [s for s in prefixes if s not in sequences]
                candidate_quota = 192
                for sequence_index, sequence in enumerate(sequences):
                    if stopped() or time.monotonic() >= window_deadline or candidate_quota <= 0:
                        break
                    chosen = [list(order) for order in orders]
                    chosen[order_index][start:end] = sequence
                    reason = reading_order_error(problem, chosen)
                    if reason:
                        count("regional.rejected." + reason)
                        active_window["rejected"][reason] = (
                            active_window["rejected"].get(reason, 0) + 1
                        )
                        continue
                    pv = by_id[scope]["variant"] if scope else 0
                    local_nodes = [
                        nodes[id].model_copy(
                            update={
                                "shapes": [
                                    shape
                                    for shape in nodes[id].shapes
                                    if shape.parentVariant == pv
                                ],
                            }
                        )
                        for id in sequence
                    ]
                    if any(not node.shapes for node in local_nodes):
                        continue
                    count("regional.profiles")
                    profile_diagnostics = {}
                    groups = [
                        group
                        for group in changed_regions(problem, chosen)
                        if set(group) <= set(ids)
                    ]
                    plans = region_plans(
                        local_nodes,
                        min(
                            profile_cutoff,
                            time.monotonic() + (0.25 if sequence_index == 0 else 0.1),
                        ),
                        cancelled,
                        ordered_metrics=True,
                        diagnostics=profile_diagnostics,
                        cache=profile_cache,
                        cohesive_groups=groups,
                    )
                    active_window["prunedProfiles"] = min(
                        10000000,
                        active_window["prunedProfiles"]
                        + profile_diagnostics.get("prunedProfiles", 0),
                    )
                    pairs = list(_region_pairs(
                        plans, repair_options or [([], [])],
                        min(candidate_quota, 96),
                    ))
                    active_window["generated"] += len(pairs)
                    candidate_quota -= len(pairs)
                    if profile_diagnostics.get("deadline"):
                        active_window["rejected"]["bounded_window_deadline"] = (
                            active_window["rejected"].get("bounded_window_deadline", 0)
                            + 1
                        )
                    for local, (repair, repair_sequence) in pairs:
                        if stopped() or time.monotonic() >= window_deadline:
                            break
                        height = max(row["y"] + row["h"] for row in local)
                        tail_height = max(
                            (row["y"] + row["h"] for row in repair), default=0
                        )
                        delta = height + tail_height - (bottom - top)
                        proposal = [
                            dict(row, y=row["y"] + delta)
                            if nodes[row["id"]].parentId == scope
                            and row["y"] >= bottom
                            else dict(row)
                            for row in base[1]["placements"]
                            if row["id"] not in ids + repair_ids
                        ]
                        proposal.extend(
                            dict(row, y=row["y"] + top) for row in local
                        )
                        proposal.extend(
                            dict(row, y=row["y"] + top + height) for row in repair
                        )
                        proposal_orders = [list(order) for order in chosen]
                        proposal_orders[order_index][end:end + len(repair_ids)] = repair_sequence
                        accepted = retain(proposal, proposal_orders, regional=True)
                        if accepted and accepted[0] < current[0]:
                            current = accepted
                            active_window["chosenSequence"] = list(sequence)
                            if repair_ids:
                                active_window["repairChosenSequence"] = list(repair_sequence)
                active_window["elapsedMs"] = milliseconds(
                    time.monotonic() - window_started
                )
                active_window["bestQualityChange"]["after"] = quality_summary(
                    current[1]["quality"]
                )
                active_window["bestScoreChange"]["after"] = bounded_score((
                    current[1]["quality"].get("regionalEmptyViolation", 0),
                    *current[0],
                ))
                if cancelled is not None and cancelled.is_set():
                    active_window["stopReason"] = "cancelled"
                elif time.monotonic() >= window_deadline:
                    active_window["stopReason"] = "bounded_window_deadline"
                elif candidate_quota <= 0:
                    active_window["stopReason"] = "candidate_quota"
                elif not active_window["generated"]:
                    active_window["stopReason"] = "no_profiles"
                elif active_window["prunedProfiles"]:
                    active_window["stopReason"] = "profile_pruned"
    if stopped():
        diagnostics["stopReason"] = (
            "cancelled" if cancelled is not None and cancelled.is_set() else "deadline"
        )
        count(
            "regional.cancelled"
            if cancelled is not None and cancelled.is_set()
            else "regional.deadline"
        )
    elif windows >= 64:
        diagnostics["stopReason"] = "window_limit"
    if request_stopped():
        return stopped_result()
    ranked = []
    for _, candidate in retained.values():
        if request_stopped():
            return stopped_result()
        try:
            candidate["quality"] = layout_quality(
                problem,
                candidate["placements"],
                deadline=request_deadline,
                cancelled=cancelled,
            )
        except QualityInterrupted:
            return stopped_result()
        ranked.append(
            (
                (candidate["quality"]["regionalEmptyViolation"],
                 *score(
                     candidate["quality"],
                     candidate["placements"],
                     candidate["readingOrders"],
                 )),
                candidate,
            )
        )
    ranked.sort(key=lambda item: item[0])
    if request_stopped():
        return stopped_result()
    selected = ranked[0][1]
    output = dict(
        result,
        **selected,
        alternatives=[item[1] for item in ranked],
        gapCells=selected["quality"]["gapCells"],
        candidateCount=len(ranked),
        readingDiagnostics=diagnostics,
    )
    if selected["planKey"] != initial_key:
        output.update(optimal=False)
        count("regional.selected")
    if request_stopped():
        return stopped_result()
    diagnostics["selectedScore"] = bounded_score(ranked[0][0])
    diagnostics["selectedScoreStatus"] = "evaluated"
    return output
