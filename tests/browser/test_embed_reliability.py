"""Exercise the shipped embed bundle with a real iframe and deterministic HTTP failures."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright, expect

from app.agui.contracts import load_contract_registry

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = {
    "profileId": "dashboard",
    "catalogDigest": "catalog",
    "toolSetId": "tools",
    "tools": [],
    "state": {
        "schemaVersion": "davinci-page-state-v1",
        "page": {
            "instanceId": "page-1",
            "kind": "dashboard",
            "route": "/x",
            "resource": {"type": "dashboard", "id": "88"},
        },
        "permissions": {"canRead": True, "canOperate": True, "canPersist": True},
        "ui": {"busy": False, "activeFilters": []},
        "revisions": {"routeRevision": 1, "resourceRevision": 0, "dataRevision": 0},
        "dataStatus": {"loadingWidgetIds": [], "errorWidgetIds": []},
    },
}
PARENT = """<iframe src="http://agent.test/embed" style="width:600px;height:850px"></iframe>
<script>
window.reauthCount = 0; window.businessWrites = 0; window.receipts = {}; window.panelCommands = [];
window.sendPanelState = launcher => document.querySelector('iframe').contentWindow.postMessage(
  {...window.lastHello, type:'DAVINCI_AGENT_PANEL_STATE', bridgeNonce:'test-nonce', pageInstanceId:'page-1', launcher}, 'http://agent.test');
addEventListener('message', e => {
 const m = e.data;
 if (e.origin !== 'http://agent.test') return;
 const send = value => e.source.postMessage({...m, bridgeNonce:'test-nonce', pageInstanceId:'page-1', ...value}, e.origin);
 if (m.type === 'DAVINCI_AGENT_BRIDGE_HELLO') { window.lastHello = m; send({type:'DAVINCI_AGENT_BRIDGE_READY',receiptLookup:true,panelCommands:['resize','close','reauthenticate','drag']}); }
 if (m.type === 'DAVINCI_AGENT_PANEL_COMMAND') window.panelCommands.push(m.panel);
 if (m.type === 'DAVINCI_AGENT_UI_COMMAND') {
   window.businessWrites++;
   const result = {status:'success',data:{persisted:true,widgetId:'fixture-created'},issues:[]};
   window.receipts[m.command.toolCallId] = result;
   send({type:'DAVINCI_AGENT_UI_ACK',toolCallId:m.command.toolCallId,result});
 }
 if (m.type === 'DAVINCI_AGENT_RECEIPT_REQUEST') {
   const result = window.receipts[m.toolCallId];
   send({type:'DAVINCI_AGENT_RECEIPT_RESPONSE',requestId:m.requestId,receipt:result ? {state:'completed',result} : {state:'missing'}});
 }
 if (m.type === 'DAVINCI_AGENT_CONTEXT_REQUEST') send({type:'DAVINCI_AGENT_CONTEXT_RESPONSE',runtimeContext:__RUNTIME_CONTEXT__});
 if (m.type === 'DAVINCI_AGENT_PANEL_COMMAND' && m.panel.action === 'reauthenticate') {
   window.reauthCount++; document.querySelector('iframe').src = 'http://agent.test/embed?refreshed=1';
 }
});
</script>""".replace("__RUNTIME_CONTEXT__", json.dumps(CONTEXT))


@pytest.fixture
async def embed_page():
    """Serve only local fixtures and the built bundle; no upstream or model calls."""
    state = {
        "feedback": [],
        "writes": [],
        "runs": [],
        "mode": "ok",
        "turn": "completed",
        "reply": False,
        "reauth": False,
        "acceptedRuns": [],
        "preferences": [],
        "panelSize": None,
    }
    template = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates")
    ).get_template("embed.html")
    async with async_playwright() as playwright:
        # Local validation can reuse an installed browser without downloading
        # or upgrading dependencies. All browser assertions remain unchanged.
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        browser = await playwright.chromium.launch(
            **({"executable_path": executable} if executable else {})
        )
        page = await browser.new_page(viewport={"width": 1000, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route(request_route):
            """Intercept APIs at transport level while the real client renders and retries."""
            request = request_route.request
            path = urlparse(request.url).path
            if request.url.startswith("http://davinci.test"):
                if state.get("parentBundle"):
                    if path == "/launcher-test.js":
                        await request_route.fulfill(path=state["parentBundle"], content_type="text/javascript")
                    elif path == "/agent-api/session/bootstrap":
                        # Read the actual Host source; echoing the parent's digest
                        # would hide incompatible paired checkouts from this gate.
                        registry = load_contract_registry(ROOT / "contracts/davinci-agent-v2.json")
                        contract = {
                            "protocolVersion": registry.raw_contract["protocolVersion"],
                            "contractVersion": registry.raw_contract["contractVersion"],
                            "contractDigest": hashlib.sha256(
                                (ROOT / "contracts/davinci-agent-v2.json").read_bytes()
                            ).hexdigest(),
                        }
                        await request_route.fulfill(json={
                            **contract, "enabled": True,
                            "embedUrl": "http://agent.test/embed", "bootstrapCode": "fixture-code",
                            "expiresAt": "2099-01-01T00:00:00Z", "launcher": {"x": 0.2, "y": 0.5},
                        })
                    else:
                        await request_route.fulfill(content_type="text/html", body=(
                            '<style>*{box-sizing:border-box}</style><div id="root"></div><script>window.__runtimeContext='
                            + json.dumps(CONTEXT) + ';</script><script src="/launcher-test.js"></script>'
                        ))
                    return
                await request_route.fulfill(content_type="text/html", body=PARENT)
                return
            if path == "/embed":
                state["reauth"] = "refreshed" in request.url
                body = template.render(
                    app_name="Test",
                    asset_revision="test",
                    embed_config={
                        "protocolVersion": "agui-native-v2",
                        "parentOrigin": "http://davinci.test",
                        "obId": "fixture-user",
                        "sessionToken": "fixture-token",
                        **({"panelSize": state["panelSize"]} if state["panelSize"] else {}),
                    },
                )
                await request_route.fulfill(content_type="text/html", body=body)
                return
            if path.startswith("/static/"):
                await request_route.fulfill(path=ROOT / "app/web" / path.lstrip("/"))
                return
            payload = {}
            if path == "/api/workspaces":
                payload = [
                    {
                        "id": "actual",
                        "available": True,
                        "kind": "team",
                        "skill_count": 0,
                    }
                ]
            elif path == "/api/workspaces/actual/sessions":
                payload = (
                    {"id": "session-1"}
                    if request.method == "POST"
                    else [{"id": "session-1", "title": "测试", "status": "idle"}]
                )
            elif path.endswith("/frontendToolRecovery"):
                payload = {"calls": []}
                if state.get("deferredRun") and not state.get("recovered"):
                    payload["calls"] = [{"toolCallId": "create-1", "originRunId": state["deferredRun"],
                        "continuationRunId": None, "continuationStatus": None,
                        "toolName": "dashboard.apply_widget_spec", "arguments": {}, "page": CONTEXT["state"]["page"]}]
            elif path.endswith("/messages"):
                if state.get("deferredRun") and not state.get("recovered"):
                    run = state["deferredRun"]
                    await request_route.fulfill(json=[
                        {"id": "request", "turn_id": run, "event_type": "message.user", "payload": {"text": "测试请求"}},
                        {"id": "deferred", "turn_id": run, "event_type": "frontend_tool.deferred",
                         "payload": {"tool_use_id": "create-1", "name": "dashboard.apply_widget_spec", "arguments": {}}},
                        {"id": "segment-end", "turn_id": run, "event_type": "turn.completed", "payload": {}}
                    ])
                    return
                if "history" in state:
                    await request_route.fulfill(json=state["history"])
                    return
                payload = []
                if state["acceptedRuns"]:
                    run = state["acceptedRuns"][-1]["runId"]
                    payload = [
                        {
                            "id": "user-1",
                            "turn_id": run,
                            "event_type": "message.user",
                            "role": "user",
                            "payload": {"text": "测试请求"},
                            "created_at": "2026-09-05T00:00:00Z",
                        },
                        {
                            "id": "start-1",
                            "turn_id": run,
                            "event_type": "turn.started",
                            "payload": {},
                            "created_at": "2026-09-05T00:00:00Z",
                        },
                    ]
                    if state["turn"] in ("outcome_unknown", "recovery_required"):
                        payload.append(
                            {
                                "id": "unknown-1",
                                "turn_id": run,
                                "event_type": "turn." + state["turn"],
                                "payload": {},
                                "created_at": "2026-09-05T00:00:02Z",
                            }
                        )
                    if state["turn"] == "completed":
                        payload += [
                            {
                                "id": "assistant-1",
                                "turn_id": run,
                                "event_type": "message.assistant.completed",
                                "role": "assistant",
                                "payload": {"text": "已处理测试请求"},
                                "created_at": "2026-09-05T00:00:02Z",
                            },
                            {
                                "id": "end-1",
                                "turn_id": run,
                                "event_type": "turn.completed",
                                "payload": {},
                                "created_at": "2026-09-05T00:00:02Z",
                            },
                        ]
                    if state["turn"] in ("cancelled", "interrupted", "failed"):
                        payload.append({
                            "id": "stopped-1", "turn_id": run,
                            "event_type": "turn." + state["turn"], "payload": {},
                            "created_at": "2026-09-05T00:00:02Z",
                        })
            elif path == "/api/me/preferences":
                data = request.post_data_json
                state["preferences"].append(data)
                payload = {"panelSize": data.get("panelSize"), "launcher": data.get("launcher")}
            elif "/feedback" in path:
                if request.method == "PUT":
                    data = request.post_data_json
                    state["writes"].append(data)
                    if state.get("feedbackFail"):
                        await request_route.fulfill(
                            status=500, json={"error": {"message": "fixture failure"}}
                        )
                        return
                    payload = {
                        "messageId": path.split("/")[-2],
                        "taskId": "C" if path.split("/")[-2] == "history-14" else "A" if path.split("/")[-2] == "history-10" else state["acceptedRuns"][-1]["runId"] if state["acceptedRuns"] else path.split("/")[-2],
                        "updatedAt": "2026-09-05T00:00:00Z",
                        **data,
                    }
                    state["feedback"] = [row for row in state["feedback"] if row["messageId"] != payload["messageId"]]
                    if data["rating"]:
                        state["feedback"].append(payload)
                else:
                    payload = state["feedback"]
            elif path == "/api/ag-ui":
                data = request.post_data_json
                state["runs"].append(data)
                if state["mode"] == "tool401":
                    results = [message for message in data["messages"] if message["role"] == "tool"]
                    if results and not state["reauth"]:
                        await request_route.fulfill(status=401, json={"error": {"code": "identity_missing"}})
                        return
                    if results:
                        state["recovered"] = True
                    else:
                        state["deferredRun"] = data["runId"]
                        events = [
                            {"type": "RUN_STARTED", "threadId": "session-1", "runId": data["runId"]},
                            {"type": "TOOL_CALL_START", "toolCallId": "create-1", "toolCallName": "dashboard.apply_widget_spec"},
                            {"type": "TOOL_CALL_ARGS", "toolCallId": "create-1", "delta": "{}"},
                            {"type": "TOOL_CALL_END", "toolCallId": "create-1"},
                            {"type": "RUN_FINISHED", "threadId": "session-1", "runId": data["runId"]}
                        ]
                        await request_route.fulfill(content_type="text/event-stream",
                            body="".join("data: " + json.dumps(event) + "\n\n" for event in events))
                        return
                if state.get("httpError"):
                    await request_route.fulfill(
                        status=state["httpError"]["status"],
                        json={"error": state["httpError"]["error"]},
                    )
                    return
                if state.get("runtimeError"):
                    events = [
                        {"type": "RUN_STARTED", "threadId": "session-1", "runId": data["runId"]},
                        {"type": "CUSTOM", "name": "workspace.trace", "value": {
                            "event_type": "turn.started", "turn_id": data["runId"],
                            "at": "2026-09-13T10:00:00Z", "payload": {},
                        }},
                        {"type": "CUSTOM", "name": "workspace.trace", "value": {
                            "event_type": "turn.failed", "turn_id": data["runId"],
                            "at": "2026-09-13T10:00:01Z", "payload": state["runtimeError"],
                        }},
                        {"type": "RUN_ERROR", **state["runtimeError"]},
                    ]
                    await request_route.fulfill(
                        content_type="text/event-stream",
                        body="".join("data: " + json.dumps(event) + "\n\n" for event in events),
                    )
                    return
                if state["mode"] == "401" and not state["reauth"]:
                    await request_route.fulfill(
                        status=401, json={"error": {"code": "unauthorized"}}
                    )
                    return
                if state["mode"] == "403":
                    await request_route.fulfill(
                        status=403, json={"error": {"code": "forbidden"}}
                    )
                    return
                if not state.get("notAccepted"):
                    state["acceptedRuns"].append(data)
                if state["mode"] == "disconnect":
                    await request_route.abort("connectionreset")
                    return
                events = [
                    {
                        "type": "RUN_STARTED",
                        "threadId": "session-1",
                        "runId": data["runId"],
                    },
                    {
                        "type": "TEXT_MESSAGE_START",
                        "messageId": "temporary-sdk-id",
                        "role": "assistant",
                    },
                    {
                        "type": "TEXT_MESSAGE_CONTENT",
                        "messageId": "temporary-sdk-id",
                        "delta": "已处理测试请求",
                    },
                    {"type": "TEXT_MESSAGE_END", "messageId": "temporary-sdk-id"},
                    {
                        "type": "RUN_FINISHED",
                        "threadId": "session-1",
                        "runId": data["runId"],
                    },
                ]
                await request_route.fulfill(
                    content_type="text/event-stream",
                    body="".join(
                        "data: " + json.dumps(event) + "\n\n" for event in events
                    ),
                )
                return
            elif path.startswith("/api/turns/"):
                if "history" in state:
                    run = path.split("/")[-1]
                    terminal = next((
                        event["event_type"].removeprefix("turn.")
                        for event in reversed(state["history"])
                        if event["turn_id"] == run and event["event_type"].startswith("turn.")
                        and event["event_type"] != "turn.started"
                    ), "running")
                    await request_route.fulfill(json={
                        "id": run, "status": terminal, "session_id": "session-1"
                    })
                    return
                if state.get("notAccepted"):
                    await request_route.fulfill(
                        status=404, json={"error": {"message": "Turn not found"}}
                    )
                    return
                payload = {
                    "id": state["runs"][-1]["runId"],
                    "status": state["turn"],
                    "session_id": "session-1",
                }
            await request_route.fulfill(json=payload)

        await page.route("**/*", route)
        async with page.expect_response("http://agent.test/api/workspaces"):
            await page.goto("http://davinci.test")
        frame = page.frame_locator("iframe")
        await frame.locator("#agentNewSessionButton").click()
        await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
        yield page, frame, state
        assert not errors, errors
        await browser.close()


async def send(frame):
    """Submit from the actual composer."""
    await frame.locator("#agentMessageInput").fill("测试请求")
    await frame.locator("#agentSendButton").click()


@pytest.mark.asyncio
async def test_header_drag_uses_screen_coordinates_and_leaves_buttons_clickable(embed_page):
    """The real header sends one drag gesture; its controls still act as buttons."""
    page, frame, _state = embed_page
    title = await frame.locator(".assistant-title h2").bounding_box()
    x, y = title["x"] + 30, title["y"] + title["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 100, y + 5, steps=6)
    await page.mouse.up()
    await page.wait_for_function("window.panelCommands.filter(p => p.action === 'drag').at(-1)?.phase === 'end'")
    commands = await page.evaluate("window.panelCommands.filter(p => p.action === 'drag')")
    assert commands and commands[0]["phase"] == "start"
    assert commands[-1]["phase"] == "end"
    assert commands[-1]["screenX"] - commands[0]["screenX"] == 100
    assert commands[-1]["screenY"] - commands[0]["screenY"] == 5
    await frame.locator("#sizeMenuTrigger").click()
    await expect(frame.locator("#sizeMenuPopover")).to_be_visible()
    assert await page.evaluate("window.panelCommands.filter(p => p.action === 'drag').length") == len(commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset_editor_open", [False, True])
async def test_header_and_launcher_drag_together_with_real_davinci_parent(
    embed_page, tmp_path, dataset_editor_open
):
    """Exercise both shipped halves across origins, including persisted launcher updates."""
    davinci = Path(os.environ.get("DAVINCI_REPO_ROOT", ROOT.parent / "watcher_agent/davinci"))
    if not (davinci / "webapp/node_modules/less").is_dir():
        pytest.skip("paired Davinci checkout and frontend dependencies are required")
    bundle = tmp_path / "launcher-parent.js"
    build = await asyncio.create_subprocess_exec(
        "node", str(ROOT / "tests/browser/build_launcher_drag_parent.cjs"),
        str(davinci), str(bundle), cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await build.communicate()
    assert build.returncode == 0, stderr.decode()
    page, frame, state = embed_page
    state["parentBundle"] = bundle
    await page.set_viewport_size({"width": 1400, "height": 1000})
    await page.reload()
    await expect(frame.locator(".assistant-head")).to_have_attribute("data-draggable", "")
    await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
    if dataset_editor_open:
        await frame.locator("#closeAssistant").click(timeout=3000)
        await expect(page.locator("[data-agent-panel-size]")).to_be_hidden()
        # Reproduce marketplace.tsx's full-screen editor portal above the shell.
        await page.evaluate("""() => {
            const editor = document.createElement('div');
            editor.dataset.testid = 'dataset-editor-overlay';
            Object.assign(editor.style, {
                position: 'fixed', inset: '0', zIndex: '1400', background: '#fff'
            });
            document.body.append(editor);
        }""")
        # Check hit-testing, not visibility alone: a covered button is still visible.
        trigger = page.locator("[data-agent-launcher] > button")
        assert await trigger.evaluate("""button => {
            const rect = button.getBoundingClientRect();
            return button.contains(document.elementFromPoint(
                rect.x + rect.width / 2, rect.y + rect.height / 2
            ));
        }"""), "Dataset editor covers the Agent launcher"
        button = await trigger.bounding_box()
        await page.mouse.click(button["x"] + button["width"] / 2,
                               button["y"] + button["height"] / 2)
        panel = page.locator("[data-agent-panel-size]")
        await expect(panel).to_be_visible()
        # Finish the real opening transition before measuring drag distances.
        await panel.evaluate("""element => Promise.all(
            element.getAnimations().map(animation => animation.finished)
        )""")
        await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
    panel = page.locator("[data-agent-panel-size]")
    trigger = page.locator("[data-agent-launcher] > button")
    # Manual mouse coordinates must be measured after the opening transition.
    await panel.evaluate("""element => Promise.all(
        element.getAnimations().map(animation => animation.finished)
    )""")
    before_panel = await panel.bounding_box()
    before_trigger = await trigger.bounding_box()
    title = await frame.locator(".assistant-title h2").bounding_box()
    x, y = title["x"] + 50, title["y"] + title["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x + 100, y + 5, steps=12)
    await page.mouse.up()
    await page.wait_for_function("window.panelCommands.at(-1)?.phase === 'end'")
    await page.wait_for_function("""({x, y}) => {
        const rect = document.querySelector('[data-agent-panel-size]').getBoundingClientRect();
        return Math.abs(rect.x - x) <= 1 && Math.abs(rect.y - y) <= 1;
    }""", arg={"x": before_panel["x"] + 100, "y": before_panel["y"] + 5})
    after_panel = await panel.bounding_box()
    after_trigger = await trigger.bounding_box()
    assert after_panel["x"] - before_panel["x"] == pytest.approx(100, abs=1)
    assert after_panel["y"] - before_panel["y"] == pytest.approx(5, abs=1)
    assert after_trigger["x"] - before_trigger["x"] == pytest.approx(100, abs=2)
    assert after_trigger["y"] - before_trigger["y"] == pytest.approx(5, abs=2)
    # The launcher's exposed left edge remains outside the overlapping iframe.
    x, y = after_trigger["x"] + 2, after_trigger["y"] + after_trigger["height"] / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(x - 60, y - 4, steps=8)
    await page.mouse.up()
    await expect(panel).to_be_visible()
    await page.wait_for_function("""({x, y}) => {
        const rect = document.querySelector('[data-agent-panel-size]').getBoundingClientRect();
        return Math.abs(rect.x - x) <= 1 && Math.abs(rect.y - y) <= 1;
    }""", arg={"x": after_panel["x"] - 60, "y": after_panel["y"] - 4})
    moved_panel = await panel.bounding_box()
    assert moved_panel["x"] - after_panel["x"] == pytest.approx(-60, abs=1)
    assert moved_panel["y"] - after_panel["y"] == pytest.approx(-4, abs=1)
    await frame.locator("#closeAssistant").click()
    await expect(panel).to_be_hidden()
    button = await trigger.bounding_box()
    await page.mouse.click(button["x"] + button["width"] / 2,
                           button["y"] + button["height"] / 2)
    await expect(panel).to_be_visible()
    await panel.evaluate("""element => Promise.all(
        element.getAnimations().map(animation => animation.finished)
    )""")
    reopened = await panel.bounding_box()
    assert reopened["x"] == pytest.approx(moved_panel["x"], abs=1)
    assert reopened["y"] == pytest.approx(moved_panel["y"], abs=1)
    await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
    assert len([value for value in state["preferences"] if "launcher" in value]) == 2
    assert state["runs"] == []


@pytest.fixture
async def stoppable_embed(embed_page):
    """Keep one real client request pending and expose independent stop/status APIs."""
    page, frame, state = embed_page
    pending = []
    request_held = asyncio.Event()
    state.update(cancellations=[], statusReads=[])

    async def hold_first_run(route):
        """Keep the real client's first fetch pending until the user stops it."""
        data = route.request.post_data_json
        state["runs"].append(data)
        state["acceptedRuns"].append(data)
        pending.append(route)
        request_held.set()

    async def cancel_turn(route):
        """Acknowledge server cancellation independently of the aborted stream."""
        state["cancellations"].append(route.request.url)
        state["turn"] = state.get("stopResponse", "cancelled")
        await pending[0].abort("aborted")
        await route.fulfill(status=202, json={
            "id": state["runs"][0]["runId"],
            "session_id": "session-1",
            "status": state["turn"],
        })

    async def read_turn(route):
        """Model asynchronous cancellation without waiting on wall-clock timers."""
        statuses = state.get("stopStatuses", [])
        if statuses:
            state["turn"] = statuses.pop(0)
        state["statusReads"].append(state["turn"])
        await route.fulfill(json={
            "id": state["runs"][0]["runId"],
            "session_id": "session-1", "status": state["turn"],
        })

    await page.route("**/api/ag-ui", hold_first_run, times=1)
    await page.route("**/api/turns/*", read_turn)
    await page.route("**/api/turns/*/cancel", cancel_turn)
    async with page.expect_request("**/api/ag-ui"):
        await send(frame)
    await asyncio.wait_for(request_held.wait(), timeout=5)
    yield page, frame, state


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_status", ["cancelled", "interrupted", "running"])
async def test_stop_then_reopen_panel_allows_next_message(stoppable_embed, stop_status):
    """A confirmed stop releases the retained iframe and survives a full reload."""
    page, frame, state = stoppable_embed
    state["stopResponse"] = stop_status
    if stop_status == "running":
        state["stopStatuses"] = ["running", "cancelled"]
    await frame.locator("#agentStopButton").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("已停止")
    # Davinci closes the panel by hiding it; it deliberately retains the iframe.
    await page.locator("iframe").evaluate("node => { node.hidden = true; }")
    await page.locator("iframe").evaluate("node => { node.hidden = false; }")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    await expect(frame.locator("#agentCheckRun")).to_be_hidden()
    assert len(state["cancellations"]) == 1
    assert len(state["runs"]) == 1
    if stop_status == "running":
        assert state["statusReads"] == ["running", "cancelled"]
    await page.reload()
    await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    state["turn"] = "completed"
    await send(frame)
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    assert len(state["runs"]) == 2


@pytest.mark.asyncio
async def test_stop_keeps_unknown_outcome_blocked(stoppable_embed):
    """A cancellation request cannot prove the outcome of an uncertain write."""
    page, frame, state = stoppable_embed
    state["stopResponse"] = "running"
    state["stopStatuses"] = ["outcome_unknown"]
    await frame.locator("#agentStopButton").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("操作结果待核对")
    await expect(frame.locator("#agentSendButton")).to_be_disabled()
    assert state["statusReads"] == ["outcome_unknown"]
    await page.reload()
    await expect(frame.locator("#agentSendButton")).to_be_disabled()
    assert len(state["runs"]) == 1


@pytest.mark.asyncio
async def test_stop_does_not_automatically_resume_frontend_tools(stoppable_embed):
    """Reconciling an explicit stop must never send a saved tool continuation."""
    page, frame, state = stoppable_embed
    state["deferredRun"] = state["runs"][0]["runId"]
    await page.evaluate("""() => {
      window.receipts['create-1'] = {
        status: 'success', data: {persisted: true}, issues: []
      };
    }""")
    await frame.locator("#agentStopButton").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("原操作结果待核对")
    await expect(frame.locator("#agentSendButton")).to_be_disabled()
    assert len(state["runs"]) == 1
    assert await page.evaluate("window.businessWrites") == 0


@pytest.mark.asyncio
async def test_continuation_history_hides_empty_bubbles(embed_page):
    """Frontend tool continuation input is not a user-visible question."""
    page, frame, state = embed_page
    state["history"] = feedback_history()
    await page.reload()
    await expect(frame.locator(".agent-message.user")).to_have_count(2)


@pytest.mark.asyncio
async def test_subscription_progress_remains_in_chat_body_after_reload(embed_page):
    """A trusted subscription receipt is visible outside collapsed tool activity."""
    page, frame, state = embed_page
    state["history"] = [
        {"id": "sub-user", "turn_id": "sub-run", "event_type": "message.user", "payload": {"text": "每天十点预警"}},
        {"id": "sub-start", "turn_id": "sub-run", "event_type": "turn.started", "payload": {}},
        {"id": "sub-progress", "turn_id": "sub-run", "event_type": "subscription.progress", "payload": {"text": "已写入：执行时间、触发条件。"}},
        {"id": "sub-answer", "turn_id": "sub-run", "event_type": "message.assistant.completed", "payload": {"text": "还需选择仪表盘来源。"}},
        {"id": "sub-end", "turn_id": "sub-run", "event_type": "turn.completed", "payload": {}},
    ]
    await page.reload()
    messages = frame.locator(".agent-message.assistant")
    await expect(messages).to_have_count(2)
    await expect(messages.nth(0)).to_contain_text("已写入：执行时间、触发条件。")
    await expect(messages.nth(1)).to_contain_text("还需选择仪表盘来源。")


@pytest.mark.asyncio
async def test_subscription_live_stages_precede_tools_and_respect_manual_navigation(embed_page, tmp_path):
    """Real iframe navigation mounts push mode without invalidating the deferred write."""
    from app.agui.adapter import AgUiEventMapper

    davinci = Path(os.environ.get("DAVINCI_REPO_ROOT", ROOT.parent / "watcher_agent/davinci"))
    if not (davinci / "webapp/node_modules/less").is_dir():
        pytest.skip("paired Davinci checkout and frontend dependencies are required")
    bundle = tmp_path / "subscription-parent.js"
    build = await asyncio.create_subprocess_exec(
        "node", str(ROOT / "tests/browser/build_subscription_progress_parent.cjs"),
        str(davinci), str(bundle), cwd=ROOT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await build.communicate()
    assert build.returncode == 0, stderr.decode()
    page, frame, state = embed_page
    state["parentBundle"] = bundle
    state["history"] = []
    await page.set_viewport_size({"width": 1600, "height": 1000})
    await page.reload()
    await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
    await expect(page.locator("#native-step")).to_have_text("finalize")
    await expect(page.locator("#native-time")).to_have_text("09:00")
    original = await page.evaluate("window.subscriptionSnapshot()")
    assert original["revision"] == 1
    responses = []

    async def subscription_run(route):
        data = route.request.post_data_json
        state["runs"].append(data)
        state["acceptedRuns"].append(data)
        responses.append(data)
        index = len(responses)
        run_id = data["runId"]
        mapper = AgUiEventMapper("session-1", run_id, bridge=None)
        stream = []
        snapshot = await page.evaluate("window.subscriptionSnapshot()")

        def emit(kind, payload):
            state["history"].append({"id": f"event-{len(state['history'])}", "turn_id": run_id,
                                     "event_type": kind, "payload": payload})
            stream.extend(mapper.map(kind, payload))

        emit("turn.started", {})
        if index == 1:
            emit("message.assistant.completed", {"text": "我理解你希望每天10:00收到昨日成交量表格。"})
            notice = {"noticeId": f"{run_id}:subscription:time:started", "runId": run_id,
                      "taskId": snapshot["taskId"], "revision": snapshot["revision"],
                      "step": "pushMode", "phase": "started", "text": "正在配置：每天10:00执行，全部数据生成一张卡片。"}
            emit("subscription.progress", notice)
            emit("frontend_tool.deferred", {"tool_use_id": "time", "origin_run_id": run_id,
                "name": "space.message_rule.apply_draft", "arguments": {
                    "expectedRevision": snapshot["revision"],
                    "presentation": {"step": "pushMode", "nextStep": "datasets", "purpose": "每天10:00执行，全部数据生成一张卡片"},
                    "operations": [
                        {"operation": "set_push_mode", "mode": "all"},
                        {"operation": "set_schedule", "frequency": "daily", "times": ["10:00"]},
                    ],
                }})
        elif index == 2:
            assert snapshot["state"]["trigger"]["dailyTimes"] == ["10:00"]
            assert snapshot["state"]["pushMode"] == {"mode": "all"}
            assert snapshot["revision"] == original["revision"] + 1
            assert any(message.get("role") == "tool" and message.get("toolCallId") == "time"
                       for message in data["messages"])
            emit("subscription.progress", {"noticeId": f"{responses[0]['runId']}:subscription:time:completed",
                "runId": responses[0]["runId"], "phase": "completed", "text": "已设置：每天10:00执行。"})
            emit("subscription.progress", {"noticeId": f"{run_id}:subscription:lookup:started", "runId": run_id,
                "taskId": snapshot["taskId"], "revision": snapshot["revision"], "step": "datasets",
                "phase": "started", "text": "正在查找昨日成交量，以及区域和负责人所需字段。"})
            emit("frontend_tool.deferred", {"tool_use_id": "lookup", "origin_run_id": run_id,
                "name": "space.message_rule.search_options", "arguments": {
                    "kind": "dataset", "query": "昨日成交量",
                    "presentation": {"step": "datasets", "purpose": "昨日成交量、区域和负责人字段"},
                }})
        elif index == 3:
            # The next live stage must reach the parent while native manual
            # navigation prevents it from taking the user away from trigger.
            emit("subscription.progress", {"noticeId": f"{run_id}:subscription:content:started", "runId": run_id,
                "taskId": snapshot["taskId"], "revision": snapshot["revision"], "step": "content",
                "phase": "started", "text": "正在确认消息内容设置。"})
            emit("frontend_tool.deferred", {"tool_use_id": "read-content", "origin_run_id": run_id,
                "name": "space.message_rule.get_context", "arguments": {
                    "presentation": {"step": "content", "purpose": "消息内容设置"},
                }})
        else:
            assert index == 4
            emit("message.assistant.completed", {"text": "时间已设置，数据来源仍需选择，现有配置已保留。"})
        emit("turn.completed", {})
        await route.fulfill(content_type="text/event-stream", body="".join(
            "data: " + json.dumps(event.model_dump(by_alias=True, exclude_none=True, mode="json"), ensure_ascii=False) + "\n\n"
            for event in stream))

    await page.route("**/api/ag-ui", subscription_run)
    await frame.locator("#agentMessageInput").fill("每天10点给我推送昨日成交量表格")
    await frame.locator("#agentSendButton").click()
    await page.wait_for_function("typeof window.releaseApply === 'function'")
    messages = frame.locator(".agent-message.assistant")
    await expect(messages).to_contain_text([
        "我理解你希望每天10:00收到昨日成交量表格。", "正在配置：每天10:00执行，全部数据生成一张卡片。",
    ])
    await expect(page.locator("#native-step")).to_have_text("pushMode")
    await expect(page.get_by_role("region", name="数据怎么推送", exact=True)).to_be_visible()
    await expect(page.locator("#native-time")).to_have_text("09:00")
    # Stage delivery has already mounted the real node and flushed its effects;
    # neither defaults nor display normalization may alter this pending revision.
    assert await page.evaluate("window.subscriptionSnapshot()") == original
    assert await page.evaluate("window.toolEvents.filter(e=>e.phase==='completed').length") == 0
    assert await page.evaluate("window.stageEvents[0].step") == "pushMode"
    await page.evaluate("window.releaseApply()")
    await page.wait_for_function("window.lookupPending === true")
    await expect(frame.locator(".agent-message.assistant").filter(has_text="已设置：每天10:00执行。")).to_have_count(1)
    await expect(frame.locator(".agent-message.assistant").filter(has_text="正在查找昨日成交量")).to_have_count(1)
    await expect(page.locator("#native-step")).to_have_text("datasets")
    await expect(page.locator("#native-time")).to_have_text("10:00")
    assert len(responses) == 2
    await page.locator("#manual-trigger").click()
    await expect(page.locator("#native-step")).to_have_text("trigger")
    await page.evaluate("window.releaseLookup()")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    await expect(frame.locator(".agent-message.assistant").filter(has_text="数据来源仍需选择")).to_have_count(1)
    assert len(responses) == 4
    assert await page.evaluate("window.stageEvents.map(stage=>stage.step)") == ["pushMode", "datasets", "content"]
    await expect(page.locator("#native-step")).to_have_text("trigger")
    assert await page.evaluate("window.toolEvents.filter(e=>e.phase==='failed')") == []
    stage_count = await page.evaluate("window.stageEvents.length")
    # Duplicate persisted delivery must neither duplicate text nor replay navigation.
    duplicate = next(event for event in state["history"] if event["event_type"] == "subscription.progress")
    state["history"].append({**duplicate, "id": "replayed-delivery"})
    await page.locator("iframe").evaluate("node=>{node.src='http://agent.test/embed?history-reload=1'}")
    await expect(frame.locator("#agentSessionId")).to_have_text("session-1")
    await expect(frame.locator(".agent-message.assistant").filter(has_text="正在配置：每天10:00执行，全部数据生成一张卡片。")).to_have_count(1)
    await expect(frame.locator(".agent-message.assistant").filter(has_text="正在查找昨日成交量")).to_have_count(1)
    await expect(page.locator("#native-step")).to_have_text("trigger")
    assert await page.evaluate("window.stageEvents.length") == stage_count
    assert len(responses) == 4
    await page.screenshot(path="/private/tmp/subscription-live-stages-browser.png")


@pytest.mark.asyncio
async def test_tool_summary_lists_calls_and_jumps_to_skill_output(embed_page):
    """The call index opens the business summary; technical output requires consent."""
    page, frame, state = embed_page
    history = feedback_history()[:12]
    history[3]["payload"]["input_preview"] = '{}'
    history[8]["payload"]["output_preview"] = '成员查询结果'
    history.insert(-1, {
        "id": "skill-start", "turn_id": "B", "event_type": "tool.started",
        "payload": {"tool_use_id": "skill-1", "name": "Skill", "input_preview": '{"skill":"dashboard-analysis"}'},
        "created_at": "2026-09-06T00:00:10Z",
    })
    history.insert(-1, {
        "id": "skill-end", "turn_id": "B", "event_type": "tool.completed",
        "payload": {"tool_use_id": "skill-1", "output_preview": '分析技能已加载'},
        "created_at": "2026-09-06T00:00:11Z",
    })
    state["history"] = history
    await page.reload()
    trigger = frame.get_by_role("button", name="查看工具和 Skill 调用")
    await trigger.click()
    calls = frame.get_by_role("dialog", name="工具和 Skill 调用")
    await expect(calls).to_be_visible()
    await expect(calls.get_by_role("button")).to_have_count(2)
    await page.screenshot(path="/private/tmp/agent-tool-list-browser.png")
    await calls.get_by_role("button").first.press("Escape")
    await expect(calls).to_be_hidden()
    await trigger.click()
    await calls.get_by_role("button", name="加载技能", exact=False).click()
    output = frame.locator('.ti[data-tool-use-id="skill-1"] .ti-io')
    await expect(output).to_be_hidden()
    await frame.locator('.ti[data-tool-use-id="skill-1"]').get_by_role("button", name="技术详情", exact=True).click()
    await expect(output).to_be_visible()
    await expect(output).to_contain_text("Skill · dashboard-analysis")
    await expect(output).to_contain_text("分析技能已加载")
    await expect(calls).to_be_hidden()
    await page.screenshot(path="/private/tmp/agent-tool-output-browser.png")


@pytest.mark.asyncio
async def test_vote_optional_details_failure_cancel_and_refresh(embed_page):
    page, frame, state = embed_page
    await send(frame)
    vote = frame.locator('.message-feedback[data-message-id="assistant-1"]')
    await expect(vote).to_be_visible()
    await vote.get_by_role("button", name="👍 赞", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("评价已记录")
    assert state["writes"][-1] == {"rating": "up", "reasons": [], "comment": ""}
    await vote.get_by_label("结果准确", exact=True).check()
    await vote.get_by_label("展示清晰、容易理解", exact=True).check()
    await vote.get_by_label("补充说明", exact=True).fill("内容有帮助🙂")
    await vote.get_by_role("button", name="提交补充", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("补充已提交，感谢反馈")
    await expect(vote.locator(".feedback-details")).to_be_hidden()
    assert state["writes"][-1]["reasons"] == ["accurate", "clear"]
    await vote.get_by_role("button", name="补充原因", exact=True).click()
    await expect(vote.get_by_label("补充说明", exact=True)).to_have_value("内容有帮助🙂")
    await vote.get_by_role("button", name="关闭", exact=True).click()
    await page.screenshot(path="/private/tmp/agent-feedback-browser.png")
    await page.reload()
    await expect(vote.locator('[data-rating="up"]')).to_have_attribute(
        "aria-pressed", "true"
    )
    state["feedbackFail"] = True
    await vote.get_by_role("button", name="👎 踩", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("评价未保存，可重试")
    assert state["writes"][-1] == {"rating": "down", "reasons": [], "comment": ""}
    state["feedbackFail"] = False
    await vote.get_by_role("button", name="重试保存评价").click()
    await expect(vote.get_by_role("status")).to_have_text("评价已记录")
    await vote.get_by_label("补充说明", exact=True).fill("🙂" * 1001)
    before = len(state["writes"])
    await vote.get_by_role("button", name="提交补充", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("补充说明最多 1000 个字符")
    assert len(state["writes"]) == before
    await vote.get_by_role("button", name="👎 踩", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("评价已取消")
    assert state["feedback"] == []


@pytest.mark.asyncio
async def test_401_preserves_input_and_reauth_does_not_replay(embed_page):
    page, frame, state = embed_page
    state["mode"] = "401"
    await send(frame)
    await expect(frame.locator("#agentReauthenticate")).to_be_visible()
    await expect(frame.locator("#agentMessageInput")).to_have_value("测试请求")
    await frame.locator("#agentReauthenticate").click()
    await expect(frame.locator("#agentMessageInput")).to_have_value("测试请求")
    await expect(frame.locator("#agentReauthenticate")).to_be_hidden()
    assert await page.evaluate("window.reauthCount") == 1
    assert len(state["runs"]) == 1
    await expect(frame.locator("#agentSessionId")).to_have_text("session-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active_status", ["running", "waiting_for_memory", "assigned", "finalizing"]
)
async def test_disconnect_checks_existing_turn_without_resending(
    embed_page, active_status
):
    _page, frame, state = embed_page
    state.update(mode="disconnect", turn=active_status)
    await send(frame)
    await expect(frame.locator("#agentCheckRun")).to_be_visible()
    await expect(frame.locator("#agentSendButton")).to_be_disabled()
    await frame.locator("#agentCheckRun").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("仍在执行")
    state["turn"] = "completed"
    await frame.locator("#agentCheckRun").click()
    await expect(frame.locator("#agentCheckRun")).to_be_hidden()
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    await expect(frame.locator(".turn-activity")).to_contain_text("已工作 2s")
    assert len(state["runs"]) == 1


@pytest.mark.asyncio
async def test_403_does_not_request_reauthentication(embed_page):
    _page, frame, state = embed_page
    state["mode"] = "403"
    await send(frame)
    await expect(frame.locator("#agentCheckRun")).to_be_visible()
    await expect(frame.locator("#agentReauthenticate")).to_be_hidden()


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [390, 1000])
async def test_business_errors_hide_raw_details_and_fit_panel(embed_page, tmp_path, width):
    """Exercise real HTTP rejection copy without changing retry or save behavior."""
    page, frame, state = embed_page
    await page.set_viewport_size({"width": width, "height": 1000})
    await page.locator("iframe").evaluate(
        "(frame, width) => frame.style.width = `${Math.min(width - 20, 600)}px`", width
    )
    state["httpError"] = {"status": 422, "error": {
        "code": "invalid_request", "message": "SQL_TRACE retryable:false secret-token",
        "request_id": "internal-request-123", "details": {"stage": "state"},
    }}
    await send(frame)
    error = frame.locator(".agent-message.error")
    await expect(error).to_contain_text("技术支持")
    await expect(error).to_contain_text("操作时间")
    text = await error.inner_text()
    assert not any(raw in text for raw in (
        "SQL_TRACE", "retryable", "secret-token", "internal-request-123", "invalid_request",
    ))
    await expect(frame.locator("#agentMessageInput")).to_have_value("测试请求")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    assert await error.evaluate("el => el.scrollWidth <= el.clientWidth")
    assert len(state["runs"]) == 1
    assert await page.evaluate("window.businessWrites") == 0
    await page.screenshot(path=tmp_path / f"business-error-{width}.png")


@pytest.mark.asyncio
@pytest.mark.parametrize("code, reason, action", [
    ("claude_auth_failed", "服务配置", "系统管理员"),
    ("claude_rate_limited", "繁忙", "技术支持"),
])
async def test_runtime_and_activity_errors_share_business_copy(embed_page, code, reason, action):
    """Model service failures and replayable timeline entries both hide raw payloads."""
    page, frame, state = embed_page
    state["runtimeError"] = {
        "code": code, "message": "Claude proxy API key invalid: secret-token",
    }
    await send(frame)
    await expect(frame.locator(".agent-message.error")).to_contain_text(action)
    await expect(frame.locator(".agent-message.error")).to_contain_text("不要重复提交")
    await frame.locator(".turn-activity .ta-bar").click()
    await expect(frame.locator(".ti.error .name")).to_contain_text(reason)
    await expect(frame.locator(".ti.error .name")).to_contain_text(action)
    await expect(frame.locator(".ti.error .name")).to_contain_text("不要重复提交")
    for locator in [frame.locator(".agent-message.error"), frame.locator(".ti.error .name")]:
        text = await locator.inner_text()
        assert "secret-token" not in text
        assert code not in text
        assert "API key" not in text
    assert len(state["runs"]) == 1
    assert await page.evaluate("window.businessWrites") == 0


@pytest.mark.asyncio
async def test_unknown_outcome_stays_blocked_after_reload(embed_page):
    page, frame, state = embed_page
    state.update(mode="disconnect", turn="outcome_unknown")
    await send(frame)
    await expect(frame.locator("#agentCheckRun")).to_be_visible()
    await frame.locator("#agentCheckRun").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("操作结果待核对")
    await page.reload()
    await expect(frame.locator("#agentSendButton")).to_be_disabled()
    await expect(frame.locator("#agentCheckRun")).to_be_visible()
    assert len(state["runs"]) == 1


@pytest.mark.asyncio
async def test_unaccepted_request_restores_draft_without_duplicate_user_message(
    embed_page,
):
    _page, frame, state = embed_page
    state.update(mode="disconnect", notAccepted=True)
    await send(frame)
    await expect(frame.locator("#agentCheckRun")).to_be_visible()
    await frame.locator("#agentCheckRun").click()
    await expect(frame.locator("#agentMessageInput")).to_have_value("测试请求")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    await expect(frame.locator(".agent-message.user")).to_have_count(0)
    assert len(state["runs"]) == 1


def feedback_history(status="completed"):
    """Replay one task across progress messages, tool continuations and clarification."""
    rows = [
        ("A", "message.user", {"text": "建一个看板，增加指标卡和日同环比"}),
        ("A", "turn.started", {}),
        ("A", "message.assistant.completed", {"text": "先查询成员"}),
        ("A", "tool.started", {"tool_use_id": "lookup", "name": "space.members"}),
        ("A", "frontend_tool.deferred", {"tool_use_id": "lookup"}),
        ("A", "turn.completed", {}),
        ("B", "message.user", {"text": ""}),
        ("B", "turn.started", {}),
        ("B", "tool.completed", {"tool_use_id": "lookup"}),
        ("B", "message.assistant.completed", {"text": "找到成员，继续执行"}),
        ("B", "message.assistant.completed", {"text": "比较口径选哪种？1 日环比；2 日环比加周同比"}),
        ("B", "turn.completed", {}),
        ("C", "message.user", {"text": "2"}),
        ("C", "turn.started", {}),
        ("C", "message.assistant.completed", {"text": "看板和指标卡已创建"}),
    ]
    if status == "deferred":
        rows += [
            ("C", "tool.started", {"tool_use_id": "pending", "name": "space.get"}),
            ("C", "frontend_tool.deferred", {"tool_use_id": "pending"}),
            ("C", "turn.completed", {}),
        ]
    elif status != "running":
        rows += [("C", "turn." + status, {})]
    return [
        {
            "id": f"history-{index}", "turn_id": turn, "event_type": event,
            "role": "user" if event == "message.user" else "assistant",
            "payload": payload, "created_at": f"2026-09-06T00:00:{index:02d}Z",
        }
        for index, (turn, event, payload) in enumerate(rows)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "running", "deferred", "outcome_unknown"])
async def test_feedback_is_once_per_question(embed_page, status):
    """Each real question is rated once; unfinished later questions do not hide earlier feedback."""
    page, frame, state = embed_page
    state["history"] = feedback_history(status)
    await page.reload()
    votes = frame.locator(".message-feedback")
    await expect(votes).to_have_count(2 if status == "completed" else 1)
    await expect(votes.first).to_have_attribute("data-message-id", "history-10")
    if status != "completed":
        return
    votes = frame.locator('.message-feedback[data-message-id="history-14"]')
    await expect(votes.first).to_have_attribute("data-message-id", "history-14")
    await expect(votes.first.locator("..")).to_contain_text("看板和指标卡已创建")
    await votes.first.get_by_role("button", name="👍 赞", exact=True).click()
    await expect(votes.first.get_by_role("status")).to_have_text("评价已记录")
    assert state["feedback"][0]["messageId"] == "history-14"
    await page.reload()
    await expect(frame.locator(".message-feedback")).to_have_count(2)
    await expect(votes.first.get_by_role("button", name="👍 赞", exact=True)).to_have_attribute("aria-pressed", "true")


@pytest.mark.asyncio
async def test_supplement_failure_retains_draft_and_retry_confirms_submission(embed_page):
    """A failed supplement stays editable; only a successful receipt closes it."""
    _page, frame, state = embed_page
    await send(frame)
    vote = frame.locator(".message-feedback")
    await vote.get_by_role("button", name="👎 踩", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("评价已记录")
    await vote.get_by_label("处理太慢或卡住", exact=True).check()
    await vote.get_by_label("补充说明", exact=True).fill("整轮等待太久")
    state["feedbackFail"] = True
    await vote.get_by_role("button", name="提交补充", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("补充未提交，请重试")
    await expect(vote.locator(".feedback-details")).to_be_visible()
    await expect(vote.get_by_label("补充说明", exact=True)).to_have_value("整轮等待太久")
    await expect(vote.get_by_label("处理太慢或卡住", exact=True)).to_be_checked()
    state["feedbackFail"] = False
    await vote.get_by_role("button", name="重试提交补充", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("补充已提交，感谢反馈")
    await expect(vote.locator(".feedback-details")).to_be_hidden()
    assert state["writes"][-1] == {"rating": "down", "reasons": ["slow"], "comment": "整轮等待太久"}


@pytest.mark.asyncio
async def test_streamed_instruction_gets_one_vote_after_history_sync(embed_page):
    """The send/stream/history path uses the same final reply as a later reload."""
    page, frame, state = embed_page
    state["history"] = feedback_history()[:12]
    await send(frame)
    votes = frame.locator(".message-feedback")
    await expect(votes).to_have_count(1)
    await expect(votes).to_have_attribute("data-message-id", "history-10")
    await expect(frame.locator("#agentSendButton")).to_be_enabled()
    await page.reload()
    await expect(votes).to_have_count(1)
    await expect(votes).to_have_attribute("data-message-id", "history-10")


@pytest.mark.asyncio
async def test_question_votes_remain_independent_after_followup(embed_page):
    """A saved vote stays on its question when another reply arrives and after reload."""
    page, frame, state = embed_page
    state["history"] = feedback_history()[:12]
    await page.reload()
    vote = frame.locator(".message-feedback")
    await vote.get_by_role("button", name="👍 赞", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("评价已记录")
    state["history"] = feedback_history()
    await send(frame)
    await expect(vote).to_have_count(2)
    first = frame.locator('.message-feedback[data-message-id="history-10"]')
    vote = frame.locator('.message-feedback[data-message-id="history-14"]')
    await expect(first.get_by_role("button", name="👍 赞", exact=True)).to_have_attribute("aria-pressed", "true")
    await expect(vote.get_by_role("button", name="👍 赞", exact=True)).to_have_attribute("aria-pressed", "false")
    await vote.get_by_role("button", name="👎 踩", exact=True).click()
    await expect(vote.get_by_role("status")).to_have_text("评价已记录")
    assert len(state["feedback"]) == 2
    await page.reload()
    await expect(vote).to_have_count(1)
    await expect(first.get_by_role("button", name="👍 赞", exact=True)).to_have_attribute("aria-pressed", "true")
    await expect(vote.get_by_role("button", name="👎 踩", exact=True)).to_have_attribute("aria-pressed", "true")


@pytest.mark.asyncio
async def test_previous_question_can_be_rated_while_followup_runs(embed_page):
    """Starting a later question does not remove the prior completed reply's controls."""
    page, frame, state = embed_page
    state["history"] = feedback_history()[:12]
    await page.reload()
    previous = frame.locator('.message-feedback[data-message-id="history-10"]')
    await expect(previous).to_be_visible()
    state.update(mode="disconnect", turn="running")
    await send(frame)
    await expect(frame.locator("#agentCheckRun")).to_be_visible()
    await expect(previous).to_be_visible()
    await previous.get_by_role("button", name="👍 赞", exact=True).click()
    await expect(previous.get_by_role("status")).to_have_text("评价已记录")


@pytest.mark.asyncio
async def test_write_receipt_401_reauth_uses_same_run_without_reexecuting(embed_page):
    """Reload the iframe through trusted reauthentication and recover the parent's receipt."""
    page, frame, state = embed_page
    state["mode"] = "tool401"
    await send(frame)
    await expect(frame.locator("#agentReauthenticate")).to_be_visible()
    failed = state["runs"][-1]
    original_result = next(message for message in failed["messages"] if message["role"] == "tool")
    await frame.locator("#agentMessageInput").fill("下一条尚未提交的问题")
    await frame.locator("#agentReauthenticate").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("原操作结果已恢复")
    assert state["recovered"] is True
    assert await page.evaluate("window.businessWrites") == 1
    assert state["runs"][-1]["runId"] == failed["runId"]
    recovered = next(message for message in state["runs"][-1]["messages"] if message["role"] == "tool")
    assert recovered["toolCallId"] == original_result["toolCallId"]
    assert recovered["content"] == original_result["content"]
    await expect(frame.locator("#agentMessageInput")).to_have_value("下一条尚未提交的问题")
    stored = await page.frame(url="http://agent.test/embed?refreshed=1").evaluate("JSON.stringify(sessionStorage)")
    assert "fixture-created" not in stored


def _is_preferences_put(response):
    """The fixture records the body while fulfilling, so wait for the response, not the request."""
    request = response.request
    return request.method == "PUT" and request.url.endswith("/api/me/preferences")


@pytest.mark.asyncio
async def test_size_choice_is_saved_and_restored_on_the_next_load(embed_page):
    page, frame, state = embed_page
    await frame.locator("#sizeMenuTrigger").click()
    async with page.expect_response(_is_preferences_put):
        await frame.locator('[data-size-option="large"]').click()
    await expect(frame.locator("#assistantPanel")).to_have_attribute("data-size", "large")
    assert state["preferences"] == [{"panelSize": "large"}]

    # A later visit starts from the saved size and replays it to the parent without re-saving.
    state["panelSize"] = "medium"
    async with page.expect_response("http://agent.test/api/workspaces"):
        await page.goto("http://davinci.test")
    frame = page.frame_locator("iframe")
    await expect(frame.locator("#assistantPanel")).to_have_attribute("data-size", "medium")
    await expect(frame.locator("#currentSizeLabel")).to_have_text("中")
    assert await page.evaluate("window.panelCommands.at(-1)") == {"action": "resize", "size": "medium"}
    assert state["preferences"] == [{"panelSize": "large"}]


@pytest.mark.asyncio
async def test_parent_panel_state_is_saved_as_a_preference(embed_page):
    page, _frame, state = embed_page
    async with page.expect_response(_is_preferences_put):
        await page.evaluate("window.sendPanelState({x: 0.25, y: 0.75})")
    assert state["preferences"] == [{"launcher": {"x": 0.25, "y": 0.75}}]
