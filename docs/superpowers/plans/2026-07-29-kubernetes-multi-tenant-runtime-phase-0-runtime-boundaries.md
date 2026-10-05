# Phase 0 Runtime and Domain Boundaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create stable internal runtime and dispatch boundaries around the current local Claude Agent SDK execution without changing any user-visible behavior.

**Architecture:** Keep `TurnService` and the existing local filesystem lifecycle operational, but make execution an explicit `ExecutionDispatcher` port and make Claude SDK the only adapter allowed to import SDK types. Add normalized capabilities, result, cohort, and protocol metadata so later Kubernetes components can depend on project-owned contracts.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, Claude Agent SDK 0.2.128 baseline, MCP Python SDK 1.29.0 baseline, Pydantic Settings, pytest, Ruff/AST inspection.

## Global Constraints

- This phase must not add PostgreSQL-only behavior, Kubernetes clients, S3, Redis, OIDC, or remote Runner processes.
- Preserve the current Session `cwd`, `CLAUDE_CONFIG_DIR`, `claude_session_id`, MCP readiness gate, Skill snapshot, attachments, and personal Auto Memory behavior.
- Preserve all creator-private access checks.
- The only supported dispatcher in this phase is `local_inline`.
- Business and API modules may import project-owned runtime contracts only; only `app/runtime/claude.py` may import `claude_agent_sdk`.
- Existing uncommitted SDK compatibility changes in `app/runtime/claude.py`, `pyproject.toml`, `tests/test_claude_runtime.py`, and `uv.lock` are user work. Commit them first or preserve them explicitly; never overwrite them mechanically.
- The phase Gate is zero visible regressions and a clean compatibility seam, not a remote execution prototype.

---

## File Structure

### New files

- `app/runtime/contracts.py` — project-owned request, event, result, capability, cancellation, and protocol contracts.
- `app/runtime/cohorts.py` — immutable runtime cohort description and capability probe.
- `app/turns/dispatcher.py` — execution-dispatch port and current local implementation.
- `app/bootstrap.py` — service construction separated from ASGI lifecycle.
- `tests/test_runtime_contracts.py` — serialization and compatibility contract tests.
- `tests/test_runtime_import_boundary.py` — AST guard for SDK imports.
- `tests/test_runtime_cohorts.py` — cohort validation and capability tests.
- `tests/test_dispatcher.py` — local dispatcher lifecycle and cancellation tests.
- `tests/test_bootstrap.py` — dependency wiring tests.
- `docs/operations/runtime-v2-verification-ledger.md` — append-only Gate evidence.

### Modified files

- `app/runtime/base.py` — compatibility re-exports for existing imports.
- `app/runtime/claude.py` — implement the project-owned `AgentRuntimePort` and emit normalized result metadata.
- `app/runtime/fake.py` — implement the same port for deterministic tests.
- `app/turns/service.py` — submit execution through `ExecutionDispatcher` rather than constructing private tasks directly.
- `app/api/dependencies.py` — expose the dispatcher and cohort through `AppServices`.
- `app/main.py` — call `build_app_services()` and keep lifecycle-only responsibilities.
- `app/config.py` — add validated local runtime cohort/protocol settings.
- `tests/test_turns.py` — assert dispatch contract and unchanged Turn behavior.
- `tests/test_claude_runtime.py` — assert normalized adapter output while preserving existing SDK tests.
- `tests/test_api.py` — assert runtime capability metadata does not expose secrets.
- `README.md` — document the runtime adapter and supported local mode.

---

### Task 1: Define project-owned runtime contracts

**Files:**
- Create: `app/runtime/contracts.py`
- Modify: `app/runtime/base.py`
- Modify: `app/runtime/fake.py`
- Create: `tests/test_runtime_contracts.py`

**Interfaces:**
- Produces `RuntimeRequest`, `RuntimeEvent`, `RuntimeResult`, `RuntimeCapabilities`, `RuntimeCancelled`, and `AgentRuntimePort`.
- Preserves the field names currently consumed by `ClaudeAgentRuntime` and `TurnService`.
- `RuntimeEvent.payload` stays JSON-compatible; SDK objects cannot cross this boundary.

- [ ] **Step 1: Add failing contract tests**

Create tests that construct the existing request fields, serialize an event, validate capabilities, and type-check a fake async runtime.

```python
def test_runtime_capabilities_are_project_owned_and_serializable() -> None:
    capabilities = RuntimeCapabilities(
        protocol_version="1",
        supports_resume=True,
        supports_interrupt=True,
        supports_auto_memory=True,
        supports_mcp=True,
        supports_skills=True,
    )
    assert capabilities.to_dict()["protocol_version"] == "1"


@pytest.mark.asyncio
async def test_runtime_port_returns_terminal_result(runtime_request) -> None:
    runtime = FakeAgentRuntime(events=[RuntimeEvent(type="assistant", payload={"text": "ok"})])
    events = [event async for event in runtime.run(runtime_request, asyncio.Event())]
    assert events[-1].type == "result"
    assert events[-1].payload["status"] == "completed"
```

- [ ] **Step 2: Run the tests and observe missing contracts**

Run:

```bash
uv run pytest tests/test_runtime_contracts.py -q
```

Expected: FAIL because `app.runtime.contracts` does not exist.

- [ ] **Step 3: Implement immutable internal contracts**

Use frozen dataclasses or Pydantic models with these required properties:

```python
@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    protocol_version: str
    supports_resume: bool
    supports_interrupt: bool
    supports_auto_memory: bool
    supports_mcp: bool
    supports_skills: bool


class AgentRuntimePort(Protocol):
    @property
    def capabilities(self) -> RuntimeCapabilities: ...

    def run(
        self,
        request: RuntimeRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeEvent]: ...
```

Keep `app/runtime/base.py` as a compatibility module that re-exports the new names. Do not maintain two definitions.

- [ ] **Step 4: Update `FakeAgentRuntime` and pass tests**

Run:

```bash
uv run pytest tests/test_runtime_contracts.py tests/test_runtime_events.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/runtime/contracts.py app/runtime/base.py app/runtime/fake.py tests/test_runtime_contracts.py
git commit -m "refactor(runtime): define internal runtime contracts"
```

---

### Task 2: Enforce the Claude SDK adapter boundary

**Files:**
- Modify: `app/runtime/claude.py`
- Create: `tests/test_runtime_import_boundary.py`
- Modify: `tests/test_claude_runtime.py`

**Interfaces:**
- `ClaudeAgentRuntime` implements `AgentRuntimePort`.
- Only `app/runtime/claude.py` may import modules whose root name is `claude_agent_sdk`.
- All SDK messages are normalized to `RuntimeEvent` before leaving the adapter.

- [ ] **Step 1: Add a failing AST import-boundary test**

```python
ALLOWED = {Path("app/runtime/claude.py")}


def test_claude_sdk_imports_are_confined_to_adapter() -> None:
    offenders: list[Path] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".", 1)[0] == "claude_agent_sdk" for name in names):
                if path not in ALLOWED:
                    offenders.append(path)
    assert offenders == []
```

- [ ] **Step 2: Add adapter capability and normalization tests**

Assert that no SDK dataclass instance appears recursively in an emitted `RuntimeEvent.payload`, and that capabilities reflect the currently locked SDK behavior.

- [ ] **Step 3: Run focused tests**

```bash
uv run pytest tests/test_runtime_import_boundary.py tests/test_claude_runtime.py -q
```

Expected: import-boundary test reveals any accidental SDK imports; adapter tests fail until capabilities are exposed.

- [ ] **Step 4: Implement the adapter boundary without changing SDK options**

Add a `capabilities` property and route every SDK message through existing normalization helpers. Preserve the current settings, environment, MCP readiness, Auto Memory, Skill, interrupt, and error mapping code byte-for-byte unless a test requires a project-contract conversion.

- [ ] **Step 5: Re-run and commit**

```bash
uv run pytest tests/test_runtime_import_boundary.py tests/test_claude_runtime.py -q
git add app/runtime/claude.py tests/test_runtime_import_boundary.py tests/test_claude_runtime.py
git commit -m "refactor(runtime): enforce sdk adapter boundary"
```

---

### Task 3: Add runtime cohort and capability metadata

**Files:**
- Create: `app/runtime/cohorts.py`
- Modify: `app/config.py`
- Create: `tests/test_runtime_cohorts.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces `RuntimeCohort(name, image_digest, sdk_version, cli_version, mcp_sdk_version, protocol_version)`.
- Produces `probe_runtime_cohort(runtime, settings) -> RuntimeCohortReport`.
- No credential, model API key, MCP header, local path, or environment dump is returned by the probe.

- [ ] **Step 1: Write validation tests**

Test that blank names, mutable image tags in non-local mode, and protocol mismatches fail. Local development may use `image_digest="local"`. Assert that the probe reads installed Claude Agent SDK and MCP package versions from package metadata, reports MCP SDK major support separately from internal Runner protocol, and does not claim MCP v2 support while Claude Agent SDK metadata requires `mcp<2.0.0`.

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_runtime_cohorts.py -q
```

- [ ] **Step 3: Add settings and cohort types**

Add server-owned settings with safe defaults:

```text
APP_RUNTIME_MODE=local_inline
APP_RUNTIME_COHORT=local
APP_RUNTIME_IMAGE_DIGEST=local
APP_RUNTIME_PROTOCOL_VERSION=1
```

Read installed versions with `importlib.metadata.version("claude-agent-sdk")` and `importlib.metadata.version("mcp")` inside the cohort module, not from SDK private APIs. Read `claude-agent-sdk` requirements to derive the supported MCP package range; do not hardcode a future v2 claim from the currently installed `mcp` version alone.

- [ ] **Step 4: Expose a redacted health capability block**

Extend the existing health response only with:

```json
{
  "runtime": {
    "mode": "local_inline",
    "cohort": "local",
    "protocol_version": "1",
    "capabilities": ["resume", "interrupt", "auto_memory", "mcp", "skills"],
    "dependencies": {
      "claude_agent_sdk": "0.2.128",
      "mcp_python_sdk": "1.29.0",
      "mcp_python_sdk_v2": false
    }
  }
}
```

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_runtime_cohorts.py tests/test_api.py -q
git add app/runtime/cohorts.py app/config.py tests/test_runtime_cohorts.py tests/test_api.py
git commit -m "feat(runtime): report cohort capabilities"
```

---

### Task 4: Introduce an explicit execution dispatcher

**Files:**
- Create: `app/turns/dispatcher.py`
- Modify: `app/turns/service.py`
- Create: `tests/test_dispatcher.py`
- Modify: `tests/test_turns.py`

**Interfaces:**
- `ExecutionDispatcher.submit(turn_id: str) -> None`
- `ExecutionDispatcher.cancel(turn_id: str) -> bool`
- `LocalInlineExecutionDispatcher` owns local asyncio tasks and invokes a supplied Turn executor.
- `TurnService` owns business transitions; the dispatcher owns where execution starts.

- [ ] **Step 1: Add failing tests for idempotent submit and cancel**

Cover duplicate submit, cancellation before the coroutine starts, cancellation while running, task cleanup, and propagation of an unexpected executor exception to the existing failure path.

- [ ] **Step 2: Run focused tests**

```bash
uv run pytest tests/test_dispatcher.py tests/test_turns.py -q
```

- [ ] **Step 3: Move task ownership out of `TurnService`**

Do not move state mutation logic. Extract only `_tasks`, task creation, cancellation signaling, and done-callback cleanup. Inject the dispatcher after creating `TurnService`, using a narrow callback such as `turn_service.execute_turn(turn_id)` to avoid a circular constructor.

- [ ] **Step 4: Verify existing idempotency and memory locking**

```bash
uv run pytest \
  tests/test_dispatcher.py \
  tests/test_turns.py \
  tests/test_memory_locks.py \
  tests/test_sse.py -q
```

Expected: same Session still serializes Turns, same memory scope still serializes SDK work, and event order is unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/turns/dispatcher.py app/turns/service.py tests/test_dispatcher.py tests/test_turns.py
git commit -m "refactor(turns): separate execution dispatch"
```

---

### Task 5: Separate service construction from ASGI lifecycle

**Files:**
- Create: `app/bootstrap.py`
- Modify: `app/main.py`
- Modify: `app/api/dependencies.py`
- Create: `tests/test_bootstrap.py`

**Interfaces:**
- `build_app_services(settings: Settings, runtime: AgentRuntimePort | None = None) -> AppServices`
- `initialize_app_services(services: AppServices) -> AsyncContextManager[None]`
- `create_app()` remains the public ASGI factory.

- [ ] **Step 1: Add failing construction tests**

Verify one construction graph, dependency identity, runtime override for tests, and cleanup when initialization fails halfway.

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/test_bootstrap.py -q
```

- [ ] **Step 3: Extract construction without reordering startup**

Keep the current ordering: data/memory readiness, database migration, Workspace sync, Skill bootstrap, stale Turn interruption, and attachment cleanup. Preserve the application instance lock.

- [ ] **Step 4: Run lifecycle and API regressions**

```bash
uv run pytest tests/test_bootstrap.py tests/test_database.py tests/test_api.py tests/test_sessions.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/bootstrap.py app/main.py app/api/dependencies.py tests/test_bootstrap.py
git commit -m "refactor(app): isolate service bootstrap"
```

---

### Task 6: Record the Phase 0 Gate and update operator documentation

**Files:**
- Create: `docs/operations/runtime-v2-verification-ledger.md`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Document concepts and supported mode**

Document Product Workspace, Session Workdir, Team Knowledge Bundle, `local_inline`, SDK adapter boundary, and the fact that Phase 0 is not production multi-tenancy.

- [ ] **Step 2: Run static and full test suites**

```bash
uv run ruff check app tests
uv run pytest -q
```

Expected: all pre-existing and new tests pass. Record the exact result rather than copying a historical count.

- [ ] **Step 3: Run the current service smoke test**

Start the service using the repository-supported command, then verify:

```bash
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/api/workspaces
```

Create one Session and one text Turn through the browser or API. Verify response streaming, transcript resume, Skills, MCP readiness, and Auto Memory remain functional.

- [ ] **Step 4: Append Gate evidence**

Add the commit, SDK version, protocol version, commands, exact results, and reviewer to `docs/operations/runtime-v2-verification-ledger.md`.

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example docs/operations/runtime-v2-verification-ledger.md
git commit -m "docs(runtime): record phase zero gate"
```

## Phase Gate

- SDK import boundary test passes.
- `TurnService` no longer owns deployment-location decisions.
- Health exposes a redacted runtime protocol/capability report.
- Health distinguishes Claude Agent SDK, MCP Python SDK, internal Runner protocol, and verified MCP feature support; it does not overclaim MCP v2.
- Current creator-private, Skill, MCP, resume, attachment, SSE, and Auto Memory tests pass.
- Current local browser flow behaves identically.
- Verification ledger contains reproducible evidence.

Do not start Phase 1 until all seven conditions are recorded.
