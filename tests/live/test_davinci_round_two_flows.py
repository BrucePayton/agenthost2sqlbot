"""Run real-model multi-turn flows against isolated, deterministic page receipts."""

import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
import pytest
from jsonschema import validate

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.test_davinci_skill_routing import _native_body, _write_live_workspace
from tests.live.test_subscription_agui_qwen import _frontend_calls

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SKILL_ROUTING") != "1",
    reason="Set RUN_LIVE_DAVINCI_SKILL_ROUTING=1 to call the real model.",
)

CASES = [
    ("rename", "把经营空间改名为经营分析，只改名称。", "space.update_info"),
    ("members", "列出经营空间和研发空间的成员。", "space.member.list_by_spaces"),
    (
        "transfer",
        "把经营空间的所有权转给已加入的李四，我保留管理员。",
        "space.transfer_owner",
    ),
    (
        "cancel",
        "把经营空间的所有权转给已加入的李四，我保留管理员。",
        "space.transfer_owner",
    ),
    ("delete", "删除经营空间，按页面确认提交。", "space.delete"),
    ("upgrade", "把经营空间升级为组织空间，归属部门为经营中心。", "space.upgrade"),
    (
        "personal",
        "在个人空间新建晨检分组，在下面创建空看板经营日报。",
        "workspace.dashboard.create_and_open",
    ),
    (
        "interpret",
        "解读当前看板，指出日变化幅度最大的日期和数值，不修改配置。",
        "dashboard.get_widget_data",
    ),
]


def page(case: str) -> dict:
    """Return a trusted page identity for each isolated scenario."""
    if case in {"personal", "interpret"}:
        return {
            "instanceId": "round-two-personal",
            "kind": "dashboard",
            "route": "/share/workbench-new",
            "viewMode": "self",
            "resource": {"type": "dashboard", "id": "88", "name": "经营日报"},
        }
    return {
        "instanceId": "round-two-space",
        "kind": "collaborative-space",
        "route": "/share/collaborative-space",
    }


def result(case: str, name: str, arguments: dict) -> dict:
    """Implement only scenario-owned read and write receipts, never real APIs."""
    spaces = [
        {
            "spaceRef": "space:1:1",
            "name": "经营空间",
            "role": "owner",
            "type": "organization" if case == "delete" else "team",
            "canOpen": True,
        },
        {
            "spaceRef": "space:1:2",
            "name": "研发空间",
            "role": "admin",
            "type": "team",
            "canOpen": True,
        },
    ]
    if name == "space.list":
        return {
            "summary": json.dumps(
                {
                    "spaces": spaces,
                    "total": 2,
                    "page": 1,
                    "pageSize": 20,
                    "truncated": False,
                },
                ensure_ascii=False,
            ),
            "contextVersion": 1,
        }
    if name == "space.get_context":
        return {"summary": '{"page":"home","canCreate":true,"pendingInvitations":[]}'}
    if name == "space.member.list_by_spaces":
        return {
            "status": "success",
            "data": {
                "spaces": [
                    {
                        "spaceRef": query["spaceRef"],
                        "name": "经营空间"
                        if query["spaceRef"] == "space:1:1"
                        else "研发空间",
                        "status": "ready",
                        "total": 1,
                        "page": 1,
                        "pageSize": 20,
                        "hasMore": False,
                        "members": [
                            {
                                "memberRef": "member:1:1"
                                if query["spaceRef"] == "space:1:1"
                                else "member:1:2",
                                "name": "李四",
                                "department": "经营中心",
                                "role": "member",
                                "status": "joined",
                            }
                        ],
                    }
                    for query in arguments["queries"]
                ]
            },
            "summary": "已按空间返回成员",
            "contextVersion": 1,
        }
    if name == "space.update_info":
        assert arguments == {"spaceRef": "space:1:1", "name": "经营分析"}
        return {
            "status": "success",
            "summary": "已改名，原描述保留",
            "contextVersion": 1,
        }
    if name == "space.transfer_owner":
        assert arguments["spaceRef"] == "space:1:1"
        assert arguments["memberRef"] == "member:1:1"
        if case == "cancel":
            return {
                "status": "error",
                "error": {
                    "code": "USER_CANCELLED",
                    "message": "用户在原生确认框取消，未执行转让",
                    "retryable": False,
                },
            }
        return {
            "status": "success",
            "data": {"state": "transferred", "refreshed": True},
            "summary": "所有权已转让",
            "contextVersion": 1,
        }
    if name in {"space.delete", "space.upgrade"}:
        assert arguments["spaceRef"] == "space:1:1"
        if name == "space.upgrade":
            assert arguments["departmentName"] == "经营中心"
        return {
            "status": "success",
            "data": {"state": "approval_pending", "refreshed": True},
            "summary": "审批已提交，等待审批结果",
            "contextVersion": 1,
        }
    if name == "workspace.dashboard_group.create":
        assert arguments["name"] == "晨检"
        return {
            "status": "success",
            "data": {
                "created": True,
                "name": "晨检",
                "iconAssigned": True,
                "menuRefreshed": True,
            },
            "issues": [],
        }
    if name == "workspace.dashboard.create_and_open":
        assert arguments == {"name": "经营日报", "groupName": "晨检"}
        return {
            "status": "success",
            "data": {
                "created": True,
                "name": "经营日报",
                "opened": True,
                "iconAssigned": True,
                "menuRefreshed": True,
            },
            "issues": [],
        }
    if name == "dashboard.get_structure":
        return {"summary": "经营日报；组件：日成交额[widgetId=daily]，单一指标日序列"}
    if name == "dashboard.get_widget_config":
        one = {
            "summary": "日成交额，2026-08-01至2026-08-03，金额单位元，按日汇总",
            "effectiveSpec": {
                "title": "日成交额",
                "metrics": [{"fieldId": "amount", "name": "成交额"}],
                "dimensions": [{"fieldId": "date", "name": "成交日期"}],
            },
        }
        if arguments.get("widgetIds"):
            assert arguments["widgetIds"] == ["daily"]
            return {
                "summary": one["summary"],
                "widgets": [{"widgetId": "daily", **one}],
            }
        assert arguments["widgetId"] == "daily"
        return one
    if name == "dashboard.get_widget_data":
        assert arguments["widgetIds"] == ["daily"]
        return {
            "status": "success",
            "data": {
                "resourceId": "88",
                "widgets": [
                    {
                        "widgetId": "daily",
                        "title": "日成交额",
                        "type": "chart",
                        "state": "ready",
                        "rows": [
                            {"date": "2026-08-01", "amount": 100},
                            {"date": "2026-08-02", "amount": 150},
                            {"date": "2026-08-03", "amount": 0},
                        ],
                        "metadata": {
                            "fields": ["date", "amount"],
                            "rowCount": 3,
                            "returnedRowCount": 3,
                            "dailyChangeSummary": {
                                "scope": "returned_rows",
                                "formula": "delta=current-previous; percent=100*delta/previous",
                                "maximumAbsoluteChange": {
                                    "date": "2026-08-03",
                                    "previousDate": "2026-08-02",
                                    "delta": -150,
                                    "percent": -100,
                                },
                                "maximumPercentChange": {
                                    "date": "2026-08-03",
                                    "previousDate": "2026-08-02",
                                    "delta": -150,
                                    "percent": -100,
                                },
                            },
                        },
                    }
                ],
            },
            "pagination": {"truncated": False},
            "issues": [],
        }
    raise AssertionError(f"Unexpected tool for {case}: {name}")


def tool_message(case: str, call: dict, registry, message_id: str) -> dict:
    """Validate fake business receipts and use the same direct/adapter envelopes as Davinci."""
    data = result(case, call["name"], call["arguments"])
    error = data.get("error", {}).get("code") if data.get("status") == "error" else None
    if error:
        envelope = {**data, "issues": []}
    else:
        validate(data, dict(registry.get(call["name"]).output_schema))
        direct = (
            call["name"].startswith("workspace.dashboard")
            or call["name"] == "dashboard.get_widget_data"
        )
        envelope = (
            data
            if direct
            else {"status": "success", "data": data, "observed": {}, "issues": []}
        )
    return {
        "id": message_id,
        "role": "tool",
        "toolCallId": call["id"],
        "content": json.dumps(envelope, ensure_ascii=False),
        **({"error": error} if error else {}),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,prompt,terminal_action", CASES, ids=[case[0] for case in CASES]
)
async def test_round_two_reaches_truthful_completion(
    case: str,
    prompt: str,
    terminal_action: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep one session through every deferred tool and require the final report."""
    from app.config import Settings

    workspace_root = tmp_path / "workspaces"
    _write_live_workspace(workspace_root)
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspace_root))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    from app.main import create_app

    settings = Settings(
        workspaces_root=workspace_root,
        app_data_dir=tmp_path / "data",
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner"},
        turn_timeout_seconds=180,
        claude_model="deepseek-v4-pro-0813",
        claude_selectable_models="deepseek-v4-pro-0813",
    )
    app = create_app(settings=settings)
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    bundles = {"personal-workspace"} if case == "personal" else {"space-core"}
    if case == "interpret":
        names = [
            "dashboard.get_structure",
            "dashboard.get_widget_config",
            "dashboard.get_widget_data",
        ]
    else:
        names = [
            contract.action
            for contract in registry.public_contracts
            if contract.bundle in bundles
        ]
    tools = [
        {
            "name": name,
            "description": registry.get(name).description,
            "parameters": dict(registry.get(name).input_schema),
        }
        for name in names
    ]
    sequence = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client,
    ):
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        body = _native_body(session["id"], str(uuid.uuid4()), prompt, page(case), ())
        body["tools"] = tools
        final = ""
        started = time.monotonic()
        for step in range(10):
            response = await client.post(
                "/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body
            )
            assert response.status_code == 200, (step, sequence, response.text)
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            errors = [event for event in events if event.get("type") == "RUN_ERROR"]
            assert not errors, {
                "step": step,
                "sequence": sequence,
                "runtimeErrors": errors,
            }
            calls, assistant = _frontend_calls(response.text)
            if not calls:
                final = assistant
                break
            sequence.extend(call["name"] for call in calls)
            body["runId"] = str(uuid.uuid4())
            body["messages"] = [
                tool_message(case, call, registry, f"round-two-{step}-{i}")
                for i, call in enumerate(calls)
            ]
        assert terminal_action in sequence, (sequence, final)
        assert sequence.count(terminal_action) == 1, sequence
        assert "space.open" not in sequence, sequence
        assert final, sequence
        # These business prompts never ask for raw runtime fields or enums.
        assert not re.search(
            r"\b(?:approval_pending|transferred|joined|spaceRef|memberRef|contextVersion)\b",
            final,
        ), final
        if case in {"delete", "upgrade"}:
            assert "审批" in final, final
        if case == "cancel":
            assert "取消" in final or "未执行" in final, final
        if case == "personal":
            assert sequence.index("workspace.dashboard_group.create") < sequence.index(
                terminal_action
            )
        if case == "interpret":
            assert sequence.count("dashboard.get_widget_config") == 1, sequence
            assert "2026-08-03" in final and "150" in final and "100" in final, final
            # Signed values and downward prose are equivalent evidence of direction.
            assert (
                re.search(r"[-−－]\s*150", final)
                and re.search(r"[-−－]\s*100\s*%", final)
            ) or re.search(r"下降|下跌|减少|跌幅|降[至到为]|回落", final), final
            # The stated date interval also bounds the answer to returned rows.
            assert "2026-08-01" in final or any(
                word in final for word in ("本次", "返回", "三天", "3 天")
            ), final
            assert not re.search(
                r"\b(?:amount|widgetId|fieldId|dailyChangeSummary)\b", final
            ), final
        (tmp_path / "round-two-result.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "sequence": sequence,
                    "final": final,
                    "elapsedSeconds": round(time.monotonic() - started, 2),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
