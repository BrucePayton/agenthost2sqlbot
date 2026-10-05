"""Live gate: each intent reaches the intended Skill or direct page Tool.

拆分把一个万能 Skill 拆成五个，路由质量成了新的失败面。本 gate 用真实模型和
真实 CLAUDE.md 跑核心意图，并覆盖不应新增 Skill 的个人仪表盘分组动作。

    RUN_LIVE_DAVINCI_SKILL_ROUTING=1 pytest tests/live/test_davinci_skill_routing.py
"""

import json
import os
import shutil
import uuid
from pathlib import Path

import httpx
import pytest

from app.agui.contracts import CONTRACT_PATH, load_contract_registry

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SKILL_ROUTING") != "1",
    reason="Set RUN_LIVE_DAVINCI_SKILL_ROUTING=1 to call the live model.",
)

SKILLS = (
    "configure-dashboard-widget",
    "configure-subscription-rule",
    "interpret-dashboard",
    "locate-data",
    "manage-space",
)

HOT_TOOLS = (
    "page.get_context",
    "workspace.list_dashboards",
    "ui.open_dashboard",
    "dashboard.get_structure",
    "dashboard.get_widget_config",
    "dashboard.get_widget_data",
    "dashboard.apply_widget_spec",
)

MESSAGE_RULE_TOOLS = (
    "space.message_rule.get_context",
    "space.message_rule.search_options",
    "space.message_rule.start_draft",
    "space.message_rule.apply_draft",
    "space.message_rule.review_draft",
    "space.message_rule.save_draft",
)

SPACE_DASHBOARD_PAGE = {
    "instanceId": "routing-live-gate-page",
    "kind": "collaborative-space",
    "route": "/share/collaborative-space/83/dashboard",
    "space": {"id": "83", "name": "Live Gate Space", "role": "owner"},
    "viewMode": "self",
}

PERSONAL_DASHBOARD_PAGE = {
    "instanceId": "routing-live-gate-personal",
    "kind": "dashboard",
    "route": "/share/workbench-new",
    "resource": {"type": "dashboard", "id": "1966", "name": "海外数据"},
    "viewMode": "self",
}

# (意图, 期望 Skill, 期望直接页面 Tool, 页面, 额外工具)
ROUTING_CASES = (
    (
        # Unbound metric discovery is covered with the real MCP contract in
        # test_davinci_feedback_regressions; this fixture has no Data MCP tools.
        "查看当前已绑定组件 daily 的可编辑样式，不更改数据口径。",
        "configure-dashboard-widget",
        None,
        SPACE_DASHBOARD_PAGE,
        (),
    ),
    (
        "帮我解读一下这个看板，最近是不是出问题了？",
        "interpret-dashboard",
        None,
        SPACE_DASHBOARD_PAGE,
        (),
    ),
    (
        "我们有没有跟奢侈品回收订单相关的数据集？成交金额字段叫什么？",
        "locate-data",
        None,
        SPACE_DASHBOARD_PAGE,
        (),
    ),
    (
        "把这个空间的成员看一下，我想把其中一个人提成管理员。",
        "manage-space",
        None,
        SPACE_DASHBOARD_PAGE,
        (),
    ),
    (
        "每天早上 9 点把这个看板的结算价推送给我，帮我建一条订阅。",
        "configure-subscription-rule",
        None,
        PERSONAL_DASHBOARD_PAGE,
        MESSAGE_RULE_TOOLS,
    ),
    (
        "在个人空间的仪表盘下面新建一个分组，名字叫海外经营。",
        None,
        "workspace.dashboard_group.create",
        PERSONAL_DASHBOARD_PAGE,
        ("workspace.dashboard_group.create",),
    ),
)


def _write_live_workspace(root: Path) -> None:
    """复制真实 workspace（五个 Skill + 真实 CLAUDE.md），但去掉 MCP 依赖。"""
    source = Path(__file__).resolve().parents[2] / "workspaces/davinci-dashboard"
    workspace = root / "actual"
    for skill in SKILLS:
        shutil.copytree(
            source / ".claude/skills" / skill,
            workspace / ".claude/skills" / skill,
        )
    shutil.copy2(source / "CLAUDE.md", workspace / "CLAUDE.md")
    skills_yaml = "\n".join(f"  - {name}" for name in SKILLS)
    (workspace / "workspace.yaml").write_text(
        "version: 1\n"
        "id: actual\n"
        "name: Davinci Skill routing live gate\n"
        "description: Isolated live gate for five-Skill routing.\n"
        "model: deepseek-v4-pro-0813\n"
        "skills:\n"
        f"{skills_yaml}\n"
        "allowed_tools:\n"
        "  - Read\n"
        "  - Grep\n"
        "  - Skill\n"
        "mcp_servers: {}\n",
        encoding="utf-8",
    )


def _native_body(
    session_id: str,
    run_id: str,
    prompt: str,
    page: dict,
    extra_tools: tuple[str, ...],
) -> dict:
    registry = load_contract_registry(
        CONTRACT_PATH.with_name("davinci-agent-v2.json")
    )
    tools = []
    for name in HOT_TOOLS + tuple(extra_tools):
        contract = registry.get(name)
        tools.append(
            {
                "name": contract.action,
                "description": contract.description,
                "parameters": dict(contract.input_schema),
            }
        )
    return {
        "threadId": session_id,
        "runId": run_id,
        "state": {
            "schemaVersion": "davinci-page-state-v1",
            "page": page,
            "permissions": {
                "canRead": True,
                "canOperate": True,
                "canPersist": True,
                "workspaceCapabilities": {
                    "canCreateSpace": True,
                    "canCreateDashboardGroup": "space" not in page,
                },
            },
            "ui": {"busy": False, "activeFilters": []},
            "revisions": {"routeRevision": 1, "resourceRevision": 1},
            "dataStatus": {"loadingWidgetIds": [], "errorWidgetIds": []},
        },
        "messages": [
            {"id": f"routing-{run_id}", "role": "user", "content": prompt}
        ],
        "tools": tools,
        "context": [],
        "forwardedProps": {
            "workspaceId": "actual",
            "profile": "davinci-agui-native-v2",
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,expected_skill,expected_action,page,extra_tools", ROUTING_CASES
)
async def test_intent_routes_to_expected_skill_or_action(
    prompt: str,
    expected_skill: str | None,
    expected_action: str | None,
    page: dict,
    extra_tools: tuple[str, ...],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import Settings

    workspaces_root = tmp_path / "workspaces"
    _write_live_workspace(workspaces_root)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    from app.main import create_app

    settings = Settings(
        workspaces_root=workspaces_root,
        app_data_dir=tmp_path / "data",
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner"},
        turn_timeout_seconds=180,
        claude_model="deepseek-v4-pro-0813",
        claude_selectable_models="deepseek-v4-pro-0813",
    )
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=240,
    ) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        run_id = str(uuid.uuid4())
        response = await client.post(
            "/api/ag-ui",
            headers={"Accept": "text/event-stream"},
            json=_native_body(session["id"], run_id, prompt, page, extra_tools),
        )
        messages = (
            await client.get(f"/api/sessions/{session['id']}/messages")
        ).json()

    skill_calls = [
        json.loads(message["payload"]["input_preview"])
        for message in messages
        if message["event_type"] == "tool.started"
        and message["payload"]["name"] == "Skill"
    ]
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    frontend_calls = [
        event["toolCallName"]
        for event in events
        if event.get("type") == "TOOL_CALL_START"
    ]

    assert response.status_code == 200
    if expected_skill is not None:
        assert skill_calls, "模型没有调用任何 Skill"
        assert skill_calls[0]["skill"] == expected_skill, (
            f"意图「{prompt}」路由到了 {skill_calls[0]['skill']}，"
            f"预期 {expected_skill}"
        )
        return

    assert expected_action is not None
    assert not skill_calls, f"意图「{prompt}」不应加载新 Skill"
    assert frontend_calls == [expected_action]
