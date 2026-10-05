"""Use the real model and runtime budget with a local fake catalog server."""

import json
import os
import time
import uuid
from pathlib import Path

import httpx
import pytest
from claude_agent_sdk import create_sdk_mcp_server, tool

from tests.live.test_davinci_round_two_flows import page
from tests.live.test_davinci_skill_routing import _native_body, _write_live_workspace
from tests.live.test_subscription_agui_qwen import _frontend_calls

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SKILL_ROUTING") != "1",
    reason="Set RUN_LIVE_DAVINCI_SKILL_ROUTING=1 to call the real model.",
)


@pytest.mark.asyncio
async def test_catalog_budget_finishes_with_bounded_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the real four-call gate and require truthful partial completion."""
    from app.config import Settings
    from app.runtime.claude import ClaudeAgentRuntime

    workspace_root = tmp_path / "workspaces"
    _write_live_workspace(workspace_root)
    manifest = workspace_root / "actual/workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  - Skill\n",
            "  - Skill\n  - mcp__davinci_data__catalog_get_dataset_schema\n",
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspace_root))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    executed = []
    refs = [f"warehouseTopic:{index}" for index in range(901, 907)]

    @tool(
        "catalog_get_dataset_schema",
        "Read one Davinci dataset's authorized metadata and requiredFilters.",
        {
            "type": "object",
            "properties": {
                "datasetRef": {"type": "string"},
                "fieldRefs": {"type": "array", "items": {"type": "string"}},
                "query": {"type": "string"},
                "roles": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "required": ["datasetRef"],
            "additionalProperties": False,
        },
    )
    async def catalog_schema(arguments: dict) -> dict:
        """Return synthetic metadata without contacting the business MCP."""
        ref = arguments["datasetRef"]
        assert ref in refs
        executed.append(ref)
        receipt = {
            "status": "success",
            "dataset": {"datasetRef": ref, "name": f"成交历史表{refs.index(ref) + 1}"},
            "fields": [],
            "requiredFilters": [
                {"fieldRef": f"{ref}/date", "name": "成交日期", "required": True}
            ],
            "grainFieldRefs": [],
            "sourceRefs": [],
            "relationSummary": None,
            "metadataVersion": "isolated-1",
            "fieldCount": 0,
            "truncated": False,
            "issues": [],
        }
        return {
            "content": [
                {"type": "text", "text": json.dumps(receipt, ensure_ascii=False)}
            ]
        }

    def isolated_servers(self, snapshot, owner_key) -> dict:
        """Replace business transports with one in-process SDK test server."""
        assert snapshot.get("mcp_servers") == {}
        return {
            "davinci_data": create_sdk_mcp_server(
                name="davinci_data", tools=[catalog_schema]
            )
        }

    monkeypatch.setattr(ClaudeAgentRuntime, "_resolve_mcp_servers", isolated_servers)
    from app.main import create_app

    app = create_app(
        settings=Settings(
            workspaces_root=workspace_root,
            app_data_dir=tmp_path / "data",
            mock_personal_workspace_id="actual",
            mock_workspace_roles={"actual": "owner"},
            turn_timeout_seconds=180,
            claude_model="deepseek-v4-pro-0813",
            claude_selectable_models="deepseek-v4-pro-0813",
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client,
    ):
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        prompt = (
            "请逐个查看以下六张已确定的成交历史表，列出每张表的必填筛选。"
            "这次只读元数据，不找其他表、不创建组件。\n"
            + "\n".join(
                f"成交历史表{index + 1}：{ref}" for index, ref in enumerate(refs)
            )
        )
        body = _native_body(
            session["id"], str(uuid.uuid4()), prompt, page("interpret"), ()
        )
        started = time.monotonic()
        response = await client.post(
            "/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body
        )
        assert response.status_code == 200, response.text
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert not [event for event in events if event.get("type") == "RUN_ERROR"], (
            response.text
        )
        calls, final = _frontend_calls(response.text)
        assert not calls, calls
        ledger = app.state.services.runtime.tool_ledger.get(session["id"])
        assert len(executed) == len(set(executed)) == 4, (executed, final)
        assert ledger.count_catalog_searches() == 4
        denied = [op for op in ledger.operations if op.execution_result == "denied"]
        assert not denied, [(op.tool_name, op.execution_result) for op in denied]
        assert "成交日期" in final, final
        assert any(
            word in final for word in ("未查", "尚未", "剩余", "上限", "额度", "限制")
        ), final
        (tmp_path / "round-two-result.json").write_text(
            json.dumps(
                {
                    "case": "catalog_budget",
                    "sequence": [op.tool_name for op in ledger.operations],
                    "catalogExecuted": len(executed),
                    "denied": len(denied),
                    "final": final,
                    "elapsedSeconds": round(time.monotonic() - started, 2),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
