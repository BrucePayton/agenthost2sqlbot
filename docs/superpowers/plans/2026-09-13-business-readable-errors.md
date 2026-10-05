# Business-Readable Errors Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Explain assistant failures in business language with a verified reason, affected card when known, and a safe next action.

**Architecture:** Add a shared runtime instruction for model-authored failure explanations and one deterministic formatter for errors rendered directly by the embedded chat. Preserve protocol receipts, error codes, retry restrictions and persistence evidence; do not rewrite arbitrary assistant prose or change layout behavior.

**Tech Stack:** Python runtime prompts, browser JavaScript, pytest, Node test runner, Playwright.

---

### Task 1: Runtime Failure Explanations

Files: `app/runtime/error_communication.py`, `app/runtime/claude.py`, `tests/test_error_communication.py`.

- [x] Add a parametrized test for default, native and legacy runtime paths: `assert BUSINESS_ERROR_GUIDANCE in options.system_prompt['append']`.
- [x] Run `pytest tests/test_error_communication.py -q`; confirm missing policy fails.
- [x] Append `BUSINESS_ERROR_GUIDANCE` once, globally, after building the memory prompt. Cover card-title resolution, empty data versus loading versus unknown failure, permission recovery, unsaved edits, unavailable functionality, and unknown write outcomes. Never treat a measurement timeout as proof of empty data.
- [x] Run the new tests and `tests/test_claude_runtime.py`; raw tool receipts remain unchanged. Result: 137 passed, one existing catalog-count failure (see verification notes).

### Task 2: Direct Chat Errors

Files: `web/embed/user-facing-error.js`, `web/embed/main.js`, `tests/js/test_user_facing_error.cjs`.

- [x] Add table-driven tests for authentication, permission, service credentials, throttling, invalid requests, missing resources, conflicts, layout measurement, connection failure and unknown errors. Assert messages include a next action and never echo arbitrary backend messages.
- [x] Run `node --test tests/js/test_user_facing_error.cjs`; confirm missing formatter fails.
- [x] Implement `formatUserFacingError(error, {context, outcomeUnknown})`. Use exact codes/statuses and known local messages, not guesses from arbitrary backend prose. Unknown errors must state uncertainty and offer support escalation. An unresolved operation must warn against repeated submission.
- [x] Route error articles, timeline failures, authentication failures, stop failures and skill-management notices through the formatter. Pass error objects, not only `.message`, so code/status survive until presentation. Keep existing tool IO details and recovery behavior.
- [x] Run `npm run test:js`: 168 passed after review fixes.

### Task 3: Shipped UI Verification

Files: `tests/browser/test_embed_reliability.py`, generated embed bundle.

- [x] Add real-iframe tests injecting raw HTTP/runtime errors. Check the visible message, actionable instructions, no leaked technical text and no repeated business writes. Check mobile/desktop text containment and save a screenshot to a temporary test artifact. The old bundle failed all three new browser assertions by displaying raw errors; the rebuilt bundle passed.
- [x] Run `npm run build:agui`, the full browser reliability suite (33 passed after review fixes), and `git diff --check`.
- [x] Run paired Davinci startup validation with `DAVINCI_HOST_ROOT` pointing to this checkout and `DAVINCI_HOST_PYTHON` pointing to this checkout's `.venv/bin/python`. The temporary runtime-only environment has no pytest. Browser tests use `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/davinci-playwright`.
- [x] Record tested scope and remaining live-model uncertainty below. No commit, push, deploy or business-dashboard write performed.

## Verification Notes

- Runtime: new policy suite 4 passed; combined runtime suite 137 passed, 1 failed. `test_read_only_frontend_tool_catalog_unions_v1_and_v2_contracts` still expects 30 tools, while the unchanged registry contains 31. `git diff --exit-code HEAD -- app/agui/claude_tools.py contracts tests/test_claude_runtime.py` is clean.
- Frontend: 168 JavaScript tests passed; production bundle rebuilt and `check:agui-build` passed; 33 real-iframe browser tests passed, including authentication recovery, interruption/reconciliation and four new error-presentation cases. Screenshots at 390px and 1000px were inspected: error text fits without overlap.
- Paired startup command: **failed**, not a full pass. Contract checks and bundle verification passed; Jest reported 219 passed and 5 failures in the unchanged `DashboardLayoutController.test.ts`: captured 2642 geometry gap, synthetic 40-widget/6-rank alignment, hidden tabs with stack sizes 1 and 2, and unchanged compact persistence. The Davinci checkout is clean; no layout code was edited this turn.
- Remaining Host bootstrap/contracts/models/tool-plan gate was run separately: 90 passed. The real dual-origin parent/iframe test also passed as part of the final 33-case browser suite; these successes do not erase the aggregate gate failure.
- Lint on the new Python files and `git diff --check` passed.
- Model-authored business explanations are governed by the new shared instruction. Live model wording and online deployment have not been verified; existing assistant prose in conversation history is not rewritten. Unknown errors intentionally use an honest support-escalation message instead of exposing arbitrary backend text or inventing a cause.
- Independent review found three presentation issues and confirmed their fixes: activity entries now share unresolved-result protection; server-side skill storage errors no longer blame uploaded files; ambiguous instruction-save failures preserve unknown persistence. Added failing regression cases before each fix and reran JS/browser tests afterwards.
