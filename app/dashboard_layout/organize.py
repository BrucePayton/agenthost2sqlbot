"""Bounded, scope-local dashboard cleanup using readable shape candidates only."""
from __future__ import annotations

import time
from collections import defaultdict

from app.dashboard_layout.incumbent import build_complete_incumbent, _depths, _scope_order
from app.dashboard_layout.regions import region_plans
from app.dashboard_layout.solver import (
    COLS, LayoutProblem, Shape, _row_seed, audit, rank_candidates,
)

MAX_BATCH_NODES = 12
MAX_SECONDS = 2.0
BATCH_SECONDS = 0.12


def _stack_plans(nodes):
    """Try one/two metric columns beside a consecutive stack of readable charts."""
    metrics = [node for node in nodes if node.kind == "metric"]
    charts = [node for node in nodes if node.kind in ("chart", "rank")]
    if not metrics or not charts or len(metrics) + len(charts) != len(nodes):
        return []
    left_metrics = nodes == metrics + charts
    if not left_metrics and nodes != charts + metrics:
        return []
    plans = []
    for columns in ([1] if len(metrics) in (2, 3) else [1, 2]):
        for width in (3, 4, 6, 8, 12):
            side = width * columns
            if side >= COLS:
                continue
            common = None
            choices = []
            for i, node in enumerate(metrics):
                w = side if columns == 2 and len(metrics) % 2 and i == len(metrics)-1 else width
                matching = {s.h: s for s in sorted(node.shapes, key=lambda s: -s.cost)
                            if s.w == w and s.h <= node.maxH}
                choices.append(matching)
                common = set(matching) if common is None else common & set(matching)
            if not common:
                continue
            h = min(common)
            height = ((len(metrics) + columns - 1) // columns) * h
            # A bounded height DP avoids multiplying chart shape combinations.
            states = {0: (0, [])}
            for chart in charts:
                updated = {}
                for used, (cost, picked) in states.items():
                    for s in chart.shapes:
                        total = used + s.h
                        if s.w != COLS-side or s.h > chart.maxH or total > height:
                            continue
                        value = (cost+s.cost, picked+[s])
                        if total not in updated or value[0] < updated[total][0]:
                            updated[total] = value
                states = updated
            if height not in states:
                continue
            plan = []
            mx, cx = (0, side) if left_metrics else (COLS-side, 0)
            for i, node in enumerate(metrics):
                s = choices[i][h]
                plan.append(dict(id=node.id, x=mx+(i % columns)*width,
                                 y=(i // columns)*h, w=s.w, h=h, variant=s.variant))
            y = 0
            for node, s in zip(charts, states[height][1]):
                plan.append(dict(id=node.id, x=cx, y=y, w=s.w, h=s.h, variant=s.variant))
                y += s.h
            plans.append(plan)
    return plans


def _batches(nodes):
    """Keep small metric/chart runs together; cap long runs deterministically."""
    start = 0
    while start < len(nodes):
        end = min(len(nodes), start + MAX_BATCH_NODES)
        # Prefer a chart-to-metric boundary near the end of a batch.
        if end < len(nodes):
            boundaries = [i for i in range(start+3, end+1)
                          if nodes[i-1].kind != "metric" and nodes[i].kind == "metric"]
            if boundaries:
                end = boundaries[-1]
        yield nodes[start:end]
        start = end


def _refine_boundaries(local, plan, boundaries, deadline, cancelled):
    """Recompose complete bands around batch joins; never detach a crossing card."""
    result = plan
    for boundary_id in boundaries:
        if time.monotonic() >= deadline or cancelled and cancelled.is_set():
            break
        by_id = {r["id"]: r for r in result}
        seam = by_id[boundary_id]["y"]
        levels = sorted({r["y"] for r in result} | {r["y"]+r["h"] for r in result})
        cuts = [y for y in levels if not any(r["y"] < y < r["y"]+r["h"] for r in result)]
        starts = [y for y in cuts if y < seam][-2:]
        ends = [y for y in cuts if y > seam][:2]
        for top in starts:
            for bottom in reversed(ends):
                if time.monotonic() >= deadline:
                    return result
                nodes = [n for n in local.nodes if top <= by_id[n.id]["y"] < bottom]
                if not 1 < len(nodes) <= MAX_BATCH_NODES:
                    continue
                ids = {n.id for n in nodes}
                indices = [i for i, n in enumerate(local.nodes) if n.id in ids]
                if indices[-1]-indices[0]+1 != len(indices):
                    continue
                candidates = region_plans(nodes, min(deadline, time.monotonic()+BATCH_SECONDS), cancelled)
                for candidate in candidates:
                    height = max(r["y"]+r["h"] for r in candidate)
                    proposal = [dict(r, y=r["y"]+height-(bottom-top)) if r["y"] >= bottom else r
                                for r in result if r["id"] not in ids]
                    proposal.extend(dict(r, y=r["y"]+top) for r in candidate)
                    ranked = rank_candidates(local, [result, proposal], 1, deadline=deadline)
                    if ranked and list(ranked[0].placements) != result:
                        result = list(ranked[0].placements)
                        break
                if result != plan:
                    break
            if result != plan:
                break
        plan = result
    return result


def organize(problem, *, deadline, cancelled=None):
    """Build a complete plan first, then improve bounded batches within each scope."""
    started = time.monotonic()
    deadline = min(deadline, started + MAX_SECONDS)
    plan = build_complete_incumbent(problem)
    base = dict(version=problem.version, method="bounded_scope_batches")
    if not plan:
        return dict(base, status="infeasible_candidates", reason="no_valid_scope_profile")
    scopes = defaultdict(list)
    for node in problem.nodes:
        scopes[node.parentId].append(node)
    depths = _depths(problem)
    batch_count = 0
    for parent in sorted(scopes, key=lambda p: depths[p]+1 if p else 0, reverse=True):
        if cancelled and cancelled.is_set():
            return dict(base, status="cancelled")
        if time.monotonic() >= deadline:
            break
        by_id = {r["id"]: r for r in plan}
        ordered = _scope_order(problem, scopes[parent])
        pv = by_id[parent]["variant"] if parent else 0
        local_nodes = []
        for node in ordered:
            shapes = [s.model_copy(update={"parentVariant": 0}) for s in node.shapes
                      if s.parentVariant == pv]
            if node.container:
                row = by_id[node.id]
                shapes = [Shape(w=row["w"], h=row["h"], variant=row["variant"])]
            local_nodes.append(node.model_copy(update={
                "parentId": None, "container": False, "shapes": shapes,
            }))
        local = LayoutProblem(version=problem.version, objective=problem.objective, nodes=local_nodes,
                              orders=[[n.id for n in local_nodes]])
        isolated = len(local_nodes) <= 3 and all(n.kind == "metric" for n in local_nodes)
        if isolated and all(n.original for n in local_nodes):
            preserved = []
            for n in local_nodes:
                s = next((s for s in n.shapes if (s.w, s.h) == (n.original.w, n.original.h)), None)
                if s is None:
                    break
                preserved.append(dict(id=n.id, **n.original.model_dump(), variant=s.variant))
            proposal = [r for r in plan if r["id"] not in {n.id for n in local_nodes}] + preserved
            if audit(problem, proposal)["valid"]:
                plan = proposal
            continue
        assembled = []
        boundaries = []
        y = 0
        for batch in _batches(local_nodes):
            if cancelled and cancelled.is_set():
                return dict(base, status="cancelled")
            part = LayoutProblem(version=problem.version, objective=problem.objective, nodes=batch,
                                 orders=[[n.id for n in batch]])
            fallback = build_complete_incumbent(part)
            candidates = [fallback] if fallback else []
            if time.monotonic() < deadline:
                end = min(deadline, time.monotonic() + BATCH_SECONDS)
                templates = rank_candidates(part, _stack_plans(batch), 100, deadline=end)
                if templates:
                    # All templates fill the block. Prefer a shorter readable
                    # chart before switching four metrics to a tall single column.
                    best = min(templates, key=lambda c: (
                        c.quality["totalHeight"], c.quality["shapeCost"], c.score))
                    candidates.append(list(best.placements))
                candidates.extend(region_plans(batch, end, cancelled))
                if templates:
                    # Retain the user's explicit metric preferences on a full
                    # block unless a readable regional plan is shorter.
                    shorter = [c for c in rank_candidates(part, candidates, 100, deadline=deadline)
                               if c.quality["gapCells"] == 0 and
                               c.quality["totalHeight"] < best.quality["totalHeight"]]
                    candidates = [list(c.placements) for c in shorter] or [list(best.placements)]
                if not templates and time.monotonic() < end:
                    seed = _row_seed(part, min(end, time.monotonic()+0.03))
                    if seed:
                        candidates.append(seed)
                batch_count += 1
            ranked = rank_candidates(part, candidates, 1, deadline=deadline)
            if not ranked:
                assembled = []
                break
            chosen = ranked[0].placements
            if assembled:
                boundaries.append(batch[0].id)
            assembled.extend(dict(r, y=r["y"]+y) for r in chosen)
            y += max(r["y"]+r["h"] for r in chosen)
        if assembled and boundaries:
            assembled = _refine_boundaries(local, assembled, boundaries, deadline, cancelled)
        current = [by_id[n.id] for n in local_nodes]
        ranked = rank_candidates(local, [current, assembled], 1, deadline=deadline)
        if ranked:
            proposal = [r for r in plan if r["id"] not in {n.id for n in local_nodes}]
            proposal.extend(ranked[0].placements)
            if audit(problem, proposal)["valid"]:
                plan = proposal
    if cancelled and cancelled.is_set():
        return dict(base, status="cancelled")
    ranked = rank_candidates(problem, [plan], 1, deadline=deadline)
    candidate = ranked[0]
    alternative = dict(planKey=candidate.plan_key, placements=list(candidate.placements),
                       quality=candidate.quality)
    return dict(base, status="feasible", placements=alternative["placements"],
                alternatives=[alternative], candidateCount=1,
                gapCells=candidate.quality["gapCells"], searchPruned=True, optimal=False,
                batchCount=batch_count, elapsedMs=round((time.monotonic()-started)*1000))
