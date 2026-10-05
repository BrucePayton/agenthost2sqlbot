# Dashboard Mathematical Layout Solver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 40-card compact layout return a ranked legal backend solution without losing the incumbent at the compute deadline, while implementing the user's confirmed sizing, reading-order, whitespace, alignment, and fallback rules.

**Architecture:** AgentHost replaces recursive MaxRects with an OR-Tools CP-SAT anytime model and an independently audited complete incumbent. Davinci prepares admitted shapes and DOM evidence, validates the same flexible reading-order relation, persists one selected candidate, and uses the confirmed fallback map when evidence is unavailable.

**Tech Stack:** Python 3.12, OR-Tools CP-SAT, FastAPI, TypeScript, Jest, React dashboard geometry utilities.

---

### Task 1: Reproduce The Backend Exhaustion And Reading-Order Failures

**Files:**
- Modify: `tests/test_dashboard_layout.py`
- Modify: `tests/test_dashboard_layout_candidates.py`

- [ ] **Step 1: Write failing 40-card anytime test**

Add a realistic mixed candidate-domain fixture with metrics, charts, rankings,
tables, and 40 ordered nodes. Assert `status == "feasible"`, 40 audited
placements, `method == "cp_sat_anytime"`, and wall time below the supplied
seven-second budget.

- [ ] **Step 2: Write failing flexible order tests**

Add one plan for small-left-stack plus large-right and one for large-left plus
small-right-stack. Assert both pass `audit`, while a true pairwise reversal
fails with reason `order`.

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
./.venv/bin/pytest tests/test_dashboard_layout.py tests/test_dashboard_layout_candidates.py -q
```

Expected: failures show the current `bounded_maxrects` method and strict
row-major order.

### Task 2: Implement Shared Mathematical Order And Candidate Quality

**Files:**
- Modify: `app/dashboard_layout/solver.py`
- Modify: `tests/test_dashboard_layout_candidates.py`

- [ ] **Step 1: Implement pairwise precedence audit**

For each adjacent requested pair, accept the earlier card when it is entirely
left of or entirely above the later card:

```python
def precedes(first, second):
    return (first["x"] + first["w"] <= second["x"] or
            first["y"] + first["h"] <= second["y"])
```

- [ ] **Step 2: Replace fixed gap constants with measured ratios**

Keep geometry validity independent of quality. Extend quality metadata with
`emptyRatio`, `largestGapRatio`, and `misalignmentRatio`; rank total and largest
gap before alignment, height, and shape cost.

- [ ] **Step 3: Run focused tests and verify GREEN**

Run the candidate tests and confirm both approved stacks pass without making a
reversed pair legal.

### Task 3: Add An Independently Audited Complete Incumbent

**Files:**
- Create: `app/dashboard_layout/incumbent.py`
- Create: `tests/test_dashboard_layout_incumbent.py`

- [ ] **Step 1: Write failing incumbent tests**

Cover 40 mixed roots, fixed columns, parent variants, nested FlatLayout
children, and a single non-metric 24-column shape. Require deterministic,
complete, audited placements.

- [ ] **Step 2: Build bottom-up scope profiles**

Process container depth from deepest to root. Select admitted shapes by parent
variant and place siblings in stable order into complete 24-column shelves,
using a new row when needed. Grow container height only to the audited child
containment minimum.

- [ ] **Step 3: Preserve product sizing rules**

Prefer exact row fill, then lower gap, then shared boundaries, then lower
height/cost. Prefer width 24 for a lone non-metric region when admitted. Never
invent a shape absent from the problem.

- [ ] **Step 4: Run incumbent tests and verify GREEN**

Run:

```bash
./.venv/bin/pytest tests/test_dashboard_layout_incumbent.py -q
```

### Task 4: Replace MaxRects With CP-SAT Anytime Optimization

**Files:**
- Create: `app/dashboard_layout/cp_sat.py`
- Modify: `app/dashboard_layout/solver.py`
- Modify: `tests/test_dashboard_layout.py`
- Modify: `tests/test_dashboard_layout_bounded.py`

- [ ] **Step 1: Write failing deadline-incumbent tests**

Assert a one-millisecond optimization budget still returns an audited
`feasible` incumbent when construction succeeded. Assert cancellation remains
`cancelled` and proven impossible candidate sets remain
`infeasible_candidates`.

- [ ] **Step 2: Build the CP-SAT model**

Create position, end, shape, area, variant, and cost variables; use allowed
assignments for candidate shapes, `NoOverlap2D` per scope, parent-variant and
containment constraints, fixed columns, and left-or-above precedence Boolean
constraints.

- [ ] **Step 3: Add mathematical quality objectives**

Minimize empty area first, maximize repeated internal edges second, then
minimize root height and shape cost. Use bounded objective weights whose maxima
are derived from node count and grid bounds, avoiding the old repeated gap-cut
solve loop.

- [ ] **Step 4: Retain audited callback solutions**

Seed the model with the complete incumbent. Collect distinct improving
solutions, merge the incumbent, audit and rank them, and return up to eight.
When time expires with any incumbent, return `feasible`, `optimal:false`, and
`searchPruned:true`.

- [ ] **Step 5: Route production solve to CP-SAT**

Change `solve` to call the new module. Keep `bounded.py` only as non-production
historical compatibility until all callers and tests are migrated.

- [ ] **Step 6: Run backend tests and verify GREEN**

Run:

```bash
./.venv/bin/pytest tests/test_dashboard_layout.py tests/test_dashboard_layout_candidates.py tests/test_dashboard_layout_bounded.py tests/test_dashboard_layout_routes.py -q
```

### Task 5: Apply The Confirmed Frontend Fallback Sizes

**Files:**
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardFallbackSizes.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardRemotePreparation.test.ts`

- [ ] **Step 1: Write failing exact-map test**

Assert all entries from the user's JSON, including MetricCard `4x4`, Progress
`6x4`, Leaderboard `6x15`, PivotTable `14x10`, Iframe `24x11`, Text `24x5`,
and the message/task types.

- [ ] **Step 2: Replace the map exactly**

Change only confirmed fallback dimensions; retain existing Chinese type labels.

- [ ] **Step 3: Run the test and verify GREEN**

Run the remote-preparation Jest suite.

### Task 6: Make Davinci Match The Mathematical Reading Order

**Files:**
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/utils/widgetPlacement.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardConstraintLayout.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/DashboardLayoutController.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardConstraintLayout.test.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/DashboardRuleController.test.ts`

- [ ] **Step 1: Write failing frontend stack tests**

Assert both approved column-stack arrangements validate, preserve the requested
compact order stamp, and survive the final persistence-order guard. Assert a
pairwise reversal still fails.

- [ ] **Step 2: Add one shared precedence helper**

Export a geometry-only `preservesCompactReadingOrder` helper and use it in
remote candidate validation and the controller final guard.

- [ ] **Step 3: Stamp solver order before persistence**

Use `problem.orders` as the saved compact reading order instead of recomputing
remote candidates by `(y, x)`.

- [ ] **Step 4: Run focused controller tests and verify GREEN**

Run both modified Jest suites in-band.

### Task 7: Implement Fallback, Table, Tab, And 15 Percent Rules

**Files:**
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardConstraintLayout.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardRemotePreparation.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardLinearFallback.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardLayoutAcceptance.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardLayoutQuality.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardLinearFallback.test.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardRemotePreparation.test.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/tabLayoutMeasurement.test.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardConstraintLayout.test.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardLayoutAcceptance.test.ts`
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardLayoutQuality.test.ts`

- [ ] **Step 1: Write failing rule tests**

Cover root and FlatLayout table half-width, half-screen FlatLayout table width
24, common TabLayout sizes with one unavailable child fallback, a full-width
single non-metric fallback row, exact 24-column completed rows, and 15 percent
warning boundaries.

- [ ] **Step 2: Implement scope-local table constraints**

Compute table minimum from the current scope. When the table is the only child
of a narrower FlatLayout, retain only width 24 in that inner scope.

- [ ] **Step 3: Continue TabLayout with fallback evidence**

When a pane cannot be measured, use its type fallback to constrain the tab
frame and append a readable per-card fallback usage rather than throwing for the
whole tab.

- [ ] **Step 4: Improve emergency fallback**

Choose admitted shapes closest to the confirmed map, fill each completed row,
and widen a lone non-metric card to admitted width 24. Keep runtime linear in
node count plus shape count.

- [ ] **Step 5: Replace quality thresholds**

Use `0.15` for external whitespace, connected-hole ratio, and unmatched
cross-row boundary ratio. Keep these as ranking/warning signals only.

- [ ] **Step 6: Run all focused Jest suites and verify GREEN**

Run the fallback, remote preparation, tab measurement, constraint, quality,
acceptance, and controller tests in-band.

### Task 8: End-To-End Verification And Evidence

**Files:**
- Modify: `davinci/docs/dashboard-layout-benchmark/baseline-status.json`
- Create: `davinci/docs/dashboard-layout-benchmark/mathematical-solver-2026-09-14.md`

- [ ] **Step 1: Run backend regression and stress tests**

Run all dashboard-layout Python tests plus cold and three warm 40/80/200-card
runs. Record status, solver time, candidate count, and pruning/optimality.

- [ ] **Step 2: Run frontend layout/controller suites**

Run all affected Jest suites and scoped TypeScript diagnostics.

- [ ] **Step 3: Verify the unchanged public contract boundary**

The private solver response remains backward compatible and the public tool
schema is unchanged. Run the focused Bridge/controller contract tests; do not
regenerate public contracts for private solver metadata.

- [ ] **Step 4: Run mandatory visual acceptance where inputs exist**

Run S01-S07, original dashboard 333, and the complete 30-second browser/save
workflow one cold plus three warm times. If original inputs or live credentials
are absent, record those scenarios as `not_run`; do not substitute synthetic
tests.

- [ ] **Step 5: Check diffs and repository state**

Run `git diff --check` and inspect both repositories for unrelated changes.
