# Dashboard Grouping Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all seven reported 3B/3C grouping failures while adding grounded AI-generated group summaries, adaptive group colors, and bounded execution.

**Architecture:** Host semantic planning owns visible-card extraction, one batch title-generation call, and canonical planner-to-write arguments. Davinci owns native flat-layout geometry, transition validation, batch color assignment, and persistence. Each boundary gets regression coverage before production changes.

**Tech Stack:** Python 3, pytest, TypeScript, React/Davinci dashboard layout code, Jest, Claude Agent SDK, deferred AG-UI frontend tools.

---

### Task 1: Existing-group semantic extraction and title generation

**Files:**
- Modify: `app/runtime/semantic_grouping.py`
- Modify: `app/runtime/claude.py`
- Test: `tests/test_semantic_grouping.py`
- Test: `tests/test_claude_runtime.py`

- [ ] Add a failing receipt fixture with six flat containers and eighteen non-editable visible children; assert all eighteen are extracted while hidden/orphan implementation nodes and Tab children are ignored.
- [ ] Add failing tests for one batch title request, schema validation, concise grounded unique titles, timeout/error fallback, and zero retries.
- [ ] Run the focused pytest tests and confirm the new assertions fail for the expected filtering and generic-title behavior.
- [ ] Separate planning visibility from `layoutEditable`, compile provisional groups, invoke one batch title decider, validate output, and apply deterministic fallback.
- [ ] Run focused tests and the complete semantic/runtime test modules.

### Task 2: Canonical handoff, planner gate, and bounded deferred execution

**Files:**
- Modify: `app/runtime/claude.py`
- Modify: `app/agui/bridge.py`
- Modify: `app/agui/claude_tools.py`
- Test: `tests/test_claude_runtime.py`
- Test: `tests/test_frontend_tool_bridge.py`
- Test: `tests/test_claude_tools_plan.py`

- [ ] Add failing tests proving a successful planner result is the canonical next layout input, failed planning does not consume the one-call gate, malformed/missing arguments stop immediately, and non-retryable/deferred failures cannot loop.
- [ ] Run focused tests and verify each fails at the current reconstruction, ledger, or timeout boundary.
- [ ] Store successful canonical planner arguments in per-operation state, inject/validate them for the layout write, move planner-call consumption to success, and bound deferred completion with truthful errors.
- [ ] Run focused tests and all Host runtime/bridge suites.

### Task 3: Flat-layout transition and header-aware geometry

**Files:**
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardFlatGrouping.ts`
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardFlatGrouping.test.ts`
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/DashboardConstraintController.test.ts`

- [ ] Add a failing test for an unchanged hidden orphan node and a companion rejection test for a newly changed invalid parent.
- [ ] Add a failing five-metric fixture where child bottom is nine and assert the generated container height satisfies the solver-required header-aware height of ten.
- [ ] Run the focused Jest tests and verify the failures reproduce sessions 10ddc922 and a3eb2203.
- [ ] Narrow invalid-parent validation to changed/user-visible ownership and unify container height derivation with solver header accounting while preserving 12/24 widths.
- [ ] Run focused grouping and constraint tests.

### Task 4: Batch adaptive group backgrounds

**Files:**
- Create: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/flatGroupBackground.ts`
- Create: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/flatGroupBackground.test.ts`
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardFlatGrouping.ts`
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardFlatGrouping.test.ts`

- [ ] Add failing tests for all-colored, all-white, and mixed ordered group batches, including adjacent-color avoidance and exclusion of `#EDF7ED`.
- [ ] Run the new focused test and confirm current per-group scoring cannot satisfy the batch expectations.
- [ ] Implement a pure batch classifier/allocator using actual chart types and effective backgrounds, then integrate it before group containers are created.
- [ ] Run color and grouping suites and confirm no geometry changes.

### Task 5: Integrated verification and delivery

**Files:**
- Modify only files required by failures discovered during verification.

- [ ] Run Host focused tests, then all Host Python and JavaScript tests used by the AG-UI runtime.
- [ ] Run Davinci focused grouping/constraint/persistence tests, then the complete affected frontend test command.
- [ ] Inspect both git diffs for unrelated changes and verify both worktrees were clean before this implementation.
- [ ] Commit each repository with scoped messages and push its existing branch to the configured remote.
- [ ] Report commit hashes, test counts, and any verification that could not be run.
