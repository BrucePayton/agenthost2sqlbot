"""Exercise dataset feedback with the real model and isolated native receipts.

RUN_LIVE_DAVINCI_SKILL_ROUTING=1 pytest tests/live/test_dataset_editor_feedback.py
Only model requests leave this test. It never connects to a business data API.
"""

import json
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from jsonschema import validate

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.test_davinci_skill_routing import _native_body, _write_live_workspace
from tests.live.test_subscription_agui_qwen import _frontend_calls

REF = "warehouseTopic:10"
FIELD_IDS = ["542", "540", "879", "443"]
FIELD_NAMES = ["成交订单金额", "成交物品金额", "成交优惠金额", "门店名称"]


def empty_context() -> dict:
    """Retain the incomplete draft shape observed in Session 2b8a31a7."""
    return {
        "draftRevision": "draft-0",
        "metadata": {"name": "", "description": "", "businessSystem": ""},
        "mainDatasetRef": REF,
        "sources": [
            {
                "datasetRef": REF,
                "name": "回收订单分析",
                "fieldIds": [],
                "filters": [{"fieldId": "339", "value": []}],
            }
        ],
        "relations": [],
        "dirty": True,
        "validation": {"valid": False, "issues": ["每张来源表至少选择一个输出字段"]},
        "summary": "1 张来源表，0 个关联",
    }


class EditorReceipts:
    """Simulate only page I/O; validate every request and receipt with the contract."""

    def __init__(self, reject_write: bool = False):
        """Create an empty authorized editor, optionally rejecting all writes."""
        self.context = empty_context()
        self.reject_write = reject_write
        self.registry = load_contract_registry(
            CONTRACT_PATH.with_name("davinci-agent-v2.json")
        )
        self.calls = []

    def respond(self, call: dict) -> dict:
        """Return schema-checked native results without touching any real dataset."""
        name, args = call["name"], call["arguments"]
        contract = self.registry.get(name)
        validate(args, dict(contract.input_schema))
        self.calls.append(deepcopy(call))
        error = None
        if name == "dataset.editor.open":
            error = {
                "code": "TOOL_NOT_AVAILABLE",
                "retryable": False,
                "message": "数据集编辑器已打开，请先读取当前草稿",
                "layer": "page",
                "committed": False,
                "phase": "precondition",
                "requiredAction": "dataset.editor.get_context",
                "details": {"failureCode": "EDITOR_ALREADY_OPEN"},
            }
        elif name == "dataset.editor.get_source_fields":
            assert args["datasetRef"] == REF
            fields = [
                {
                    "fieldId": key,
                    "name": label,
                    "fieldType": "dimension",
                    "dataType": "number",
                }
                for key, label in zip(FIELD_IDS, FIELD_NAMES)
            ] + [
                {
                    "fieldId": "339",
                    "name": "订单创建日期",
                    "fieldType": "dimension",
                    "dataType": "date",
                    "requiredCondition": True,
                }
            ]
            fields[-2]["dataType"] = "varchar"
            query = args.get("query", "")
            fields = [f for f in fields if query in f["name"] or query in f["fieldId"]]
            offset, limit = args.get("offset", 0), args.get("limit", 100)
            data = {
                "datasetRef": REF,
                "name": "回收订单分析",
                "fields": fields[offset : offset + limit],
                "queryVars": [],
                "total": len(fields),
                "offset": offset,
                "limit": limit,
                "summary": "普通字段；订单创建日期需要完整区间，未定义查询变量",
            }
        elif name == "dataset.editor.apply_draft":
            assert args["expectedDraftRevision"] == self.context["draftRevision"]
            assert args["mainDatasetRef"] == REF
            assert len(args["sources"]) == 1
            source = args["sources"][0]
            assert source["datasetRef"] == REF
            assert set(source["fieldIds"]) >= set(FIELD_IDS)
            assert not any(f.get("isQueryVar") for f in source["filters"])
            date = next(f for f in source["filters"] if f["fieldId"] == "339")
            assert date["operator"] == "between"
            assert date["value"] == ["2026-09-01", "2026-09-10"]
            if self.reject_write:
                error = {
                    "code": "DATA_NOT_READY",
                    "retryable": False,
                    "message": "数据源元数据暂不可用，本次修改未生效",
                    "layer": "page",
                    "committed": False,
                    "phase": "precondition",
                }
            else:
                self.context.update(
                    draftRevision="draft-" + str(len(self.calls)),
                    sources=[{**deepcopy(source), "name": "回收订单分析"}],
                    metadata=deepcopy(args.get("metadata", self.context["metadata"])),
                    validation={"valid": True, "issues": []},
                )
                data = self.context
        elif name in {"dataset.editor.get_context", "dataset.editor.validate"}:
            data = self.context
        elif name == "dataset.editor.preview":
            assert self.context["validation"]["valid"]
            assert args["expectedDraftRevision"] == self.context["draftRevision"]
            data = {
                "draftRevision": self.context["draftRevision"],
                "rowCount": 2,
                "columns": FIELD_NAMES,
                "summary": "预览已打开，展示 2 行数据",
            }
        elif name == "dataset.editor.save_draft":
            assert self.context["validation"]["valid"]
            assert args["expectedDraftRevision"] == self.context["draftRevision"]
            data = {"status": "opened", "summary": "请确认保存；当前尚未持久化"}
        else:
            raise AssertionError("Unexpected action: " + name)
        if error:
            envelope = {"status": "error", "error": error, "issues": []}
        else:
            validate(data, dict(contract.output_schema))
            envelope = {
                "status": "success",
                "data": deepcopy(data),
                "issues": [],
                "observed": {},
            }
        return {
            "id": str(uuid.uuid4()),
            "role": "tool",
            "toolCallId": call["id"],
            "content": json.dumps(envelope, ensure_ascii=False),
            **({"error": error["code"]} if error else {}),
        }


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SKILL_ROUTING") != "1",
    reason="Explicit live-model opt-in required.",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("reject_write", [False, True])
async def test_live_dataset_editor_reports_only_observed_progress(
    reject_write: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep one SDK session through missing dates, correction and native receipts."""
    from app.config import Settings

    root = tmp_path / "workspaces"
    _write_live_workspace(root)
    monkeypatch.setenv("WORKSPACES_ROOT", str(root))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    from app.main import create_app

    settings = Settings(
        workspaces_root=root,
        app_data_dir=tmp_path / "data",
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner"},
        identity_mode="mock",
        turn_timeout_seconds=180,
        claude_model="deepseek-v4-pro-0813",
        claude_selectable_models="deepseek-v4-pro-0813",
        claude_default_effort="low",
        claude_thinking_budget_tokens=None,
    )
    app = create_app(settings=settings)
    receipts = EditorReceipts(reject_write)
    evidence = {
        "model": settings.claude_model,
        "rejectWrite": reject_write,
        "phases": [],
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client,
    ):
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        body = _native_body(
            session["id"],
            str(uuid.uuid4()),
            "",
            {
                "instanceId": "dataset-feedback",
                "kind": "dataset-editor",
                "route": "/share/workbench-new",
            },
            (),
        )
        body["tools"] = [
            {
                "name": c.action,
                "description": c.description,
                "parameters": dict(c.input_schema),
            }
            for c in receipts.registry.public_contracts
            if c.bundle == "dataset-editor" or c.action == "dataset.editor.open"
        ]
        materialized = settings.app_data_dir / "sessions" / session["id"] / "workspace"
        for relative in ("CLAUDE.md", ".claude/skills/locate-data/SKILL.md"):
            assert (materialized / relative).read_bytes() == (
                root / "actual" / relative
            ).read_bytes()

        async def run_phase(prompt: str) -> str:
            """Resume through bounded fake receipts and retain only visible evidence."""
            body["messages"] = [
                {"id": str(uuid.uuid4()), "role": "user", "content": prompt}
            ]
            start = len(receipts.calls)
            for _ in range(10):
                body["runId"] = str(uuid.uuid4())
                response = await client.post("/api/ag-ui", json=body)
                assert response.status_code == 200, response.text
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                assert not [e for e in events if e.get("type") == "RUN_ERROR"]
                calls, reply = _frontend_calls(response.text)
                if not calls:
                    evidence["phases"].append(
                        {
                            "prompt": prompt,
                            "reply": reply,
                            "calls": receipts.calls[start:],
                        }
                    )
                    (tmp_path / "editor-evidence.json").write_text(
                        json.dumps(evidence, ensure_ascii=False, indent=2)
                    )
                    return reply
                body["messages"] = [receipts.respond(call) for call in calls]
            raise AssertionError("Agent did not finish within 10 tool continuations")

        missing = await run_phase(
            "字段口径已确认：回收订单分析的成交订单金额、成交物品金额、成交优惠金额和门店名称。"
            "进入创建页面配置这些字段，数据集叫订单核对；目前能完成到哪一步？"
        )
        assert not any(
            c["name"] == "dataset.editor.apply_draft" for c in receipts.calls
        )
        assert re.search(r"日期|时间范围|起止", missing), missing
        assert not re.search(r"字段已选定并通过校验|已完成创建|创建成功", missing), (
            missing
        )
        reply = await run_phase(
            "订单创建日期用 2026-09-01 至 2026-09-10。请写入草稿、校验、预览并打开保存确认窗口，告诉我实际结果。"
        )
        assert any(c["name"] == "dataset.editor.apply_draft" for c in receipts.calls)
        if reject_write:
            assert receipts.context["sources"][0]["fieldIds"] == []
            assert not any(
                c["name"] in {"dataset.editor.preview", "dataset.editor.save_draft"}
                for c in receipts.calls
            )
            assert re.search(r"未生效|未写入|未成功|失败|没[有能].{0,8}写入", reply), (
                reply
            )
            assert not re.search(r"字段已选定并通过校验|已完成创建|创建成功", reply), (
                reply
            )
        else:
            assert any(c["name"] == "dataset.editor.save_draft" for c in receipts.calls)
            assert re.search(r"确认|尚未保存|未落库|未持久化", reply), reply
            assert not re.search(r"数据集已保存|已保存成功|创建成功", reply), reply
