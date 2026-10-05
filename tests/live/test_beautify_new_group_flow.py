"""Real Host/model, isolated workspace, simulated page tools; never a live dashboard.

RUN_LIVE_BEAUTIFY_GROUPING=1 pytest tests/live/test_beautify_new_group_flow.py
The managed workspace model is used unless DAVINCI_BEAUTIFY_EVAL_MODEL is set.
DAVINCI_BEAUTIFY_SKILL_REF=HEAD evaluates pre-edit skill text in the temp copy.
"""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
import yaml
from jsonschema import validate

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.test_davinci_skill_routing import PERSONAL_DASHBOARD_PAGE, _native_body
from tests.live.test_subscription_agui_qwen import _frontend_calls

ROOT = Path(__file__).resolve().parents[2]
CARDS = [
    ("m1", "销售额", "metric"), ("m2", "订单数", "metric"),
    ("t1", "日销售趋势", "line"), ("c1", "城市分布", "bar"),
    ("d1", "订单明细", "table"),
]
LIVE = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_BEAUTIFY_GROUPING") != "1",
    reason="Set RUN_LIVE_BEAUTIFY_GROUPING=1 to call the actual workspace model.",
)


def structure_receipt(existing_flat=False):
    widgets = [
        {"widgetId": widget_id, "title": title, "type": kind,
         "parentId": "flat-old" if existing_flat and widget_id in {"m1", "m2"} else None,
         "coordinateSpace": "container" if existing_flat and widget_id in {"m1", "m2"} else "root",
         "layoutEditable": True,
         "layout": {"x": 0, "y": i * 4, "width": 12, "height": 4, "order": i}}
        for i, (widget_id, title, kind) in enumerate(CARDS)
    ]
    if existing_flat:
        widgets.insert(0, {
            "widgetId": "flat-old", "title": "旧经营总览", "type": "flatLayout",
            "parentId": None, "coordinateSpace": "root", "layoutEditable": True,
            "layout": {"x": 0, "y": 0, "width": 24, "height": 8, "order": 0},
        })
    return {
        "summary": ("完整结构，含一个原生平铺且无Tab；type是实际chartType。"
                    if existing_flat else "完整结构，无平铺或Tab容器；type是实际chartType。") +
                   "所有卡片数据及非布局配置必须保留。",
        "resourceRevision": 8, "totalCount": len(widgets),
        "rootCount": 4 if existing_flat else 5,
        "childCount": 2 if existing_flat else 0,
        "returnedCount": len(widgets), "hasMore": False,
        "widgets": widgets,
    }


class Flow:
    def __init__(self, client, body, registry, report_path, metadata):
        self.client, self.body, self.registry = client, body, registry
        self.calls = []
        self.replies = []
        self.report_path, self.metadata = report_path, metadata
        self.runtime_error = None
        self.write_report()

    def write_report(self):
        self.report_path.write_text(json.dumps({
            **self.metadata, "execution": "real-model-real-host-simulated-page-tools",
            "replies": self.replies, "calls": self.calls,
            "runtimeErrorCategory": self.runtime_error,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    async def turn(self, prompt):
        self.body["messages"] = [{"id": str(uuid.uuid4()), "role": "user", "content": prompt}]
        for _ in range(8):
            self.body["runId"] = str(uuid.uuid4())
            response = await self.client.post(
                "/api/ag-ui", headers={"Accept": "text/event-stream"}, json=self.body,
            )
            assert response.status_code == 200, "Host request failed (raw response omitted)"
            events = [json.loads(line[6:]) for line in response.text.splitlines()
                      if line.startswith("data: ")]
            errors = [e for e in events if e.get("type") == "RUN_ERROR"]
            if errors:
                # Classify locally; do not persist raw errors, settings or stderr.
                raw = json.dumps(errors).lower()
                self.runtime_error = next((category for category, words in [
                    ("timeout", ("timeout", "timed out")),
                    ("authentication", ("unauthorized", "authentication", "401")),
                    ("connection", ("connection", "network", "enotfound", "eacces")),
                ] if any(word in raw for word in words)), "runtime-error")
                self.write_report()
                pytest.fail(f"Actual model/runtime failed: {self.runtime_error}; raw diagnostics omitted")
            calls, assistant = _frontend_calls(response.text)
            if not calls:
                assert assistant, "Model returned neither page tools nor assistant text"
                self.replies.append(assistant)
                self.write_report()
                return assistant
            self.calls.extend(calls)
            self.write_report()
            self.body["messages"] = [self.receipt(call) for call in calls]
        pytest.fail("Model exceeded bounded read/continuation turns")

    def receipt(self, call):
        name, arguments = call["name"], call["arguments"]
        validate(arguments, dict(self.registry.get(name).input_schema))
        if name == "dashboard.get_structure":
            data = structure_receipt(self.metadata.get("existingFlat", False))
            validate(data, dict(self.registry.get(name).output_schema))
            envelope = {"status": "success", "data": data, "observed": {}, "issues": []}
        elif name == "dashboard.get_widget_config":
            requested = arguments.get("widgetIds", [arguments.get("widgetId")])
            configs = [{"widgetId": wid, "summary": f"{title}；实际chartType={kind}；原数据与样式不变",
                        "effectiveSpec": {"title": title, "chartType": kind}}
                       for wid, title, kind in CARDS if wid in requested]
            data = {"summary": "完整的指定组件配置", "widgets": configs}
            if "widgetId" in arguments:
                data = {key: value for key, value in configs[0].items() if key != "widgetId"}
            validate(data, dict(self.registry.get(name).output_schema))
            envelope = {"status": "success", "data": data, "observed": {}, "issues": []}
        elif name == "dashboard.set_widget_layout":
            preview = arguments.get("dryRun", False)
            groups = arguments["preset"].get("groups", [])
            data = {"resourceId": "1966", "resourceRevision": 9 if groups and not preview else 8,
                    "persisted": bool(groups) and not preview, "preview": preview,
                    "layoutChangeCount": 0}
            if groups:
                data["grouping"] = {"groups": [dict(group, containerWidgetId=f"new-{i}")
                                                for i, group in enumerate(groups)]}
                if arguments["preset"].get("regroup"):
                    data["grouping"]["removedContainerWidgetIds"] = \
                        arguments["preset"]["regroup"]["containerWidgetIds"]
            envelope = {"status": "success", "data": data, "issues": []}
            validate(envelope, dict(self.registry.get(name).output_schema))
        else:
            pytest.fail(f"Unexpected tool in isolated grouping flow: {name}")
        return {"id": str(uuid.uuid4()), "role": "tool", "toolCallId": call["id"],
                "content": json.dumps(envelope, ensure_ascii=False)}

    def layouts(self):
        return [call["arguments"] for call in self.calls
                if call["name"] == "dashboard.set_widget_layout"]


@LIVE
@pytest.mark.asyncio
@pytest.mark.parametrize("choice,decision,existing_flat", [
    ("3A", "save", False), ("3B", "save", False),
    ("3C，先预览", "preview", False),
    ("3B，只排序、不分组", "only-sort", False),
    ("3C", "save", True),
])
async def test_option_three_produces_one_grouping_result_without_follow_up(
    choice, decision, existing_flat, tmp_path, monkeypatch
):
    from app.config import Settings

    source = ROOT / "workspaces/davinci-dashboard"
    model = os.environ.get("DAVINCI_BEAUTIFY_EVAL_MODEL") or yaml.safe_load(
        (source / "workspace.yaml").read_text(encoding="utf-8")
    )["model"]
    workspace = tmp_path / "workspaces/actual"
    shutil.copytree(source / ".claude/skills/beautify-dashboard",
                    workspace / ".claude/skills/beautify-dashboard")
    revision = os.environ.get("DAVINCI_BEAUTIFY_SKILL_REF")
    if revision:
        # Read old prose into the isolated fixture without changing the checkout.
        for relative in ("SKILL.md", "references/beautification.md"):
            path = f"workspaces/davinci-dashboard/.claude/skills/beautify-dashboard/{relative}"
            snapshot = (await asyncio.to_thread(
                subprocess.run, ["git", "show", f"{revision}:{path}"], cwd=ROOT,
                check=True, capture_output=True,
            )).stdout
            (workspace / ".claude/skills/beautify-dashboard" / relative).write_bytes(snapshot)
    shutil.copy2(source / "CLAUDE.md", workspace / "CLAUDE.md")
    (workspace / "workspace.yaml").write_text(yaml.safe_dump({
        "version": 1, "id": "actual", "name": "Grouping consent live gate",
        "description": "Isolated skill evaluation", "model": model,
        "skills": ["beautify-dashboard"], "allowed_tools": ["Read", "Grep", "Skill"],
        "mcp_servers": {},
    }), encoding="utf-8")
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspace.parent))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    settings = Settings(
        workspaces_root=workspace.parent, app_data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data/app.db'}",
        app_env="test", identity_mode="mock", app_runtime_mode="local_inline",
        mock_personal_workspace_id="actual", mock_workspace_roles={"actual": "owner"},
        turn_timeout_seconds=120, claude_model=model, claude_selectable_models=model,
    )
    assert settings.anthropic_api_key is not None, "Actual model credentials unavailable"
    from app.main import create_app

    app = create_app(settings=settings)
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=180,
    ) as client:
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        body = _native_body(session["id"], str(uuid.uuid4()), "", PERSONAL_DASHBOARD_PAGE, ())
        body["state"]["revisions"]["resourceRevision"] = 8
        body["tools"] = [{"name": name, "description": registry.get(name).description,
                          "parameters": dict(registry.get(name).input_schema)}
                         for name in ("dashboard.get_structure", "dashboard.get_widget_config",
                                      "dashboard.set_widget_layout")]
        skill_root = workspace / ".claude/skills/beautify-dashboard"
        flow = Flow(client, body, registry, tmp_path / "grouping-flow-evidence.json", {
            "model": model, "choice": choice, "decision": decision,
            "existingFlat": existing_flat,
            "skillRevision": revision or "working-tree",
            "skillHashes": {relative: hashlib.sha256((skill_root / relative).read_bytes()).hexdigest()
                            for relative in ("SKILL.md", "references/beautification.md")},
        })
        first = await flow.turn("帮我美化一下当前看板。")
        assert not flow.layouts(), first
        messages = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
        skill_loaded = any(
            message.get("event_type") == "tool.started"
            and message.get("payload", {}).get("name") == "Skill"
            and "beautify-dashboard" in message["payload"].get("input_preview", "")
            for message in messages
        )
        flow.metadata["skillLoaded"] = skill_loaded
        flow.write_report()
        assert skill_loaded, "The actual model did not load the evaluated Skill"
        for option in ("颜色", "紧凑", "核心指标", "总览", "原因"):
            assert option in first, first
        result = await flow.turn(f"{choice}，快点，照你判断做。")
        if decision == "only-sort":
            assert len(flow.layouts()) == 1, result
            preset = flow.layouts()[0]["preset"]
            assert preset["mode"] == "reorder"
            assert sorted(preset["orderedWidgetIds"]) == sorted(card[0] for card in CARDS)
            assert not {"groups", "groupingConfirmed", "regroup"} & preset.keys()
            return
        messages = (await client.get(
            f"/api/sessions/{session['id']}/messages"
        )).json()
        planner_starts = [
            message for message in messages
            if message.get("event_type") == "tool.started"
            and message.get("payload", {}).get("name")
            == "mcp__davinci_planner__plan_semantic_grouping"
        ]
        assert len(planner_starts) == 1, "Option 3 must use one isolated semantic plan"
        assert len(flow.layouts()) == 1, result
        request = flow.layouts()[0]
        preset = request["preset"]
        assert preset["mode"] == "reorder"
        assert sorted(preset["orderedWidgetIds"]) == sorted(card[0] for card in CARDS)
        if existing_flat:
            assert preset["regroup"] == {"containerWidgetIds": ["flat-old"], "confirmed": True}
        else:
            assert "regroup" not in preset
        assert preset["groupingConfirmed"] is True
        members = [wid for group in preset["groups"] for wid in group["widgetIds"]]
        assert members and len(members) == len(set(members))
        assert set(members) <= {card[0] for card in CARDS}
        assert request["expectedResourceRevision"] == 8
        if decision == "preview":
            assert request["dryRun"] is True
            await flow.turn("确认保存刚才预览的结果。")
            assert len(flow.layouts()) == 2
            saved = flow.layouts()[1]
            assert not saved.get("dryRun", False)
            assert saved["preset"] == preset
