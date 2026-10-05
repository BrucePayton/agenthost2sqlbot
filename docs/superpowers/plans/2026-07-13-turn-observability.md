# Turn Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, event-driven execution panel that shows trustworthy Turn phases, tool duration, heartbeat freshness, backend disconnects, and restart interruptions without exposing raw model thinking.

**Architecture:** `ClaudeAgentRuntime` emits deterministic `turn.progress` events around SDK and MCP boundaries; `TurnService` persists them through the existing `messages` stream. Startup recovery appends a terminal `turn.interrupted` event. The native JavaScript UI renders a compact execution panel, consumes heartbeats, and reconciles SSE failures with the Turn API.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Claude Agent SDK, Server-Sent Events, native HTML/CSS/JavaScript, pytest, Playwright.

## Global Constraints

- Show event-backed progress summaries only; never expose or persist `ThinkingBlock`, `thinking_delta`, signatures, or hidden reasoning.
- Tool input and output remain truncated through `preview()` and are collapsed by default.
- Persist phase transitions, not heartbeat ticks or MCP polling iterations.
- Reuse the existing `messages` table and monotonic per-Turn `sequence`; do not add a migration.
- Keep the current single-user, single-process architecture and 15-second SSE heartbeat.
- Do not install `launchd`, systemd, or another operating-system daemon.
- Preserve the user's existing uncommitted Hive MCP changes in `.env.example`, `README.md`, `tests/test_workspaces.py`, and `workspaces/example/workspace.yaml`.

---

### Task 1: Runtime progress events and truthful tool timing

**Files:**
- Modify: `app/runtime/claude.py:106-160`
- Modify: `app/runtime/events.py`
- Modify: `app/runtime/fake.py`
- Test: `tests/test_runtime_events.py`
- Test: `tests/test_claude_runtime.py`

**Interfaces:**
- Produces: `progress_event(phase: str, message: str) -> RuntimeEvent` with `occurred_at` in UTC.
- Produces: `normalize_sdk_message(message, *, now=None, tool_started_at=None) -> list[RuntimeEvent]` with `started_at`, `completed_at`, and optional `duration_ms`.
- Preserves: thinking blocks and thinking deltas normalize to no public events.

- [ ] **Step 1: Write failing normalization tests**

Add tests that construct `ThinkingBlock`, a `thinking_delta` `StreamEvent`, `ToolUseBlock`, and `ToolResultBlock` with fixed UTC timestamps:

```python
def test_normalization_ignores_thinking_and_tracks_tool_duration() -> None:
    from datetime import UTC, datetime, timedelta
    from claude_agent_sdk import AssistantMessage, ThinkingBlock, ToolResultBlock, ToolUseBlock, UserMessage
    from app.runtime.claude import normalize_sdk_message

    started = datetime(2026, 7, 13, 1, 0, tzinfo=UTC)
    starts = {}
    thinking = AssistantMessage([ThinkingBlock("private", "signature")], "model")
    tool = AssistantMessage([ToolUseBlock("tool-1", "Read", {"path": "x"})], "model")
    result = UserMessage([ToolResultBlock("tool-1", "ok", False)])

    assert normalize_sdk_message(thinking, now=started, tool_started_at=starts) == []
    started_event = normalize_sdk_message(tool, now=started, tool_started_at=starts)[0]
    completed_event = normalize_sdk_message(
        result,
        now=started + timedelta(milliseconds=1250),
        tool_started_at=starts,
    )[0]
    assert started_event.payload["started_at"] == started.isoformat()
    assert completed_event.payload["duration_ms"] == 1250
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv/bin/pytest -q tests/test_runtime_events.py -k 'thinking or tool_duration'`

Expected: FAIL because `normalize_sdk_message` does not accept timing arguments and currently reports `duration_ms: 0`.

- [ ] **Step 3: Implement timing-aware normalization**

In `app/runtime/claude.py`, use a per-run `dict[str, datetime]` and add timestamps only when supported by the event:

```python
def normalize_sdk_message(
    message: Any,
    *,
    now: datetime | None = None,
    tool_started_at: dict[str, datetime] | None = None,
) -> list[RuntimeEvent]:
    observed_at = now or datetime.now(UTC)
    starts = tool_started_at if tool_started_at is not None else {}
    # Text handling remains unchanged. ThinkingBlock and thinking_delta fall through.
    # ToolUseBlock stores starts[block.id] and emits started_at.
    # ToolResultBlock pops the start and emits duration_ms only when it exists.
```

Pass one `tool_started_at` dictionary through every `normalize_sdk_message` call inside a single `run()`.

- [ ] **Step 4: Write failing runtime phase tests**

Extend the fake SDK client test so it records the emitted event types and phases:

```python
events = [event async for event in runtime.run(request, asyncio.Event())]
phases = [event.payload["phase"] for event in events if event.type == "turn.progress"]
assert phases == [
    "connecting_runtime",
    "connecting_mcp",
    "mcp_ready",
    "waiting_model",
    "generating",
    "finalizing",
]
```

- [ ] **Step 5: Run the runtime phase test and verify RED**

Run: `.venv/bin/pytest -q tests/test_claude_runtime.py -k progress`

Expected: FAIL because no `turn.progress` events exist.

- [ ] **Step 6: Implement deterministic progress emission**

Add a helper in `app/runtime/events.py`, import it from `app/runtime/claude.py`, and emit phases before or after proven boundaries:

```python
def progress_event(phase: str, message: str) -> RuntimeEvent:
    return RuntimeEvent(
        "turn.progress",
        {
            "phase": phase,
            "message": message,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        "system",
    )
```

`ClaudeAgentRuntime.run()` must yield `connecting_runtime` before `client.connect()`, `connecting_mcp` before readiness waiting, `mcp_ready` after readiness, `waiting_model` after `query()`, `generating` on the first text delta only, and `finalizing` on `ResultMessage` before yielding the result events.

Update `FakeAgentRuntime` to emit representative `waiting_model`, `generating`, and `finalizing` phases so browser tests exercise the contract without using model quota.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_runtime_events.py tests/test_claude_runtime.py`

Expected: PASS with no thinking content present in persisted payload assertions.

- [ ] **Step 8: Commit Task 1**

```bash
git add app/runtime/claude.py app/runtime/events.py app/runtime/fake.py tests/test_runtime_events.py tests/test_claude_runtime.py
git commit -m "feat: emit observable runtime progress"
```

### Task 2: Persist progress and recover interrupted Turns

**Files:**
- Modify: `app/turns/service.py:170-360`
- Modify: `app/turns/sse.py`
- Modify: `app/db/base.py:56-89`
- Test: `tests/test_turns.py`
- Test: `tests/test_database.py`
- Test: `tests/test_sse.py`

**Interfaces:**
- Consumes: `RuntimeEvent("turn.progress", payload, "system")` from Task 1.
- Produces: persisted `turn.progress` records in normal sequence order.
- Produces: terminal `turn.interrupted` records with `code`, `message`, and `interrupted_at`.

- [ ] **Step 1: Write failing Turn persistence assertions**

Update the successful Turn test to assert progress events are persisted in order and precede completion:

```python
event_types = [event.event_type for event in events]
assert "turn.progress" in event_types
assert event_types.index("turn.progress") < event_types.index("turn.completed")
```

Add `preparing` from `TurnService` immediately after `turn.started`, using a normal `RuntimeEvent` so all runtime implementations get an initial visible state.

- [ ] **Step 2: Run the Turn test and verify RED**

Run: `.venv/bin/pytest -q tests/test_turns.py -k successful_turn`

Expected: FAIL because the service does not yet append `preparing`.

- [ ] **Step 3: Implement the initial persisted phase**

After `_mark_running()` and before `_runtime_request()`, append:

```python
await self._append_event(
    turn_id,
    RuntimeEvent(
        "turn.progress",
        {
            "phase": "preparing",
            "message": "正在准备 Session 环境",
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        "system",
    ),
)
```

- [ ] **Step 4: Write failing restart recovery test**

Extend `test_initialize_interrupts_stale_turns`:

```python
messages = list(
    (await db.scalars(select(MessageRecord).where(MessageRecord.turn_id == "turn-1"))).all()
)
assert len(messages) == 1
assert messages[0].sequence == 1
assert messages[0].event_type == "turn.interrupted"
payload = json.loads(messages[0].payload_json)
assert payload["code"] == "service_restarted"
```

Also cover a stale Turn that already has messages and assert the interruption uses `max(sequence) + 1`.

- [ ] **Step 5: Run recovery tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_database.py -k interrupts_stale_turns`

Expected: FAIL because startup recovery updates rows but does not insert a message.

- [ ] **Step 6: Insert one terminal recovery event per stale Turn**

In `Database.interrupt_stale_turns()`, select the stale `TurnRecord` rows before updating. For each row, query the maximum sequence and add:

```python
MessageRecord(
    id=str(uuid.uuid4()),
    session_id=turn.session_id,
    turn_id=turn.id,
    sequence=max_sequence + 1,
    event_type="turn.interrupted",
    role="system",
    payload_json=json.dumps(
        {
            "code": "service_restarted",
            "message": "服务在该 Turn 执行期间退出，任务已中断。",
            "interrupted_at": now.isoformat(),
        },
        ensure_ascii=False,
    ),
    created_at=now,
)
```

- [ ] **Step 7: Make interruption terminal in SSE and add replay test**

Add `turn.interrupted` to `TERMINAL_EVENTS` in `app/turns/sse.py`. Add a test whose persisted event sequence ends with `turn.interrupted` and assert `stream_turn_events()` returns without heartbeats after that event.

- [ ] **Step 8: Run Task 2 tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_turns.py tests/test_database.py tests/test_sse.py`

Expected: PASS; restart recovery leaves no queued/running Turn and history contains a terminal explanation.

- [ ] **Step 9: Commit Task 2**

```bash
git add app/turns/service.py app/turns/sse.py app/db/base.py tests/test_turns.py tests/test_database.py tests/test_sse.py
git commit -m "feat: recover interrupted turns visibly"
```

### Task 3: Page execution panel, heartbeat, and disconnect handling

**Files:**
- Modify: `app/web/templates/index.html:16-85`
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/app.css`
- Modify: `tests/test_web_page.py`
- Modify: `tests/browser/test_workbench.py`

**Interfaces:**
- Consumes: `turn.progress`, `tool.started`, `tool.completed`, `turn.interrupted`, and non-persisted `heartbeat` SSE events.
- Produces: execution panel DOM under `#executionPanel` and dynamic `#serviceStatus` values `就绪`, `重连中`, or `已断开`.
- Preserves: `<details>` tool cards are closed unless the user opens them.

- [ ] **Step 1: Write failing HTML and browser assertions**

Add required DOM IDs to `test_workbench_page_contains_required_accessible_controls`:

```python
for element_id in (
    "executionPanel",
    "executionPhase",
    "executionElapsed",
    "executionHeartbeat",
    "executionConnection",
):
    assert f'id="{element_id}"' in html
```

Extend the browser workflow:

```python
await page.locator("#messageInput").fill("First request")
await page.locator("#sendButton").click()
await expect(page.locator("#executionPanel")).to_be_visible()
await expect(page.locator("#executionPhase")).to_contain_text("模型")
await expect(page.locator("#serviceStatus")).to_contain_text("就绪")
```

- [ ] **Step 2: Run page tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_web_page.py tests/browser/test_workbench.py`

Expected: FAIL because the execution panel does not exist.

- [ ] **Step 3: Add the execution panel markup and styling**

Insert the panel between the message timeline and composer:

```html
<section id="executionPanel" class="execution-panel" aria-live="polite" hidden>
  <div class="execution-summary">
    <strong id="executionPhase">准备执行</strong>
    <span id="executionElapsed">0 秒</span>
  </div>
  <div class="execution-meta">
    <span id="executionConnection">已连接</span>
    <span id="executionHeartbeat">等待心跳</span>
  </div>
  <div id="executionSteps" class="execution-steps"></div>
</section>
```

Style it as a compact bordered panel that remains above the composer on desktop and mobile. Reuse existing green/amber/red variables and include text labels.

- [ ] **Step 4: Implement progress state and rendering**

Extend `elements` and `state` with panel references, timestamps, a one-second elapsed timer, and service connection state. Handle `turn.progress` by updating the current phase and appending one deduplicated step. Handle heartbeat separately:

```javascript
function handleHeartbeat() {
  const now = Date.now();
  state.lastTransportActivityAt = now;
  setServiceState("ready");
  updateExecutionPanel();
}

function renderProgress(payload) {
  state.lastBusinessActivityAt = Date.now();
  elements.executionPanel.hidden = false;
  elements.executionPhase.textContent = payload.message || "正在执行";
  appendExecutionStep(payload.phase, payload.message, payload.occurred_at);
}
```

If `Date.now() - lastBusinessActivityAt >= 30_000` while running and transport activity remains fresh, render “仍在运行，等待模型响应”. Do not append one history row per timer tick.

- [ ] **Step 5: Add truthful SSE error reconciliation**

Register `source.onerror` and make exactly one Turn lookup per disconnect episode:

```javascript
source.onerror = async () => {
  if (state.eventSource !== source || state.disconnectEpisodeActive) return;
  state.disconnectEpisodeActive = true;
  state.connectionCheckPending = true;
  setServiceState("reconnecting");
  try {
    const turn = await api(`/api/turns/${turnId}`);
    if (["completed", "failed", "cancelled", "interrupted"].includes(turn.status)) {
      finishTurn(turn.status === "failed" ? "error" : turn.status);
    }
  } catch (_) {
    setServiceState("disconnected");
  } finally {
    state.connectionCheckPending = false;
  }
};
```

Reset `disconnectEpisodeActive` only after `EventSource.onopen`, heartbeat, or another business event proves the stream recovered. Change the five-second refresh catch path to set `已断开` rather than silently swallowing the error. When health/session refresh succeeds again, reload the selected Session so startup recovery can render `turn.interrupted` and re-enable the composer.

- [ ] **Step 6: Render terminal interruption and tool duration**

Add `turn.interrupted` to event types and terminal handling. Keep tool `<details>` closed by omitting the `open` attribute. Add a duration span to its summary:

```javascript
const duration = details.querySelector(".tool-duration");
duration.textContent = payload.duration_ms == null ? "" : formatDuration(payload.duration_ms);
```

Tool input/output remains inside `.tool-content`; errors change color without opening the element.

- [ ] **Step 7: Add a browser disconnect test**

Use Playwright route interception to abort both SSE status reconciliation and session refresh after a Turn begins. Assert `#serviceStatus` contains `已断开`, `#executionConnection` contains `已断开`, and the UI no longer claims the transport is active. Restore routes and assert a recovered `interrupted` Session becomes selectable after application restart coverage at the API layer.

- [ ] **Step 8: Run Task 3 tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_web_page.py tests/browser/test_workbench.py`

Expected: PASS in desktop and mobile viewport assertions; tool details are collapsed by default.

- [ ] **Step 9: Commit Task 3 without staging unrelated MCP files**

```bash
git add app/web/templates/index.html app/web/static/app.js app/web/static/app.css tests/test_web_page.py tests/browser/test_workbench.py
git commit -m "feat: show live turn execution status"
```

### Task 4: Full regression and live acceptance

**Files:**
- Verify only; modify earlier files only if a failing test reveals a requirement gap.

**Interfaces:**
- Consumes: all event, recovery, and UI contracts from Tasks 1-3.
- Produces: evidence that the feature works without exposing thinking or breaking existing MCP behavior.

- [ ] **Step 1: Run the complete automated suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass with the existing intentional live-test skip only.

- [ ] **Step 2: Check formatting and repository scope**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; pre-existing Hive MCP files remain preserved and are not accidentally included in observability commits.

- [ ] **Step 3: Start the app from a long-lived user terminal**

Run with the existing environment plus:

```bash
export AHS_HIVE_QUERY_MCP_ENTRYPOINT=/Users/a110356/work/code/luxury_data/hive_query_mcp_server/server.py
uv run workspace-agent
```

Expected: `/api/health` returns `status=ok`, the example Workspace remains available with five MCP servers, and startup marks the previously stale Turn as interrupted.

- [ ] **Step 4: Verify a real fresh Session without executing Hive SQL**

Create a new Session and request `hive_connection_info`. Confirm:

- progress phases appear before the tool call;
- the Hive tool card remains collapsed;
- expanding it shows the safe connection payload;
- tool duration is visible;
- the final Turn becomes idle;
- no `thinking` event or private reasoning text exists in `/api/sessions/{id}/messages`.

- [ ] **Step 5: Verify backend disconnect behavior**

During a harmless delayed Turn, stop the service process. Confirm the page changes from `就绪` to `已断开`, shows the last heartbeat, and does not continue an indefinite running animation. Restart from the long-lived terminal and confirm the Turn becomes `interrupted` with a visible explanation.

- [ ] **Step 6: Final scope review**

Run:

```bash
git log --oneline -5
git status --short
```

Expected: observability commits are present; user-owned/pre-existing MCP edits remain identifiable and no credentials or raw thinking content are staged.
