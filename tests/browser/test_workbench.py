import asyncio
import io
import json
import re
import socket
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from playwright.async_api import async_playwright, expect

from tests.test_workspaces import write_workspace


@dataclass(frozen=True)
class LiveApp:
    url: str
    services: Any


@asynccontextmanager
async def _serve_app(app) -> AsyncIterator[LiveApp]:
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
        for _ in range(100):
            try:
                if (await client.get(f"{url}/api/health")).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.02)
        else:
            raise RuntimeError("Test server did not start")
    try:
        yield LiveApp(url=url, services=app.state.services)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


@pytest.fixture
async def live_url(settings_factory) -> AsyncIterator[str]:
    """Compatibility wrapper for browser tests that need the legacy live URL."""
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    workspace = write_workspace(settings.workspaces_root, "actual")
    seed = workspace / "seed"
    seed.mkdir()
    (seed / "Agent.md").write_text("agent guide", encoding="utf-8")
    (seed / "数据中心 Agent 入门材料.html").write_text("guide", encoding="utf-8")
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(chunks=("hello", " world"), emit_tool=True),
    )
    async with _serve_app(app) as live_app:
        yield live_app.url


def _managed_skill_content(name: str, description: str) -> str:
    return f"""---
name: {name}
description: {description}
---
# {name}

Follow the managed Skill instructions.
"""


def _skill_archive(
    name: str,
    description: str,
    files: tuple[tuple[str, bytes], ...] = (),
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", _managed_skill_content(name, description))
        for path, content in files:
            archive.writestr(path, content)
    return buffer.getvalue()


def _artifact_payloads(live_app: LiveApp) -> list[dict[str, bytes]]:
    store = live_app.services.skill_artifacts
    maximum_size = live_app.services.settings.max_skill_bundle_size_bytes
    payloads = []
    for item in store.iter_objects():
        raw = store.read(item.artifact_key, maximum_size=maximum_size)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            payloads.append({name: archive.read(name) for name in archive.namelist()})
    return payloads


async def _create_browser_session(page) -> str:
    async with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/workspaces/[^/]+/sessions$", response.url) is not None
    ) as response_info:
        await page.locator("#newSessionButton").click()
    session = await (await response_info.value).json()
    await expect(page.locator("#sessionList .session-row.active")).to_have_attribute(
        "data-session-id", session["id"]
    )
    return session["id"]


def _skill_row(page, scope: str, name: str):
    list_id = "#globalSkillList" if scope == "global" else "#personalSkillList"
    return page.locator(f"{list_id} .skill-manager-row").filter(
        has=page.locator(
            ".skill-manager-name", has_text=re.compile(rf"^{re.escape(name)}$")
        )
    )


async def _upload_archive(page, input_id: str, name: str, raw: bytes):
    archive_input = page.locator(input_id)
    await expect(archive_input).to_be_enabled()
    async with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/(?:global-skills|skills)/import$", response.url) is not None
    ) as response_info:
        await archive_input.set_input_files(
            {
                "name": f"{name}.skill",
                "mimeType": "application/zip",
                "buffer": raw,
            }
        )
    return await response_info.value


async def _seed_enabled_skill(services, identity, name: str, description: str):
    from app.skills.bundle import build_bundle

    bundle = build_bundle(
        _managed_skill_content(name, description).encode("utf-8"),
        (),
        services.skills.limits,
    )
    return await services.skills.import_bundle(
        "personal",
        identity,
        bundle,
        enabled=True,
        origin={"type": "browser_fixture"},
    )


@pytest.fixture
async def skill_live_app(settings_factory) -> AsyncIterator[LiveApp]:
    from app.auth.provider import MockIdentityProvider
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner", "team": "admin"},
    )
    write_workspace(settings.workspaces_root, "personal", skills=())
    write_workspace(settings.workspaces_root, "team", skills=())
    identity_provider = MockIdentityProvider("manager", "manager", "Manager")
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(chunks=("hello", " world"), emit_tool=True),
        identity_provider=identity_provider,
    )
    async with _serve_app(app) as live_app:
        for index in range(77):
            name = "personal-only" if index == 0 else f"skill-{index:02d}"
            await _seed_enabled_skill(
                live_app.services,
                identity_provider.identity,
                name,
                f"Usage introduction for {name} " + ("x" * 200),
            )
        yield live_app


@pytest.fixture
async def embed_skill_live_app(settings_factory) -> AsyncIterator[LiveApp]:
    from app.auth.provider import MockIdentityProvider
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        claude_model="qwen3.8-max",
        claude_selectable_models=(
            "qwen3.8-max",
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3.7-flash",
        ),
        claude_default_effort="medium",
        davinci_local_integration=True,
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"00-davinci-team": "admin", "personal": "owner"},
    )
    write_workspace(settings.workspaces_root, "00-davinci-team", skills=())
    write_workspace(settings.workspaces_root, "personal", skills=())
    identity_provider = MockIdentityProvider("manager", "manager", "Manager")
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(chunks=("hello", " world"), emit_tool=True),
        identity_provider=identity_provider,
    )
    async with _serve_app(app) as live_app:
        yield live_app


@pytest.fixture
async def embed_activity_live_app(settings_factory) -> AsyncIterator[LiveApp]:
    from app.auth.provider import MockIdentityProvider
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        davinci_local_integration=True,
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"00-davinci-team": "admin", "personal": "owner"},
    )
    write_workspace(settings.workspaces_root, "00-davinci-team", skills=())
    write_workspace(settings.workspaces_root, "personal", skills=())
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(
            chunks=("hello", " world"), emit_tool=True, emit_thinking=True
        ),
        identity_provider=MockIdentityProvider("manager", "manager", "Manager"),
    )
    async with _serve_app(app) as live_app:
        yield live_app


async def _open_embed_local(page, live_app: LiveApp) -> None:
    """走一遍 bootstrap，把浏览器停在 /embed/local 这个面板页上。

    面板在握手完成前一直停在"等待宿主页面"，所以必须先把父页桥接打桩。
    """
    await page.add_init_script(
        """window.addEventListener("message", event => {
          const message = event.data;
          if (message?.type === "DAVINCI_AGENT_BRIDGE_HELLO") {
            window.postMessage({
              protocolVersion: message.protocolVersion,
              contractVersion: message.contractVersion,
              contractDigest: message.contractDigest,
              type: "DAVINCI_AGENT_BRIDGE_READY",
              helloId: message.helloId,
              bridgeNonce: "browser-bridge",
              pageInstanceId: "browser-page"
            }, location.origin);
          }
          if (message?.type === "DAVINCI_AGENT_CONTEXT_REQUEST") {
            window.postMessage({
              protocolVersion: message.protocolVersion,
              contractVersion: message.contractVersion,
              contractDigest: message.contractDigest,
              type: "DAVINCI_AGENT_CONTEXT_RESPONSE",
              bridgeNonce: message.bridgeNonce,
              pageInstanceId: "browser-page",
              runtimeContext: {
                profileId: "dashboard",
                catalogDigest: "browser-contract:dashboard",
                toolSetId: "browser-contract:dashboard:1",
                state: {
                  schemaVersion: "davinci-page-state-v1",
                  page: {
                    instanceId: "browser-page",
                    kind: "dashboard",
                    route: "/share/workbench-new?dashboard=88",
                    resource: {type: "dashboard", id: "88", name: "海外数据"}
                  },
                  permissions: {canRead: true, canOperate: true, canPersist: true},
                  ui: {busy: false, activeFilters: []},
                  revisions: {routeRevision: 1},
                  dataStatus: {loadingWidgetIds: [], errorWidgetIds: []}
                },
                tools: [],
                context: []
              }
            }, location.origin);
          }
        })"""
    )
    await page.goto(f"{live_app.url}/embed")
    live_app.services.settings.davinci_local_parent_origins = (live_app.url,)
    bootstrap_response = await page.request.post(
        f"{live_app.url}/agent-api/session/bootstrap",
        data={
            "parentOrigin": live_app.url,
            "protocolVersion": "agui-native-v2",
            "obId": "manager",
        },
    )
    assert bootstrap_response.status == 200
    bootstrap = await bootstrap_response.json()
    await page.evaluate(
        """formData => {
          const form = document.createElement("form");
          form.method = "post";
          form.action = "/embed/local";
          for (const [name, value] of Object.entries(formData)) {
            const input = document.createElement("input");
            input.name = name;
            input.value = value;
            form.append(input);
          }
          document.body.append(form);
          form.submit();
        }""",
        {
            "bootstrapCode": bootstrap["bootstrapCode"],
            "parentOrigin": live_app.url,
            "protocolVersion": bootstrap["protocolVersion"],
            "contractVersion": bootstrap["contractVersion"],
            "contractDigest": bootstrap["contractDigest"],
        },
    )
    await page.wait_for_url(f"{live_app.url}/embed/local")


@pytest.mark.asyncio
async def test_turn_activity_bar_exposes_thinking_and_tool_io(
    embed_activity_live_app: LiveApp,
) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await _open_embed_local(page, embed_activity_live_app)
        await expect(page.locator("#agentNewSessionButton")).to_be_visible()

        async with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.endswith("/sessions")
        ):
            await page.locator("#agentNewSessionButton").click()
        await expect(page.locator("#agentSessionId")).not_to_be_empty()

        await page.locator("#agentMessageInput").fill("读一下配置")
        await page.locator("#agentSendButton").click()

        activity = page.locator(".turn-activity")
        await expect(activity).to_have_count(1)
        bar = activity.locator(".ta-bar")
        await expect(bar).to_contain_text("工作")

        # 折叠态不暴露细节。
        await expect(activity.locator(".ta-body")).to_be_hidden()
        await bar.click()
        await expect(activity.locator(".ta-body")).to_be_visible()

        await expect(activity.locator(".ti.thinking .name")).to_have_text("思考")
        await expect(activity.locator(".ti.thinking .dur")).to_have_text("12s")
        await expect(activity.locator(".ti.thinking .note")).to_contain_text(
            "先确认这个文件存不存在"
        )

        tool_row = activity.locator('.ti[data-tool-use-id="fake-tool"]')
        await expect(tool_row.locator(".name")).to_have_text("读取文件")
        await tool_row.locator(".ti-toggle").click()
        await expect(tool_row.locator(".ti-io")).to_contain_text("fake tool result")

        foot = activity.locator(".ta-foot")
        await expect(foot).to_contain_text("模型")
        await expect(foot).to_contain_text("工具")
        await expect(foot).to_contain_text("tok")

        await browser.close()


@pytest.mark.asyncio
async def test_chained_turns_collapse_into_one_activity_bar(
    embed_activity_live_app: LiveApp,
) -> None:
    """调一次前端工具就重开一个 Turn，但用户只该看到一条计时条。

    续跑的 Turn 会先以自己为链首画出一块，出参一到才被并进上一段——那一块
    必须被回收，否则它会僵在原地成为第二条计时条。
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await _open_embed_local(page, embed_activity_live_app)
        await expect(page.locator("#agentNewSessionButton")).to_be_visible()

        async def push(entry: dict) -> None:
            await page.evaluate("entry => window.__davinciMvp.pushTrace(entry)", entry)

        def trace(turn_id: str, event_type: str, payload: dict, at: str) -> dict:
            return {
                "event_type": event_type,
                "turn_id": turn_id,
                "at": at,
                "payload": payload,
            }

        await push(trace("A", "turn.started", {}, "2026-08-30T12:16:19+00:00"))
        await push(
            trace(
                "A",
                "tool.started",
                {
                    "tool_use_id": "toolu_86f8",
                    "name": "mcp__davinci_ui__dashboard__get_widget_config",
                    "input_preview": '{"widgetIds":["758"]}',
                },
                "2026-08-30T12:16:26+00:00",
            )
        )
        await push(
            trace(
                "A",
                "frontend_tool.deferred",
                {"tool_use_id": "toolu_86f8", "name": "dashboard.get_widget_config"},
                "2026-08-30T12:16:27+00:00",
            )
        )
        await push(trace("A", "turn.completed", {}, "2026-08-30T12:16:28+00:00"))

        # 续跑刚开始：此刻它还是独立的一块。
        await push(trace("B", "turn.started", {}, "2026-08-30T12:16:29+00:00"))
        await expect(page.locator(".turn-activity")).to_have_count(2)

        # 出参回来，两段合一，残留的那块必须消失。
        await push(
            trace(
                "B",
                "tool.completed",
                {
                    "tool_use_id": "toolu_86f8",
                    "name": "tool",
                    "output_preview": "758: 组件: 结算价-运营中心分布",
                    "is_error": False,
                },
                "2026-08-30T12:16:30+00:00",
            )
        )
        await push(trace("B", "turn.completed", {}, "2026-08-30T12:16:40+00:00"))

        activity = page.locator(".turn-activity")
        await expect(activity).to_have_count(1)
        await expect(activity.locator(".ta-title")).to_have_text("已工作 21s")
        await expect(activity.locator(".ta-sub")).to_contain_text("2 段")

        await activity.locator(".ta-bar").click()
        row = activity.locator('.ti[data-tool-use-id="toolu_86f8"]')
        # deferred 的公开名要盖掉 tool.started 里的 SDK 名
        await expect(row.locator(".name")).to_have_text("读取看板")
        await expect(row).to_have_attribute("data-state", "done")

        # 展开出入参后再来一个事件，展开状态不能被重绘吞掉
        await row.locator(".ti-toggle").click()
        await expect(row.locator(".ti-io")).to_be_visible()
        await push(
            trace(
                "B",
                "message.assistant.thinking.delta",
                {"index": 0, "text": "继续分析"},
                "2026-08-30T12:16:41+00:00",
            )
        )
        await expect(row.locator(".ti-io")).to_be_visible()
        await expect(row.locator(".ti-io")).to_contain_text("结算价-运营中心分布")

        await browser.close()


@pytest.fixture
async def embed_without_personal_live_app(
    settings_factory,
) -> AsyncIterator[LiveApp]:
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        mock_personal_workspace_id="missing-personal",
        mock_workspace_roles={"davinci-team": "admin"},
    )
    write_workspace(settings.workspaces_root, "davinci-team", skills=())
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with _serve_app(app) as live_app:
        yield live_app


async def paste_png(page) -> None:
    await expect(page.locator("#attachmentButton")).to_be_enabled()
    await paste_pngs(page, 1)


@pytest.mark.asyncio
async def test_embed_skill_management_upload_preserves_davinci_session(
    embed_skill_live_app: LiveApp,
) -> None:
    archive = _skill_archive(
        "embed-upload",
        "Uploaded through the real Davinci iframe",
        (("references/proof.txt", b"real iframe bytes"),),
    )
    session_creates = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 420, "height": 780})
        await page.add_init_script(
            """window.addEventListener("message", event => {
              const message = event.data;
              if (message?.type === "DAVINCI_AGENT_BRIDGE_HELLO") {
                window.postMessage({
                  protocolVersion: message.protocolVersion,
                  contractVersion: message.contractVersion,
                  contractDigest: message.contractDigest,
                  type: "DAVINCI_AGENT_BRIDGE_READY",
                  helloId: message.helloId,
                  bridgeNonce: "browser-bridge",
                  pageInstanceId: "browser-page"
                }, location.origin);
              }
              if (message?.type === "DAVINCI_AGENT_CONTEXT_REQUEST") {
                window.postMessage({
                  protocolVersion: message.protocolVersion,
                  contractVersion: message.contractVersion,
                  contractDigest: message.contractDigest,
                  type: "DAVINCI_AGENT_CONTEXT_RESPONSE",
                  bridgeNonce: message.bridgeNonce,
                  pageInstanceId: "browser-page",
                  runtimeContext: {
                    profileId: "workspace",
                    catalogDigest: "browser-contract:workspace",
                    toolSetId: "browser-contract:workspace:1",
                    state: {
                      schemaVersion: "davinci-page-state-v1",
                      page: {instanceId: "browser-page"}
                    },
                    tools: [],
                    context: []
                  }
                }, location.origin);
              }
            })"""
        )
        page.on(
            "request",
            lambda request: session_creates.append(request.url)
            if request.method == "POST" and request.url.endswith("/sessions")
            else None,
        )
        await page.goto(f"{embed_skill_live_app.url}/embed")
        embed_skill_live_app.services.settings.davinci_local_parent_origins = (
            embed_skill_live_app.url,
        )
        bootstrap_response = await page.request.post(
            f"{embed_skill_live_app.url}/agent-api/session/bootstrap",
            data={
                "parentOrigin": embed_skill_live_app.url,
                "protocolVersion": "agui-native-v2",
                "obId": "manager",
            },
        )
        assert bootstrap_response.status == 200
        bootstrap = await bootstrap_response.json()
        await page.evaluate(
            """formData => {
              const form = document.createElement("form");
              form.method = "post";
              form.action = "/embed/local";
              for (const [name, value] of Object.entries(formData)) {
                const input = document.createElement("input");
                input.name = name;
                input.value = value;
                form.append(input);
              }
              document.body.append(form);
              form.submit();
            }""",
            {
                "bootstrapCode": bootstrap["bootstrapCode"],
                "parentOrigin": embed_skill_live_app.url,
                "protocolVersion": bootstrap["protocolVersion"],
                "contractVersion": bootstrap["contractVersion"],
                "contractDigest": bootstrap["contractDigest"],
            },
        )
        await page.wait_for_url(f"{embed_skill_live_app.url}/embed/local")
        await expect(page.locator("#agentSkillsButton")).to_be_visible()
        # 运行参数由 composer 的快捷菜单驱动，两个 select 只做隐藏状态源。
        model_select = page.locator("#agentModelSelect")
        effort_select = page.locator("#agentEffortSelect")
        model_trigger = page.locator("#composerModelTrigger")
        await expect(model_trigger).to_be_visible()
        await expect(page.locator("#composerModelLabel")).to_have_text("qwen3.8-max")
        await expect(page.locator("#composerReasoningLabel")).to_have_text("中")
        await expect(model_select).to_have_value("qwen3.8-max")
        await expect(effort_select).to_have_value("medium")

        async with page.expect_response(
            re.compile(r".*/api/workspaces/00-davinci-team/sessions$")
        ) as session_response_info:
            await page.locator("#agentNewSessionButton").click()
        session = await (await session_response_info.value).json()
        await expect(page.locator("#agentSessionId")).to_have_text(session["id"])
        status = await page.locator("#agentStatus").inner_text()
        await model_trigger.click()
        await page.locator("#quickModelSubmenu").get_by_role(
            "button", name="qwen3.7-flash", exact=True
        ).click()
        await model_trigger.click()
        await page.locator('[data-quick-menu="reasoning"]').click()
        await page.locator("#quickReasoningSubmenu").get_by_role(
            "button", name="高", exact=True
        ).click()
        await expect(model_select).to_have_value("qwen3.7-flash")
        await expect(effort_select).to_have_value("high")

        async with page.expect_response(
            re.compile(r".*/api/workspaces/personal/skills$")
        ):
            await page.locator("#agentSkillsButton").click()
        await expect(page.locator("#assistantPanel")).to_be_hidden()
        await expect(page.locator("#skillManagementView")).to_be_visible()
        await expect(page.locator("#skillManagementWorkspaceName")).to_have_text(
            "Personal"
        )

        async with page.expect_response(
            re.compile(r".*/api/workspaces/personal/skills/import$")
        ) as upload_response_info:
            await page.locator("#personalSkillArchiveInput").set_input_files(
                {
                    "name": "embed-upload.skill",
                    "mimeType": "application/zip",
                    "buffer": archive,
                }
            )
        upload_response = await upload_response_info.value
        assert upload_response.status == 201
        await expect(_skill_row(page, "personal", "embed-upload")).to_contain_text(
            "已开启"
        )
        await expect(page.locator("#agentSkillCount")).to_have_text("1")

        await page.locator("#skillManagementBackButton").click()
        await expect(page.locator("#assistantPanel")).to_be_visible()
        await expect(page.locator("#skillManagementView")).to_be_hidden()
        await expect(page.locator("#agentSessionId")).to_have_text(session["id"])
        await expect(page.locator("#agentStatus")).to_have_text(status)
        await expect(page.locator("#composerModelLabel")).to_have_text("qwen3.7-flash")
        await expect(page.locator("#composerReasoningLabel")).to_have_text("高")
        await expect(model_select).to_have_value("qwen3.7-flash")
        await expect(effort_select).to_have_value("high")
        assert len(session_creates) == 1
        await browser.close()


@pytest.mark.asyncio
async def test_embed_direct_skill_open_without_personal_workspace_is_guarded(
    embed_without_personal_live_app: LiveApp,
) -> None:
    catalog_requests = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        page.on(
            "request",
            lambda request: catalog_requests.append(request.url)
            if re.search(r"/api/workspaces/[^/]+/skills$", request.url)
            else None,
        )
        await page.goto(f"{embed_without_personal_live_app.url}/embed")
        entry = page.locator("#agentSkillsButton")
        await expect(entry).to_be_hidden()
        await expect(entry).to_be_disabled()
        opened = await page.evaluate(
            "() => window.__davinciMvp.openSkillManagement()"
        )
        assert opened is False
        assert catalog_requests == []
        await expect(page.locator("#skillManagementView")).to_be_hidden()
        await browser.close()


async def paste_pngs(page, count: int) -> bool:
    message = page.locator("#messageInput")
    await expect(message).to_be_enabled()
    return await message.evaluate(
        """async (element, count) => {
          const canvas = document.createElement('canvas');
          canvas.width = 16;
          canvas.height = 16;
          const context = canvas.getContext('2d');
          context.fillStyle = '#1d4ed8';
          context.fillRect(0, 0, canvas.width, canvas.height);
          const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
          const transfer = new DataTransfer();
          for (let index = 0; index < count; index += 1) {
            transfer.items.add(new File([blob], 'image.png', {type: 'image/png'}));
          }
          const event = new Event('paste', {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clipboardData', {value: transfer});
          element.dispatchEvent(event);
          return event.defaultPrevented;
        }""",
        count,
    )


async def paste_text_default_prevented(page, text: str) -> bool:
    message = page.locator("#messageInput")
    await expect(message).to_be_enabled()
    return await message.evaluate(
        """(element, text) => {
          const transfer = new DataTransfer();
          transfer.setData('text/plain', text);
          const event = new Event('paste', {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clipboardData', {value: transfer});
          element.dispatchEvent(event);
          return event.defaultPrevented;
        }""",
        text,
    )


@pytest.mark.asyncio
async def test_workbench_shows_personal_workspace_memory(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)

        memory_pill = page.get_by_text("个人记忆", exact=True)
        await expect(memory_pill).to_be_visible()
        await expect(memory_pill).to_have_attribute(
            "title",
            "仅当前用户和当前 Workspace 可用",
        )
        await browser.close()


@pytest.mark.asyncio
async def test_composer_pastes_image_and_submits_attachment_id(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)

        await expect(page.locator("#attachmentTray")).to_contain_text(
            re.compile(r"clipboard-\d{8}-\d{6}-1\.png")
        )
        await expect(
            page.locator("#attachmentTray .attachment-thumbnail")
        ).to_be_visible()
        await expect(page.locator("#attachmentTray .attachment-size")).to_contain_text(
            "B"
        )
        await page.locator("#messageInput").fill("描述图片")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        await expect(
            page.locator(
                ".message-user .history-attachment .attachment-thumbnail"
            ).last
        ).to_be_visible()
        await page.reload()
        await page.locator("#sessionList .session-row").first.click()
        await expect(
            page.locator(
                ".message-user .history-attachment .attachment-thumbnail"
            ).last
        ).to_be_visible()
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )

        assert len(submitted) == 1
        assert len(submitted[0]["attachment_ids"]) == 1
        assert "base64" not in str(submitted[0]).lower()
        await browser.close()


@pytest.mark.asyncio
async def test_pending_pasted_image_does_not_leak_to_new_session(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)

        await page.locator("#newSessionButton").click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)

        await page.locator("#sessionList .session-row").nth(1).click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)
        await browser.close()


@pytest.mark.asyncio
async def test_pasted_image_upload_state_and_removal(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})

        async def delay_upload(route) -> None:
            await asyncio.sleep(0.2)
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            delay_upload,
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)

        await expect(page.locator("#sendButton")).to_be_disabled()
        await expect(page.locator("#messageInput")).to_be_enabled()
        await page.locator("#messageInput").fill("输入内容不会因上传而丢失")
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#messageInput")).to_have_value(
            "输入内容不会因上传而丢失"
        )

        await page.locator("#attachmentTray .attachment-chip button").click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio
async def test_pasted_image_upload_success_after_session_switch_stays_with_origin(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()

        async def hold_upload(route) -> None:
            if route.request.method == "POST":
                upload_started.set()
                await release_upload.wait()
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            hold_upload,
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        first_session_id = await page.locator(
            "#sessionList .session-row.active"
        ).get_attribute("data-session-id")
        await paste_png(page)
        await asyncio.wait_for(upload_started.wait(), timeout=2)

        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=first_session_id,
        )
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)

        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/attachments$")
        ):
            release_upload.set()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)
        await expect(page.locator("#toast")).to_be_hidden()

        await page.locator("#sessionList .session-row").nth(1).click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)
        await browser.close()


@pytest.mark.asyncio
async def test_pasted_image_upload_failure_after_session_switch_does_not_toast_current(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()

        async def fail_upload(route) -> None:
            if route.request.method != "POST":
                await route.continue_()
                return
            upload_started.set()
            await release_upload.wait()
            await route.fulfill(
                status=500,
                json={"error": {"message": "old session upload failed"}},
            )

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            fail_upload,
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        first_session_id = await page.locator(
            "#sessionList .session-row.active"
        ).get_attribute("data-session-id")
        await paste_png(page)
        await asyncio.wait_for(upload_started.wait(), timeout=2)

        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=first_session_id,
        )
        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/attachments$")
        ):
            release_upload.set()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)
        await expect(page.locator("#toast")).to_be_hidden()
        await browser.close()


@pytest.mark.asyncio
async def test_old_upload_success_does_not_steal_reselected_pending_load(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_committed = asyncio.Event()
        release_upload_response = asyncio.Event()
        reselected_pending_started = asyncio.Event()
        release_reselected_pending = asyncio.Event()
        reselected_selection_continued = asyncio.Event()
        submitted = []

        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#messageTimeline")).to_contain_text(
            "开始新的对话"
        )
        first_session_id = await page.locator(
            "#sessionList .session-row.active"
        ).get_attribute("data-session-id")

        async def interleave_attachments(route) -> None:
            path = urlparse(route.request.url).path
            if route.request.method == "POST":
                response = await route.fetch()
                upload_committed.set()
                await release_upload_response.wait()
                await route.fulfill(response=response)
                return
            if path.endswith(f"/sessions/{first_session_id}/attachments"):
                reselected_pending_started.set()
                await release_reselected_pending.wait()
            await route.continue_()

        async def capture_messages(route) -> None:
            if urlparse(route.request.url).path.endswith(
                f"/sessions/{first_session_id}/messages"
            ):
                reselected_selection_continued.set()
            await route.continue_()

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            interleave_attachments,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)

        await paste_png(page)
        await asyncio.wait_for(upload_committed.wait(), timeout=2)
        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=first_session_id,
        )
        await page.locator(
            f'#sessionList .session-row[data-session-id="{first_session_id}"]'
        ).click()
        await asyncio.wait_for(reselected_pending_started.wait(), timeout=2)

        release_upload_response.set()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)
        await expect(page.locator("#sendButton")).to_be_disabled()

        release_reselected_pending.set()
        await asyncio.wait_for(reselected_selection_continued.wait(), timeout=2)
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(chips).to_have_count(1)

        await page.locator("#messageInput").fill("提交恢复的草稿")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        attachment_ids = submitted[0]["attachment_ids"]
        assert len(attachment_ids) == 1
        assert len(set(attachment_ids)) == 1
        await browser.close()


@pytest.mark.asyncio
async def test_old_upload_failure_does_not_toast_reselected_origin(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()
        reselected_selection_continued = asyncio.Event()

        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#messageTimeline")).to_contain_text(
            "开始新的对话"
        )
        first_session_id = await page.locator(
            "#sessionList .session-row.active"
        ).get_attribute("data-session-id")

        async def fail_upload(route) -> None:
            if route.request.method != "POST":
                await route.continue_()
                return
            upload_started.set()
            await release_upload.wait()
            await route.fulfill(
                status=500,
                json={"error": {"message": "superseded upload failed"}},
            )

        async def capture_messages(route) -> None:
            if urlparse(route.request.url).path.endswith(
                f"/sessions/{first_session_id}/messages"
            ):
                reselected_selection_continued.set()
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            fail_upload,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )

        await paste_png(page)
        await asyncio.wait_for(upload_started.wait(), timeout=2)
        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=first_session_id,
        )
        await page.locator(
            f'#sessionList .session-row[data-session-id="{first_session_id}"]'
        ).click()
        await asyncio.wait_for(reselected_selection_continued.wait(), timeout=2)

        release_upload.set()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#toast")).to_be_hidden()
        await browser.close()


@pytest.mark.asyncio
async def test_remove_waits_for_reselected_snapshot_and_preserves_server_order(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        await client.post(
            f"/api/sessions/{session['id']}/attachments",
            files={"files": ("old.txt", b"old draft", "text/plain")},
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_committed = asyncio.Event()
        release_upload_response = asyncio.Event()
        snapshot_fetched = asyncio.Event()
        release_snapshot_response = asyncio.Event()
        reselected_selection_continued = asyncio.Event()
        uploaded_attachment_id = None
        delete_requests = 0

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#messageTimeline")).to_contain_text(
            "开始新的对话"
        )

        async def interleave_attachments(route) -> None:
            nonlocal uploaded_attachment_id
            path = urlparse(route.request.url).path
            if route.request.method == "POST":
                response = await route.fetch()
                uploaded_attachment_id = (await response.json())[0]["id"]
                upload_committed.set()
                await release_upload_response.wait()
                await route.fulfill(response=response)
                return
            if path.endswith(f"/sessions/{session['id']}/attachments"):
                response = await route.fetch()
                snapshot_fetched.set()
                await release_snapshot_response.wait()
                await route.fulfill(response=response)
                return
            await route.continue_()

        async def capture_delete(route) -> None:
            nonlocal delete_requests
            delete_requests += 1
            await route.continue_()

        async def capture_messages(route) -> None:
            if urlparse(route.request.url).path.endswith(
                f"/sessions/{session['id']}/messages"
            ):
                reselected_selection_continued.set()
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            interleave_attachments,
        )
        await page.route(re.compile(r".*/api/attachments/[^/]+$"), capture_delete)
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )

        await paste_png(page)
        await asyncio.wait_for(upload_committed.wait(), timeout=2)
        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=session["id"],
        )
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await asyncio.wait_for(snapshot_fetched.wait(), timeout=2)

        release_upload_response.set()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)
        remove = chips.locator("button")
        await expect(remove).to_be_disabled()
        await remove.dispatch_event("click")
        assert delete_requests == 0
        await page.evaluate("attachmentId => removeAttachment(attachmentId)", uploaded_attachment_id)
        assert delete_requests == 0

        release_snapshot_response.set()
        await asyncio.wait_for(reselected_selection_continued.wait(), timeout=2)
        await expect(chips).to_have_count(2)
        await expect(chips.locator(".attachment-name")).to_have_text(
            ["old.txt", re.compile(r"clipboard-\d{8}-\d{6}-1\.png")]
        )
        await expect(chips.locator("button:disabled")).to_have_count(0)

        await page.get_by_role(
            "button", name=re.compile(r"移除 clipboard-\d{8}-\d{6}-1\.png")
        ).click()
        await expect(chips).to_have_count(1)
        await expect(chips).to_contain_text("old.txt")
        await page.get_by_role("button", name="移除 old.txt").click()
        await expect(chips).to_have_count(0)
        assert delete_requests == 2

        async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
            pending = (
                await client.get(f"/api/sessions/{session['id']}/attachments")
            ).json()
        assert pending == []
        await browser.close()


@pytest.mark.asyncio
async def test_remove_is_guarded_while_another_attachment_uploads(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        old_attachment = (
            await client.post(
                f"/api/sessions/{session['id']}/attachments",
                files={"files": ("old.txt", b"old draft", "text/plain")},
            )
        ).json()[0]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()
        delete_requests = 0

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)
        await expect(page.locator("#attachmentButton")).to_be_enabled()

        async def hold_upload(route) -> None:
            if route.request.method == "POST":
                upload_started.set()
                await release_upload.wait()
            await route.continue_()

        async def capture_delete(route) -> None:
            nonlocal delete_requests
            delete_requests += 1
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            hold_upload,
        )
        await page.route(re.compile(r".*/api/attachments/[^/]+$"), capture_delete)

        await paste_png(page)
        await asyncio.wait_for(upload_started.wait(), timeout=2)
        await expect(chips.locator("button")).to_be_disabled()
        await page.evaluate(
            "attachmentId => removeAttachment(attachmentId)", old_attachment["id"]
        )
        assert delete_requests == 0

        release_upload.set()
        await expect(chips).to_have_count(2)
        await expect(chips.locator("button:disabled")).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio
async def test_attachment_delete_in_flight_blocks_mutations_then_unlocks(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        await client.post(
            f"/api/sessions/{session['id']}/attachments",
            files={"files": ("delete-me.txt", b"delete me", "text/plain")},
        )
        await client.post(
            f"/api/sessions/{session['id']}/attachments",
            files={"files": ("keep-me.txt", b"keep me", "text/plain")},
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.add_init_script("window.setInterval = () => 0")
        delete_started = asyncio.Event()
        release_delete = asyncio.Event()
        attachment_delete_requests = 0
        upload_requests = 0
        turn_requests = 0
        session_delete_requests = 0

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(2)

        async def hold_delete(route) -> None:
            nonlocal attachment_delete_requests
            attachment_delete_requests += 1
            if attachment_delete_requests == 1:
                response = await route.fetch()
                delete_started.set()
                await release_delete.wait()
                await route.fulfill(response=response)
                return
            await route.continue_()

        async def capture_upload(route) -> None:
            nonlocal upload_requests
            if route.request.method == "POST":
                upload_requests += 1
            await route.continue_()

        async def capture_turn(route) -> None:
            nonlocal turn_requests
            turn_requests += 1
            await route.continue_()

        async def capture_session_delete(route) -> None:
            nonlocal session_delete_requests
            if route.request.method == "DELETE":
                session_delete_requests += 1
            await route.continue_()

        await page.route(re.compile(r".*/api/attachments/[^/]+$"), hold_delete)
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            capture_upload,
        )
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.route(re.compile(r".*/api/sessions/[^/]+$"), capture_session_delete)

        await page.get_by_role("button", name="移除 delete-me.txt").click()
        await asyncio.wait_for(delete_started.wait(), timeout=2)

        controls_disabled = await page.evaluate(
            """() => ({
              attachment: elements.attachmentButton.disabled,
              send: elements.sendButton.disabled,
              deleteSession: elements.deleteSessionButton.disabled,
              removes: Array.from(
                elements.attachmentTray.querySelectorAll('.attachment-chip button'),
              ).every((button) => button.disabled),
            })"""
        )
        await page.evaluate(
            """async () => {
              await uploadFiles([
                new File(['blocked upload'], 'blocked.txt', {type: 'text/plain'}),
              ]);
            }"""
        )
        assert await paste_pngs(page, 1) is True
        await page.wait_for_timeout(50)
        await page.locator("#messageInput").fill("blocked turn")
        await page.evaluate("sendMessage()")
        await page.evaluate(
            "attachmentId => removeAttachment(attachmentId)",
            await page.evaluate(
                "state.pendingAttachments.find((item) => item.original_filename === 'keep-me.txt').id"
            ),
        )
        await page.locator("#deleteSessionButton").dispatch_event("click")
        delete_dialog_open = await page.locator("#deleteDialog").evaluate(
            "dialog => dialog.open"
        )
        await page.locator("#deleteForm").dispatch_event("submit")

        release_delete.set()
        await expect(chips).to_have_count(1)
        await expect(chips).to_contain_text("keep-me.txt")
        await expect(chips.locator("button")).to_be_enabled()
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#deleteSessionButton")).to_be_enabled()

        assert controls_disabled == {
            "attachment": True,
            "send": True,
            "deleteSession": True,
            "removes": True,
        }
        assert delete_dialog_open is False
        assert attachment_delete_requests == 1
        assert upload_requests == 0
        assert turn_requests == 0
        assert session_delete_requests == 0
        await browser.close()


@pytest.mark.asyncio
async def test_upload_response_and_session_delete_response_own_mutation_state(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.add_init_script("window.setInterval = () => 0")
        upload_committed = asyncio.Event()
        release_upload_response = asyncio.Event()
        delete_committed = asyncio.Event()
        release_delete_response = asyncio.Event()
        allow_session_delete = False
        upload_requests = 0
        session_delete_requests = 0
        turn_requests = 0
        attachment_delete_requests = 0

        async def gate_upload(route) -> None:
            nonlocal upload_requests
            if route.request.method != "POST":
                await route.continue_()
                return
            upload_requests += 1
            if upload_requests == 1:
                response = await route.fetch()
                upload_committed.set()
                await release_upload_response.wait()
                await route.fulfill(response=response)
                return
            await route.continue_()

        async def gate_session_delete(route) -> None:
            nonlocal session_delete_requests
            if route.request.method != "DELETE":
                await route.continue_()
                return
            session_delete_requests += 1
            if not allow_session_delete:
                await route.fulfill(
                    status=409,
                    json={"error": {"message": "delete must be blocked"}},
                )
                return
            response = await route.fetch()
            delete_committed.set()
            await release_delete_response.wait()
            await route.fulfill(response=response)

        async def capture_turn(route) -> None:
            nonlocal turn_requests
            turn_requests += 1
            await route.continue_()

        async def capture_attachment_delete(route) -> None:
            nonlocal attachment_delete_requests
            attachment_delete_requests += 1
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            gate_upload,
        )
        await page.route(re.compile(r".*/api/sessions/[^/]+$"), gate_session_delete)
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.route(
            re.compile(r".*/api/attachments/[^/]+$"),
            capture_attachment_delete,
        )

        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)
        await asyncio.wait_for(upload_committed.wait(), timeout=2)

        upload_delete_disabled = await page.locator(
            "#deleteSessionButton"
        ).is_disabled()
        await page.locator("#deleteSessionButton").dispatch_event("click")
        await page.locator("#deleteForm").dispatch_event("submit")
        await page.wait_for_timeout(50)
        upload_phase_delete_requests = session_delete_requests

        release_upload_response.set()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)
        await expect(page.locator("#deleteSessionButton")).to_be_enabled()
        await page.evaluate(
            "elements.deleteDialog.open && elements.deleteDialog.close()"
        )
        allow_session_delete = True
        await page.locator("#deleteSessionButton").click()
        await page.locator("#deleteForm button[type=submit]").click()
        await asyncio.wait_for(delete_committed.wait(), timeout=2)

        deleting_controls_disabled = await page.evaluate(
            """() => ({
              attachment: elements.attachmentButton.disabled,
              send: elements.sendButton.disabled,
              deleteSession: elements.deleteSessionButton.disabled,
              removes: Array.from(
                elements.attachmentTray.querySelectorAll('.attachment-chip button'),
              ).every((button) => button.disabled),
            })"""
        )
        attachment_id = await page.evaluate("state.pendingAttachments[0].id")
        await page.evaluate(
            """async () => {
              await uploadFiles([
                new File(['blocked upload'], 'blocked.txt', {type: 'text/plain'}),
              ]);
              await sendMessage();
            }"""
        )
        await page.evaluate(
            "attachmentId => removeAttachment(attachmentId)",
            attachment_id,
        )

        release_delete_response.set()
        await expect(chips).to_have_count(0)
        await expect(page.locator("#sessionActions")).to_be_hidden()

        assert upload_delete_disabled is True
        assert upload_phase_delete_requests == 0
        assert deleting_controls_disabled == {
            "attachment": True,
            "send": True,
            "deleteSession": True,
            "removes": True,
        }
        assert session_delete_requests == 1
        assert upload_requests == 1
        assert turn_requests == 0
        assert attachment_delete_requests == 0
        await browser.close()


@pytest.mark.asyncio
async def test_stale_refresh_snapshot_cannot_resurrect_deleted_session(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        await client.post(
            f"/api/sessions/{session['id']}/attachments",
            files={"files": ("delete-with-session.txt", b"delete", "text/plain")},
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        stale_snapshot_fetched = asyncio.Event()
        release_stale_snapshot = asyncio.Event()
        held_requests = 0

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)

        async def hold_first_session_list(route) -> None:
            nonlocal held_requests
            held_requests += 1
            if held_requests == 1:
                response = await route.fetch()
                stale_snapshot_fetched.set()
                await release_stale_snapshot.wait()
                await route.fulfill(response=response)
                return
            await route.continue_()

        await page.route(
            re.compile(r".*/api/workspaces/actual/sessions$"),
            hold_first_session_list,
        )
        await page.evaluate(
            """() => {
              window.staleRefreshCompleted = false;
              void refreshSessions().then(() => {
                window.staleRefreshCompleted = true;
              });
            }"""
        )
        await asyncio.wait_for(stale_snapshot_fetched.wait(), timeout=2)

        await page.locator("#deleteSessionButton").click()
        await page.locator("#deleteForm button[type=submit]").click()
        await page.wait_for_function("state.deletingSession === null")
        await expect(page.locator("#sessionActions")).to_be_hidden()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)

        release_stale_snapshot.set()
        await page.wait_for_function("window.staleRefreshCompleted === true")

        await expect(
            page.locator(
                f'#sessionList .session-row[data-session-id="{session["id"]}"]'
            )
        ).to_have_count(0)
        await expect(page.locator("#sessionActions")).to_be_hidden()
        await expect(page.locator("#sessionTitle")).to_have_text("选择或新建会话")
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)
        assert await page.evaluate("state.session === null") is True
        await browser.close()


@pytest.mark.asyncio
async def test_authoritative_list_missing_current_session_clears_selection_state(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        await client.post(
            f"/api/sessions/{session['id']}/attachments",
            files={"files": ("orphaned-tray.txt", b"orphan", "text/plain")},
        )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await expect(page.locator("#attachmentTray")).to_contain_text(
            "orphaned-tray.txt"
        )

        async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
            deleted = await client.delete(f"/api/sessions/{session['id']}")
        assert deleted.status_code == 204
        await page.evaluate("refreshSessions()")

        assert await page.evaluate("state.session === null") is True
        await expect(page.locator("#sessionActions")).to_be_hidden()
        await expect(page.locator("#sessionTitle")).to_have_text("选择或新建会话")
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)
        await expect(page.locator("#messageTimeline")).to_contain_text(
            "选择或新建会话"
        )
        await browser.close()


@pytest.mark.asyncio
async def test_post_delete_preserve_list_cannot_roll_selection_from_c_to_b(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session_a = (await client.post("/api/workspaces/actual/sessions")).json()
        session_b = (await client.post("/api/workspaces/actual/sessions")).json()
        session_c = (await client.post("/api/workspaces/actual/sessions")).json()
        for session, label in ((session_b, "B"), (session_c, "C")):
            turn = (
                await client.post(
                    f"/api/sessions/{session['id']}/turns",
                    json={
                        "message": f"history {label}",
                        "attachment_ids": [],
                        "client_request_id": f"history-{label.lower()}",
                    },
                )
            ).json()
            await client.get(f"/api/turns/{turn['turn_id']}/events")
            await client.post(
                f"/api/sessions/{session['id']}/attachments",
                files={
                    "files": (
                        f"pending-{label}.txt",
                        f"pending {label}".encode(),
                        "text/plain",
                    )
                },
            )

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        preserve_snapshot_fetched = asyncio.Event()
        release_preserve_snapshot = asyncio.Event()

        async def hold_tagged_preserve_list(route) -> None:
            if route.request.headers.get("x-test-session-list") == "manual-preserve":
                response = await route.fetch()
                preserve_snapshot_fetched.set()
                await release_preserve_snapshot.wait()
                await route.fulfill(response=response)
                return
            await route.continue_()

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session_a["id"]}"]'
        ).click()
        await page.locator("#deleteSessionButton").click()
        await page.locator("#deleteForm button[type=submit]").click()
        await page.wait_for_function("state.deletingSession === null")
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session_b["id"]}"]'
        ).click()
        await expect(page.locator("#attachmentTray")).to_contain_text("pending-B.txt")
        await expect(page.locator("#messageTimeline")).to_contain_text("history B")

        await page.route(
            re.compile(r".*/api/workspaces/actual/sessions$"),
            hold_tagged_preserve_list,
        )
        await page.evaluate(
            """() => {
              const originalFetch = window.fetch.bind(window);
              window.fetch = (input, options = {}) => {
                const url = typeof input === 'string' ? input : input.url;
                if (
                  window.tagNextSessionList
                  && url.endsWith('/api/workspaces/actual/sessions')
                ) {
                  window.tagNextSessionList = false;
                  const headers = new Headers(options.headers || {});
                  headers.set('X-Test-Session-List', 'manual-preserve');
                  return originalFetch(input, {...options, headers});
                }
                return originalFetch(input, options);
              };
              window.tagNextSessionList = true;
              window.preserveListCompleted = false;
              void loadSessions({preserveSelection: true}).then(() => {
                window.preserveListCompleted = true;
              });
            }"""
        )
        await asyncio.wait_for(preserve_snapshot_fetched.wait(), timeout=2)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session_c["id"]}"]'
        ).click()
        await expect(page.locator("#attachmentTray")).to_contain_text("pending-C.txt")
        await expect(page.locator("#attachmentTray")).not_to_contain_text("pending-B.txt")
        await expect(page.locator("#messageTimeline")).to_contain_text("history C")
        await expect(page.locator("#messageTimeline")).not_to_contain_text("history B")

        release_preserve_snapshot.set()
        await page.wait_for_function("window.preserveListCompleted === true")

        assert await page.evaluate("state.session.id") == session_c["id"]
        await expect(page.locator("#sessionTitle")).to_have_text("history C")
        await expect(
            page.locator(
                f'#sessionList .session-row[data-session-id="{session_c["id"]}"]'
            )
        ).to_have_class(re.compile("active"))
        await expect(page.locator("#attachmentTray")).to_contain_text("pending-C.txt")
        await expect(page.locator("#attachmentTray")).not_to_contain_text("pending-B.txt")
        await expect(page.locator("#messageTimeline")).to_contain_text("history C")
        await expect(page.locator("#messageTimeline")).not_to_contain_text("history B")
        await browser.close()


@pytest.mark.asyncio
async def test_failed_delete_does_not_toast_after_switching_sessions(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session_a = (await client.post("/api/workspaces/actual/sessions")).json()
        session_b = (await client.post("/api/workspaces/actual/sessions")).json()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        delete_started = asyncio.Event()
        release_delete_failure = asyncio.Event()

        async def fail_delete_after_switch(route) -> None:
            delete_started.set()
            await release_delete_failure.wait()
            await route.fulfill(
                status=500,
                json={"error": {"message": "delete A failed"}},
            )

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session_a["id"]}"]'
        ).click()
        await page.route(
            re.compile(rf".*/api/sessions/{session_a['id']}$"),
            fail_delete_after_switch,
        )
        await page.locator("#deleteSessionButton").click()
        await page.locator("#deleteForm button[type=submit]").click()
        await asyncio.wait_for(delete_started.wait(), timeout=2)

        await page.locator(
            f'#sessionList .session-row[data-session-id="{session_b["id"]}"]'
        ).click()
        await expect(page.locator("#sessionTitle")).to_have_text("新会话")
        release_delete_failure.set()
        await page.wait_for_function("state.deletingSession === null")

        assert await page.evaluate("state.session.id") == session_b["id"]
        await expect(page.locator("#toast")).to_be_hidden()
        await browser.close()


@pytest.mark.asyncio
async def test_remove_is_guarded_while_turn_submission_is_running(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        old_attachment = (
            await client.post(
                f"/api/sessions/{session['id']}/attachments",
                files={"files": ("old.txt", b"old draft", "text/plain")},
            )
        ).json()[0]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        turn_started = asyncio.Event()
        release_turn = asyncio.Event()
        delete_requests = 0
        session_delete_requests = 0

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)

        async def hold_turn(route) -> None:
            turn_started.set()
            await release_turn.wait()
            await route.continue_()

        async def capture_delete(route) -> None:
            nonlocal delete_requests
            delete_requests += 1
            await route.continue_()

        async def capture_session_delete(route) -> None:
            nonlocal session_delete_requests
            if route.request.method == "DELETE":
                session_delete_requests += 1
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), hold_turn)
        await page.route(re.compile(r".*/api/attachments/[^/]+$"), capture_delete)
        await page.route(
            re.compile(r".*/api/sessions/[^/]+$"),
            capture_session_delete,
        )

        await page.locator("#messageInput").fill("提交旧草稿")
        await page.locator("#sendButton").click()
        await asyncio.wait_for(turn_started.wait(), timeout=2)
        await expect(chips.locator("button")).to_be_disabled()
        await expect(page.locator("#deleteSessionButton")).to_be_disabled()
        await page.evaluate(
            "attachmentId => removeAttachment(attachmentId)", old_attachment["id"]
        )
        await page.locator("#deleteSessionButton").dispatch_event("click")
        await page.locator("#deleteForm").dispatch_event("submit")
        assert delete_requests == 0
        assert session_delete_requests == 0

        release_turn.set()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        await expect(chips).to_have_count(0)
        await browser.close()


@pytest.mark.asyncio
async def test_successful_delete_tombstone_blocks_a_delayed_reselected_snapshot(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        attachment = (
            await client.post(
                f"/api/sessions/{session['id']}/attachments",
                files={"files": ("delete-me.txt", b"delete me", "text/plain")},
            )
        ).json()[0]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        delete_started = asyncio.Event()
        allow_delete = asyncio.Event()
        snapshot_fetched = asyncio.Event()
        release_snapshot = asyncio.Event()
        reselected_selection_continued = asyncio.Event()
        submitted = []

        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)
        await expect(page.locator("#messageTimeline")).to_contain_text(
            "开始新的对话"
        )

        async def hold_delete(route) -> None:
            delete_started.set()
            await allow_delete.wait()
            response = await route.fetch()
            await route.fulfill(response=response)

        async def hold_reselected_snapshot(route) -> None:
            path = urlparse(route.request.url).path
            if path.endswith(f"/sessions/{session['id']}/attachments"):
                response = await route.fetch()
                snapshot_fetched.set()
                await release_snapshot.wait()
                await route.fulfill(response=response)
                return
            await route.continue_()

        async def capture_messages(route) -> None:
            if urlparse(route.request.url).path.endswith(
                f"/sessions/{session['id']}/messages"
            ):
                reselected_selection_continued.set()
            await route.continue_()

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/attachments/[^/]+$"), hold_delete)
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            hold_reselected_snapshot,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)

        await page.evaluate(
            """
            attachmentId => {
              window.removeCompleted = false;
              void removeAttachment(attachmentId).then(() => {
                window.removeCompleted = true;
              });
            }
            """,
            attachment["id"],
        )
        await asyncio.wait_for(delete_started.wait(), timeout=2)
        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=session["id"],
        )
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await asyncio.wait_for(snapshot_fetched.wait(), timeout=2)

        allow_delete.set()
        await page.wait_for_function("window.removeCompleted === true")
        await expect(chips).to_have_count(0)

        release_snapshot.set()
        await asyncio.wait_for(reselected_selection_continued.wait(), timeout=2)
        await expect(chips).to_have_count(0)

        async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
            pending = (
                await client.get(f"/api/sessions/{session['id']}/attachments")
            ).json()
        assert pending == []

        await page.locator("#messageInput").fill("删除后正常发送")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        assert submitted[0]["attachment_ids"] == []
        await browser.close()


@pytest.mark.asyncio
async def test_failed_delete_does_not_tombstone_authoritative_pending_attachment(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        attachment = (
            await client.post(
                f"/api/sessions/{session['id']}/attachments",
                files={"files": ("keep-me.txt", b"keep me", "text/plain")},
            )
        ).json()[0]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})

        async def fail_delete(route) -> None:
            await route.fulfill(
                status=500,
                json={"error": {"message": "delete failed"}},
            )

        await page.route(re.compile(r".*/api/attachments/[^/]+$"), fail_delete)
        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)

        await page.get_by_role("button", name="移除 keep-me.txt").click()
        await expect(page.locator("#toast")).to_contain_text("delete failed")
        await page.locator("#newSessionButton").click()
        await expect(page.locator("#sessionList .session-row")).to_have_count(2)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await expect(chips).to_have_count(1)
        await expect(chips).to_contain_text("keep-me.txt")

        async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
            pending = (
                await client.get(f"/api/sessions/{session['id']}/attachments")
            ).json()
        assert [item["id"] for item in pending] == [attachment["id"]]
        await browser.close()


@pytest.mark.asyncio
async def test_pending_load_blocks_upload_then_preserves_old_and_new_drafts(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        old_attachment = (
            await client.post(
                f"/api/sessions/{session['id']}/attachments",
                files={"files": ("old.txt", b"old draft", "text/plain")},
            )
        ).json()[0]

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        pending_started = asyncio.Event()
        release_pending = asyncio.Event()
        messages_requested = asyncio.Event()
        attachment_posts = 0
        session_delete_requests = 0
        submitted = []

        async def gate_pending(route) -> None:
            nonlocal attachment_posts
            if route.request.method == "GET":
                pending_started.set()
                await release_pending.wait()
            else:
                attachment_posts += 1
            await route.continue_()

        async def capture_messages(route) -> None:
            messages_requested.set()
            await route.continue_()

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        async def capture_session_delete(route) -> None:
            nonlocal session_delete_requests
            if route.request.method == "DELETE":
                session_delete_requests += 1
                await route.fulfill(
                    status=409,
                    json={"error": {"message": "delete must be blocked while loading"}},
                )
                return
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            gate_pending,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.route(
            re.compile(r".*/api/sessions/[^/]+$"),
            capture_session_delete,
        )
        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await asyncio.wait_for(pending_started.wait(), timeout=2)

        await expect(page.locator("#attachmentButton")).to_be_disabled()
        await expect(page.locator("#sendButton")).to_be_disabled()
        await expect(page.locator("#deleteSessionButton")).to_be_disabled()
        await expect(page.locator("#messageInput")).to_be_enabled()
        assert await paste_text_default_prevented(page, "保留原生文本粘贴") is False
        assert await paste_pngs(page, 1) is True
        await page.locator("#deleteSessionButton").dispatch_event("click")
        await page.locator("#deleteForm").dispatch_event("submit")
        assert attachment_posts == 0
        assert session_delete_requests == 0

        release_pending.set()
        await asyncio.wait_for(messages_requested.wait(), timeout=2)
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#sendButton")).to_be_enabled()
        chips = page.locator("#attachmentTray .attachment-chip")
        await expect(chips).to_have_count(1)
        await expect(chips.filter(has_text="old.txt")).to_have_count(1)

        await paste_png(page)
        await expect(chips).to_have_count(2)
        await expect(chips.filter(has_text="old.txt")).to_have_count(1)
        await expect(
            chips.filter(has_text=re.compile(r"clipboard-\d{8}-\d{6}-1\.png"))
        ).to_have_count(1)
        assert attachment_posts == 1

        assert await paste_pngs(page, 4) is True
        await expect(page.locator("#toast")).to_contain_text(
            "最多还能添加 3 个附件。"
        )
        await expect(chips).to_have_count(2)
        assert attachment_posts == 1

        await page.locator("#messageInput").fill("提交两个草稿")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        attachment_ids = submitted[0]["attachment_ids"]
        assert len(attachment_ids) == 2
        assert len(set(attachment_ids)) == len(attachment_ids)
        assert old_attachment["id"] in attachment_ids
        await browser.close()


@pytest.mark.asyncio
async def test_current_pending_load_failure_unlocks_composer_and_toasts(
    live_url: str,
) -> None:
    async with httpx.AsyncClient(base_url=live_url, trust_env=False) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        pending_started = asyncio.Event()
        release_pending = asyncio.Event()
        selection_continued = asyncio.Event()

        async def fail_pending(route) -> None:
            pending_started.set()
            await release_pending.wait()
            await route.fulfill(
                status=500,
                json={"error": {"message": "current pending failed"}},
            )

        async def capture_messages(route) -> None:
            selection_continued.set()
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            fail_pending,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )
        await page.goto(live_url)
        await page.locator(
            f'#sessionList .session-row[data-session-id="{session["id"]}"]'
        ).click()
        await asyncio.wait_for(pending_started.wait(), timeout=2)

        await expect(page.locator("#attachmentButton")).to_be_disabled()
        await expect(page.locator("#sendButton")).to_be_disabled()
        await expect(page.locator("#messageInput")).to_be_enabled()

        release_pending.set()
        await asyncio.wait_for(selection_continued.wait(), timeout=2)
        await expect(page.locator("#attachmentButton")).to_be_enabled()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#toast")).to_contain_text("current pending failed")
        await browser.close()


@pytest.mark.asyncio
async def test_pending_snapshot_and_upload_response_merge_attachment_ids_once(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        upload_committed = asyncio.Event()
        release_upload_response = asyncio.Event()
        messages_requested = asyncio.Event()
        submitted = []

        async def interleave_attachments(route) -> None:
            if route.request.method == "GET":
                await route.continue_()
                return
            response = await route.fetch()
            upload_committed.set()
            await release_upload_response.wait()
            await route.fulfill(response=response)

        async def capture_messages(route) -> None:
            messages_requested.set()
            await route.continue_()

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        active_session = page.locator("#sessionList .session-row.active")
        await expect(active_session).to_be_visible()
        await expect(page.locator("#messageInput")).to_be_enabled()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            interleave_attachments,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            capture_messages,
        )
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)

        await paste_png(page)
        await asyncio.wait_for(upload_committed.wait(), timeout=2)
        await active_session.click()
        await asyncio.wait_for(messages_requested.wait(), timeout=2)
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)

        release_upload_response.set()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)

        await page.locator("#messageInput").fill("描述这张图片")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        attachment_ids = submitted[0]["attachment_ids"]
        assert len(attachment_ids) == 1
        assert len(set(attachment_ids)) == len(attachment_ids)
        await browser.close()


@pytest.mark.asyncio
async def test_superseded_pending_failure_does_not_toast_reselected_session(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        first_pending_started = asyncio.Event()
        release_first_pending = asyncio.Event()
        superseded_selection_continued = asyncio.Event()
        pending_requests = 0
        message_requests = 0

        async def delay_first_pending(route) -> None:
            nonlocal pending_requests
            pending_requests += 1
            if pending_requests == 1:
                first_pending_started.set()
                await release_first_pending.wait()
                await route.fulfill(
                    status=500,
                    json={"error": {"message": "superseded pending failed"}},
                )
                return
            await route.continue_()

        async def count_messages(route) -> None:
            nonlocal message_requests
            message_requests += 1
            if message_requests == 3:
                superseded_selection_continued.set()
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            delay_first_pending,
        )
        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"),
            count_messages,
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await asyncio.wait_for(first_pending_started.wait(), timeout=2)
        first_session_id = await page.locator(
            "#sessionList .session-row.active"
        ).get_attribute("data-session-id")

        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/messages$")
        ):
            await page.locator("#newSessionButton").click()
        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/messages$")
        ):
            await page.locator(
                f'#sessionList .session-row[data-session-id="{first_session_id}"]'
            ).click()

        release_first_pending.set()
        await asyncio.wait_for(superseded_selection_continued.wait(), timeout=2)
        await expect(page.locator("#toast")).to_be_hidden()
        await browser.close()


@pytest.mark.asyncio
async def test_complete_browser_chat_workflow(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(live_url)

        await expect(page.locator("#workspaceSelect")).to_have_value("actual")
        await page.locator("#newSessionButton").click()
        await expect(page.locator("#sessionTitle")).to_have_text("新会话")

        await page.locator("#messageInput").fill("First request")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        await expect(page.locator("#executionPanel")).to_be_visible()
        await expect(page.locator("#executionPhase")).to_contain_text("保存")
        await expect(page.locator("#executionConnection")).to_have_text("已连接")
        await expect(page.locator("#serviceStatus")).to_contain_text("就绪")
        tool = page.locator(".tool-event").first
        await expect(tool).to_be_visible()
        await expect(tool.locator(".tool-business-summary")).to_be_visible()
        assert await tool.locator("details").get_attribute("open") is None
        await expect(tool.locator(".tool-duration")).to_have_text("0 ms")
        await expect(page.locator("#sessionStatus")).to_have_text("idle")
        await expect(page.locator("#stopButton")).to_be_hidden()
        await expect(page.locator("#sendButton")).to_be_visible()

        await page.reload()
        await expect(page.locator("#sessionList .session-row").first).to_contain_text(
            "First request"
        )
        await page.locator("#sessionList .session-row").first.click()
        await expect(page.locator(".message-user .message-body").first).to_have_text(
            "First request"
        )

        await page.locator("#attachmentInput").set_input_files(
            {
                "name": "notes.txt",
                "mimeType": "text/plain",
                "buffer": b"attachment contents",
            }
        )
        await expect(page.locator("#attachmentTray")).to_contain_text("notes.txt")
        await page.locator("#messageInput").fill("Read the file")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_timeout(250)
        await expect(page.locator("#mobileScrim")).to_be_hidden()
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        await page.locator("#sidebarToggle").click()
        await expect(page.locator("#sessionSidebar")).to_have_class(re.compile("open"))

        await browser.close()


@pytest.mark.asyncio
async def test_composer_autocompletes_skill_and_session_files(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        message = page.locator("#messageInput")
        menu = page.locator("#composerAutocomplete")
        await message.fill("/sum")
        await expect(menu).to_be_visible()
        await expect(menu).to_contain_text("summary")
        await message.press("Enter")
        await expect(message).to_have_value("/summary ")

        await message.fill("/summary review @Age")
        await expect(menu).to_contain_text("Agent.md")
        await message.press("Tab")
        await expect(message).to_have_value("/summary review @Agent.md")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )

        assert submitted[-1]["file_references"] == ["Agent.md"]
        await browser.close()


@pytest.mark.asyncio
async def test_skill_autocomplete_overlays_without_resizing_workbench(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        skills = [
            {
                "name": f"skill-{index:02d}",
                "description": "Long skill usage introduction " + ("x" * 400),
            }
            for index in range(12)
        ]

        async def route_skills(route) -> None:
            await route.fulfill(json={"items": skills})

        await page.route(re.compile(r".*/api/sessions/[^/]+/skills$"), route_skills)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        before = await page.evaluate(
            """() => ({
              bodyScrollWidth: document.body.scrollWidth,
              timelineHeight: document.querySelector(
                '.message-timeline'
              ).getBoundingClientRect().height,
              shellHeight: document.querySelector(
                '.composer-shell'
              ).getBoundingClientRect().height,
            })"""
        )
        await page.locator("#messageInput").fill("/")
        menu = page.locator("#composerAutocomplete")
        await expect(menu).to_be_visible()
        after = await page.evaluate(
            """() => {
              const timelineBounds = document.querySelector(
                '.message-timeline'
              ).getBoundingClientRect();
              const shellBounds = document.querySelector(
                '.composer-shell'
              ).getBoundingClientRect();
              const menuBounds = document.querySelector(
                '#composerAutocomplete'
              ).getBoundingClientRect();
              return {
                viewportWidth: window.innerWidth,
                bodyScrollWidth: document.body.scrollWidth,
                timelineHeight: timelineBounds.height,
                shellHeight: shellBounds.height,
                shellTop: shellBounds.top,
                menuBottom: menuBounds.bottom,
              };
            }"""
        )

        assert after["bodyScrollWidth"] == before["bodyScrollWidth"]
        assert after["bodyScrollWidth"] <= after["viewportWidth"]
        assert abs(after["timelineHeight"] - before["timelineHeight"]) <= 1
        assert abs(after["shellHeight"] - before["shellHeight"]) <= 1
        assert after["menuBottom"] <= after["shellTop"]
        await browser.close()


@pytest.mark.asyncio
class Test_skill_management_acceptance:
    async def test_skill_management_is_full_screen_and_returns_to_selected_session(
        self,
        skill_live_app: LiveApp,
    ) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 800})
            await page.goto(skill_live_app.url)
            await expect(page.locator("#workspaceSelect")).to_have_value("personal")
            await _create_browser_session(page)
            original_title = await page.locator("#sessionTitle").inner_text()

            await page.locator("#skillsButton").click()
            await expect(page.locator("#skillManagementView")).to_be_visible()
            await expect(page.locator(".app-shell")).to_be_hidden()
            await page.locator("#skillManagementBackButton").click()
            await expect(page.locator(".app-shell")).to_be_visible()
            await expect(page.locator("#sessionTitle")).to_have_text(original_title)
            await browser.close()

    async def test_personal_folder_and_zip_upload_default_enabled(
        self,
        skill_live_app: LiveApp,
        tmp_path: Path,
    ) -> None:
        directory = tmp_path / "folder-upload"
        (directory / "assets").mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            _managed_skill_content(
                "folder-upload", "Uploaded from a real browser directory"
            ),
            encoding="utf-8",
        )
        directory_bytes = b"\x00folder-artifact\xff"
        (directory / "assets" / "proof.bin").write_bytes(directory_bytes)
        archive_bytes = b"\x00archive-artifact\xff"
        archive = _skill_archive(
            "archive-upload",
            "Uploaded from real ZIP bytes",
            (("references/proof.bin", archive_bytes),),
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 800})
            await page.goto(skill_live_app.url)
            await page.locator("#skillsButton").click()

            async with page.expect_response(
                re.compile(
                    r".*/api/workspaces/personal/skills/import-directory$"
                )
            ) as directory_response_info:
                await page.locator("#personalSkillDirectoryInput").set_input_files(
                    str(directory)
                )
            directory_response = await directory_response_info.value
            directory_result = await directory_response.json()
            assert directory_response.status == 201
            assert directory_result["skill"]["enabled"] is True
            await expect(_skill_row(page, "personal", "folder-upload")).to_contain_text(
                "已开启"
            )

            archive_response = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "archive-upload",
                archive,
            )
            archive_result = await archive_response.json()
            assert archive_response.status == 201
            assert archive_result["skill"]["enabled"] is True
            await expect(_skill_row(page, "personal", "archive-upload")).to_contain_text(
                "已开启"
            )

            artifact_root = (
                skill_live_app.services.settings.resolved_skill_artifact_root
            )
            assert artifact_root.is_relative_to(
                skill_live_app.services.settings.app_data_dir
            )
            assert artifact_root.is_dir()
            payloads = _artifact_payloads(skill_live_app)
            assert any(
                payload.get("assets/proof.bin") == directory_bytes
                for payload in payloads
            )
            assert any(
                payload.get("references/proof.bin") == archive_bytes
                for payload in payloads
            )
            await browser.close()

    async def test_global_default_toggle_and_new_session_snapshot(
        self,
        skill_live_app: LiveApp,
    ) -> None:
        from app.skills.bundle import build_bundle

        description = "Global Skill enabled by default"
        bundle = build_bundle(
            _managed_skill_content("global-default", description).encode("utf-8"),
            (),
            skill_live_app.services.skills.limits,
        )
        published = await skill_live_app.services.skills.publish_trusted_global_bundle(
            bundle,
            created_by="manager",
            origin={"type": "browser_fixture"},
        )

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 800})
            await page.goto(skill_live_app.url)
            session_a = await _create_browser_session(page)
            first_response = await page.request.get(
                f"{skill_live_app.url}/api/sessions/{session_a}/skills"
            )
            first_descriptions = {
                item["name"]: item["description"]
                for item in (await first_response.json())["items"]
            }
            assert first_descriptions["global-default"] == description

            await page.locator("#skillsButton").click()
            row = _skill_row(page, "global", "global-default")
            await expect(row).to_contain_text("已开启")
            async with page.expect_response(
                lambda response: response.request.method == "PUT"
                and response.url.endswith(
                    f"/global-skills/{published.skill.id}/setting"
                )
            ):
                await row.get_by_role("button", name="关闭").click()
            await expect(row).to_contain_text("已关闭")
            await expect(page.locator("#skillChangesNotice")).to_have_text(
                "仅对新会话生效"
            )
            await page.locator("#skillManagementBackButton").click()

            session_b = await _create_browser_session(page)
            second_response = await page.request.get(
                f"{skill_live_app.url}/api/sessions/{session_b}/skills"
            )
            second_names = {
                item["name"] for item in (await second_response.json())["items"]
            }
            unchanged_response = await page.request.get(
                f"{skill_live_app.url}/api/sessions/{session_a}/skills"
            )
            unchanged_descriptions = {
                item["name"]: item["description"]
                for item in (await unchanged_response.json())["items"]
            }
            assert "global-default" not in second_names
            assert unchanged_descriptions["global-default"] == description
            await browser.close()

    async def test_authenticated_user_can_upload_global_skill(
        self,
        skill_live_app: LiveApp,
    ) -> None:
        from app.db.models import PlatformRoleBindingRecord

        async with skill_live_app.services.database.session() as db:
            assert (
                await db.get(
                    PlatformRoleBindingRecord,
                    ("manager", "skill_admin"),
                )
                is None
            )

        proof = b"\x00global-collaborator-artifact\xff"
        archive = _skill_archive(
            "shared-global",
            "Uploaded by an authenticated collaborator",
            (("assets/collaborator-proof.bin", proof),),
        )
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 800})
            await page.goto(skill_live_app.url)
            await page.locator("#skillsButton").click()
            await expect(page.locator("#globalSkillImportRoot")).to_be_visible()

            response = await _upload_archive(
                page,
                "#globalSkillArchiveInput",
                "shared-global",
                archive,
            )
            result = await response.json()
            assert response.status == 201
            assert result["skill"]["scope"] == "global"
            assert result["skill"]["enabled"] is True
            await expect(_skill_row(page, "global", "shared-global")).to_contain_text(
                "Uploaded by an authenticated collaborator"
            )
            assert any(
                payload.get("assets/collaborator-proof.bin") == proof
                for payload in _artifact_payloads(skill_live_app)
            )
            await page.locator("#skillManagementBackButton").click()
            session_id = await _create_browser_session(page)
            response = await page.request.get(
                f"{skill_live_app.url}/api/sessions/{session_id}/skills"
            )
            assert response.status == 200
            skills = {
                item["name"]: item["description"]
                for item in (await response.json())["items"]
            }
            assert skills["shared-global"] == (
                "Uploaded by an authenticated collaborator"
            )
            await browser.close()

    async def test_existing_session_keeps_old_skill_after_replacement(
        self,
        skill_live_app: LiveApp,
    ) -> None:
        old_description = "Description captured by Session A"
        new_description = "Description captured by Session B"
        first_archive = _skill_archive("session-pinned", old_description)
        second_archive = _skill_archive("session-pinned", new_description)

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 800})
            await page.goto(skill_live_app.url)
            await page.locator("#skillsButton").click()
            created = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "session-pinned",
                first_archive,
            )
            assert created.status == 201
            await page.locator("#skillManagementBackButton").click()
            session_a = await _create_browser_session(page)

            await page.locator("#skillsButton").click()
            conflict = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "session-pinned",
                second_archive,
            )
            assert conflict.status == 409
            await expect(page.locator("#skillImportConflictDialog")).to_be_visible()
            async with page.expect_response(
                re.compile(r".*/api/workspaces/personal/skills/import$")
            ) as overwritten_response_info:
                await page.locator("#skillImportOverwriteButton").click()
            overwritten_response = await overwritten_response_info.value
            assert overwritten_response.status == 200
            await page.locator("#skillManagementBackButton").click()
            session_b = await _create_browser_session(page)

            snapshots = {}
            for label, session_id in (("A", session_a), ("B", session_b)):
                response = await page.request.get(
                    f"{skill_live_app.url}/api/sessions/{session_id}/skills"
                )
                assert response.status == 200
                snapshots[label] = {
                    item["name"]: item["description"]
                    for item in (await response.json())["items"]
                }
            assert snapshots["A"]["session-pinned"] == old_description
            assert snapshots["B"]["session-pinned"] == new_description
            await browser.close()

    async def test_skill_import_conflict_overwrite_rename_and_cancel(
        self,
        skill_live_app: LiveApp,
    ) -> None:
        archives = {
            version: _skill_archive(
                "conflict-flow",
                f"Conflict flow version {version}",
                (("references/version.bin", version.encode("ascii")),),
            )
            for version in ("v1", "v2", "v3", "v4")
        }

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1200, "height": 800})
            await page.goto(skill_live_app.url)
            await page.locator("#skillsButton").click()

            created = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "conflict-flow",
                archives["v1"],
            )
            assert created.status == 201
            source_row = _skill_row(page, "personal", "conflict-flow")
            await expect(source_row).to_contain_text("Conflict flow version v1")

            conflict = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "conflict-flow",
                archives["v2"],
            )
            assert conflict.status == 409
            await expect(page.locator("#skillImportConflictDialog")).to_be_visible()
            async with page.expect_response(
                re.compile(r".*/api/workspaces/personal/skills/import$")
            ) as overwritten_response_info:
                await page.locator("#skillImportOverwriteButton").click()
            overwritten_response = await overwritten_response_info.value
            assert overwritten_response.status == 200
            await expect(source_row).to_contain_text("Conflict flow version v2")
            await expect(source_row).to_contain_text("已开启")

            conflict = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "conflict-flow",
                archives["v3"],
            )
            assert conflict.status == 409
            await expect(page.locator("#skillImportConflictDialog")).to_be_visible()
            await page.locator("#skillImportRenameInput").fill("conflict-flow-copy")
            async with page.expect_response(
                re.compile(r".*/api/workspaces/personal/skills/import$")
            ) as renamed_response_info:
                await page.locator("#skillImportRenameButton").click()
            renamed_response = await renamed_response_info.value
            renamed = await renamed_response.json()
            assert renamed_response.status == 201
            assert renamed["status"] == "renamed"
            assert renamed["skill"]["enabled"] is True
            copy_row = _skill_row(page, "personal", "conflict-flow-copy")
            await expect(copy_row).to_contain_text("Conflict flow version v3")
            await expect(copy_row).to_contain_text("已开启")

            conflict = await _upload_archive(
                page,
                "#personalSkillArchiveInput",
                "conflict-flow",
                archives["v4"],
            )
            assert conflict.status == 409
            await expect(page.locator("#skillImportConflictDialog")).to_be_visible()
            await page.locator("#skillImportCancelButton").click()
            await expect(page.locator("#skillImportConflictDialog")).to_be_hidden()
            await expect(source_row).to_contain_text("Conflict flow version v2")
            await expect(copy_row).to_contain_text("Conflict flow version v3")
            payloads = _artifact_payloads(skill_live_app)
            assert any(
                payload.get("references/version.bin") == b"v2"
                for payload in payloads
            )
            assert any(
                payload.get("references/version.bin") == b"v3"
                for payload in payloads
            )
            assert not any(
                payload.get("references/version.bin") == b"v4"
                for payload in payloads
            )
            await browser.close()
@pytest.mark.asyncio
@pytest.mark.parametrize("reselect_origin", [False, True], ids=["A-B", "A-B-A"])
async def test_deferred_session_create_cannot_cross_workspace_epoch(
    skill_live_app: LiveApp,
    reselect_origin: bool,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.add_init_script(
            r"""
            (() => {
              const nativeFetch = window.fetch.bind(window);
              window.__sessionRace = {
                pending: false,
                delivered: false,
                candidateLoads: [],
                release: null,
              };
              window.fetch = async (input, options = {}) => {
                const url = typeof input === 'string' ? input : input.url;
                const method = String(options.method || 'GET').toUpperCase();
                if (method === 'POST' && /\/api\/workspaces\/personal\/sessions$/.test(url)) {
                  window.__sessionRace.pending = true;
                  return new Promise((resolve) => {
                    window.__sessionRace.release = () => {
                      resolve(new Response(JSON.stringify({
                        id: 'deferred-personal-session',
                        workspace_id: 'personal',
                        title: 'Deferred',
                        title_source: 'default',
                        status: 'idle',
                        created_at: '2026-07-25T00:00:00Z',
                        updated_at: '2026-07-25T00:00:00Z',
                      }), {status: 201, headers: {'content-type': 'application/json'}}));
                      setTimeout(() => { window.__sessionRace.delivered = true; }, 0);
                    };
                  });
                }
                if (/\/api\/sessions\/deferred-personal-session\/skills$/.test(url)) {
                  window.__sessionRace.candidateLoads.push(url);
                }
                return nativeFetch(input, options);
              };
            })();
            """
        )
        await page.goto(skill_live_app.url)
        await expect(page.locator("#workspaceSelect")).to_have_value("personal")

        await page.locator("#newSessionButton").click()
        await page.wait_for_function("() => window.__sessionRace.pending")
        await page.locator("#workspaceSelect").select_option("team")
        await expect(page.locator("#workspaceSelect")).to_have_value("team")
        if reselect_origin:
            await page.locator("#workspaceSelect").select_option("personal")
            await expect(page.locator("#workspaceSelect")).to_have_value("personal")
        await page.evaluate("() => window.__sessionRace.release()")
        await page.wait_for_function("() => window.__sessionRace.delivered")

        expected_workspace = "personal" if reselect_origin else "team"
        await expect(page.locator("#workspaceSelect")).to_have_value(expected_workspace)
        await expect(page.locator("#sessionList .session-row.active")).to_have_count(0)
        await expect(
            page.locator(
                '#sessionList .session-row[data-session-id="deferred-personal-session"]'
            )
        ).to_have_count(0)
        assert await page.evaluate("() => window.__sessionRace.candidateLoads") == []
        await browser.close()


@pytest.mark.asyncio
async def test_skill_autocomplete_reaches_last_item_and_compacts_descriptions(
    skill_live_app: LiveApp,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(skill_live_app.url)
        await page.locator("#workspaceSelect").select_option("personal")
        await page.locator("#newSessionButton").click()
        message = page.locator("#messageInput")
        menu = page.locator("#composerAutocomplete")
        options = menu.locator('[role="option"]')
        before = await page.evaluate(
            """() => ({
              timelineHeight: document.querySelector('.message-timeline').getBoundingClientRect().height,
              shellHeight: document.querySelector('.composer-shell').getBoundingClientRect().height,
              inputHeight: document.querySelector('#messageInput').getBoundingClientRect().height,
            })"""
        )
        await message.fill("/")
        await expect(options).to_have_count(77)
        after = await page.evaluate(
            """() => ({
              timelineHeight: document.querySelector('.message-timeline').getBoundingClientRect().height,
              shellHeight: document.querySelector('.composer-shell').getBoundingClientRect().height,
              inputHeight: document.querySelector('#messageInput').getBoundingClientRect().height,
            })"""
        )
        for dimension in ("timelineHeight", "shellHeight", "inputHeight"):
            assert abs(after[dimension] - before[dimension]) <= 1

        first_description = options.first.locator(
            ".composer-autocomplete-description"
        )
        await expect(first_description).to_have_attribute(
            "title", "Usage introduction for personal-only " + ("x" * 200)
        )
        layout = await options.first.evaluate(
            """element => {
              const name = element.querySelector('.composer-autocomplete-name');
              const description = element.querySelector(
                '.composer-autocomplete-description'
              );
              const nameBounds = name.getBoundingClientRect();
              const descriptionBounds = description.getBoundingClientRect();
              return {
                display: getComputedStyle(element).display,
                whiteSpace: getComputedStyle(description).whiteSpace,
                overflow: getComputedStyle(description).overflow,
                textOverflow: getComputedStyle(description).textOverflow,
                descriptionScrollWidth: description.scrollWidth,
                descriptionClientWidth: description.clientWidth,
                nameRight: nameBounds.right,
                descriptionLeft: descriptionBounds.left,
                rowOverlap: Math.min(nameBounds.bottom, descriptionBounds.bottom)
                  - Math.max(nameBounds.top, descriptionBounds.top),
              };
            }"""
        )
        assert {
            key: layout[key]
            for key in ("display", "whiteSpace", "overflow", "textOverflow")
        } == {
            "display": "flex",
            "whiteSpace": "nowrap",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        }
        assert layout["descriptionScrollWidth"] > layout["descriptionClientWidth"]
        assert layout["nameRight"] <= layout["descriptionLeft"] + 1
        assert layout["rowOverlap"] > 0

        await menu.hover()
        await page.mouse.wheel(0, 10_000)
        await page.wait_for_function(
            """menu => Math.abs(
              menu.scrollTop - (menu.scrollHeight - menu.clientHeight)
            ) <= 1""",
            arg=await menu.element_handle(),
        )
        wheel_position = await menu.evaluate(
            "menu => menu.scrollTop + menu.clientHeight"
        )
        scroll_height = await menu.evaluate("menu => menu.scrollHeight")
        assert abs(wheel_position - scroll_height) <= 1

        await message.fill("/")
        await message.focus()
        await menu.evaluate("menu => { menu.scrollTop = 0; }")
        assert await menu.evaluate("menu => menu.scrollTop") == 0
        initial_bounds = await menu.evaluate(
            """menu => ({
              menuBottom: menu.getBoundingClientRect().bottom,
              lastTop: menu.lastElementChild.getBoundingClientRect().top,
            })"""
        )
        assert initial_bounds["lastTop"] >= initial_bounds["menuBottom"] - 1
        for _ in range(76):
            await message.press("ArrowDown")
        await expect(options.last).to_have_attribute("aria-selected", "true")
        bounds = await menu.evaluate(
            """menu => ({
              menuTop: menu.getBoundingClientRect().top,
              menuBottom: menu.getBoundingClientRect().bottom,
              lastTop: menu.lastElementChild.getBoundingClientRect().top,
              lastBottom: menu.lastElementChild.getBoundingClientRect().bottom,
            })"""
        )
        assert bounds["lastTop"] >= bounds["menuTop"] - 1
        assert bounds["lastBottom"] <= bounds["menuBottom"] + 1

        await page.locator("#workspaceSelect").select_option("team")
        await page.locator("#newSessionButton").click()
        await message.fill("/")
        await expect(menu).not_to_contain_text("personal-only")
        await browser.close()


@pytest.mark.asyncio
async def test_composer_trigger_pointer_escape_and_mobile_bounds(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        message = page.locator("#messageInput")
        menu = page.locator("#composerAutocomplete")
        await message.fill("text /sum")
        await expect(menu).to_be_hidden()

        await message.fill("/sum")
        await expect(menu).to_be_visible()
        await expect(message).to_have_attribute("aria-expanded", "true")
        await menu.get_by_role("option", name=re.compile("summary")).dispatch_event(
            "pointerdown"
        )
        await expect(message).to_have_value("/summary ")

        await message.fill("/sum")
        await expect(menu).to_be_visible()
        await message.press("Escape")
        await expect(menu).to_be_hidden()
        await expect(message).to_have_value("/sum")
        await expect(message).to_have_attribute("aria-expanded", "false")

        await page.set_viewport_size({"width": 390, "height": 844})
        await message.fill("/")
        await expect(menu).to_be_visible()
        box = await menu.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["y"] >= 0
        assert box["x"] + box["width"] <= 390
        assert box["y"] + box["height"] <= 844
        await browser.close()


@pytest.mark.asyncio
async def test_file_references_are_quoted_deduplicated_and_filtered(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        message = page.locator("#messageInput")
        menu = page.locator("#composerAutocomplete")
        quoted = '@"数据中心 Agent 入门材料.html"'
        await message.fill("@数据")
        await expect(menu).to_contain_text("数据中心 Agent 入门材料.html")
        await message.press("Tab")
        await expect(message).to_have_value(quoted)

        await message.fill(f"{quoted} @Age")
        await expect(menu).to_contain_text("Agent.md")
        await message.press("Tab")
        await message.fill(f"{quoted} @Agent.md @Age")
        await expect(menu).to_contain_text("Agent.md")
        await message.press("Tab")
        await expect(message).to_have_value(f"{quoted} @Agent.md @Agent.md")

        # Deleting the quoted token must also remove it from the submitted references.
        await message.fill("@Agent.md @Agent.md send")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        assert submitted[-1]["file_references"] == ["Agent.md"]
        await browser.close()


@pytest.mark.asyncio
async def test_composer_ignores_stale_file_responses(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def route_files(route) -> None:
            query = parse_qs(urlparse(route.request.url).query).get("q", [""])[0]
            if query == "Age":
                first_started.set()
                await release_first.wait()
                items = [{"path": "Agent.md"}]
            else:
                items = [{"path": "数据中心 Agent 入门材料.html"}]
            await route.fulfill(json={"items": items, "truncated": False})

        await page.route(re.compile(r".*/api/sessions/[^/]+/files\?.*$"), route_files)
        message = page.locator("#messageInput")
        menu = page.locator("#composerAutocomplete")
        await message.fill("@Age")
        await asyncio.wait_for(first_started.wait(), timeout=2)
        await message.fill("@数据")
        await expect(menu).to_contain_text("数据中心 Agent 入门材料.html")
        release_first.set()
        await page.wait_for_timeout(250)
        await expect(menu).to_contain_text("数据中心 Agent 入门材料.html")
        await expect(menu).not_to_contain_text("Agent.md")
        await browser.close()


@pytest.mark.asyncio
async def test_composer_does_not_send_ime_composition_enter(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        message = page.locator("#messageInput")
        await message.fill("输入法候选")
        await message.dispatch_event("compositionstart")
        await message.press("Enter")
        await page.wait_for_timeout(200)
        assert submitted == []
        await expect(message).to_have_value(re.compile(r"^输入法候选"))
        await message.dispatch_event("compositionend")
        await browser.close()


@pytest.mark.asyncio
async def test_composer_respects_an_already_prevented_enter(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await page.evaluate(
            """
            document.addEventListener("keydown", (event) => {
              if (event.target.id === "messageInput" && event.key === "Enter") {
                event.preventDefault();
              }
            }, true);
            """
        )
        message = page.locator("#messageInput")
        await message.fill("handled elsewhere")
        await message.press("Enter")
        await page.wait_for_timeout(200)
        assert submitted == []
        await expect(message).to_have_value("handled elsewhere")
        await browser.close()


@pytest.mark.asyncio
async def test_session_switch_rejects_stale_skills(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        skill_requests = 0

        async def route_skills(route) -> None:
            nonlocal skill_requests
            skill_requests += 1
            if skill_requests == 1:
                first_started.set()
                await release_first.wait()
                items = [{"name": "oldskill", "description": "old session"}]
            else:
                items = [{"name": "newskill", "description": "new session"}]
            await route.fulfill(json={"items": items})

        await page.route(re.compile(r".*/api/sessions/[^/]+/skills$"), route_skills)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await asyncio.wait_for(first_started.wait(), timeout=2)
        await expect(page.locator("#messageInput")).to_be_enabled()
        await page.locator("#newSessionButton").click()
        await expect(page.locator("#messageInput")).to_be_enabled()
        release_first.set()
        await page.wait_for_timeout(200)

        await page.locator("#messageInput").fill("/")
        menu = page.locator("#composerAutocomplete")
        await expect(menu).to_contain_text("newskill")
        await expect(menu).not_to_contain_text("oldskill")
        await browser.close()


@pytest.mark.asyncio
async def test_hung_skills_do_not_block_history_files_or_turn_stream(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        skills_started = asyncio.Event()
        release_skills = asyncio.Event()
        submitted = []

        async def hang_skills(route) -> None:
            skills_started.set()
            await release_skills.wait()
            await route.fulfill(json={"items": []})

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/skills$"), hang_skills)
        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await asyncio.wait_for(skills_started.wait(), timeout=2)
        try:
            message = page.locator("#messageInput")
            menu = page.locator("#composerAutocomplete")
            await expect(message).to_be_enabled()
            await expect(page.locator("#messageTimeline")).to_contain_text(
                "开始新的对话"
            )
            await message.fill("read @Age")
            await expect(menu).to_contain_text("Agent.md")
            await message.press("Tab")
            await page.locator("#sendButton").click()
            await expect(
                page.locator(".message-assistant .message-body").last
            ).to_have_text("hello world")
            assert submitted[-1]["file_references"] == ["Agent.md"]
        finally:
            release_skills.set()
            await browser.close()


@pytest.mark.asyncio
async def test_delayed_turn_success_does_not_mutate_the_new_session(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        submitted = []
        event_urls = []

        async def delay_first_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            if len(submitted) == 1:
                first_started.set()
                await release_first.wait()
            await route.continue_()

        async def capture_events(route) -> None:
            event_urls.append(route.request.url)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), delay_first_turn)
        await page.route(re.compile(r".*/api/turns/[^/]+/events$"), capture_events)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        message = page.locator("#messageInput")
        await message.fill("old session request")
        await page.locator("#sendButton").click()
        await asyncio.wait_for(first_started.wait(), timeout=2)

        await page.locator("#newSessionButton").click()
        await expect(message).to_be_enabled()
        await message.fill("new draft @Age")
        await expect(page.locator("#composerAutocomplete")).to_contain_text("Agent.md")
        await message.press("Tab")
        await page.locator("#attachmentInput").set_input_files(
            {
                "name": "new-session.txt",
                "mimeType": "text/plain",
                "buffer": b"new session attachment",
            }
        )
        await expect(page.locator("#attachmentTray")).to_contain_text(
            "new-session.txt"
        )

        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/turns$")
        ) as response_info:
            release_first.set()
        first_response = await response_info.value
        first_turn_id = (await first_response.json())["turn_id"]
        await page.wait_for_timeout(250)
        await expect(message).to_have_value("new draft @Agent.md")
        await expect(page.locator("#attachmentTray")).to_contain_text(
            "new-session.txt"
        )
        assert all(first_turn_id not in url for url in event_urls)

        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        assert submitted[-1]["file_references"] == ["Agent.md"]
        await browser.close()


@pytest.mark.asyncio
async def test_delayed_turn_failure_does_not_toast_the_new_session(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def fail_delayed_turn(route) -> None:
            first_started.set()
            await release_first.wait()
            await route.fulfill(
                status=500,
                json={"error": {"message": "old session failed"}},
            )

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/turns$"), fail_delayed_turn
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        message = page.locator("#messageInput")
        await message.fill("old session request")
        await page.locator("#sendButton").click()
        await asyncio.wait_for(first_started.wait(), timeout=2)

        await page.locator("#newSessionButton").click()
        await expect(message).to_be_enabled()
        await message.fill("new session draft")
        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/turns$")
        ):
            release_first.set()
        await page.wait_for_timeout(100)
        await expect(message).to_have_value("new session draft")
        await expect(page.locator("#toast")).to_be_hidden()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await browser.close()


@pytest.mark.asyncio
async def test_stale_history_failure_does_not_toast_current_session(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        message_requests = 0

        async def delay_first_history(route) -> None:
            nonlocal message_requests
            message_requests += 1
            if message_requests == 1:
                first_started.set()
                await release_first.wait()
                await route.fulfill(
                    status=500,
                    json={"error": {"message": "old history failed"}},
                )
                return
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/messages$"), delay_first_history
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await asyncio.wait_for(first_started.wait(), timeout=2)
        first_session_id = await page.locator(
            "#sessionList .session-row.active"
        ).get_attribute("data-session-id")
        await page.locator("#newSessionButton").click()
        await page.wait_for_function(
            """
            (oldId) => document.querySelector("#sessionList .session-row.active")
              ?.dataset.sessionId !== oldId
            """,
            arg=first_session_id,
        )
        message = page.locator("#messageInput")
        await expect(message).to_be_enabled()
        await message.fill("current draft")
        async with page.expect_response(
            re.compile(r".*/api/sessions/[^/]+/messages$")
        ):
            release_first.set()
        await page.wait_for_timeout(100)
        await expect(message).to_have_value("current draft")
        await expect(page.locator("#toast")).to_be_hidden()
        await browser.close()


@pytest.mark.asyncio
async def test_failed_turn_keeps_text_and_references_for_retry(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def fail_once(route) -> None:
            submitted.append(route.request.post_data_json)
            if len(submitted) == 1:
                await route.fulfill(
                    status=500,
                    json={"error": {"message": "retry this turn"}},
                )
                return
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), fail_once)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        message = page.locator("#messageInput")
        await message.fill("retry @Age")
        await expect(page.locator("#composerAutocomplete")).to_contain_text("Agent.md")
        await message.press("Tab")
        await page.locator("#sendButton").click()
        await expect(page.locator("#toast")).to_contain_text("retry this turn")
        await expect(message).to_have_value("retry @Agent.md")
        assert submitted[0]["file_references"] == ["Agent.md"]

        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )
        assert submitted[1]["file_references"] == ["Agent.md"]
        await browser.close()


@pytest.mark.asyncio
async def test_browser_reports_backend_disconnect(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        async def abort_turn_requests(route) -> None:
            await route.abort("failed")

        await page.route(
            re.compile(r".*/api/turns/[^/]+(?:/events)?$"),
            abort_turn_requests,
        )
        await page.locator("#messageInput").fill("Disconnect test")
        await page.locator("#sendButton").click()

        await expect(page.locator("#serviceStatus")).to_contain_text("已断开")
        await expect(page.locator("#executionConnection")).to_have_text("已断开")
        await expect(page.locator("#executionPanel")).to_be_visible()

        await browser.close()


@pytest.mark.asyncio
async def test_degraded_execution_preserves_history_and_recovers(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        degraded = True

        async def serve_health(route) -> None:
            execution_status = "degraded" if degraded else "ready"
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "status": execution_status,
                        "database": "ok",
                        "memory": "ok",
                        "workspace_count": 1,
                        "valid_workspace_count": 1,
                        "runtime": {},
                        "execution": {
                            "status": execution_status,
                            "worker": (
                                "unavailable" if degraded else "available"
                            ),
                            "last_seen_at": None,
                        },
                    }
                ),
            )

        await page.route("**/api/health", serve_health)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        await expect(page.locator("#sessionList .session-row")).to_have_count(1)
        await expect(page.locator("#serviceStatus")).to_contain_text(
            "执行服务不可用"
        )
        await expect(page.locator("#composerState")).to_have_text(
            "执行服务暂不可用，请稍后重试"
        )
        await expect(page.locator("#messageInput")).to_be_disabled()
        await expect(page.locator("#sendButton")).to_be_disabled()

        degraded = False
        await page.evaluate("refreshServiceHealth()")

        await expect(page.locator("#serviceStatus")).to_contain_text("就绪")
        await expect(page.locator("#messageInput")).to_be_enabled()
        await expect(page.locator("#sendButton")).to_be_enabled()
        await browser.close()


@pytest.mark.asyncio
async def test_browser_recovers_terminal_turn_when_only_sse_is_broken(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()

        async def abort_event_stream(route) -> None:
            await route.abort("failed")

        await page.route(
            re.compile(r".*/api/turns/[^/]+/events$"),
            abort_event_stream,
        )
        await page.locator("#messageInput").fill("SSE recovery test")
        await page.locator("#sendButton").click()

        await expect(page.locator("#stopButton")).to_be_hidden(timeout=7000)
        await expect(page.locator("#sendButton")).to_be_visible()
        await expect(page.locator("#sessionStatus")).to_have_text("idle")

        await browser.close()
