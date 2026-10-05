# Davinci Mock AG-UI Iframe MVP Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-origin local MVP in which an embedded Workspace Agent reads the current Mock Davinci dashboard through a frontend-defined tool, explains its live Store data, and navigates the parent application without losing the Session.

**Architecture:** Keep the existing Workspace Agent Session, Turn, persistence, Claude Agent SDK, and root workbench intact. Add an AG-UI HTTP/SSE adapter at the iframe-to-Agent-Host boundary, a process-local Pending Bridge that lets an in-process Claude SDK MCP tool wait for a browser ToolMessage, and a strict custom `postMessage` protocol between the iframe and Mock Davinci parent.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, Claude Agent SDK 0.2.128, `ag-ui-protocol==0.1.19`, `@ag-ui/client==0.0.57`, esbuild 0.28.1, vanilla JavaScript, Node test runner, pytest, Playwright, Qwen `qwen3.8-max` through the configured Anthropic-compatible endpoint.

## Global Constraints

- The validation runtime is exactly `local_inline`; `execution_disabled` and `opensandbox_docker` must reject new AG-UI runs with `CAPABILITY_UNAVAILABLE`.
- Mock Davinci binds to `http://127.0.0.1:4173`; Agent Host and iframe bind to `http://127.0.0.1:8000`.
- AG-UI `threadId` equals the existing Session ID and `runId` equals the existing Turn ID.
- A Session has at most one active Run, preserving the current Turn serialization contract.
- The first Run uses official `HttpAgent`; Tool Result continuation uses a separate `ToolResultSubmitter` and must not close the first Run's SSE response.
- Parent communication uses exact `targetOrigin` and validates `origin`, `source`, nonce, protocol version, expiry, `contextVersion`, and message Schema; never use `"*"` as a target.
- Frontend tools are exactly `dashboard.capture_current_view` and `navigateTo`; only `navigateTo` is advertised on the Dataset page.
- A frontend Tool Call waits at most 15 seconds; Snapshot JSON is at most 64 KiB.
- Mock dashboard facts come from the parent Store and remain exactly `itemCount=4734`, `weeklyChangePct=-10.88`, and `bidAmount=40388380` for the default South-region seven-day view.
- Model Base URL, API key, resolved MCP credentials, and process environment secrets never enter HTML, JavaScript, AG-UI payloads, SSE, `postMessage`, database events, or ordinary logs.
- Existing `/`, Session/Turn APIs, per-Turn SSE, Docker Web default startup, Skills, MCP, attachments, and browser flows remain compatible.
- No Artifact Store, real Davinci backend, persistent dashboard mutations, full AG-UI conformance claim, Docker/OpenSandbox Bridge, or Kubernetes work is included.
- Generated browser bundles are committed because the Python and Docker startup paths do not run Node; `npm run build:agui` must reproduce them without a diff.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml`, `uv.lock` | Pin the official Python AG-UI protocol package. |
| `package.json`, `package-lock.json` | Pin the official JavaScript AG-UI client and deterministic browser bundler. |
| `app/agui/models.py` | Validate HostContext, exact frontend tool catalog, Snapshot, UI ACK, and Tool error payloads. |
| `app/agui/bridge.py` | Own per-Run Tool Call records, Pending Futures, expiry, replay, conflict, cancellation, and registry lookup. |
| `app/agui/claude_tools.py` | Map public frontend tool names to safe SDK MCP tool names and build the in-process `davinci_ui` server. |
| `app/agui/adapter.py` | Convert persisted Runtime events into official AG-UI `BaseEvent` objects without duplicate text or terminal events. |
| `app/agui/routes.py` | Accept initial `RunAgentInput` and ToolMessage continuation requests and stream encoded SSE. |
| `app/api/dependencies.py` | Expose the singleton frontend Tool Bridge registry through `AppServices`. |
| `app/bootstrap.py`, `app/main.py` | Construct and shut down the registry, inject it into Claude Runtime, and mount the AG-UI router. |
| `app/turns/service.py` | Accept a validated caller-supplied Turn ID so `runId == Turn ID`. |
| `app/runtime/claude.py` | Add Run-scoped frontend MCP tools, allowlist entries, Hook correlation, and the frontend-tool system instruction. |
| `app/web/routes.py`, `app/web/templates/embed.html`, `app/web/static/embed.css` | Serve the standalone iframe shell without changing the root workbench. |
| `web/shared/davinci-protocol.js` | Shared protocol constants, envelope creation, expiry/origin/source checks, and tool definitions. |
| `web/embed/main.js`, `web/embed/tool-result-submitter.js` | Create/resume Sessions, run `HttpAgent`, render events, call the parent, and submit ToolMessage results. |
| `app/web/static/embed.js` | Reproducible generated iframe bundle. |
| `demo/davinci_mock/app.py` | Serve both Mock routes and static assets on the second Origin. |
| `demo/davinci_mock/templates/index.html`, `demo/davinci_mock/static/styles.css` | Mock dashboard/dataset shell and visual layout. |
| `web/davinci-mock/main.js` | Parent Store, route rendering, constant iframe lifecycle, Capability execution, and UI Command ACK. |
| `demo/davinci_mock/static/app.js` | Reproducible generated parent bundle. |
| `scripts/run-davinci-agui-mvp.sh` | Start the Agent Host and Mock Davinci processes together and cleanly stop both. |
| `tests/test_agui_models.py` | Exact schema, catalog, size, and context validation. |
| `tests/agui_helpers.py` | Shared constructors for HostContext, tool catalogs, ToolMessages, and RuntimeRequest fixtures. |
| `tests/test_frontend_tool_bridge.py` | Pending, timeout, cancellation, early result, replay, and conflict behavior. |
| `tests/test_agui_adapter.py` | Runtime-to-AG-UI event ordering and single terminal semantics. |
| `tests/test_agui_api.py` | Identity, Session/Run mapping, continuation, runtime-mode, and SSE API behavior. |
| `tests/test_claude_runtime.py` | Dynamic MCP, Hook correlation, allowlist, system prompt, and handler-result behavior. |
| `tests/test_davinci_mock.py` | Mock route and second-Origin asset contract. |
| `tests/js/test_davinci_protocol.cjs` | Browser-independent protocol, tool catalog, and continuation request tests. |
| `tests/browser/test_davinci_agui_mvp.py` | Deterministic two-Origin vertical-slice browser Gate with a Bridge-aware fake Runtime. |
| `tests/live/test_davinci_agui_qwen.py` | Opt-in real `qwen3.8-max` tool-use, dashboard interpretation, navigation, and Session continuity Gate. |
| `README.md` | Manual startup, automated checks, real-model cost warning, and validation boundary. |

---

### Task 1: Pin AG-UI Dependencies and Define Exact Payload Models

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `package.json`
- Create: `package-lock.json`
- Create: `app/agui/__init__.py`
- Create: `app/agui/models.py`
- Create: `tests/agui_helpers.py`
- Create: `tests/test_agui_models.py`

**Interfaces:**
- Produces: `HostContext`, `DashboardSnapshot`, `UiAck`, and `FrontendToolErrorPayload` Pydantic models.
- Produces: `validate_frontend_tools(host_context, tools) -> tuple[Tool, ...]` with exact catalog matching.
- Produces: `validate_tool_message_content(tool_name, content, error) -> str`, returning canonical JSON bounded to 64 KiB.
- Consumes later: official `RunAgentInput`, `Tool`, `ToolMessage`, and `EventEncoder` from `ag_ui`.

- [ ] **Step 1: Add failing payload and tool-catalog tests**

Create `tests/test_agui_models.py` with focused tests using the real `ag_ui.core.Tool` type:

```python
import json

import pytest
from ag_ui.core import Tool
from pydantic import ValidationError

from app.agui.models import (
    CAPTURE_TOOL,
    NAVIGATE_TOOL,
    DashboardSnapshot,
    HostContext,
    validate_frontend_tools,
    validate_tool_message_content,
)
from tests.agui_helpers import dashboard_context


def test_dashboard_accepts_exact_catalog_and_dataset_rejects_capture() -> None:
    assert validate_frontend_tools(
        dashboard_context(), [CAPTURE_TOOL, NAVIGATE_TOOL]
    ) == (CAPTURE_TOOL, NAVIGATE_TOOL)
    dataset = HostContext(
        pageType="dataset",
        resourceId=None,
        contextVersion=4,
        supportedCapabilities=[],
        supportedCommands=["navigateTo"],
    )
    with pytest.raises(ValueError, match="CAPABILITY_UNAVAILABLE"):
        validate_frontend_tools(dataset, [CAPTURE_TOOL, NAVIGATE_TOOL])


def test_snapshot_is_exact_and_bounded() -> None:
    content = json.dumps(
        {
            "schemaVersion": "mock-dashboard-snapshot-v1",
            "page": {
                "pageType": "dashboard",
                "dashboardId": "1024",
                "title": "南区经营仪表盘",
                "contextVersion": 3,
                "capturedAt": "2026-08-06T16:00:00+08:00",
            },
            "filters": [
                {"field": "区域", "operator": "eq", "value": "南区"},
                {"field": "时间", "operator": "relative", "value": "最近7天"},
            ],
            "metrics": {
                "itemCount": 4734,
                "weeklyChangePct": -10.88,
                "bidAmount": 40388380,
            },
            "widgets": [],
        },
        ensure_ascii=False,
    )
    canonical = validate_tool_message_content(
        "dashboard.capture_current_view", content, None
    )
    assert DashboardSnapshot.model_validate_json(canonical).metrics.item_count == 4734
    with pytest.raises(ValueError, match="64 KiB"):
        validate_tool_message_content(
            "dashboard.capture_current_view", "x" * 65_537, None
        )


def test_host_context_rejects_wrong_dashboard_id() -> None:
    with pytest.raises(ValidationError):
        HostContext(
            pageType="dashboard",
            resourceId="9999",
            contextVersion=1,
            supportedCapabilities=["dashboard.capture_current_view"],
            supportedCommands=["navigateTo"],
        )
```

- [ ] **Step 2: Run the model tests and verify red**

Run:

```bash
uv run pytest tests/test_agui_models.py -q
```

Expected: collection fails because `app.agui.models` does not exist.

- [ ] **Step 3: Pin Python and JavaScript dependencies**

Run:

```bash
uv add "ag-ui-protocol==0.1.19"
npm install --save-exact @ag-ui/client@0.0.57
npm install --save-dev --save-exact esbuild@0.28.1
```

Set `package.json` scripts to these exact commands:

```json
{
  "private": true,
  "type": "module",
  "scripts": {
    "build:agui": "esbuild web/embed/main.js --bundle --format=esm --platform=browser --outfile=app/web/static/embed.js && esbuild web/davinci-mock/main.js --bundle --format=esm --platform=browser --outfile=demo/davinci_mock/static/app.js",
    "test:js": "node --test tests/js/*.cjs"
  },
  "dependencies": {
    "@ag-ui/client": "0.0.57"
  },
  "devDependencies": {
    "esbuild": "0.28.1"
  }
}
```

- [ ] **Step 4: Implement strict models and immutable tool constants**

In `app/agui/models.py`, use aliased fields matching browser JSON and forbid extras:

```python
MAX_TOOL_RESULT_BYTES = 64 * 1024

CAPTURE_TOOL = Tool(
    name="dashboard.capture_current_view",
    description="Capture the dashboard exactly as the user currently sees it.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)
NAVIGATE_TOOL = Tool(
    name="navigateTo",
    description="Navigate the Davinci host to an allowed application view.",
    parameters={
        "type": "object",
        "properties": {
            "destination": {"type": "string", "enum": ["dashboard", "datasets"]},
            "resourceId": {"type": "string"},
        },
        "required": ["destination"],
        "additionalProperties": False,
    },
)


class StrictCamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class HostContext(StrictCamelModel):
    page_type: Literal["dashboard", "dataset"]
    resource_id: str | None
    context_version: int = Field(ge=0)
    supported_capabilities: list[Literal["dashboard.capture_current_view"]]
    supported_commands: list[Literal["navigateTo"]]

    @model_validator(mode="after")
    def validate_resource(self) -> "HostContext":
        if self.page_type == "dashboard" and self.resource_id != "1024":
            raise ValueError("dashboard resourceId must be 1024")
        if self.page_type == "dataset" and self.resource_id is not None:
            raise ValueError("dataset resourceId must be null")
        return self
```

Define nested Snapshot metric and page models, `UiAck` with `status: Literal["executed"]`, and `FrontendToolErrorPayload` with the approved error-code literals. Serialize validated results with `model_dump_json(by_alias=True)` so replay comparison uses one canonical representation.

Create `tests/agui_helpers.py` with `dashboard_context(version=3)`, `dashboard_tools()`, `snapshot_tool_message(tool_call_id)`, and `make_runtime_request(tmp_path, platform_session_id="thread-1", workspace_snapshot=None)`. The RuntimeRequest helper must create real `workspace`, `claude-config`, and `memory` directories before returning the dataclass so Runtime option tests do not fail on unrelated path validation.

- [ ] **Step 5: Run focused tests and dependency import checks**

Run:

```bash
uv run pytest tests/test_agui_models.py -q
uv run python -c "from ag_ui.core import RunAgentInput; from ag_ui.encoder import EventEncoder; print('ag-ui python ok')"
node -e "import('@ag-ui/client').then(({HttpAgent}) => console.log(Boolean(HttpAgent)))"
```

Expected: all tests pass and both commands print a truthy success marker.

- [ ] **Step 6: Commit the dependency and model boundary**

```bash
git add pyproject.toml uv.lock package.json package-lock.json app/agui/__init__.py app/agui/models.py tests/agui_helpers.py tests/test_agui_models.py
git commit -m "build: add pinned AG-UI protocol dependencies"
```

---

### Task 2: Implement the Process-Local Frontend Tool Bridge

**Files:**
- Create: `app/agui/bridge.py`
- Create: `tests/test_frontend_tool_bridge.py`

**Interfaces:**
- Produces: `FrontendToolBridgeRegistry.register(thread_id, run_id, host_context, tools) -> RunFrontendToolBridge`.
- Produces: `RunFrontendToolBridge.begin_call(tool_call_id, public_name, arguments) -> None`.
- Produces: `await RunFrontendToolBridge.claim_and_wait(public_name, arguments) -> ToolSubmission`.
- Produces: `await FrontendToolBridgeRegistry.submit(thread_id, run_id, tool_message) -> SubmissionOutcome`.
- Produces: `FrontendToolBridgeRegistry.get(thread_id, run_id)`, `active_for_thread(thread_id)`, `remove(thread_id, run_id, code, message=None)`, `shutdown()`, and `pending_count`.
- Produces: `RunFrontendToolBridge.run_id`, `host_context`, `public_tool_names`, `lookup_call(tool_call_id)`, and `fail_all(code, message)`.
- Produces: `FrontendToolBridgeError(code, message, status_code)` with `to_tool_json()`, plus `ToolSubmission` and `SubmissionOutcome(status, tool_call_id)`.

- [ ] **Step 1: Write failing Future, replay, timeout, and early-result tests**

Create tests that use a 20 ms timeout instead of sleeping 15 seconds:

```python
@pytest.mark.asyncio
async def test_result_resolves_only_matching_claim() -> None:
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.2)
    bridge = registry.register("thread-1", "run-1", dashboard_context(), dashboard_tools())
    bridge.begin_call("tool-1", "dashboard.capture_current_view", {})
    waiter = asyncio.create_task(
        bridge.claim_and_wait("dashboard.capture_current_view", {})
    )
    await asyncio.sleep(0)
    outcome = await registry.submit(
        "thread-1", "run-1", snapshot_tool_message("tool-1")
    )
    result = await waiter
    assert outcome.status == "accepted"
    assert json.loads(result.content)["metrics"]["itemCount"] == 4734


@pytest.mark.asyncio
async def test_identical_replay_is_idempotent_and_conflict_is_rejected() -> None:
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.2)
    bridge = registry.register("thread-1", "run-1", dashboard_context(), dashboard_tools())
    bridge.begin_call("tool-1", "dashboard.capture_current_view", {})
    message = snapshot_tool_message("tool-1")
    first = await registry.submit("thread-1", "run-1", message)
    replay = await registry.submit("thread-1", "run-1", message)
    changed = message.model_copy(update={"content": message.content.replace("4734", "4735")})
    with pytest.raises(FrontendToolBridgeError) as exc_info:
        await registry.submit("thread-1", "run-1", changed)
    assert first.status == replay.status == "accepted"
    assert exc_info.value.code == "TOOL_RESULT_CONFLICT"


@pytest.mark.asyncio
async def test_claim_times_out_and_cleanup_cancels_waiters() -> None:
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.02)
    bridge = registry.register("thread-1", "run-1", dashboard_context(), dashboard_tools())
    bridge.begin_call("tool-1", "navigateTo", {"destination": "datasets"})
    with pytest.raises(FrontendToolBridgeError, match="TOOL_TIMEOUT"):
        await bridge.claim_and_wait("navigateTo", {"destination": "datasets"})
    await registry.remove("thread-1", "run-1", code="IFRAME_CLOSED")
    assert registry.get("thread-1", "run-1") is None
```

Also test result arrival immediately before `begin_call`; `submit()` must wait on an `asyncio.Condition` for at most `registration_grace_seconds=1.0`, then bind to the subsequently registered call rather than returning a false mismatch.

- [ ] **Step 2: Run the bridge tests and verify red**

Run:

```bash
uv run pytest tests/test_frontend_tool_bridge.py -q
```

Expected: collection fails because `app.agui.bridge` does not exist.

- [ ] **Step 3: Implement call records with canonical argument matching**

Use focused data classes and monotonic deadlines:

```python
@dataclass(slots=True)
class ToolSubmission:
    content: str
    error: str | None


@dataclass(slots=True)
class PendingToolCall:
    tool_call_id: str
    public_name: str
    arguments_json: str
    future: asyncio.Future[ToolSubmission]
    expires_at: float
    claimed: bool = False
    submitted_fingerprint: str | None = None


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

`begin_call()` rejects duplicate IDs with different name/arguments, `claim_and_wait()` claims the oldest unclaimed exact name/argument record, and `submit()` validates `ToolMessage.content` using Task 1 before hashing `content + error`. Do not log result content.

For `dashboard.capture_current_view`, require `snapshot.page.contextVersion == bridge.host_context.context_version`. For successful `navigateTo`, require the ACK destination/path to match the claimed arguments and require `ack.contextVersion == bridge.host_context.context_version + 1`. A structured error ToolMessage may report the parent's newer version, but it must use one of the approved error codes. Violations return `CONTEXT_STALE` or `TARGET_NOT_FOUND` without resolving a success result.

- [ ] **Step 4: Implement registry ownership and shutdown**

Use `(thread_id, run_id)` as the registry key and maintain one active run per `thread_id`. A second registration for another Run returns `SESSION_MISMATCH`. `remove()` and `shutdown()` complete every unresolved Future with a structured bridge error and delete all references.

- [ ] **Step 5: Run bridge tests including leak checks**

Run:

```bash
uv run pytest tests/test_frontend_tool_bridge.py -q
```

Expected: success, including assertions that `registry.pending_count == 0` after timeout, cancellation, and shutdown.

- [ ] **Step 6: Commit the Bridge**

```bash
git add app/agui/bridge.py tests/test_frontend_tool_bridge.py
git commit -m "feat: add pending frontend tool bridge"
```

---

### Task 3: Bind AG-UI Run IDs to Existing Turns and App Services

**Files:**
- Modify: `app/turns/service.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/bootstrap.py`
- Modify: `app/main.py`
- Modify: `tests/test_turns.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: optional keyword `turn_id: str | None = None` on `TurnService.start`, returning `TurnRecord` while preserving all current callers.
- Produces: one `AppServices.frontend_tool_bridges: FrontendToolBridgeRegistry` per FastAPI app.
- Produces: optional `frontend_tool_bridges: FrontendToolBridgeRegistry | None = None` argument on `create_app` for deterministic browser tests.
- Consumes later: `ClaudeAgentRuntime(settings, frontend_tool_bridges=registry)`.

- [ ] **Step 1: Write failing caller-supplied Turn ID and singleton-wiring tests**

Add to `tests/test_turns.py`:

```python
@pytest.mark.asyncio
async def test_turn_start_can_use_validated_agui_run_id(settings_factory) -> None:
    *_, session, _sessions, _attachments, _runtime, _broker, turns = (
        await build_turn_services(settings_factory)
    )
    run_id = "9ee1d0ac-96a5-46ba-856f-7442713cbb77"
    turn = await turns.start(
        session.id, "hello", [], run_id, turn_id=run_id
    )
    assert turn.id == run_id
    await turns.wait(run_id)
    await turns.shutdown()
```

Add to `tests/test_api.py` an assertion that the injected registry is exactly `app.state.services.frontend_tool_bridges`, and that `services.runtime.frontend_tool_bridges` is the same object when the runtime is constructed by `build_app_services` in `local_inline` mode.

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
uv run pytest \
  tests/test_turns.py::test_turn_start_can_use_validated_agui_run_id \
  tests/test_api.py::test_app_services_share_frontend_tool_bridge_registry -q
```

Expected: `TurnService.start()` rejects the new keyword and `AppServices` lacks the registry field.

- [ ] **Step 3: Add the optional Turn ID without changing normal API behavior**

Extend the method signature:

```python
async def start(
    self,
    session_id: str,
    message: str,
    attachment_ids: list[str],
    client_request_id: str,
    file_references: list[str] | tuple[str, ...] = (),
    *,
    turn_id: str | None = None,
) -> TurnRecord:
```

Set the new record's `id` to `turn_id or str(uuid.uuid4())` while leaving every other current `TurnRecord` field unchanged. Existing idempotency remains keyed by `(session_id, client_request_id)`, and the AG-UI route will pass the same UUID as both values.

- [ ] **Step 4: Wire one registry through bootstrap and shutdown**

Add `frontend_tool_bridges` to `AppServices`. In `build_app_services`, resolve `frontend_tool_bridges or FrontendToolBridgeRegistry()` before Runtime construction, inject it into a default `ClaudeAgentRuntime`, and return it through services. In lifespan cleanup, shut down Turns first, then call `await services.frontend_tool_bridges.shutdown()` before disposing the database.

- [ ] **Step 5: Run focused and existing Turn/API tests**

Run:

```bash
uv run pytest tests/test_turns.py tests/test_api.py -q
```

Expected: all tests pass with existing caller-generated Turn IDs unchanged.

- [ ] **Step 6: Commit Turn and service wiring**

```bash
git add app/turns/service.py app/api/dependencies.py app/bootstrap.py app/main.py tests/test_turns.py tests/test_api.py
git commit -m "feat: bind AG-UI runs to workspace turns"
```

---

### Task 4: Expose Run-Scoped Davinci Tools through Claude Agent SDK

**Files:**
- Create: `app/agui/claude_tools.py`
- Modify: `app/runtime/claude.py`
- Modify: `tests/test_claude_runtime.py`

**Interfaces:**
- Produces: `PUBLIC_TO_SDK_TOOL` mapping `dashboard.capture_current_view -> dashboard_capture_current_view` and `navigateTo -> navigate_to`.
- Produces: `build_davinci_tools(bridge) -> list[SdkMcpTool]` and `build_davinci_mcp_server(bridge) -> McpSdkServerConfig`.
- Produces: `sdk_qualified_name(public_name)`, `public_name_for_sdk_tool(qualified_name)`, `is_davinci_sdk_tool(qualified_name)`, and `frontend_tool_system_prompt(bridge)`.
- Produces: fully-qualified allowlist names `mcp__davinci_ui__dashboard_capture_current_view` and `mcp__davinci_ui__navigate_to` only when declared by the active Bridge.
- Consumes: `RunFrontendToolBridge.begin_call()` in `PreToolUse` and `claim_and_wait()` in MCP handlers.

- [ ] **Step 1: Write failing dynamic-server, Hook, and handler tests**

Add tests that create a registry and active dashboard Bridge before calling `runtime.build_options(request)`. Import `dashboard_context`, `dashboard_tools`, and `make_runtime_request` from `tests.agui_helpers`, then set `runtime_request = make_runtime_request(tmp_path)`:

```python
def test_build_options_adds_only_active_davinci_tools(runtime_request, settings_factory) -> None:
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.2)
    bridge = registry.register(
        runtime_request.platform_session_id,
        "run-1",
        dashboard_context(),
        dashboard_tools(),
    )
    runtime = ClaudeAgentRuntime(settings_factory(), frontend_tool_bridges=registry)
    options = runtime.build_options(runtime_request)
    assert options.mcp_servers["davinci_ui"]["type"] == "sdk"
    assert "mcp__davinci_ui__dashboard_capture_current_view" in options.allowed_tools
    assert "mcp__davinci_ui__navigate_to" in options.allowed_tools
    assert "must call dashboard.capture_current_view" in options.system_prompt["append"]
    assert bridge.run_id == "run-1"
```

Invoke the installed Hook with `_tool_use_id="tool-1"` and assert the Bridge record stores the real ID and exact arguments. Invoke the `SdkMcpTool.handler` in an async task, submit a matching ToolMessage, and assert the returned MCP result is:

```python
{
    "content": [{"type": "text", "text": canonical_snapshot_json}],
    "is_error": False,
}
```

For a Dataset Bridge, assert the capture tool and allowlist entry are absent.

- [ ] **Step 2: Run the Claude Runtime tests and verify red**

Run:

```bash
uv run pytest tests/test_claude_runtime.py -q
```

Expected: new tests fail because the Runtime constructor has no Bridge registry and no dynamic SDK server.

- [ ] **Step 3: Implement safe public-to-SDK tool mapping and handlers**

Create SDK tools with model-safe names while retaining public names inside the Bridge:

```python
PUBLIC_TO_SDK_TOOL = {
    "dashboard.capture_current_view": "dashboard_capture_current_view",
    "navigateTo": "navigate_to",
}


async def tool_response(
    bridge: RunFrontendToolBridge, public_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        result = await bridge.claim_and_wait(public_name, arguments)
        return {
            "content": [{"type": "text", "text": result.content}],
            "is_error": result.error is not None,
        }
    except FrontendToolBridgeError as exc:
        return {
            "content": [{"type": "text", "text": exc.to_tool_json()}],
            "is_error": True,
        }
```

Build only the tools listed in `bridge.public_tool_names`, then call `create_sdk_mcp_server(name="davinci_ui", version="1.0.0", tools=tools)`.

- [ ] **Step 4: Extend Claude options and Hook correlation**

In `ClaudeAgentRuntime.build_options`, resolve the active Bridge by `request.platform_session_id`. Merge the SDK server into a new MCP dictionary and append only its fully-qualified names to `sdk_allowed_tools`. Extend the existing `PreToolUse` Hook rather than installing a second competing Hook:

```python
async def enforce_tool_allowlist(input_data, tool_use_id, _context):
    tool_name = str(input_data.get("tool_name", ""))
    allowed = tool_is_allowed(tool_name, effective_allowed_tools)
    if allowed and bridge is not None and is_davinci_sdk_tool(tool_name):
        bridge.begin_call(
            str(tool_use_id),
            public_name_for_sdk_tool(tool_name),
            dict(input_data.get("tool_input") or {}),
        )
    return permission_hook_result(tool_name, allowed)
```

Append an instruction that requires capture before dashboard interpretation and requires successful navigation ACK before claiming completion. The instruction names tools but contains no Mock metric values.

- [ ] **Step 5: Run Claude Runtime tests**

Run:

```bash
uv run pytest tests/test_claude_runtime.py -q
```

Expected: all existing normalization, secret-redaction, memory, MCP readiness, and new frontend-tool tests pass.

- [ ] **Step 6: Commit the Runtime integration**

```bash
git add app/agui/claude_tools.py app/runtime/claude.py tests/test_claude_runtime.py
git commit -m "feat: expose Davinci frontend tools to Claude"
```

---

### Task 5: Add the AG-UI Event Adapter and HTTP/SSE Endpoint

**Files:**
- Create: `app/agui/adapter.py`
- Create: `app/agui/routes.py`
- Modify: `app/main.py`
- Create: `tests/test_agui_adapter.py`
- Create: `tests/test_agui_api.py`

**Interfaces:**
- Produces: `AgUiEventMapper(thread_id, run_id, bridge).map(event_type, payload) -> list[BaseEvent]`.
- Produces: `POST /api/ag-ui` accepting official `RunAgentInput` for either a new Run or one ToolMessage continuation.
- Produces: initial SSE with one `RUN_STARTED`, streamed text/tool events, and exactly one `RUN_FINISHED` or `RUN_ERROR`.
- Produces: continuation SSE with `CUSTOM(name="tool_result.accepted")` after Bridge acceptance.

- [ ] **Step 1: Write failing event-order tests**

Create `tests/test_agui_adapter.py` around a stateful mapper:

```python
def test_mapper_emits_one_text_message_and_one_terminal() -> None:
    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = []
    events += mapper.map("turn.started", {"turn_id": "turn-1"})
    events += mapper.map("message.assistant.delta", {"text": "南区"})
    events += mapper.map("message.assistant.delta", {"text": "下降"})
    events += mapper.map("message.assistant.completed", {"text": "南区下降"})
    events += mapper.map("turn.completed", {"completed_at": "2026-08-06T16:00:00+08:00"})
    assert [event.type.value for event in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]


def test_mapper_emits_exact_public_tool_name_and_valid_json_args() -> None:
    mapper = AgUiEventMapper("session-1", "turn-1", bridge_with_navigation_call())
    events = mapper.map(
        "tool.started",
        {
            "tool_use_id": "tool-1",
            "name": "mcp__davinci_ui__navigate_to",
            "input_preview": '{"destination":"datasets"}',
        },
    )
    assert events[0].tool_call_name == "navigateTo"
    assert json.loads(events[1].delta) == {"destination": "datasets"}
```

Also test completed text without deltas, runtime failure redaction, cancellation, tool fallback preview, and duplicate terminal input.

- [ ] **Step 2: Write failing API classification and ownership tests**

In `tests/test_agui_api.py`, post a valid `RunAgentInput` with a Session ID and UUID Run ID, then assert the first encoded event is `RUN_STARTED` and the persisted Turn has exactly that ID. Add tests for:

- foreign Session returns the normal privacy-preserving not-found envelope;
- non-UUID `runId` returns `invalid_request`;
- missing terminal UserMessage returns `invalid_request`;
- a ToolMessage with wrong Session/Run/Tool Call returns `SESSION_MISMATCH`;
- `opensandbox_docker` and `execution_disabled` return `CAPABILITY_UNAVAILABLE` without creating a Turn;
- identical continuation replay is accepted while conflicting replay returns HTTP 409;
- API response and persisted events do not contain the configured API key.

- [ ] **Step 3: Run focused tests and verify red**

Run:

```bash
uv run pytest tests/test_agui_adapter.py tests/test_agui_api.py -q
```

Expected: collection fails because the adapter and route do not exist.

- [ ] **Step 4: Implement the stateful mapper**

Use official event classes and one deterministic assistant message ID:

```python
class AgUiEventMapper:
    def __init__(self, thread_id: str, run_id: str, bridge: RunFrontendToolBridge | None):
        self.thread_id = thread_id
        self.run_id = run_id
        self.message_id = f"{run_id}:assistant"
        self.text_started = False
        self.text_ended = False
        self.terminal = False
        self.bridge = bridge
```

On the first delta, emit `TextMessageStartEvent` then `TextMessageContentEvent`. Ignore `message.assistant.completed` when deltas already exist; otherwise emit its full text. On every terminal event, close an open text message first. Map `turn.progress` to `CustomEvent(name="workspace.progress", value={phase, message})`. Never include raw exceptions or environment data.

- [ ] **Step 5: Implement initial Run and continuation classification**

The route must first validate identity and runtime mode. A request whose final message has role `tool` is a continuation; every other valid initial request must end in one string-content UserMessage.

For an initial request:

```python
bridge = services.frontend_tool_bridges.register(
    body.thread_id,
    body.run_id,
    extract_host_context(body.state),
    validate_frontend_tools(host_context, body.tools),
)
turn = await services.turns.start(
    body.thread_id,
    user_message.content,
    [],
    body.run_id,
    turn_id=body.run_id,
)
```

Wrap registration and Turn creation so any validation, ownership, database, or `TurnService.start()` failure immediately calls `registry.remove(thread_id, run_id, code="RUN_ERROR")`; a failed initial request must never leave an active Bridge.

Stream `services.event_stream.iter_events()` through the mapper and `EventEncoder().encode()`. Emit SSE comment heartbeats as `: heartbeat\n\n`. On client cancellation, fail pending calls with `IFRAME_CLOSED`, request Turn cancellation, and remove the Bridge. On a terminal Runtime event, remove the Bridge after encoding its final event.

For a continuation, call `registry.submit()` and return one encoded `CustomEvent(name="tool_result.accepted", value={"toolCallId": id, "status": status})`. It is consumed by `ToolResultSubmitter`, not by the active `HttpAgent`.

- [ ] **Step 6: Mount the router and run API tests**

Include the AG-UI router before the web router in `app/main.py`, then run:

```bash
uv run pytest tests/test_agui_adapter.py tests/test_agui_api.py tests/test_api.py tests/test_sse.py -q
```

Expected: all tests pass and existing per-Turn SSE behavior is unchanged.

- [ ] **Step 7: Commit the AG-UI server boundary**

```bash
git add app/agui/adapter.py app/agui/routes.py app/main.py tests/test_agui_adapter.py tests/test_agui_api.py
git commit -m "feat: stream workspace turns over AG-UI"
```

---

### Task 6: Build the Standalone Agent Iframe Client

**Files:**
- Modify: `app/web/routes.py`
- Create: `app/web/templates/embed.html`
- Create: `app/web/static/embed.css`
- Create: `web/shared/davinci-protocol.js`
- Create: `web/embed/tool-result-submitter.js`
- Create: `web/embed/main.js`
- Create: `app/web/static/embed.js`
- Modify: `tests/test_web_page.py`
- Create: `tests/js/test_davinci_protocol.cjs`

**Interfaces:**
- Produces: `GET /embed` with no model credential or root-workbench JavaScript.
- Produces: `createDavinciEnvelope()`, `validateDavinciEnvelope()`, and `toolsForHostContext()` shared browser functions.
- Produces: `buildToolResultInput({threadId, runId, toolCallId, content, error, hostContext})` returning a `RunAgentInput`-shaped object with exactly one ToolMessage.
- Produces: `ToolResultSubmitter.submit({threadId, runId, toolCallId, content, error}) -> Promise<Ack>`.
- Produces: an iframe controller that uses `HttpAgent.addMessage()` then `runAgent({runId, tools, context, forwardedProps})`.

- [ ] **Step 1: Write failing route, static, protocol, and continuation tests**

Extend `tests/test_web_page.py` to assert `/embed` contains `agentTimeline`, `agentMessageInput`, `agentSendButton`, `agentNewSessionButton`, and loads only `/static/embed.css` and `/static/embed.js` from the new UI.

Create Node tests that verify:

```javascript
test("dataset exposes navigation but not dashboard capture", () => {
  const tools = toolsForHostContext({
    pageType: "dataset",
    resourceId: null,
    contextVersion: 4,
    supportedCapabilities: [],
    supportedCommands: ["navigateTo"],
  });
  assert.deepEqual(tools.map((tool) => tool.name), ["navigateTo"]);
});

test("validation rejects stale, expired, wrong-source messages", () => {
  assert.equal(validateDavinciEnvelope(validEnvelope(), trustedEvent()).ok, true);
  assert.equal(validateDavinciEnvelope(staleEnvelope(), trustedEvent()).code, "CONTEXT_STALE");
  assert.equal(validateDavinciEnvelope(expiredEnvelope(), trustedEvent()).code, "ORIGIN_REJECTED");
  assert.equal(validateDavinciEnvelope(validEnvelope(), foreignSourceEvent()).code, "ORIGIN_REJECTED");
});

test("tool result continuation contains one standard ToolMessage", () => {
  const input = buildToolResultInput({
    threadId: "session-1",
    runId: "turn-1",
    toolCallId: "tool-1",
    content: '{"status":"executed"}',
    error: null,
    hostContext: dashboardContext(),
  });
  assert.deepEqual(input.messages, [{
    id: "tool-result-tool-1",
    role: "tool",
    content: '{"status":"executed"}',
    toolCallId: "tool-1",
  }]);
});
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
uv run pytest tests/test_web_page.py -q
node --test tests/js/test_davinci_protocol.cjs
```

Expected: `/embed` is 404 and browser modules do not exist.

- [ ] **Step 3: Implement the shared protocol contract**

Define exactly these message types and protocol fields:

```javascript
export const PROTOCOL = "davinci-agent-host";
export const PROTOCOL_VERSION = "1";
export const MESSAGE_TYPES = Object.freeze({
  HOST_CONTEXT: "HOST_CONTEXT",
  CAPABILITY_REQUEST: "DAVINCI_CAPABILITY_REQUEST",
  CAPABILITY_RESULT: "DAVINCI_CAPABILITY_RESULT",
  UI_COMMAND: "UI_COMMAND",
  UI_ACK: "UI_ACK",
});
```

Every request/result/command/ACK envelope contains `protocol`, `protocolVersion`, `messageType`, `messageId`, `requestId`, `toolCallId`, `nonce`, `issuedAt`, `expiresAt`, `contextVersion`, and `payload`. `HOST_CONTEXT` contains the protocol identity, message ID, nonce, issue/expiry timestamps, current route/resource/version, and supported Capability/Command lists; it has no request or Tool Call correlation fields.

`validateDavinciEnvelope(envelope, {origin, source, expectedOrigin, expectedSource, nonce, contextVersion, now})` returns `{ok: true}` or `{ok: false, code}` without throwing browser-controlled payloads into logs. Use `crypto.randomUUID()` for IDs and `Date.now() + 15_000` for expiry.

- [ ] **Step 4: Implement ToolResultSubmitter as a separate fetch path**

Post one valid `RunAgentInput` with a ToolMessage to `/api/ag-ui`, parse the SSE `CUSTOM` acknowledgement, require matching `toolCallId`, and never invoke `agent.runAgent()` from this class. Use `credentials: "same-origin"` and `Accept: "text/event-stream"`.

- [ ] **Step 5: Implement Session chat with official HttpAgent**

On iframe initialization:

1. fetch `/api/workspaces` and select the available `example` workspace;
2. create a Session only when the user clicks “新建 Session”;
3. fetch `/api/sessions/{id}/messages` when restoring a Session;
4. construct `new HttpAgent({url: "/api/ag-ui", threadId: session.id, initialMessages})`;
5. receive and validate `HOST_CONTEXT` from the exact parent Origin and iframe parent Window;
6. add the user's message with `agent.addMessage({id, role: "user", content})`;
7. invoke `agent.runAgent({runId, tools: toolsForHostContext(context), context: [], forwardedProps: {workspaceId: "example", profile: "davinci-mvp-v1"}}, subscriber)`;
8. on `onToolCallEndEvent`, send either a Capability request or UI Command to the parent and submit its result through `ToolResultSubmitter`;
9. render final text and errors with `textContent`, never `innerHTML`.

- [ ] **Step 6: Add the route, template, styles, and deterministic bundle**

Add `@router.get("/embed")` in `app/web/routes.py`. Build both source entry points after Task 7 creates the parent entry; during this task build only the iframe directly:

```bash
npx esbuild web/embed/main.js --bundle --format=esm --platform=browser --outfile=app/web/static/embed.js
```

The iframe CSS must fit a 420 px wide drawer, expose visible Run/tool state, and keep the composer fixed while the message timeline scrolls.

- [ ] **Step 7: Run iframe contract tests**

Run:

```bash
uv run pytest tests/test_web_page.py -q
node --test tests/js/test_davinci_protocol.cjs
```

Expected: tests pass; `/` still references its existing static asset order and `/embed` contains no API key.

- [ ] **Step 8: Commit the iframe client**

```bash
git add app/web/routes.py app/web/templates/embed.html app/web/static/embed.css app/web/static/embed.js web/shared/davinci-protocol.js web/embed/tool-result-submitter.js web/embed/main.js tests/test_web_page.py tests/js/test_davinci_protocol.cjs
git commit -m "feat: add standalone AG-UI iframe client"
```

---

### Task 7: Build the Two-Route Mock Davinci Parent

**Files:**
- Create: `demo/__init__.py`
- Create: `demo/davinci_mock/__init__.py`
- Create: `demo/davinci_mock/app.py`
- Create: `demo/davinci_mock/templates/index.html`
- Create: `demo/davinci_mock/static/styles.css`
- Create: `web/davinci-mock/main.js`
- Create: `demo/davinci_mock/static/app.js`
- Create: `scripts/run-davinci-agui-mvp.sh`
- Create: `tests/test_davinci_mock.py`
- Modify: `tests/js/test_davinci_protocol.cjs`

**Interfaces:**
- Produces: `create_mock_app(agent_origin="http://127.0.0.1:8000") -> FastAPI`.
- Produces: both `/dashboard/1024` and `/datasets` as history-based routes served from the same root Shell.
- Produces: `captureCurrentView() -> DashboardSnapshot` from the live Store.
- Produces: `navigateTo(args) -> UiAck` only after `history.pushState`, render, `contextVersion += 1`, and `HOST_CONTEXT` dispatch.
- Consumes: the shared message protocol and iframe `/embed` URL.

- [ ] **Step 1: Write failing Mock route and Store behavior tests**

Create `tests/test_davinci_mock.py`:

```python
@pytest.mark.asyncio
async def test_mock_routes_share_shell_and_pin_agent_origin() -> None:
    app = create_mock_app("http://127.0.0.1:8999")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://mock") as client:
        dashboard = await client.get("/dashboard/1024")
        datasets = await client.get("/datasets")
        missing = await client.get("/dashboard/9999")
    assert dashboard.status_code == datasets.status_code == 200
    assert 'data-agent-origin="http://127.0.0.1:8999"' in dashboard.text
    assert 'id="agentFrame"' in dashboard.text
    assert missing.status_code == 404
```

Extend Node tests to import pure Store functions, update the region filter, assert `contextVersion` increments, and assert the next Snapshot contains the updated filter value rather than a hard-coded prompt string.

Add a navigation rejection case: `navigateTo({destination: "dashboard", resourceId: "9999"})` must return `TARGET_NOT_FOUND`, keep the pathname unchanged, and leave `contextVersion` unchanged.

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
uv run pytest tests/test_davinci_mock.py -q
node --test tests/js/test_davinci_protocol.cjs
```

Expected: import and route failures because the Mock application is absent.

- [ ] **Step 3: Implement the second-Origin FastAPI shell**

Mount `/static` from `demo/davinci_mock/static`, expose `GET /health -> {"status": "ready"}`, and explicitly handle `/`, `/dashboard/1024`, and `/datasets`; unknown dashboard IDs return 404. Render `agent_origin` into a data attribute after validating that it is loopback HTTP in development.

- [ ] **Step 4: Implement Root Shell, stable iframe, and Store**

Create one iframe under the root Shell:

```html
<iframe
  id="agentFrame"
  title="数据分析助手"
  src="{{ agent_origin }}/embed"
  hidden
></iframe>
```

The route renderer replaces only `#davinciContent`; opening and closing the floating drawer toggles attributes/classes and never removes or rewrites `#agentFrame`. The initial Store contains:

```javascript
const state = {
  route: "/dashboard/1024",
  contextVersion: 1,
  filters: { region: "南区", period: "最近7天" },
  metrics: { itemCount: 4734, weeklyChangePct: -10.88, bidAmount: 40388380 },
  widgets: [
    { id: "trend", title: "物品量趋势", values: [742, 711, 695, 681, 652, 631, 622] },
    { id: "category", title: "品类构成", values: { 手机: 2130, 平板: 1164, 笔记本: 865, 其他: 575 } },
    { id: "detail", title: "核心指标明细", values: [] },
  ],
};
```

`captureCurrentView()` creates a new JSON object from current Store values and the current timestamp. It must not read rendered text back from the DOM.

- [ ] **Step 5: Implement parent message execution and ACK ordering**

On iframe `load`, send `HOST_CONTEXT`. For a valid `DAVINCI_CAPABILITY_REQUEST`, require dashboard context and matching version before returning a Snapshot. For `UI_COMMAND`, validate `navigateTo`, execute only the two allowed destinations, re-render, increment version, send updated `HOST_CONTEXT`, then send `UI_ACK(status="executed")`. Wrong source/origin/nonce is ignored; stale context returns a structured error result without changing Store or route.

- [ ] **Step 6: Add the two-process runner and build both bundles**

`scripts/run-davinci-agui-mvp.sh` must use `set -euo pipefail`, start the existing Agent Host on 8000 and Mock server on 4173, install an EXIT/INT/TERM trap that terminates both child PIDs, and wait until both health URLs respond before printing the browser URL.

Run:

```bash
npm run build:agui
```

- [ ] **Step 7: Run static, Node, and Mock tests**

Run:

```bash
uv run pytest tests/test_davinci_mock.py tests/test_web_page.py -q
node --test tests/js/test_davinci_protocol.cjs
npm run build:agui
git diff --exit-code -- app/web/static/embed.js demo/davinci_mock/static/app.js
```

Expected: all tests pass and rebuilding committed bundles produces no diff.

- [ ] **Step 8: Commit the Mock host**

```bash
git add demo web/davinci-mock scripts/run-davinci-agui-mvp.sh app/web/static/embed.js tests/test_davinci_mock.py tests/js/test_davinci_protocol.cjs
git commit -m "feat: add Mock Davinci dashboard host"
```

---

### Task 8: Prove the Full Two-Origin Slice with Deterministic Browser Tests

**Files:**
- Create: `tests/browser/test_davinci_agui_mvp.py`
- Modify: `tests/test_agui_api.py`

**Interfaces:**
- Produces: `BridgeAwareFakeRuntime`, which exercises the same registry contract as Claude without a network model call.
- Proves: capture before answer, exact Store facts, navigation after ACK, stable iframe DOM, stable Session ID, dynamic tool catalog, origin rejection, stale-context failure, timeout visibility, and secret absence.

- [ ] **Step 1: Write a Bridge-aware fake Runtime in the browser test**

The fake Runtime chooses behavior from `request.text`, registers the same public call IDs the Hook would register, waits on `claim_and_wait()`, and emits existing `RuntimeEvent` types:

```python
class BridgeAwareFakeRuntime:
    def __init__(self, registry: FrontendToolBridgeRegistry) -> None:
        self.registry = registry

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            protocol_version="1",
            supports_resume=True,
            supports_interrupt=True,
            supports_auto_memory=True,
            supports_mcp=True,
            supports_skills=True,
        )

    async def run(self, request, cancel_event):
        bridge = self.registry.active_for_thread(request.platform_session_id)
        public_name = (
            "navigateTo" if "数据集" in request.text
            else "dashboard.capture_current_view"
        )
        arguments = {"destination": "datasets"} if public_name == "navigateTo" else {}
        tool_call_id = f"fake-{bridge.run_id}"
        bridge.begin_call(tool_call_id, public_name, arguments)
        yield RuntimeEvent(
            "tool.started",
            {
                "tool_use_id": tool_call_id,
                "name": sdk_qualified_name(public_name),
                "input_preview": canonical_json(arguments),
            },
            "tool",
        )
        result = await bridge.claim_and_wait(public_name, arguments)
        yield RuntimeEvent(
            "tool.completed",
            {"tool_use_id": tool_call_id, "name": public_name, "is_error": False},
            "tool",
        )
        text = render_fake_answer(public_name, result.content)
        yield RuntimeEvent("message.assistant.delta", {"text": text}, "assistant")
        yield RuntimeEvent("message.assistant.completed", {"text": text}, "assistant")
        yield RuntimeEvent("usage.updated", {"input_tokens": 10, "output_tokens": 20}, "system")
        yield RuntimeResult(
            status="completed",
            claude_session_id=request.claude_session_id or f"fake-{request.platform_session_id}",
        ).to_event()
```

- [ ] **Step 2: Write the primary dashboard-to-dataset browser Gate**

Start Agent Host and Mock Davinci on separate ephemeral loopback ports while still asserting their Origins differ. The test must:

1. open `/dashboard/1024`;
2. record `iframe` element handle and its browsing-context URL;
3. open the drawer and create one Session;
4. send “解读当前仪表盘”;
5. observe a tool row before final text;
6. assert final text includes `4,734`, `-10.88%`, and `40,388,380`;
7. send “打开数据集页面”;
8. assert parent pathname becomes `/datasets` before completion text;
9. assert the same iframe element and Session ID remain;
10. navigate back to dashboard and confirm history remains visible.

- [ ] **Step 3: Add hostile-message, stale-context, timeout, and secret tests**

Use `page.evaluate()` to send a forged parent-window message whose `source` is not the iframe; assert no route or Store change. Mutate the parent Store between request and result to force `CONTEXT_STALE`; assert the iframe shows the structured failure. Configure a 20 ms registry timeout and suppress the parent response; assert `TOOL_TIMEOUT` is visible and `pending_count == 0`.

Intercept all requests and responses for both Origins. Use a distinctive `top-secret-davinci-gate-key` in Settings and assert it is absent from request URLs/bodies, HTML, SSE text, captured console messages, persisted Turn event payloads, and Mock server responses. In a separate waiting-Tool scenario, remove the iframe DOM rather than merely hiding the drawer; assert the Run ends with `IFRAME_CLOSED` and the registry has no Pending Future.

- [ ] **Step 4: Run the browser Gate and verify red, then complete missing UI details**

Run:

```bash
uv run pytest tests/browser/test_davinci_agui_mvp.py -q
```

Expected on first run: failures identify any unimplemented focus, status, ordering, or cleanup behavior. Make only the minimal source and style changes required for these explicit assertions, rebuild bundles, and rerun until green.

- [ ] **Step 5: Run the combined deterministic vertical slice**

Run:

```bash
uv run pytest \
  tests/test_agui_models.py \
  tests/test_frontend_tool_bridge.py \
  tests/test_agui_adapter.py \
  tests/test_agui_api.py \
  tests/test_claude_runtime.py \
  tests/test_davinci_mock.py \
  tests/browser/test_davinci_agui_mvp.py -q
node --test tests/js/test_davinci_protocol.cjs
```

Expected: all deterministic MVP tests pass without a model call.

- [ ] **Step 6: Commit the deterministic Gate**

```bash
npm run build:agui
git add tests/browser/test_davinci_agui_mvp.py tests/test_agui_api.py web/shared/davinci-protocol.js web/embed/main.js web/embed/tool-result-submitter.js web/davinci-mock/main.js app/web/static/embed.js demo/davinci_mock/static/app.js
git commit -m "test: prove Davinci AG-UI iframe workflow"
```

---

### Task 9: Add the Real Qwen Gate, Manual Runbook, and Full Regression Gate

**Files:**
- Create: `tests/live/test_davinci_agui_qwen.py`
- Modify: `README.md`
- Modify: `scripts/run-davinci-agui-mvp.sh`

**Interfaces:**
- Produces: opt-in `RUN_LIVE_DAVINCI_AGUI=1` real-model Gate isolated from workspace MCP startup.
- Produces: manual commands that use `.env`, `qwen3.8-max`, the two fixed local Origins, and the existing `local_inline` Runtime.
- Proves: real Claude Agent SDK tool invocation, Snapshot-grounded answer, ACK-grounded navigation, Session continuity, reproducible bundles, and full repository regression.

- [ ] **Step 1: Write the opt-in live test with an isolated workspace**

The test must skip unless `RUN_LIVE_DAVINCI_AGUI=1`, then load real endpoint credentials through `Settings()` without printing them. Create a temporary workspace with `skills: []`, `mcp_servers: {}`, and allowed tools `[Read]` so unrelated MCP readiness cannot mask this Gate. Assert before starting:

```python
assert settings.app_runtime_mode == "local_inline"
assert settings.claude_model == "qwen3.8-max"
assert settings.anthropic_api_key is not None
```

Run the same Playwright flow as Task 8 with `ClaudeAgentRuntime`, and record AG-UI event types. Require `TOOL_CALL_START` before final `TEXT_MESSAGE_CONTENT`, the three exact dashboard facts in the answer, successful `/datasets` navigation, and the same Session ID across both Runs.

- [ ] **Step 2: Run the live Gate explicitly**

Run:

```bash
RUN_LIVE_DAVINCI_AGUI=1 uv run pytest tests/live/test_davinci_agui_qwen.py -q -s
```

Expected: one passing test with real model usage and cost. If the configured model is not exactly `qwen3.8-max`, the test fails before making a request.

- [ ] **Step 3: Document the manual MVP procedure and boundary**

Add a README section with these commands:

```bash
cd /Users/a110356/work/code/claude_workspace_mvp
uv sync --frozen
npm ci
npm run build:agui
export APP_RUNTIME_MODE=local_inline
export CLAUDE_MODEL=qwen3.8-max
./scripts/run-davinci-agui-mvp.sh
```

Tell the tester to open `http://127.0.0.1:4173/dashboard/1024`, open the floating assistant, create a Session, ask “解读当前仪表盘”, then ask “打开数据集页面”. State that this is a loopback-only architecture validation and does not validate Docker/OpenSandbox or real Davinci authorization/data APIs.

- [ ] **Step 4: Run formatting, deterministic tests, and bundle reproducibility**

Run:

```bash
uv run ruff check app demo tests
uv run pytest -q
node --test tests/js/*.cjs
npm run build:agui
git diff --exit-code -- app/web/static/embed.js demo/davinci_mock/static/app.js
git diff --check
```

Expected: Python, Node, Playwright, static bundle, and whitespace Gates all pass. Live tests remain skipped unless their explicit environment flags are set.

- [ ] **Step 5: Manually inspect the two-Origin flow**

Start the script, confirm both health checks, and use browser DevTools Network to verify:

- parent Origin is `127.0.0.1:4173` and iframe Origin is `127.0.0.1:8000`;
- the initial `/api/ag-ui` request remains streaming while Tool Result is sent by a second POST;
- the first stream sends one `RUN_FINISHED` only after the final answer;
- no request or response contains the API key;
- closing and reopening the drawer retains the iframe document and Session.

- [ ] **Step 6: Commit the live Gate and runbook**

```bash
git add tests/live/test_davinci_agui_qwen.py README.md scripts/run-davinci-agui-mvp.sh
git commit -m "docs: add Davinci AG-UI MVP validation gate"
```

- [ ] **Step 7: Record the final branch evidence**

Run:

```bash
git status --short --branch
git log --oneline --decorate -10
```

Expected: only the user's pre-existing untracked `agents.json`, `description.md`, `members.json`, and `squads.json` remain; implementation files are committed on `codex/davinci-iframe-mvp-validation`.
