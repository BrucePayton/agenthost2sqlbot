import asyncio
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from playwright.async_api import Page, async_playwright, expect

from tests.test_workspaces import write_workspace

pytestmark = pytest.mark.skip(
    reason="retired legacy Davinci V1 Mock; Phase 1 is gated by Native V2"
)


@dataclass(frozen=True)
class LiveApp:
    url: str
    services: Any


@asynccontextmanager
async def _serve_app(app, health_path: str) -> AsyncIterator[LiveApp]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    task = asyncio.create_task(server.serve())
    url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(trust_env=False) as client:
        for _ in range(150):
            try:
                if (await client.get(f"{url}{health_path}")).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("Test server did not start")
    try:
        yield LiveApp(url, app.state.services if hasattr(app.state, "services") else None)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


class BridgeAwareFakeRuntime:
    def __init__(self, registry) -> None:
        self.registry = registry

    @property
    def capabilities(self):
        from app.runtime.base import RuntimeCapabilities

        return RuntimeCapabilities(
            protocol_version="1",
            supports_resume=True,
            supports_interrupt=True,
            supports_auto_memory=True,
            supports_mcp=True,
            supports_skills=True,
        )

    async def run(self, request, cancel_event):
        from app.agui.bridge import FrontendToolBridgeError, canonical_json
        from app.agui.claude_tools import sdk_qualified_name
        from app.runtime.base import RuntimeEvent, RuntimeResult

        bridge = self.registry.active_for_thread(request.platform_session_id)
        assert bridge is not None
        if "帮我解读仪表盘" in request.text:
            tool_requests = [
                ("navigateTo", {"destination": "dashboard"}),
                ("dashboard.capture_current_view", {}),
            ]
        elif "数据集" in request.text:
            tool_requests = [("navigateTo", {"destination": "datasets"})]
        else:
            tool_requests = [("dashboard.capture_current_view", {})]

        text = ""
        for index, (public_name, arguments) in enumerate(tool_requests):
            tool_call_id = f"fake-{bridge.run_id}-{index}"
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
            try:
                result = await bridge.claim_and_wait(public_name, arguments)
            except FrontendToolBridgeError as exc:
                if exc.code == "IFRAME_CLOSED":
                    from app.runtime.base import RuntimeCancelled

                    raise RuntimeCancelled() from exc
                text = f"前端工具失败：{exc.code}"
                break
            yield RuntimeEvent(
                "tool.completed",
                {"tool_use_id": tool_call_id, "name": public_name, "is_error": False},
                "tool",
            )
            payload = json.loads(result.content)
            if public_name == "navigateTo":
                text = (
                    f"已打开数据集页面：{payload['path']}"
                    if payload["path"] == "/datasets"
                    else f"已打开页面：{payload['path']}"
                )
            else:
                metrics = payload["metrics"]
                text = (
                    f"当前仪表盘评估物品量为 {metrics['itemCount']:,}，"
                    f"周环比 {metrics['weeklyChangePct']}%，"
                    f"出价金额 {metrics['bidAmount']:,}。"
                )
        yield RuntimeEvent("message.assistant.delta", {"text": text}, "assistant")
        yield RuntimeEvent(
            "message.assistant.completed", {"text": text}, "assistant"
        )
        yield RuntimeEvent(
            "usage.updated", {"input_tokens": 10, "output_tokens": 20}, "system"
        )
        yield RuntimeResult(
            status="completed",
            claude_session_id=request.claude_session_id
            or f"fake-{request.platform_session_id}",
        ).to_event()


@asynccontextmanager
async def _live_slice(
    settings_factory,
    *,
    tool_timeout_seconds: float = 5,
) -> AsyncIterator[tuple[LiveApp, LiveApp]]:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.main import create_app
    from demo.davinci_mock.app import create_mock_app

    settings = settings_factory(anthropic_api_key="top-secret-davinci-gate-key")
    write_workspace(settings.workspaces_root, "actual")
    registry = FrontendToolBridgeRegistry(
        tool_timeout_seconds=tool_timeout_seconds
    )
    agent = create_app(
        settings=settings,
        runtime=BridgeAwareFakeRuntime(registry),
        frontend_tool_bridges=registry,
    )
    async with _serve_app(agent, "/api/health") as agent_live:
        mock = create_mock_app(agent_live.url)
        async with _serve_app(mock, "/health") as mock_live:
            yield agent_live, mock_live


async def _open_session(
    page: Page,
    mock_url: str,
    path: str = "/dashboard/1024",
):
    await page.goto(f"{mock_url}{path}")
    await page.locator("#agentLauncher").click()
    frame = page.frame_locator("#agentFrame")
    await frame.locator("#agentNewSessionButton").click()
    await expect(frame.locator("#agentStatus")).to_contain_text("Session")
    return frame


@pytest.mark.asyncio
async def test_dataset_page_navigates_back_and_captures_in_same_run(
    settings_factory,
) -> None:
    async with _live_slice(settings_factory) as (_agent, mock), async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        frame = await _open_session(page, mock.url, "/datasets")
        await page.locator("#agentFrame").evaluate(
            "element => { element.dataset.instanceToken = 'stable-reverse-frame'; }"
        )
        session_id = await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        )

        await frame.locator("#agentMessageInput").fill("帮我解读仪表盘")
        await frame.locator("#agentSendButton").click()

        # 前端工具会把这次提问切成多个 Turn，但用户只该看到一条计时条。
        await expect(frame.locator(".turn-activity")).to_have_count(1)
        await frame.locator(".turn-activity .ta-bar").first.click()
        tools = frame.locator(".turn-activity .ti[data-tool-use-id] .name")
        await expect(tools).to_have_count(2)
        assert await tools.all_inner_texts() == [
            "navigateTo",
            "dashboard.capture_current_view",
        ]
        await expect(page).to_have_url(f"{mock.url}/dashboard/1024")
        answer = frame.locator(".agent-message.assistant").last
        await expect(answer).to_contain_text("4,734")
        await expect(answer).to_contain_text("-10.88%")
        await expect(answer).to_contain_text("40,388,380")
        assert await page.locator("#agentFrame").get_attribute(
            "data-instance-token"
        ) == "stable-reverse-frame"
        assert await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        ) == session_id
        await browser.close()


@pytest.mark.asyncio
async def test_dashboard_capture_navigation_and_session_continuity(
    settings_factory,
) -> None:
    async with _live_slice(settings_factory) as (agent, mock), async_playwright() as pw:
        assert agent.url != mock.url
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        request_bodies: list[str] = []
        console_messages: list[str] = []
        page.on("console", lambda message: console_messages.append(message.text))
        page.on(
            "request",
            lambda request: request_bodies.append(request.post_data or "")
            if request.url.endswith("/api/ag-ui")
            else None,
        )
        frame = await _open_session(page, mock.url)
        await page.locator("#agentFrame").evaluate(
            "element => { element.dataset.instanceToken = 'stable-frame'; }"
        )
        session_id = await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        )

        await frame.locator("#agentMessageInput").fill("解读当前仪表盘")
        await frame.locator("#agentSendButton").click()
        await expect(frame.locator(".turn-activity")).to_contain_text(
            "dashboard.capture_current_view"
        )
        answer = frame.locator(".agent-message.assistant").last
        await expect(answer).to_contain_text("4,734")
        await expect(answer).to_contain_text("-10.88%")
        await expect(answer).to_contain_text("40,388,380")

        await frame.locator("#agentMessageInput").fill("打开数据集页面")
        await frame.locator("#agentSendButton").click()
        await expect(page).to_have_url(f"{mock.url}/datasets")
        await expect(frame.locator(".agent-message.assistant").last).to_contain_text(
            "已打开数据集页面"
        )
        assert await page.locator("#agentFrame").get_attribute("data-instance-token") == "stable-frame"
        assert await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        ) == session_id

        await frame.locator("#agentMessageInput").fill("保持数据集页面")
        await frame.locator("#agentSendButton").click()
        await expect(frame.locator(".agent-message.assistant").last).to_contain_text(
            "已打开数据集页面"
        )

        await page.locator('[data-route="/dashboard/1024"]').click()
        await expect(page).to_have_url(f"{mock.url}/dashboard/1024")
        await expect(frame.locator(".agent-message.assistant")).to_have_count(3)

        agui_inputs = [json.loads(body) for body in request_bodies if body]
        initial_inputs = [body for body in agui_inputs if body["messages"][-1]["role"] == "user"]
        assert [tool["name"] for tool in initial_inputs[0]["tools"]] == [
            "dashboard.capture_current_view",
            "navigateTo",
        ]
        assert [tool["name"] for tool in initial_inputs[2]["tools"]] == [
            "dashboard.capture_current_view",
            "navigateTo",
        ]
        assert "top-secret-davinci-gate-key" not in json.dumps(request_bodies)
        assert "top-secret-davinci-gate-key" not in "\n".join(console_messages)
        assert "top-secret-davinci-gate-key" not in await page.content()
        assert "top-secret-davinci-gate-key" not in await frame.locator(
            "html"
        ).inner_html()
        await browser.close()


@pytest.mark.asyncio
async def test_forged_parent_message_is_ignored(settings_factory) -> None:
    async with _live_slice(settings_factory) as (_agent, mock), async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await _open_session(page, mock.url)
        before = await page.evaluate("() => location.pathname")

        await page.evaluate(
            """() => window.postMessage({
              protocol: 'davinci-agent-host', protocolVersion: '1',
              messageType: 'UI_COMMAND', messageId: crypto.randomUUID(),
              requestId: crypto.randomUUID(), toolCallId: 'forged',
              nonce: document.querySelector('#davinciApp').dataset.nonce,
              issuedAt: Date.now(), expiresAt: Date.now() + 15000,
              contextVersion: window.__davinciMock.store.state.contextVersion,
              payload: {destination: 'datasets'}
            }, location.origin)"""
        )
        await page.wait_for_timeout(100)

        assert await page.evaluate("() => location.pathname") == before
        assert await page.evaluate(
            "() => window.__davinciMock.store.state.contextVersion"
        ) == 1
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control", "timeout", "expected_code"),
    [
        ("mutateBeforeCapture", 5, "CONTEXT_STALE"),
        ("delayToolResultMs", 0.05, "TOOL_TIMEOUT"),
    ],
)
async def test_stale_and_timeout_failures_are_visible_and_cleaned_up(
    settings_factory,
    control: str,
    timeout: float,
    expected_code: str,
) -> None:
    async with _live_slice(
        settings_factory, tool_timeout_seconds=timeout
    ) as (agent, mock), async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        frame = await _open_session(page, mock.url)
        if control == "mutateBeforeCapture":
            await page.evaluate(
                "() => { window.__davinciMock.controls.mutateBeforeCapture = true; }"
            )
        else:
            await page.evaluate(
                "() => { window.__davinciMock.controls.delayToolResultMs = 250; }"
            )

        await frame.locator("#agentMessageInput").fill("解读当前仪表盘")
        await frame.locator("#agentSendButton").click()
        await expect(frame.locator(".agent-message.assistant").last).to_contain_text(
            expected_code,
            timeout=5_000,
        )
        assert agent.services.frontend_tool_bridges.pending_count == 0
        assert agent.services.frontend_tool_bridges.active_run_count == 0
        await browser.close()


@pytest.mark.asyncio
async def test_removing_waiting_iframe_cancels_run_and_bridge(settings_factory) -> None:
    async with _live_slice(settings_factory) as (agent, mock), async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        run_ids: list[str] = []

        def capture_run(request) -> None:
            if not request.url.endswith("/api/ag-ui") or not request.post_data:
                return
            body = json.loads(request.post_data)
            if body["messages"][-1]["role"] == "user":
                run_ids.append(body["runId"])

        page.on("request", capture_run)
        frame = await _open_session(page, mock.url)
        await page.evaluate(
            "() => { window.__davinciMock.controls.delayToolResultMs = 1000; }"
        )
        await frame.locator("#agentMessageInput").fill("解读当前仪表盘")
        await frame.locator("#agentSendButton").click()
        await expect(frame.locator(".turn-activity")).to_contain_text(
            "dashboard.capture_current_view"
        )

        await page.locator("#agentFrame").evaluate("element => element.remove()")
        turn = None
        for _ in range(150):
            if run_ids:
                turn = await agent.services.turns.get(run_ids[0])
            if (
                agent.services.frontend_tool_bridges.active_run_count == 0
                and turn is not None
                and turn.status in {"cancelled", "interrupted"}
            ):
                break
            await asyncio.sleep(0.02)

        assert run_ids
        assert agent.services.frontend_tool_bridges.pending_count == 0
        assert agent.services.frontend_tool_bridges.active_run_count == 0
        assert turn is not None
        assert turn.status in {"cancelled", "interrupted"}
        await browser.close()
