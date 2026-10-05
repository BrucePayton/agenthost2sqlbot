# Davinci Reverse Navigation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow one user Turn that starts on `/datasets` to navigate to `/dashboard/1024`, capture the current dashboard, and return an interpretation without losing the Session.

**Architecture:** Use a stable two-tool catalog for the bounded Mock Davinci profile while retaining execution-time capability checks in the trusted parent. A successful navigation continuation carries the new HostContext; the server validates the ACK/context transition and atomically advances the Run bridge before the Claude loop can invoke dashboard capture.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Claude Agent SDK 0.2.128, AG-UI protocol 0.1.19, vanilla JavaScript, Node test runner, pytest, Playwright, optional qwen3.8-max.

## Global Constraints

- Keep `threadId == Session ID` and `runId == Turn ID`.
- Keep exact origin, source, nonce, protocol version, expiry, Tool Call ID, and contextVersion validation.
- Keep Snapshot JSON bounded to 64 KiB.
- Keep the iframe mounted across Mock route changes.
- `navigateTo({destination: "dashboard"})` resolves to `/dashboard/1024`; an explicit non-`1024` dashboard resource ID remains invalid.
- Dataset capture before successful navigation remains `CAPABILITY_UNAVAILABLE`.
- A HostContext transition is accepted only with a valid navigation ACK whose contextVersion increments by exactly one.
- Preserve the existing root workbench, Session/Turn APIs, Docker flows, Skills, MCP, attachments, and user-owned untracked files.
- Generated browser bundles remain committed and must reproduce with `npm run build:agui`.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `web/shared/davinci-protocol.js` | Return the stable bounded frontend-tool catalog for every valid HostContext. |
| `web/davinci-mock/main.js` | Resolve the default dashboard destination to Mock dashboard `1024`. |
| `app/agui/models.py` | Validate the stable catalog rather than the current-page capability subset. |
| `app/agui/bridge.py` | Validate and apply navigation HostContext transitions before releasing the Tool waiter. |
| `app/agui/routes.py` | Parse continuation HostContext/tools and pass them into Bridge submission. |
| `app/agui/claude_tools.py` | Tell Claude to navigate before capture when the Run begins off-dashboard. |
| `tests/js/test_davinci_protocol.cjs` | Gate stable catalog and default/invalid dashboard routing. |
| `tests/test_agui_models.py` | Gate stable catalog validation for dataset HostContext. |
| `tests/test_frontend_tool_bridge.py` | Gate atomic dataset v2 → dashboard v3 transition and capture. |
| `tests/test_agui_api.py` | Gate continuation state validation and transition wiring. |
| `tests/browser/test_davinci_agui_mvp.py` | Gate the deterministic same-Turn reverse-navigation vertical slice. |
| `tests/live/test_davinci_agui_qwen.py` | Gate the opt-in real qwen3.8-max reverse-navigation flow. |
| `app/web/static/embed.js`, `demo/davinci_mock/static/app.js` | Reproducible generated bundles. |

---

### Task 1: Make Navigation and Tool Discovery Consistent

**Files:**
- Modify: `tests/js/test_davinci_protocol.cjs`
- Modify: `tests/test_agui_models.py`
- Modify: `web/shared/davinci-protocol.js`
- Modify: `web/davinci-mock/main.js`
- Modify: `app/agui/models.py`

**Interfaces:**
- Produces: `toolsForHostContext(context) -> [CAPTURE_TOOL, NAVIGATE_TOOL]` for every valid context.
- Produces: `createDavinciStore().navigateTo({destination: "dashboard", resourceId?: string})` with default ID `1024`.
- Consumes later: the stable catalog stored by `RunFrontendToolBridge`.

- [ ] **Step 1: Write failing JavaScript and Python contract tests**

Change the dataset catalog expectation to both tools and add the default dashboard navigation case:

```javascript
test("dataset advertises the bounded Run tool catalog", async () => {
  const { toolsForHostContext } = await protocol();
  const tools = toolsForHostContext(datasetContext());
  assert.deepEqual(tools.map((tool) => tool.name), [
    "dashboard.capture_current_view",
    "navigateTo",
  ]);
});

test("dashboard navigation defaults to the only Mock dashboard", async () => {
  const { createDavinciStore } = await mockStore();
  const store = createDavinciStore({ route: "/datasets" });
  const result = store.navigateTo({ destination: "dashboard" });
  assert.equal(result.ok, true);
  assert.equal(result.ack.path, "/dashboard/1024");
});
```

In `tests/test_agui_models.py`, require `validate_frontend_tools(dataset_context, dashboard_tools())` to succeed while retaining rejection of unknown or mutated tools.

- [ ] **Step 2: Run the contract tests and verify red**

Run:

```bash
node --test tests/js/test_davinci_protocol.cjs
uv run pytest tests/test_agui_models.py -q
```

Expected: dataset catalog and default dashboard navigation assertions fail against the current page-scoped/required-ID implementation.

- [ ] **Step 3: Implement the minimal stable catalog and default route**

Make `toolsForHostContext` return cloned `CAPTURE_TOOL` and `NAVIGATE_TOOL` after validating HostContext. In `validate_frontend_tools`, require the exact immutable two-tool catalog for both page types. In `navigateTo`, accept dashboard when `resourceId == null || resourceId === "1024"`; keep all other IDs invalid.

- [ ] **Step 4: Run focused contract tests and verify green**

Run the two commands from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add web/shared/davinci-protocol.js web/davinci-mock/main.js app/agui/models.py tests/js/test_davinci_protocol.cjs tests/test_agui_models.py
git commit -m "fix: align Davinci navigation tool contract"
```

---

### Task 2: Advance Bridge Context After a Valid Navigation ACK

**Files:**
- Modify: `tests/test_frontend_tool_bridge.py`
- Modify: `tests/test_agui_api.py`
- Modify: `app/agui/bridge.py`
- Modify: `app/agui/routes.py`
- Modify: `app/agui/claude_tools.py`

**Interfaces:**
- Produces: `FrontendToolBridgeRegistry.submit(thread_id, run_id, message, next_host_context, next_tools)`.
- Produces: `RunFrontendToolBridge.submit(message, next_host_context, next_tools)` that advances Context only after a valid navigation ACK.
- Consumes: continuation `state.hostContext` and `tools` parsed with existing strict models.

- [ ] **Step 1: Write failing Bridge and API transition tests**

Add a Bridge test that starts with dataset Context version 2, begins `navigateTo({destination: "dashboard"})`, submits a dashboard ACK and dashboard Context version 3, asserts `bridge.host_context.page_type == "dashboard"`, then begins/submits `dashboard.capture_current_view` at version 3 and resolves it.

Add API tests proving:

```python
# accepted
body["state"]["hostContext"] = dashboard_context(version=3).model_dump(by_alias=True)
body["tools"] = [tool.model_dump(by_alias=True) for tool in dashboard_tools()]
assert response.status_code == 200

# rejected
body["state"]["hostContext"]["contextVersion"] = 4
assert response.json()["error"]["code"] == "CONTEXT_STALE"
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
uv run pytest tests/test_frontend_tool_bridge.py tests/test_agui_api.py -q
```

Expected: current submit signatures do not accept transition Context and the Bridge remains on dataset Context.

- [ ] **Step 3: Implement atomic transition validation**

In `_continue_tool_result`, parse `HostContext` and validate the stable tools exactly as initial Run input does. Pass both to registry submission. In Bridge submission:

1. Canonicalize and validate the Tool Result.
2. For a successful navigation ACK, require `next_host_context.context_version == old + 1` and require its page/resource pair to match the ACK destination/path.
3. For errors and non-navigation calls, require continuation Context to equal the current Bridge Context.
4. Assign `self.host_context = next_host_context` and `self.tools = tuple(next_tools)` before `call.future.set_result(...)`.
5. Never update Context on replay, schema failure, target mismatch, stale version, or frontend error.

Update the system instruction to say that a Run starting on datasets must call `navigateTo(destination="dashboard")` and, after success, call `dashboard.capture_current_view` before interpreting.

- [ ] **Step 4: Run focused backend tests and verify green**

Run the command from Step 2. Expected: all selected tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add app/agui/bridge.py app/agui/routes.py app/agui/claude_tools.py tests/test_frontend_tool_bridge.py tests/test_agui_api.py
git commit -m "fix: advance Davinci context within an AG-UI run"
```

---

### Task 3: Gate the Same-Turn Browser Flow and Deliver It

**Files:**
- Modify: `tests/browser/test_davinci_agui_mvp.py`
- Modify: `tests/live/test_davinci_agui_qwen.py`
- Modify: `README.md`
- Regenerate: `app/web/static/embed.js`
- Regenerate: `demo/davinci_mock/static/app.js`

**Interfaces:**
- Consumes: stable frontend tools and atomic Bridge Context transition.
- Produces: deterministic and opt-in real-model evidence for dataset → dashboard → capture → interpretation.

- [ ] **Step 1: Extend the deterministic fake Runtime and add a failing browser Gate**

For the exact prompt `帮我解读仪表盘`, make the fake Runtime emit sequential Tool Calls in one Turn:

```text
navigateTo({"destination":"dashboard"})
dashboard.capture_current_view({})
```

Start the browser at `/datasets`; assert the URL becomes `/dashboard/1024`, both Tool rows are visible in order, the reply contains all three metric values, and the localStorage Session ID is unchanged.

- [ ] **Step 2: Run the browser Gate and verify red**

Run:

```bash
uv run pytest tests/browser/test_davinci_agui_mvp.py -q
```

Expected: the reverse-navigation test fails before the bundle/backend transition fix is built.

- [ ] **Step 3: Build bundles and add the opt-in live scenario**

Run `npm run build:agui`. Extend the live Gate to navigate from datasets and wait for both Tool rows, dashboard URL, final `就绪`, metrics, and unchanged Session ID. Keep `RUN_LIVE_DAVINCI_AGUI=1` as the only switch that incurs model cost. Update README manual steps with the reverse-navigation prompt.

- [ ] **Step 4: Run deterministic and full regression Gates**

Run:

```bash
npm run build:agui
node --test tests/js/*.cjs
uv run ruff check app demo tests
uv run pytest -q
git diff --exit-code -- app/web/static/embed.js demo/davinci_mock/static/app.js
git diff --check
```

Expected: all commands exit 0. Run the opt-in live Gate only with the configured qwen3.8-max key:

```bash
RUN_LIVE_DAVINCI_AGUI=1 CLAUDE_MODEL=qwen3.8-max APP_RUNTIME_MODE=local_inline uv run pytest tests/live/test_davinci_agui_qwen.py -q -s
```

- [ ] **Step 5: Commit, restart, and perform the manual Gate**

Commit only intended files, preserving `agents.json`, `description.md`, `members.json`, and `squads.json`. Restart the existing launchd services, open `/datasets`, submit `帮我解读仪表盘`, and verify the same Session reaches `/dashboard/1024` and returns the three current dashboard metrics.

```bash
git add README.md app web demo tests
git commit -m "test: prove Davinci reverse navigation flow"
```
