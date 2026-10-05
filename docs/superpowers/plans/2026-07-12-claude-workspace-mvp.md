# Claude Workspace Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete single-user FastAPI workspace chat service defined in `docs/superpowers/specs/2026-07-12-claude-workspace-mvp-design.md`, including persistent sessions, attachments, Claude Agent SDK resume, Skills/MCP configuration, SSE streaming, browser UI, and verification.

**Architecture:** A single FastAPI process serves HTML, REST, and SSE. SQLite stores platform sessions, turns, events, and attachment metadata; each platform session owns a stable filesystem workspace and Claude config directory. A runtime protocol separates orchestration from the real Claude Agent SDK so integration and browser tests can use a deterministic fake.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, vanilla JavaScript/CSS, SQLAlchemy async, SQLite, Pydantic Settings, Claude Agent SDK Python, pytest, Playwright, uv.

## Global Constraints

- Required environment variables are `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, and `WORKSPACES_ROOT`.
- Secrets never enter browser responses, SQLite, prompts, command-line arguments, or normal logs.
- The default bind address is `127.0.0.1`; the MVP has no authentication and is not public-facing.
- A platform session uses an exact Claude `session_id` for resume and a stable absolute `cwd` plus `CLAUDE_CONFIG_DIR`.
- Each session has one active turn at most; different sessions may run concurrently.
- Only selected Skills are copied into a session workspace, and MCP/tool access comes from the immutable workspace snapshot.
- Tests use a fake runtime by default; live Claude tests require `RUN_LIVE_CLAUDE_TESTS=1`.
- Routes do not call SQLAlchemy or the Claude SDK directly; services own persistence and runtime behavior.

---

### Task 1: Project foundation, settings, and database

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/errors.py`
- Create: `app/db/base.py`
- Create: `app/db/models.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`
- Create: `tests/test_database.py`

**Interfaces:**
- Produces: `Settings`, `create_database(settings)`, `Database.session()`, ORM models `SessionRecord`, `TurnRecord`, `MessageRecord`, `AttachmentRecord`, and `AppError`.

- [ ] Write tests proving required variables fail closed, secret representations are redacted, relative paths resolve deterministically, SQLite foreign keys are enabled, and cascading Session deletion removes child rows.
- [ ] Run `uv run pytest tests/test_config.py tests/test_database.py -q` and confirm failures are caused by missing implementation.
- [ ] Add Python metadata and dependencies, implement Pydantic Settings with `SecretStr`, implement stable error envelopes, and create async SQLAlchemy engine/session initialization.
- [ ] Define all tables, enums, indexes, uniqueness constraints, UTC timestamps, and startup conversion of stale active turns to `interrupted`.
- [ ] Run the focused tests and commit with `feat: establish application foundation`.

### Task 2: Workspace registry and session materialization

**Files:**
- Create: `app/workspaces/__init__.py`
- Create: `app/workspaces/models.py`
- Create: `app/workspaces/registry.py`
- Create: `app/workspaces/materializer.py`
- Create: `tests/test_workspaces.py`
- Create: `workspaces/example/workspace.yaml`
- Create: `workspaces/example/CLAUDE.md`
- Create: `workspaces/example/.claude/skills/workspace-summary/SKILL.md`
- Create: `workspaces/example/seed/welcome.md`

**Interfaces:**
- Consumes: `Settings.workspaces_root`, `Settings.data_dir`.
- Produces: `WorkspaceManifest`, `WorkspaceEntry`, `WorkspaceRegistry.scan()`, `WorkspaceRegistry.get()`, `materialize_session_workspace(entry, session_id, data_dir)`.

- [ ] Write tests for valid manifests, invalid YAML, duplicate IDs, missing Skills, symlinks, missing MCP environment references, immutable snapshots, and selected-Skill-only copying.
- [ ] Run `uv run pytest tests/test_workspaces.py -q` and verify failure.
- [ ] Implement strict Pydantic manifest parsing, safe one-level scanning, validation isolation, canonical JSON snapshot hashing, and deterministic session directory creation.
- [ ] Add the sample Workspace with no external MCP dependency so a fresh checkout always has one valid selection.
- [ ] Run focused tests and commit with `feat: add workspace registry`.

### Task 3: Session and attachment services

**Files:**
- Create: `app/sessions/__init__.py`
- Create: `app/sessions/service.py`
- Create: `app/sessions/locks.py`
- Create: `app/attachments/__init__.py`
- Create: `app/attachments/service.py`
- Create: `tests/test_sessions.py`
- Create: `tests/test_attachments.py`

**Interfaces:**
- Consumes: `Database`, `WorkspaceRegistry`, `materialize_session_workspace`.
- Produces: `SessionService.create/list/get/rename/delete`, `SessionLockRegistry`, `AttachmentService.upload/delete/get/cleanup_pending`, and `StoredAttachment`.

- [ ] Write Session tests for creation, auto/user titles, workspace snapshot persistence, list ordering, running-delete conflict, and filesystem/database cascade deletion.
- [ ] Write attachment tests for PNG/PDF/UTF-8 detection, binary/executable rejection, size/count limits, safe generated filenames, atomic rollback, pending deletion, and path containment.
- [ ] Run focused tests and verify failure.
- [ ] Implement services with transaction boundaries, UUID filenames, streaming SHA-256 calculation, temporary files plus atomic move, and 24-hour pending cleanup.
- [ ] Run focused tests and commit with `feat: persist sessions and attachments`.

### Task 4: Runtime protocol, fake runtime, and Claude Agent SDK adapter

**Files:**
- Create: `app/runtime/__init__.py`
- Create: `app/runtime/base.py`
- Create: `app/runtime/events.py`
- Create: `app/runtime/fake.py`
- Create: `app/runtime/claude.py`
- Create: `tests/test_runtime_events.py`
- Create: `tests/test_claude_runtime.py`

**Interfaces:**
- Produces: `RuntimeRequest`, `RuntimeAttachment`, `RuntimeEvent`, `AgentRuntime.run(request, cancel_event)`, `FakeAgentRuntime`, `ClaudeAgentRuntime`, `tool_is_allowed()`.

- [ ] Write tests for image content blocks, ordinary-file path text, sanitized child environment, MCP environment resolution, exact/wildcard permission matching, SDK text/tool/result/compaction normalization, cancellation, and error redaction.
- [ ] Run focused tests and verify failure.
- [ ] Implement a deterministic fake yielding text, tool, usage, completion, cancellation, and selected failure scenarios.
- [ ] Implement Claude options with stable `cwd`, `resume`, model, project setting source, selected Skills, strict MCP, allowlist callback, Claude Code preset, partial streaming, and per-session config directory.
- [ ] Implement streaming-input image blocks and file-path annotations, map SDK messages without exposing thinking, and interrupt on cancellation.
- [ ] Run focused tests and commit with `feat: integrate Claude agent runtime`.

### Task 5: Turn orchestration, event persistence, and SSE

**Files:**
- Create: `app/turns/__init__.py`
- Create: `app/turns/broker.py`
- Create: `app/turns/service.py`
- Create: `tests/test_turns.py`
- Create: `tests/test_sse.py`

**Interfaces:**
- Consumes: `SessionService`, `AttachmentService`, `SessionLockRegistry`, `AgentRuntime`, ORM records.
- Produces: `TurnService.start/cancel/events/shutdown`, `EventBroker.subscribe/publish`, persisted monotonic event sequences.

- [ ] Write tests for atomic Turn creation and attachment binding, idempotent `client_request_id`, one active Turn per Session, successful event ordering, title generation, usage/session ID persistence, runtime failure, cancellation, timeout, and stale-Turn interruption.
- [ ] Write SSE tests proving replay after `Last-Event-ID`, live delivery, duplicate suppression, heartbeat, and terminal connection close.
- [ ] Run focused tests and verify failure.
- [ ] Implement background task ownership, per-session locks, cancellation events, durable event-before-publish semantics, and state transitions in one service.
- [ ] Run focused tests and commit with `feat: orchestrate streaming turns`.

### Task 6: FastAPI application and REST contracts

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/dependencies.py`
- Create: `app/api/schemas.py`
- Create: `app/api/routes.py`
- Create: `app/main.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: all services and runtime factory.
- Produces: `create_app(settings=None, runtime=None)` and module-level `app` for `uvicorn app.main:app`.

- [ ] Write API tests for health, Workspace availability, Session CRUD, upload rollback, Turn 202/409/idempotency, cancel, attachment content headers, stable error envelopes, and absence of secret values.
- [ ] Run `uv run pytest tests/test_api.py -q` and verify failure.
- [ ] Implement lifespan initialization/shutdown, dependency wiring, REST routes, SSE responses, request IDs, static/template mounting, and exception handlers.
- [ ] Run API tests and commit with `feat: expose workspace agent API`.

### Task 7: Browser workbench

**Files:**
- Create: `app/web/templates/index.html`
- Create: `app/web/static/app.css`
- Create: `app/web/static/app.js`
- Create: `tests/test_web_page.py`
- Create: `tests/browser/test_workbench.py`

**Interfaces:**
- Consumes: REST and SSE contracts from Task 6.
- Produces: complete no-build browser interface.

- [ ] Write page structure tests for required controls, accessible labels, no secret rendering, and static asset availability.
- [ ] Implement the stable desktop/mobile shell, Workspace selector, Session list, capability summary, message timeline, collapsible tool events, attachment tray, composer, rename/delete dialogs, loading/empty/error states, and responsive constraints.
- [ ] Implement REST state loading, optimistic controls, upload/remove, Turn start/cancel, EventSource streaming/reconnect, safe `textContent` rendering, history projection, title refresh, and periodic Session status refresh.
- [ ] Run page tests, then run Playwright against a fake-runtime test server at desktop and mobile viewports; verify no overlap and all primary workflows.
- [ ] Commit with `feat: add browser agent workbench`.

### Task 8: Documentation, live smoke, and completion audit

**Files:**
- Create: `README.md`
- Create: `scripts/run-dev.sh`
- Create: `tests/live/test_claude_smoke.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: reproducible setup, startup, Workspace authoring, test, and live verification instructions.

- [ ] Add an opt-in live test that creates a Session, captures a Claude Session ID, resumes it, and sends an image through streaming input.
- [ ] Write exact `uv sync`, environment, `uvicorn`, test, and troubleshooting commands; explain the no-auth deployment boundary and Anthropic-compatible proxy requirement.
- [ ] Run `uv run pytest -q`, run Playwright, run static compile/import checks, and inspect `git diff --check`.
- [ ] Start the real FastAPI service on an available localhost port, verify health and render the page with Playwright screenshots at desktop and mobile sizes.
- [ ] Compare every numbered acceptance criterion in the approved spec against files, tests, and runtime evidence; fix all gaps before completion.
- [ ] Commit with `docs: document and verify MVP`.

