import hashlib
import re
from pathlib import Path

import pytest

from tests.test_api import api_client


@pytest.mark.asyncio
async def test_workbench_page_contains_required_accessible_controls(
    settings_factory,
) -> None:
    async with api_client(settings_factory) as client:
        response = await client.get("/")

    assert response.status_code == 200
    html = response.text
    for element_id in (
        "workspaceSelect",
        "workspaceKind",
        "sessionIdValue",
        "copySessionIdButton",
        "sessionContextButton",
        "sessionPerformanceButton",
        "skillsButton",
        "newSessionButton",
        "sessionList",
        "messageTimeline",
        "attachmentInput",
        "messageInput",
        "composerAutocomplete",
        "composerAutocompleteStatus",
        "sendButton",
        "stopButton",
        "executionPanel",
        "executionPhase",
        "executionElapsed",
        "executionHeartbeat",
        "executionConnection",
        "executionSteps",
        "renameDialog",
        "deleteDialog",
        "skillManagementView",
        "skillManagementBackButton",
        "globalSkillList",
        "personalSkillList",
        "personalSkillImportButton",
        "personalSkillDirectoryInput",
        "personalSkillArchiveInput",
        "globalSkillImportRoot",
        "globalSkillImportButton",
        "globalSkillDirectoryInput",
        "globalSkillArchiveInput",
        "skillDetailPanel",
        "skillDetailManifest",
        "skillChangesNotice",
        "skillImportConflictDialog",
        "skillImportConflictName",
        "skillImportConflictExistingHash",
        "skillImportConflictIncomingHash",
        "skillImportRenameInput",
        "skillImportOverwriteButton",
        "skillImportRenameButton",
        "skillImportCancelButton",
        "skillImportConflictError",
        "sessionContextDialog",
        "sessionContextContent",
        "sessionPerformanceDialog",
        "sessionPerformanceContent",
    ):
        assert f'id="{element_id}"' in html
    assert 'aria-label="上传附件"' in html
    assert 'aria-label="发送消息"' in html
    assert 'role="listbox"' in html
    assert 'aria-autocomplete="list"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-live="polite"' in html
    assert 'aria-label="管理 Workspace Skills"' in html
    assert 'class="data-agent-nav" href="/data-agents"' in html
    assert re.search(
        r'<div class="workbench">.*<div class="app-shell">.*'
        r'<section id="skillManagementView"[^>]*hidden',
        html,
        re.DOTALL,
    )
    assert "仅对新会话生效" in html
    for prefix, suffix in (
        ("skillManager", "Dialog"),
        ("skillManager", "NewButton"),
        ("skillManager", "CopyButton"),
        ("skillManager", "CopyTarget"),
        ("skillManager", "EditorPanel"),
        ("skillManager", "EditorTitle"),
        ("skillManager", "Editor"),
        ("skillManager", "SaveButton"),
    ):
        assert f'id="{prefix}{suffix}"' not in html
    assert (
        'id="personalSkillDirectoryInput" type="file" webkitdirectory multiple' in html
    )
    assert 'id="globalSkillDirectoryInput" type="file" webkitdirectory multiple' in html
    assert 'aria-labelledby="skillImportConflictTitle"' in html
    assert 'for="skillImportRenameInput"' in html
    assert 'data-max-files-per-turn="5"' in html
    assert html.index('/static/composer-autocomplete.js') < html.index(
        '/static/composer-paste.js'
    ) < html.index('/static/skill-manager.js') < html.index(
        '/static/session-inspector.js'
    ) < html.index('/static/app.js')
    assert "onclick=" not in html
    assert "onchange=" not in html
    assert "onsubmit=" not in html
    assert "top-secret-test-key" not in html


@pytest.mark.asyncio
async def test_data_agent_management_page_exposes_creation_workflow(
    settings_factory,
) -> None:
    async with api_client(settings_factory) as client:
        response = await client.get("/data-agents")

    assert response.status_code == 200
    assert "创建数据 Agent" in response.text
    assert 'id="createForm"' in response.text
    assert 'id="datasets"' in response.text
    assert 'href="/"' in response.text


@pytest.mark.asyncio
async def test_development_obid_workbench_renders_debug_identity_controls(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(app_env="development", identity_mode="obid")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert 'id="debugIdentityPanel"' in response.text
    assert 'id="debugObIdSelect"' in response.text
    assert 'id="debugSessionLocatorInput"' in response.text
    assert '/static/debug-identity.js' in response.text


@pytest.mark.asyncio
async def test_non_development_workbench_omits_debug_identity_controls(
    settings_factory,
) -> None:
    async with api_client(settings_factory) as client:
        response = await client.get("/")

    assert 'id="debugIdentityPanel"' not in response.text
    assert '/static/debug-identity.js' not in response.text


@pytest.mark.asyncio
async def test_uat_obid_embed_page_marks_unverified_identity_boundary(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        app_env="uat",
        identity_mode="obid",
        database_url="postgresql+asyncpg://agent:test@localhost/agent",
    )
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/embed")

    assert response.status_code == 200
    assert 'data-security-marker="UAT_OBID_UNVERIFIED"' in response.text


@pytest.mark.asyncio
async def test_workbench_static_assets_are_served(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        css = await client.get("/static/app.css")
        autocomplete = await client.get("/static/composer-autocomplete.js")
        paste = await client.get("/static/composer-paste.js")
        skill_manager = await client.get("/static/skill-manager.js")
        javascript = await client.get("/static/app.js")

    assert css.status_code == 200
    assert "app-shell" in css.text
    assert autocomplete.status_code == 200
    assert "createController" in autocomplete.text
    assert paste.status_code == 200
    assert "extractImageFiles" in paste.text
    assert skill_manager.status_code == 200
    assert "createCandidateController" in skill_manager.text
    assert javascript.status_code == 200
    assert "EventSource" in javascript.text
    assert '"turn.progress"' in javascript.text
    assert '"turn.interrupted"' in javascript.text
    assert "个人记忆" in javascript.text
    assert "仅当前用户和当前 Workspace 可用" in javascript.text
    assert 'case "frontend_tool.deferred"' in javascript.text
    assert "SessionInspector.isVisibleUserMessage" in javascript.text
    assert "续跑时 Agent Host 注入上下文" in javascript.text
    assert "fetchEventStream" in javascript.text
    assert "new EventSource" not in javascript.text


@pytest.mark.asyncio
async def test_embed_page_is_standalone_and_contains_agent_controls(
    settings_factory,
) -> None:
    async with api_client(settings_factory) as client:
        response = await client.get("/embed")

    assert response.status_code == 200
    html = response.text
    for element_id in (
        "assistantPanel",
        "agentRail",
        "agentInspector",
        "idleView",
        "taskView",
        "compactContextBar",
        "capabilityDrawer",
        "sizeMenu",
        "closeAssistant",
        "composerModelTrigger",
        "agentTimeline",
        "agentMessageInput",
        "agentSendButton",
        "agentStopButton",
        "agentSkillsButton",
        "agentSkillCount",
        "agentNewSessionButton",
        "agentSessionIdentity",
        "agentSessionId",
        "agentCopySessionIdButton",
        "agentStatus",
        "skillManagementView",
        "skillManagementBackButton",
        "globalSkillList",
        "personalSkillList",
        "personalSkillDirectoryInput",
        "personalSkillArchiveInput",
        "skillChangesNotice",
        "skillImportConflictDialog",
    ):
        assert f'id="{element_id}"' in html
    header_actions = re.search(
        r'<div class="assistant-head-actions">(.*?)<div class="assistant-body"',
        html,
        re.DOTALL,
    )
    assert header_actions is not None
    actions = header_actions.group(1)
    # 头部按钮顺序：新对话 → 任务历史 → Skill 管理 → 窗口大小 → 关闭
    positions = [
        actions.index(f'id="{element_id}"')
        for element_id in (
            "agentNewSessionButton",
            "headerTaskHistory",
            "agentSkillsButton",
            "sizeMenu",
            "closeAssistant",
        )
    ]
    assert positions == sorted(positions)
    assert re.search(
        r'<section id="skillManagementView"[^>]*hidden', html
    )
    assert re.search(
        r'<link rel="stylesheet" href="/static/embed\.css\?v=[0-9a-f]{16}">',
        html,
    )
    assert re.search(
        r'<script type="module" src="/static/embed\.js\?v=[0-9a-f]{16}"></script>',
        html,
    )
    assert re.search(
        r'<script src="/static/skill-manager\.js\?v=([0-9a-f]{16})"></script>',
        html,
    )
    revisions = re.findall(r'\?v=([0-9a-f]{16})', html)
    assert len(set(revisions)) == 1
    assert html.index("/static/skill-manager.js") < html.index("/static/embed.js")
    assert "/static/app.js" not in html
    assert "top-secret-test-key" not in html


@pytest.mark.asyncio
async def test_embed_page_versions_static_assets_from_their_current_content(
    settings_factory,
) -> None:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for asset_name in ("embed.css", "embed.js", "skill-manager.js"):
        digest.update(asset_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / "app" / "web" / "static" / asset_name).read_bytes())
        digest.update(b"\0")
    revision = digest.hexdigest()[:16]

    async with api_client(settings_factory) as client:
        response = await client.get("/embed")

    assert response.status_code == 200
    assert f'/static/embed.css?v={revision}' in response.text
    assert f'/static/embed.js?v={revision}' in response.text
    assert f'/static/skill-manager.js?v={revision}' in response.text


@pytest.mark.asyncio
async def test_embed_static_assets_are_served(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        css = await client.get("/static/embed.css")
        javascript = await client.get("/static/embed.js")

    assert css.status_code == 200
    assert "assistant-panel" in css.text
    assert javascript.status_code == 200
    assert 'url: "/api/ag-ui"' in javascript.text
    assert "window.parent.postMessage" in javascript.text
    assert "createController" in javascript.text
    assert "agentSkillsButton" in javascript.text


def test_embed_static_bundle_contains_current_contract_digests() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = (root / "app" / "web" / "static" / "embed.js").read_text(
        encoding="utf-8"
    )
    for generated_name in (
        "davinci-contracts.js",
        "davinci-contracts-v2.js",
    ):
        generated = (
            root / "web" / "shared" / "generated" / generated_name
        ).read_text(encoding="utf-8")
        match = re.search(r"CONTRACT_DIGEST = '([^']+)'", generated)
        assert match is not None
        assert match.group(1) in bundle

    for action in (
        "ui.open_space_page",
        "space.message_rule.get_context",
        "space.message_rule.search_options",
        "space.message_rule.start_draft",
        "space.message_rule.apply_draft",
        "space.message_rule.review_draft",
        "space.message_rule.save_draft",
        "dashboard.open_data_alert_config",
    ):
        assert action in bundle


async def test_skill_manager_offers_the_workspace_instructions_editor(
    settings_factory,
) -> None:
    """Skill 管理页要能编辑本工作区的 CLAUDE.md，与两个 Skill 区块并列。"""
    async with api_client(settings_factory) as client:
        embed = await client.get("/embed")
        workbench = await client.get("/")
        script = await client.get("/static/skill-manager.js")

    for response in (embed, workbench):
        assert response.status_code == 200
        html = response.text
        for element_id in (
            "workspaceInstructionsSection",
            "workspaceInstructionsEditor",
            "workspaceInstructionsSave",
            "workspaceInstructionsReset",
            "workspaceInstructionsStatus",
        ):
            assert f'id="{element_id}"' in html, element_id
        assert "工作区指令" in html

    assert script.status_code == 200
    body = script.text
    assert "/instructions" in body
    # 保存要带乐观锁，别把别处的编辑悄悄覆盖掉。
    assert "expected_hash" in body

    # 两个页面各有自己的装配代码：workbench 走 app.js，面板走打包后的 embed.js。
    # 只测共享的 skill-manager.js 会漏掉「区块渲染出来了但没人初始化」这种情况。
    async with api_client(settings_factory) as client:
        app_js = await client.get("/static/app.js")
        embed_js = await client.get("/static/embed.js")

    for bundle in (app_js, embed_js):
        assert bundle.status_code == 200
        assert "createInstructionsController" in bundle.text
        assert "workspaceInstructionsEditor" in bundle.text
        assert "setInstructionsEditor" in bundle.text
