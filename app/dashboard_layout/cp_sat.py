"""Anytime CP-SAT dashboard layout optimization."""
from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from ortools.sat.python import cp_model

from app.dashboard_layout.solver import (
    COLS,
    LayoutProblem,
    _row_seed,
    _skyline_seed,
    audit,
    plan_key,
    rank_candidates,
)

MAX_OPTIMIZATION_SECONDS = 4.5


def _rank_refined_candidates(problem, candidates, sources, *, deadline, cancelled):
    from app.dashboard_layout.columns import refine_column_regions

    ranked = rank_candidates(problem, candidates)
    if ranked and not (cancelled and cancelled.is_set()):
        original = list(ranked[0].placements)
        refined = refine_column_regions(
            problem, original, deadline=min(deadline - 0.2, time.monotonic() + 1.5),
            cancelled=cancelled,
        )
        if (refined is not original and time.monotonic() < deadline
                and not (cancelled and cancelled.is_set())):
            sources[plan_key(problem, refined)] = "ordered_column_regions"
            ranked = rank_candidates(problem, [refined] + [list(c.placements) for c in ranked])
    return ranked


def _batched_cp_sat_incumbent(problem: LayoutProblem, *, deadline: float,
                              cancelled: Event | None = None,
                              batch_size: int = 6) -> tuple[list[dict] | None, int]:
    """Solve bounded flat scopes in small exact models, then join whole rows."""
    if len(problem.nodes) <= batch_size or any(
            node.container or node.parentId or node.fixedX is not None
            for node in problem.nodes):
        return None, 0
    by_id = {node.id: node for node in problem.nodes}
    order = next((candidate for candidate in problem.orders
                  if len(candidate) == len(by_id)), None)
    if order is None:
        return None, 0

    chunks = [order[index:index + batch_size]
              for index in range(0, len(order), batch_size)]
    def solve_chunk(chunk: list[str]) -> list[dict] | None:
        if cancelled and cancelled.is_set() or time.monotonic() >= deadline:
            return None
        local = problem.model_copy(update={
            "nodes": [by_id[node_id] for node_id in chunk],
            "orders": [chunk],
            "orderSources": None,
            "requiredBefore": [(a, b) for a, b in problem.requiredBefore if a in chunk and b in chunk],
        })
        result = solve_cp_sat(local, deadline=deadline, cancelled=cancelled,
                              _allow_batching=False, _workers=1)
        return result.get("placements") if result.get("status") == "feasible" else None

    with ThreadPoolExecutor(max_workers=min(8, len(chunks))) as executor:
        local_results = list(executor.map(solve_chunk, chunks))
    if any(rows is None for rows in local_results):
        return None, 0

    placements = []
    offset_y = 0
    for local_rows in local_results:
        placements.extend(dict(row, y=row["y"] + offset_y)
                          for row in local_rows or [])
        offset_y += max(row["y"] + row["h"] for row in local_rows)

    return (placements, len(chunks)) if audit(problem, placements)["valid"] \
        else (None, 0)


def solve_cp_sat(problem: LayoutProblem, *, deadline: float,
                 cancelled: Event | None = None, _allow_batching: bool = True,
                 _workers: int = 4) -> dict:
    from app.dashboard_layout.incumbent import build_complete_incumbent

    started = time.monotonic()

    def reply(status: str, **values):
        return {
            "version": problem.version,
            "status": status,
            "method": "cp_sat_anytime",
            "elapsedMs": round((time.monotonic() - started) * 1000),
            **values,
        }

    if cancelled and cancelled.is_set():
        return reply("cancelled")
    if time.monotonic() >= deadline:
        return reply("budget_exhausted")

    profile_failures: list[dict] = []
    incumbent = build_complete_incumbent(problem, diagnostics=profile_failures)
    if not incumbent:
        impossible = [
            node.id for node in problem.nodes
            if node.fixedX is not None and
            not any(node.fixedX + shape.w <= COLS for shape in node.shapes)
        ]
        return reply(
            "infeasible_candidates",
            reason="no_valid_scope_profile",
            widgetIds=impossible or [node.id for node in problem.nodes],
            profileFailures=profile_failures[:20],
        )

    candidates = [incumbent]
    sources = {plan_key(problem, incumbent): "complete_incumbent"}

    batch_counts = {}
    batch_sizes_tried = []
    flat_large = _allow_batching and len(problem.nodes) > 18 and not any(
        node.container or node.parentId or node.fixedX is not None
        for node in problem.nodes)
    if flat_large:
        sizes = (6, 7, 8)
        for index, batch_size in enumerate(sizes):
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0.05:
                break
            attempt_deadline = now + min(1.0, remaining / (len(sizes) - index))
            batched, batch_count = _batched_cp_sat_incumbent(
                problem, deadline=attempt_deadline, cancelled=cancelled,
                batch_size=batch_size,
            )
            batch_sizes_tried.append(batch_size)
            if batched is not None:
                candidates.append(batched)
                key = plan_key(problem, batched)
                sources[key] = "batched_cp_sat"
                batch_counts[key] = batch_count

        ranked = _rank_refined_candidates(problem, candidates, sources,
                                          deadline=deadline, cancelled=cancelled)
        if cancelled and cancelled.is_set():
            return reply("cancelled")
        alternatives = [{
            "planKey": candidate.plan_key,
            "placements": list(candidate.placements),
            "quality": candidate.quality,
        } for candidate in ranked]
        selected = alternatives[0]
        selected_source = sources.get(selected["planKey"], "complete_incumbent")
        return reply(
            "feasible",
            placements=selected["placements"], alternatives=alternatives,
            candidateCount=len(alternatives),
            gapCells=selected["quality"]["gapCells"], searchPruned=True,
            optimal=False, objectiveValue=None, objectiveBound=None,
            branches=0, conflicts=0, seedMethod=selected_source,
            batchCount=batch_counts.get(selected["planKey"], 0),
            batchSizesTried=batch_sizes_tried,
        )

    # These are deterministic mathematical constructions, not fallback paths.
    # They provide strong, distinct warm starts before branch-and-bound begins.
    seed_deadline = min(deadline, time.monotonic() +
                        (0.1 if len(problem.nodes) > 12 else 0.8))
    row_seed = _row_seed(problem, seed_deadline)
    if row_seed is not None:
        candidates.append(row_seed)
        sources.setdefault(plan_key(problem, row_seed), "row_seed")
    if time.monotonic() < deadline:
        skyline_seed = _skyline_seed(
            problem, min(deadline, time.monotonic() +
                         (0.1 if len(problem.nodes) > 12 else 0.5))
        )
        if skyline_seed is not None:
            candidates.append(skyline_seed)
            sources.setdefault(plan_key(problem, skyline_seed), "skyline_seed")

    model = cp_model.CpModel()
    variables: dict[str, dict] = {}
    scopes = defaultdict(list)
    max_bottom = min(10000, sum(node.maxH for node in problem.nodes))

    for node in problem.nodes:
        shapes = list(node.shapes)
        index = model.new_int_var(0, len(shapes) - 1, f"{node.id}.shape")
        x = model.new_int_var(0, COLS - 1, f"{node.id}.x")
        y = model.new_int_var(0, max_bottom - 1, f"{node.id}.y")
        w = model.new_int_var(min(shape.w for shape in shapes),
                              max(shape.w for shape in shapes), f"{node.id}.w")
        h = model.new_int_var(1, node.maxH, f"{node.id}.h")
        base_h = model.new_int_var(min(shape.h for shape in shapes),
                                   max(shape.h for shape in shapes), f"{node.id}.base_h")
        variant = model.new_int_var(min(shape.variant for shape in shapes),
                                    max(shape.variant for shape in shapes), f"{node.id}.variant")
        parent_variant = model.new_int_var(min(shape.parentVariant for shape in shapes),
                                           max(shape.parentVariant for shape in shapes),
                                           f"{node.id}.parent_variant")
        cost = model.new_int_var(min(shape.cost for shape in shapes),
                                 max(shape.cost for shape in shapes), f"{node.id}.cost")
        model.add_element(index, [shape.w for shape in shapes], w)
        model.add_element(index, [shape.h for shape in shapes], base_h)
        model.add_element(index, [shape.variant for shape in shapes], variant)
        model.add_element(index, [shape.parentVariant for shape in shapes], parent_variant)
        model.add_element(index, [shape.cost for shape in shapes], cost)
        if node.container:
            model.add(h >= base_h)
        else:
            model.add(h == base_h)
        if node.fixedX is not None:
            model.add(x == node.fixedX)
        end_x = model.new_int_var(1, COLS, f"{node.id}.end_x")
        end_y = model.new_int_var(1, max_bottom, f"{node.id}.end_y")
        model.add(end_x == x + w)
        model.add(end_y == y + h)
        area = model.new_int_var(1, COLS * node.maxH, f"{node.id}.area")
        model.add_multiplication_equality(area, [w, h])
        variables[node.id] = {
            "index": index, "x": x, "y": y, "w": w, "h": h,
            "variant": variant, "parent_variant": parent_variant,
            "cost": cost, "end_x": end_x, "end_y": end_y, "area": area,
            "x_interval": model.new_interval_var(x, w, end_x, f"{node.id}.ix"),
            "y_interval": model.new_interval_var(y, h, end_y, f"{node.id}.iy"),
        }
        scopes[node.parentId].append(node)

    for node in problem.nodes:
        selected_parent = variables[node.parentId]["variant"] if node.parentId else 0
        model.add(variables[node.id]["parent_variant"] == selected_parent)

    bottoms = {}
    gaps = []
    shared_edges = []
    for parent_id, nodes in scopes.items():
        rows = [variables[node.id] for node in nodes]
        model.add_no_overlap_2d([row["x_interval"] for row in rows],
                                [row["y_interval"] for row in rows])
        bottom = model.new_int_var(1, max_bottom, f"{parent_id}.bottom")
        model.add_max_equality(bottom, [row["end_y"] for row in rows])
        bottoms[parent_id] = bottom
        gap = model.new_int_var(0, COLS * max_bottom, f"{parent_id}.gap")
        model.add(gap == COLS * bottom - sum(row["area"] for row in rows))
        gaps.append(gap)
        for column in range(1, COLS):
            edges = []
            for node in nodes:
                row = variables[node.id]
                for field in ("x", "end_x"):
                    edge = model.new_bool_var(f"{node.id}.{field}.{column}")
                    model.add(row[field] == column).only_enforce_if(edge)
                    model.add(row[field] != column).only_enforce_if(edge.Not())
                    edges.append(edge)
            used = model.new_bool_var(f"{parent_id}.edge_used.{column}")
            count = sum(edges)
            model.add(count >= 1).only_enforce_if(used)
            model.add(count == 0).only_enforce_if(used.Not())
            shared = model.new_int_var(0, max(0, len(edges) - 1),
                                       f"{parent_id}.edge_shared.{column}")
            model.add(shared == count - used)
            shared_edges.append(shared)
        if parent_id is not None:
            parent_node = next(node for node in problem.nodes if node.id == parent_id)
            step, margin = (34, 4) if parent_node.parentId else (40, 10)
            model.add(variables[parent_id]["h"] * step - margin >=
                      bottom * 34 + 12 + parent_node.headerPx)

    for first_id, second_id in problem.requiredBefore:
        first, second = variables[first_id], variables[second_id]
        left = model.new_bool_var(f"required.{first_id}.{second_id}.left")
        model.add(first["end_x"] <= second["x"]).only_enforce_if(left)
        model.add(first["y"] < second["y"]).only_enforce_if(left.Not())

    for order in problem.orders:
        modes = [model.new_bool_var(f"order.{id}.column_major") for id in order]
        # Switch traversal only after an entire prefix ends above the suffix.
        prefix_bottom = variables[order[0]]["end_y"]
        suffix_tops = [variables[id]["y"] for id in order]
        for i in range(len(order)-2, -1, -1):
            top = model.new_int_var(0, 10000, f"order.{order[i]}.suffix_top")
            model.add_min_equality(top, [suffix_tops[i], suffix_tops[i+1]])
            suffix_tops[i] = top
        for i in range(len(order)-1):
            if i:
                bottom = model.new_int_var(0, 10000, f"order.{order[i]}.prefix_bottom")
                model.add_max_equality(bottom, [prefix_bottom, variables[order[i]]["end_y"]])
                prefix_bottom = bottom
            boundary = model.new_bool_var(f"order.{order[i]}.band_end")
            model.add(prefix_bottom <= suffix_tops[i+1]).only_enforce_if(boundary)
            model.add(modes[i] == modes[i+1]).only_enforce_if(boundary.Not())
        for first_index, first_id in enumerate(order):
            column_major = modes[first_index]
            for second_id in order[first_index + 1:]:
                first = variables[first_id]
                second = variables[second_id]
                left = model.new_bool_var(f"order.{first_id}.{second_id}.left")
                model.add(first["end_x"] <= second["x"]).only_enforce_if(
                    [column_major, left])
                model.add(first["end_y"] <= second["y"]).only_enforce_if(
                    [column_major, left.Not()])
                higher_row = model.new_bool_var(
                    f"order.{first_id}.{second_id}.higher_row")
                model.add(first["y"] < second["y"]).only_enforce_if(
                    [column_major.Not(), higher_row])
                model.add(first["y"] == second["y"]).only_enforce_if(
                    [column_major.Not(), higher_row.Not()])
                model.add(first["end_x"] <= second["x"]).only_enforce_if(
                    [column_major.Not(), higher_row.Not()])

    incumbent_by_id = {row["id"]: row for row in incumbent}
    node_by_id = {node.id: node for node in problem.nodes}
    for node_id, row in incumbent_by_id.items():
        values = variables[node_id]
        parent_variant = incumbent_by_id[node_by_id[node_id].parentId]["variant"] \
            if node_by_id[node_id].parentId else 0
        shape_index = next(index for index, shape in enumerate(node_by_id[node_id].shapes)
                           if shape.w == row["w"] and shape.variant == row["variant"] and
                           shape.parentVariant == parent_variant and
                           (shape.h <= row["h"] if node_by_id[node_id].container
                            else shape.h == row["h"]))
        for field in ("x", "y", "w", "h", "variant"):
            model.add_hint(values[field], row[field])
        model.add_hint(values["index"], shape_index)

    total_gap = sum(gaps)
    alignment = sum(shared_edges)
    total_height = sum(bottoms.values())
    total_cost = sum(values["cost"] for values in variables.values())
    branches = 0
    conflicts = 0
    optimal = False
    objective_value = None
    objective_bound = None

    def read_solution(reader) -> list[dict]:
        return [{
            "id": node.id,
            **{field: int(reader.value(variables[node.id][field]))
               for field in ("x", "y", "w", "h", "variant")},
        } for node in problem.nodes]

    class Collector(cp_model.CpSolverSolutionCallback):
        def on_solution_callback(self):
            if cancelled and cancelled.is_set():
                self.stop_search()
                return
            candidate = read_solution(self)
            if audit(problem, candidate)["valid"]:
                candidates.append(candidate)
                sources.setdefault(plan_key(problem, candidate), "global_cp_sat")

    optimization_deadline = min(deadline, started + MAX_OPTIMIZATION_SECONDS)
    remaining = optimization_deadline - time.monotonic()
    if remaining > 0 and not (cancelled and cancelled.is_set()):
        # Whitespace and repeated boundaries share the dominant scale so both
        # are optimized from the first branch. Shape comfort and height only
        # break ties after visual structure.
        objective = (total_gap - alignment) * 1_000_000 + \
            total_cost * 100 + total_height
        model.minimize(objective)
        solver = cp_model.CpSolver()
        # Leave room for callback auditing, ranking and JSON serialization so
        # the HTTP request itself stays inside the advertised budget.
        solver.parameters.max_time_in_seconds = max(0.001, remaining - 0.2)
        solver.parameters.num_search_workers = _workers
        solver.parameters.random_seed = 0
        collector = Collector()
        status = solver.solve(model, collector)
        branches += solver.num_branches
        conflicts += solver.num_conflicts
        if status in (cp_model.FEASIBLE, cp_model.OPTIMAL):
            candidate = read_solution(solver)
            if audit(problem, candidate)["valid"]:
                candidates.append(candidate)
                sources.setdefault(plan_key(problem, candidate), "global_cp_sat")
            objective_value = int(round(solver.objective_value))
            objective_bound = int(round(solver.best_objective_bound))
        optimal = status == cp_model.OPTIMAL

    if cancelled and cancelled.is_set():
        return reply("cancelled", branches=branches, conflicts=conflicts)
    ranked = _rank_refined_candidates(problem, candidates, sources,
                                      deadline=deadline, cancelled=cancelled)
    if cancelled and cancelled.is_set():
        return reply("cancelled", branches=branches, conflicts=conflicts)
    if not ranked:
        return reply("infeasible_candidates", reason="no_audited_candidate",
                     branches=branches, conflicts=conflicts)
    alternatives = [{
        "planKey": candidate.plan_key,
        "placements": list(candidate.placements),
        "quality": candidate.quality,
    } for candidate in ranked]
    selected_source = sources.get(alternatives[0]["planKey"], "global_cp_sat")
    return reply(
        "feasible",
        placements=alternatives[0]["placements"],
        alternatives=alternatives,
        candidateCount=len(alternatives),
        gapCells=alternatives[0]["quality"]["gapCells"],
        searchPruned=not optimal,
        optimal=optimal,
        objectiveValue=objective_value,
        objectiveBound=objective_bound,
        branches=branches,
        conflicts=conflicts,
        seedMethod=selected_source,
        batchCount=batch_counts.get(alternatives[0]["planKey"], 0),
    )
