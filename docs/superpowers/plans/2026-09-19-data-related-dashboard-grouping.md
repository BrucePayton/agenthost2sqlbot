# Data-related Dashboard Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace title-whitelist dashboard grouping with one-pass grouping based on card data configuration and card type.

**Architecture:** The frontend projects a compact semantic profile into the existing structure receipt. The Host builds deterministic topic relationships from those profiles and sends only unresolved cards to the existing single structured AI fallback; documentation and contracts describe the same behavior.

**Tech Stack:** TypeScript/Jest, Python/pytest, JSON Schema.

---

### Task 1: Structure Semantic Profile

**Files:**
- Modify: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/dashboardStructureReader.ts`
- Test: `davinci/webapp/share/containers/WorkBenchNew/DashboardV2/agent/dashboardStructureReader.test.ts`

- [ ] Add failing tests proving data cards expose bounded relationship fields while layout-only cards omit them.
- [ ] Run the focused Jest test and confirm the new assertions fail.
- [ ] Project a compact credential-safe profile from native widget configuration into each structure item.
- [ ] Run the focused Jest suite and confirm it passes.

### Task 2: Configuration-based Semantic Grouping

**Files:**
- Modify: `app/runtime/semantic_grouping.py`
- Test: `tests/test_semantic_grouping.py`

- [ ] Add failing tests proving equal titles with unrelated profiles split and different titles with related profiles group.
- [ ] Run the focused pytest cases and confirm failure under title-based classification.
- [ ] Parse profiles, remove business-title whitelists, classify by configuration relationship and card-type compatibility, and retain one AI fallback for unresolved cards.
- [ ] Run the semantic grouping suite and confirm it passes.

### Task 3: Contract And Guidance Consistency

**Files:**
- Modify: `contracts/davinci-agent-v2.json`
- Modify: `app/agui/claude_tools.py`
- Modify: `workspaces/davinci-dashboard/.claude/skills/beautify-dashboard/SKILL.md`
- Modify: `workspaces/davinci-dashboard/.claude/skills/beautify-dashboard/references/beautification.md`
- Test: `tests/test_beautify_new_group_inquiry.py`
- Test: `tests/test_grouped_layout_contract.py`

- [ ] Replace every dimension whitelist and numeric grouping rule with configuration-relevance language.
- [ ] Add contract and documentation assertions for semantic profiles, no whitelist, no card-count limit, and one ambiguity pass.
- [ ] Regenerate derived contracts and run focused contract/documentation tests.

### Task 4: Cross-layer Verification

**Files:**
- Verify all files above.

- [ ] Run the Host semantic, contract, and documentation suites.
- [ ] Run frontend structure, grouping, and background-color suites.
- [ ] Inspect both repository diffs for generated drift, title-based membership rules, numeric caps, and unrelated changes.
