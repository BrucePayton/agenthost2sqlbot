"""Bounded, ordered MaxRects construction with conditional container profiles."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from threading import Event

from rectpack.geometry import Rectangle
from rectpack.maxrects import MaxRects

from app.dashboard_layout.solver import (
    COLS, LayoutProblem, Node, Shape, _objective_score, _row_seed,
    _skyline_seed, audit, rank_candidates,
)

BEAM_WIDTH = 48
MAX_EXPANSIONS = 160000
MAX_PROFILES = 4
MAX_CANDIDATE_PLANS = 64


class LinearFallbackError(Exception):
    """The deterministic incumbent cannot satisfy hard geometry constraints."""


@dataclass(frozen=True)
class Profile:
    """One external shape and its already validated descendant placements."""
    w: int
    h: int
    variant: int
    cost: int
    descendants: tuple[dict, ...] = ()


@dataclass
class State:
    """A persistent partial layout; shared free rectangles are never mutated."""
    free: tuple[Rectangle, ...]
    placed: dict[str, dict]
    area: int = 0
    bottom: int = 0
    cost: int = 0


@dataclass
class ScopeResult:
    """Search exhaustion is not proof that the full geometry is impossible."""
    plans: list[list[dict]]
    impossible: bool = False


class Budget:
    """One monotonic deadline and work limit for all scopes and variants."""
    def __init__(self, deadline: float, cancelled: Event | None):
        self.deadline = deadline
        self.cancelled = cancelled
        self.expansions = 0
        self.pruned = False

    def stopped(self, deadline: float | None = None) -> bool:
        exhausted = self.expansions >= MAX_EXPANSIONS
        timed_out = time.monotonic() >= min(deadline or self.deadline, self.deadline)
        if exhausted or timed_out:
            self.pruned = True
        return bool((self.cancelled and self.cancelled.is_set()) or timed_out or exhausted)


class FreeRects(MaxRects):
    """Adapter around pinned rectpack's split/prune algorithm, without rotation."""
    @classmethod
    def after(cls, free: tuple[Rectangle, ...], rect: Rectangle) -> tuple[Rectangle, ...]:
        packer = cls(COLS, 10000, rot=False)
        packer._max_rects = list(free)
        packer._split(rect)
        packer._remove_duplicates()
        return tuple(packer._max_rects)


def _ordered_scope_nodes(problem: LayoutProblem, nodes: list[Node]) -> list[Node]:
    by_id = {node.id: node for node in nodes}
    order = next((order for order in problem.orders
                  if len(order) == len(nodes) and all(id in by_id for id in order)), None)
    if order:
        return [by_id[id] for id in order]
    return nodes


def _first_fit(placements: list[dict], shape: Shape | Profile,
               fixed_x: int | None = None) -> tuple[int, int]:
    occupied = set()
    previous = None
    for row in placements:
        previous = row
        for y in range(row["y"], row["y"] + row["h"]):
            for x in range(row["x"], row["x"] + row["w"]):
                occupied.add((x, y))
    minimum = (previous["y"], previous["x"] + 1) if previous else (0, 0)
    xs = [fixed_x] if fixed_x is not None else range(0, COLS - shape.w + 1)
    for y in range(10000):
        for x in xs:
            if x is None or x < 0 or x + shape.w > COLS or (y, x) < minimum:
                continue
            if all((cx, cy) not in occupied
                   for cy in range(y, y + shape.h)
                   for cx in range(x, x + shape.w)):
                return x, y
    raise LinearFallbackError("no first-fit position")


def _linear_incumbent(problem: LayoutProblem) -> list[dict] | None:
    """Build a legal O(n) shelf-style incumbent before bounded search starts."""
    scopes = defaultdict(list)
    nodes = {node.id: node for node in problem.nodes}
    for node in problem.nodes:
        scopes[node.parentId].append(node)

    def scope(parent_id: str | None, parent_variant: int = 0) -> list[dict]:
        result: list[dict] = []
        for node in _ordered_scope_nodes(problem, scopes[parent_id]):
            profiles: list[Profile] = []
            for shape in sorted(
                (shape for shape in node.shapes
                 if shape.parentVariant == parent_variant and shape.h <= node.maxH),
                key=lambda item: (item.cost, item.w * item.h, item.w, item.h),
            ):
                if node.fixedX is not None and node.fixedX + shape.w > COLS:
                    continue
                if node.container and scopes[node.id]:
                    try:
                        descendants = scope(node.id, shape.variant)
                    except LinearFallbackError:
                        continue
                    child_bottom = max((row["y"] + row["h"] for row in descendants
                                        if nodes[row["id"]].parentId == node.id), default=0)
                    step, gap = (34, 4) if node.parentId else (40, 10)
                    needed = child_bottom * 34 + 12 + node.headerPx + gap
                    height = max(shape.h, (needed + step - 1) // step)
                    if height <= node.maxH:
                        profiles.append(Profile(shape.w, height, shape.variant,
                                                shape.cost, tuple(descendants)))
                else:
                    profiles.append(Profile(shape.w, shape.h, shape.variant, shape.cost))
            if not profiles:
                raise LinearFallbackError(f"no shape for {node.id}")
            profile = profiles[0]
            x, y = _first_fit(result, profile, node.fixedX)
            result.append(dict(id=node.id, x=x, y=y, w=profile.w, h=profile.h,
                               variant=profile.variant))
            result.extend(profile.descendants)
        return result

    try:
        candidate = scope(None)
    except LinearFallbackError:
        return None
    return candidate if audit(problem, candidate)["valid"] else None


def positions(state: State, shape: Shape, fixed_x: int | None,
              predecessor: dict | None):
    """Boundary placements include both edges, fixed columns and gap backfill."""
    found = set()
    minimum = (predecessor["y"], predecessor["x"] + 1) if predecessor else (0, 0)
    for free in state.free:
        if free.width < shape.w or free.height < shape.h:
            continue
        xs = [fixed_x] if fixed_x is not None else range(free.x, free.right - shape.w + 1)
        ys = {free.y, max(free.y, minimum[0])}
        if state.bottom >= shape.h:
            ys.add(state.bottom - shape.h)
        for y in ys:
            if y < free.y or y + shape.h > free.top:
                continue
            for x in xs:
                if free.x <= x <= free.right - shape.w and (y, x) >= minimum:
                    found.add((x, y))
    return sorted(found, key=lambda p: (p[1], p[0]))


def state_score(state: State) -> int:
    """Penalize holes before height, while retaining measured shape preferences."""
    return (COLS * state.bottom - state.area) * 1000 + state.bottom * 10 + state.cost


def state_key(state: State, predecessors: dict[str, str]) -> tuple:
    """Keep distinct future geometry and order boundaries, not duplicate paths."""
    return (tuple(sorted(state.placed)),
            tuple(sorted((r.x, r.y, r.width, r.height) for r in state.free)),
            tuple((id, state.placed[id]["y"], state.placed[id]["x"])
                  for id in sorted(set(predecessors.values()) & state.placed.keys())))


def retain_profiles(problem: LayoutProblem, plans: list[list[dict]]) -> list[list[dict]]:
    """Preserve different external heights; the parent chooses, not the child."""
    best = {}
    for plan in plans:
        if not audit(problem, plan)["valid"]:
            continue
        height = max(r["y"] + r["h"] for r in plan)
        score = _objective_score(problem, plan)
        if height not in best or score < best[height][0]:
            best[height] = (score, plan)
    return [item[1] for item in sorted(best.values(), key=lambda item: item[0])[:MAX_PROFILES]]


def search_scope(problem: LayoutProblem, budget: Budget, deadline: float,
                 want_profiles: bool = False) -> ScopeResult:
    """Construct a scope; incomplete bounded search never reports infeasibility."""
    if budget.stopped(deadline):
        return ScopeResult([])
    plans = []
    seed = _row_seed(problem, min(deadline, time.monotonic() + 0.12))
    if seed:
        plans.append(seed)
    seed = _skyline_seed(problem, min(deadline, time.monotonic() + 0.18))
    if seed:
        plans.append(seed)
    predecessors = {b: a for order in problem.orders for a, b in zip(order, order[1:])}
    nodes = {n.id: n for n in problem.nodes}
    height_limit = min(10000, sum(max(s.h for s in n.shapes) for n in problem.nodes))
    states = [State((Rectangle(0, 0, COLS, height_limit),), {})]
    for index in range(len(nodes)):
        candidates = {}
        for state in states:
            if budget.stopped(deadline):
                return ScopeResult(retain_profiles(problem, plans))
            available = [n for n in nodes.values() if n.id not in state.placed and
                         (n.id not in predecessors or predecessors[n.id] in state.placed)]
            # Unordered scopes need some order alternatives, but not N! permutations.
            if len(available) > 6:
                available = sorted(available, key=lambda n: -max(s.w * s.h for s in n.shapes))[:5] + available[:1]
                available = list({n.id: n for n in available}.values())
                budget.pruned = True
            for node in available:
                prior = state.placed.get(predecessors.get(node.id))
                for shape in node.shapes:
                    for x, y in positions(state, shape, node.fixedX, prior):
                        if budget.stopped(deadline):
                            return ScopeResult(retain_profiles(problem, plans))
                        budget.expansions += 1
                        rect = Rectangle(x, y, shape.w, shape.h)
                        free = FreeRects.after(state.free, rect)
                        placed = dict(state.placed)
                        placed[node.id] = dict(id=node.id, x=x, y=y, w=shape.w,
                                               h=shape.h, variant=shape.variant)
                        candidate = State(free, placed, state.area + shape.w * shape.h,
                                          max(state.bottom, y + shape.h), state.cost + shape.cost + y)
                        if index == len(nodes) - 1:
                            plan = list(placed.values())
                            if audit(problem, plan)["valid"]:
                                plans.append(plan)
                                if len(plans) >= MAX_CANDIDATE_PLANS:
                                    if want_profiles:
                                        return ScopeResult(retain_profiles(problem, plans))
                                    return ScopeResult([
                                        list(candidate.placements)
                                        for candidate in rank_candidates(
                                            problem, plans, MAX_CANDIDATE_PLANS, deadline=deadline
                                        )
                                    ])
                            continue
                        key = state_key(candidate, predecessors)
                        if key not in candidates or state_score(candidate) < state_score(candidates[key]):
                            candidates[key] = candidate
        ranked = sorted(candidates.values(), key=state_score)
        if len(ranked) > BEAM_WIDTH:
            budget.pruned = True
        # Reserve half the beam for different bottom/shape frontiers.
        states = ranked[:BEAM_WIDTH // 2]
        signatures = {(s.bottom, tuple((r.x, r.y, r.width, r.height) for r in s.free)) for s in states}
        for candidate in ranked[BEAM_WIDTH // 2:]:
            signature = (candidate.bottom, tuple((r.x, r.y, r.width, r.height) for r in candidate.free))
            if signature not in signatures:
                states.append(candidate)
                signatures.add(signature)
            if len(states) >= BEAM_WIDTH:
                break
        if not states:
            break
    retained = retain_profiles(problem, plans) if want_profiles else [
        list(candidate.placements)
        for candidate in rank_candidates(problem, plans, MAX_CANDIDATE_PLANS, deadline=deadline)
    ]
    return ScopeResult(retained, impossible=len(nodes) == 1 and not plans)


class Constructor:
    """Memoize child profiles for each existing group and parent width variant."""
    def __init__(self, problem: LayoutProblem, budget: Budget):
        self.problem = problem
        self.budget = budget
        self.scopes = defaultdict(list)
        for node in problem.nodes:
            self.scopes[node.parentId].append(node)
        self.cache = {}
        self.impossible_ids = []

    def scope(self, parent_id: str | None, variant: int = 0) -> ScopeResult:
        """Compile descendants before selecting their parent's external shape."""
        key = (parent_id, variant)
        if key in self.cache:
            return self.cache[key]
        if self.budget.stopped():
            return ScopeResult([])
        nodes = self.scopes[parent_id]
        if not nodes:
            return ScopeResult([[]])
        profiles = {}
        leaves = []
        for node in nodes:
            choices = []
            rejected_by_search = False
            for shape in node.shapes:
                if shape.parentVariant != variant or shape.h > node.maxH:
                    continue
                if node.fixedX is not None and node.fixedX + shape.w > COLS:
                    continue
                if node.container and self.scopes[node.id]:
                    children = self.scope(node.id, shape.variant)
                    rejected_by_search |= not children.plans and not children.impossible
                    for child_plan in children.plans:
                        step, gap = (34, 4) if node.parentId else (40, 10)
                        needed = max(r["y"] + r["h"] for r in child_plan if r["id"] in
                                     {n.id for n in self.scopes[node.id]}) * 34 + 12 + node.headerPx + gap
                        height = max(shape.h, (needed + step - 1) // step)
                        if height <= min(node.maxH, max(6, (needed + step - 1) // step)):
                            choices.append(Profile(shape.w, height, shape.variant, shape.cost, tuple(child_plan)))
                else:
                    choices.append(Profile(shape.w, shape.h, shape.variant, shape.cost))
            if not choices:
                self.impossible_ids.append(node.id)
                result = ScopeResult([], impossible=not rejected_by_search and not self.budget.stopped())
                self.cache[key] = result
                return result
            profiles[node.id] = choices
            leaves.append(Node(id=node.id, maxH=node.maxH, fixedX=node.fixedX,
                               shapes=[Shape(w=p.w, h=p.h, variant=i, cost=p.cost)
                                       for i, p in enumerate(choices)]))
        ids = {n.id for n in nodes}
        orders = [order for order in self.problem.orders if order[0] in ids]
        local = LayoutProblem(version="constraint-v1", nodes=leaves, orders=orders,
                              budgetMs=self.problem.budgetMs)
        # Child search must not spend the whole request before the root is packed.
        end = min(self.budget.deadline, time.monotonic() + 0.15) if parent_id else self.budget.deadline
        result = search_scope(local, self.budget, end, want_profiles=parent_id is not None)
        expanded = []
        for plan in result.plans:
            placements = []
            for r in plan:
                profile = profiles[r["id"]][r["variant"]]
                placements.append(dict(r, variant=profile.variant))
                placements.extend(profile.descendants)
            expanded.append(placements)
        result = ScopeResult(expanded, result.impossible)
        if result.impossible:
            self.impossible_ids.extend(n.id for n in nodes)
        self.cache[key] = result
        return result


def solve_bounded(problem: LayoutProblem, *, deadline: float | None = None,
                  cancelled: Event | None = None) -> dict:
    """Return ranked audited incumbents, or an honest search termination."""
    started = time.monotonic()
    budget = Budget(min(deadline or float("inf"), started + problem.budgetMs / 1000), cancelled)

    def reply(status, **kw):
        return dict(version="constraint-v1", status=status, method="bounded_maxrects",
                    elapsedMs=round((time.monotonic() - started) * 1000),
                    expansions=budget.expansions, searchPruned=budget.pruned, **kw)

    if cancelled and cancelled.is_set():
        return reply("cancelled")
    incumbent = _linear_incumbent(problem)
    if budget.stopped():
        if incumbent:
            ranked = rank_candidates(problem, [incumbent], deadline=budget.deadline)
            if ranked:
                candidate = ranked[0]
                return reply("feasible", placements=list(candidate.placements),
                             alternatives=[{
                                 "planKey": candidate.plan_key,
                                 "placements": list(candidate.placements),
                                 "quality": candidate.quality,
                             }],
                             candidateCount=1, gapCells=candidate.quality["gapCells"])
        return reply("budget_exhausted")
    constructor = Constructor(problem, budget)
    result = constructor.scope(None)
    if cancelled and cancelled.is_set():
        return reply("cancelled")
    ranked = rank_candidates(problem, ([incumbent] if incumbent else []) + result.plans,
                             deadline=budget.deadline)
    if ranked:
        alternatives = [
            {
                "planKey": candidate.plan_key,
                "placements": list(candidate.placements),
                "quality": candidate.quality,
            }
            for candidate in ranked
        ]
        return reply(
            "feasible",
            placements=alternatives[0]["placements"],
            alternatives=alternatives,
            candidateCount=len(alternatives),
            gapCells=alternatives[0]["quality"]["gapCells"],
        )
    if result.impossible:
        return reply("infeasible_candidates", reason="no_valid_scope_profile",
                     widgetIds=list(dict.fromkeys(constructor.impossible_ids))[:200])
    return reply("budget_exhausted" if budget.stopped() else "search_exhausted",
                 reason="no_valid_scope_profile", widgetIds=list(dict.fromkeys(constructor.impossible_ids))[:200])
