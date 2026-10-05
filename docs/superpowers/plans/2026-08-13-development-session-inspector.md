# Development Session Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a replayable Session timeline, persisted context inspector, copyable Session ID, and development-only OBID/Session locator.

**Architecture:** Keep the existing owner-scoped APIs authoritative. Add one normal owner-only context endpoint and a conditionally registered read-only debug router; isolate browser policies in small UMD modules so empty continuation suppression, tool correlation, identity headers and Session lookup are directly testable.

**Tech Stack:** FastAPI, SQLAlchemy async, Jinja2, vanilla JavaScript, pytest, Node test runner.

## Global Constraints

- No new dependency.
- Preserve protocol continuation events in storage.
- Do not expose API keys, token values, environment values or runtime-only system prompts.
- Cross-user routes and controls must not exist outside `development + obid`.
- Do not create users or grant an administrator role.
- Store selected debug identity only in session storage.

---

### Task 1: Session replay policy and owner context endpoint

**Files:**
- Create: `app/web/static/session-inspector.js`
- Modify: `app/api/schemas.py`
- Modify: `app/api/routes.py`
- Test: `tests/js/test_session_inspector.cjs`
- Test: `tests/test_api.py`

- [x] Write failing tests for continuation suppression, tool correlation, safe persisted context, owner access and Session context output.
- [x] Run the targeted Node and Python tests and verify expected failures.
- [x] Implement the minimal policy module and owner-authorized endpoint.
- [x] Re-run targeted tests and verify they pass.

### Task 2: Development debug catalog and Session locator

**Files:**
- Create: `app/debug/__init__.py`
- Create: `app/debug/routes.py`
- Modify: `app/main.py`
- Test: `tests/test_debug_sessions.py`

- [x] Write failing tests for exact-profile registration, existing OBID counts, Session lookup and no user creation.
- [x] Run `uv run pytest tests/test_debug_sessions.py -q` and verify expected failures.
- [x] Implement conditional router registration and read-only SQLAlchemy queries.
- [x] Re-run the test and verify it passes.

### Task 3: Workbench Session inspector UI

**Files:**
- Modify: `app/web/templates/index.html`
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/app.css`
- Modify: `tests/test_web_page.py`

- [x] Write failing page/static assertions for Session ID, copy/context controls, script order and tool/context rendering hooks.
- [x] Run the targeted tests and verify expected failures.
- [x] Add copyable ID, context dialog, ordered inline tool cards, persisted context details, and hide protocol-only user bubbles.
- [x] Re-run targeted tests and verify they pass.

### Task 4: Development OBID selector and locator UI

**Files:**
- Create: `app/web/static/debug-identity.js`
- Modify: `app/web/templates/index.html`
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/app.css`
- Test: `tests/js/test_debug_identity.cjs`
- Test: `tests/test_web_page.py`

- [x] Write failing tests for conditional markup, session-storage selection, header merge, active-Turn refusal and Session lookup.
- [x] Run targeted tests and verify expected failures.
- [x] Implement the selector, locator and clean identity/Workspace/Session reload.
- [x] Re-run targeted tests and verify they pass.

### Task 5: Regression and local smoke

- [x] Run targeted Python, JavaScript and static checks.
- [x] Start the real `development + obid + local_inline` service and verify the
      page discovers an existing OBID without creating one from the debug API.
- [ ] With a populated local copy of UAT data, locate a known Session ID and
      visually verify copy/context controls, tool ordering and continuation
      suppression. The deterministic policy and route behavior are already
      covered by automated tests; this data-dependent check remains optional.
