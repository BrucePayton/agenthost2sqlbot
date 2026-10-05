# Dashboard Option 3 Regroup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every 3A/3B/3C execution rebuild existing native flat groups before semantic regrouping while preserving Tab containers and reusing the existing atomic regroup and compact-layout implementation.

**Architecture:** Keep the frontend regroup planner unchanged because it already removes approved containers, promotes their children, creates replacement groups, and invokes the existing compact planner atomically. Change the skill and public tool contract so option 3 supplies every existing native flat container ID in `preset.regroup`, never includes Tab containers, and treats the option-3 choice as authorization without a second exact-proposal confirmation. Regenerate Davinci contract artifacts from the canonical AgentHost contract.

**Tech Stack:** Markdown skill instructions, JSON Schema contracts, Python pytest contract guards, generated TypeScript/JSON contract artifacts.

---

### Task 1: Lock the new option-3 behavior with failing tests

**Files:**
- Modify: `tests/test_beautify_new_group_inquiry.py`
- Modify: `tests/test_grouped_layout_contract.py`

- [ ] Replace preservation assertions with requirements that 3A/3B/3C dismantles every existing native flat group, preserves Tabs, includes released cards in the full semantic pass, and never requests a second regroup confirmation.
- [ ] Run the focused pytest files and verify they fail on the old preservation wording.

### Task 2: Update canonical skill and contract instructions

**Files:**
- Modify: `workspaces/davinci-dashboard/.claude/skills/beautify-dashboard/SKILL.md`
- Modify: `workspaces/davinci-dashboard/.claude/skills/beautify-dashboard/references/beautification.md`
- Modify: `workspaces/davinci-dashboard/.claude/skills/beautify-dashboard/references/davinci-tools.md`
- Modify: `contracts/davinci-agent-v2.json`

- [ ] State that option 3 performs one global semantic pass over current roots plus children released from all native flat groups.
- [ ] Require `preset.regroup.containerWidgetIds` to contain all existing native flat Widget IDs and exclude Tab Widget IDs.
- [ ] Keep explicit `only reorder/no grouping` as the non-destructive escape hatch.
- [ ] Keep one atomic transaction and the existing Function 2 compact planner for each replacement flat group.
- [ ] Run the focused pytest files and verify they pass.

### Task 3: Regenerate and verify contract consumers

**Files:**
- Regenerate: `web/shared/generated/davinci-contracts-v2.js`
- Regenerate: `app/web/static/embed.js`
- Regenerate: `davinci/webapp/share/agent/contract-v2.fixture.json`
- Regenerate: `davinci/webapp/share/agent/generated-v2.ts`

- [ ] Run `npm run generate:contracts` and `npm run build:agui` in AgentHost.
- [ ] Run the Davinci contract sync command and its check mode.
- [ ] Run focused Python contract tests and Davinci sync tests.
- [ ] Run `git diff --check` in both repositories and inspect the final diff for stale preservation wording.
