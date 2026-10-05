from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

from tests.browser.test_davinci_agui_mvp import _serve_app

pytestmark = pytest.mark.skip(
    reason=(
        "retired legacy Davinci V1 Mock; use a Native V2 live gate instead"
    ),
)


def _write_isolated_workspace(root: Path) -> None:
    workspace = root / "actual"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text(
        """version: 1
id: actual
name: Davinci Live Gate
description: Isolated qwen3.8-max AG-UI validation workspace.
skills: []
allowed_tools: [Read]
mcp_servers: {}
""",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_real_qwen_dashboard_capture_and_navigation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.config import Settings

    workspaces_root = tmp_path / "workspaces"
    _write_isolated_workspace(workspaces_root)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
    from app.main import create_app
    from app.runtime.claude import ClaudeAgentRuntime
    from demo.davinci_mock.app import create_mock_app

    settings = Settings(
        workspaces_root=workspaces_root,
        app_data_dir=tmp_path / "data",
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner"},
        turn_timeout_seconds=180,
    )
    assert settings.app_runtime_mode == "local_inline"
    assert settings.claude_model == "qwen3.8-max"
    assert settings.anthropic_api_key is not None

    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=15)
    runtime = ClaudeAgentRuntime(settings, frontend_tool_bridges=registry)
    agent_app = create_app(
        settings=settings,
        runtime=runtime,
        frontend_tool_bridges=registry,
    )
    async with _serve_app(agent_app, "/api/health") as agent, _serve_app(
        create_mock_app(agent.url), "/health"
    ) as mock, async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"{mock.url}/dashboard/1024")
        await page.locator("#agentLauncher").click()
        frame = page.frame_locator("#agentFrame")
        await frame.locator("#agentNewSessionButton").click()
        await expect(frame.locator("#agentStatus")).to_contain_text("Session")
        await page.locator("#agentFrame").evaluate(
            "element => { element.dataset.instanceToken = 'live-stable-frame'; }"
        )
        session_id = await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        )

        await frame.locator("#agentMessageInput").fill(
            "请调用仪表盘工具读取当前数据，并简洁解读三个核心指标。"
        )
        await frame.locator("#agentSendButton").click()
        await expect(
            frame.locator(".agent-tool").filter(
                has_text="dashboard.capture_current_view"
            ).first
        ).to_be_visible(
            timeout=60_000,
        )
        await expect(frame.locator("#agentStatus")).to_have_text(
            "就绪", timeout=180_000
        )
        answers = await frame.locator(".agent-message.assistant").all_inner_texts()
        normalized = "\n".join(answers).replace(",", "")
        assert "4734" in normalized
        assert "-10.88" in normalized
        assert "40388380" in normalized
        event_types = await frame.locator("body").evaluate(
            "() => window.__davinciMvp.eventTypes"
        )
        assert event_types.index("TOOL_CALL_START") < event_types.index(
            "TEXT_MESSAGE_CONTENT"
        )

        await frame.locator("#agentMessageInput").fill("调用导航工具打开数据集页面。")
        await frame.locator("#agentSendButton").click()
        await expect(page).to_have_url(f"{mock.url}/datasets", timeout=60_000)
        await expect(frame.locator("#agentStatus")).to_have_text("就绪", timeout=120_000)
        assert await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        ) == session_id

        tools = frame.locator(".agent-tool")
        previous_tool_count = await tools.count()
        await frame.locator("#agentMessageInput").fill(
            "请从数据集页面导航回仪表盘，读取当前仪表盘数据，并简洁解读三个核心指标。"
        )
        await frame.locator("#agentSendButton").click()
        await expect(tools).to_have_count(previous_tool_count + 2, timeout=120_000)
        assert (await tools.all_inner_texts())[-2:] == [
            "工具 · navigateTo",
            "工具 · dashboard.capture_current_view",
        ]
        await expect(page).to_have_url(f"{mock.url}/dashboard/1024", timeout=60_000)
        await expect(frame.locator("#agentStatus")).to_have_text("就绪", timeout=180_000)
        reverse_answer = (
            await frame.locator(".agent-message.assistant").last.inner_text()
        ).replace(",", "")
        assert "4734" in reverse_answer
        assert "-10.88" in reverse_answer
        assert "40388380" in reverse_answer
        assert await page.locator("#agentFrame").get_attribute(
            "data-instance-token"
        ) == "live-stable-frame"
        assert await frame.locator("body").evaluate(
            "() => localStorage.getItem('davinci-mvp-session')"
        ) == session_id
        await browser.close()
