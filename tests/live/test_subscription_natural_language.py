"""Reported requests through the real model, Host and native browser Store.

Only upstream resources/validators are isolated. No model reply, intake JSON,
native completion or candidate ref is supplied by this harness. Every native
operation is produced by the model through native business tools. The browser blocks all
network, previews, persistence and sending.
"""

import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

from app.agui.contracts import CONTRACT_PATH
from tests.live.test_subscription_agui_qwen import (
    SubscriptionLiveReport, _capture_session_report, _frontend_calls, _write_isolated_workspace,
)
from tests.live.test_subscription_widget_continuation import (
    ROOT, _assert_outcome, _assert_simple_outcome, _body, _execute, _model,
    _native_page, _provider_settings,
)

REQUESTS = {
    "dashboard": "在每天9点和18点给我推送数巢仪表盘模板截图、AI解读，并可以跳转该仪表盘。",
    "data": "每周一下午14点给我推送数巢-3C回收订单分析表-模板数据集对应的数据表格。\n"
            "数据表格里需包含回收成交日期为昨天的区域经理、门店名称、总成交订单量、总成交订单金额",
    "notification": "在数据中心-组织空间，每月1号9点给BI测试群推送通知消息，内容如下。\n"
                    "@所有人\n7月数码3C标准更新-第四期：游戏机、游戏手柄与电子书。（字体为斜体）\n"
                    "7月数码3C标准更新-第四期（对应链接为https://atrenew.feishu.cn/wiki/KHSow4bRBioh3yk3BO4cFtoXnQg）",
    "widget_alert": "数巢仪表盘模板中昨日成交量<10000且日环比<-0.1时，每天上午10点给我推送预警数据。\n"
                    "内容里包含’昨日成交异常请关注。昨日成交量为【昨日成交量具体值】，日环比为【昨日成交量日环比具体值】",
    "dynamic_alert": "在数据中心-组织空间，明天上午11点监测数巢订单明细模板数据集中昨日订单状态为交易失败的小订单号数据，"
                     "若存在其提交订单额大于8000的，就发送飞书消息给该小订单号对应的区经及战区负责人。"
                     "每个异常小订单分别发送，标题里需要带昨日日期变量；文字为‘昨日未成交的大额订单如下：’，加粗、红色。"
                     "数据表展示监测异常的小订单号、门店、区经、提交订单额。",
}
ORDER_DATASET = "数巢-3C回收订单分析表-模板数据集"
ORDER_FIELDS = [
    ("date", "回收成交日期", "date", "dimension"),
    ("manager", "区域经理", "varchar", "dimension"),
    ("store", "门店名称", "varchar", "dimension"),
    ("volume", "总成交订单量", "number", "metrics"),
    ("amount", "总成交订单金额", "number", "metrics"),
]
DYNAMIC_FIELDS = [
    ("date", "订单日期", "date", "dimension"),
    ("status", "订单状态", "varchar", "dimension"),
    ("order", "小订单号", "varchar", "dimension"),
    ("store", "门店", "varchar", "dimension"),
    ("manager", "区经", "varchar", "dimension"),
    ("director", "战区负责人", "varchar", "dimension"),
    ("amount", "提交订单额", "number", "metrics"),
]
MAX_HOST_TURNS = 48
MAX_MODEL_TURNS = 16
MAX_TOTAL_SECONDS = int(os.environ.get("SUBSCRIPTION_LIVE_TOTAL_SECONDS", "600"))


def _fixture(tmp_path, monkeypatch, scenario):
    """Metadata is test-owned; final configuration is never in a resource fixture."""
    catalog = {
        "datasets": [{"datasetUid": "orders", "datasetType": "widget", "datasetName": ORDER_DATASET}],
        "fields": [{"fieldId": key, "fieldName": label, "fieldDataType": data_type,
                    "fieldType": role, "sourceUid": "orders", "sourceType": "widget"}
                   for key, label, data_type, role in ORDER_FIELDS],
    }
    if scenario in {"notification", "dynamic_alert"}:
        catalog.update(space={"id": "83", "name": "数据中心-组织空间"}, groups=[
            {"id": "fixture-bi-group", "name": "BI测试群"},
            {"id": "fixture-bi-group-2", "name": "BI测试群2"}])
    if scenario == "dynamic_alert":
        catalog.update(datasets=[{"datasetUid": "failed-orders", "datasetType": "widget", "datasetName": "数巢订单明细模板"}],
            fields=[{"fieldId": key, "fieldName": label, "fieldDataType": data_type, "fieldType": role,
                     "sourceUid": "failed-orders", "sourceType": "widget",
                     **({"isEmployeeAccount": True, "employeeAccountType": "ob"} if key in {"manager", "director"} else {})}
                    for key, label, data_type, role in DYNAMIC_FIELDS],
            enumValues=[{"label": value, "value": value} for value in ["交易失败", "交易成功"]])
    path = tmp_path / "native-resources.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SUBSCRIPTION_NATIVE_CATALOG_FIXTURE", str(path))
    return path


def _assert_result(scenario, snapshot, completion, metric_keys, native_events):
    """Check actual configuration, including recipient identity and format."""
    if scenario == "widget_alert":
        _assert_outcome(snapshot, completion, metric_keys, native_events)
        return
    state = snapshot["state"]
    assert snapshot["scene"] == {"notification": "msg-notify", "dashboard": "dashboard-push",
                                 "data": "data-alert", "dynamic_alert": "data-alert"}[scenario]
    assert completion and completion["status"] == "ready"
    assert completion["taskId"] == snapshot["taskId"] and completion["revision"] == snapshot["revision"]
    assert completion["saved"] is False and completion["dataVerified"] is False
    assert state["finalize"]["status"] == "disabled" and snapshot["lifecycle"] == "active"
    assert not any(event.get("name", "").endswith("save_draft") or
                   event.get("args", {}).get("includeDataCheck") for event in native_events)
    if scenario in {"notification", "dashboard"}:
        _assert_simple_outcome(scenario, snapshot, completion, native_events)
    if scenario == "notification":
        content = next(item["config"]["markdown"] for item in state["sendContent"]["content"]["components"]
                       if item["type"] == "richtext")
        assert "<at id=all></at>" in content
        assert "[7月数码3C标准更新-第四期](https://atrenew.feishu.cn/wiki/KHSow4bRBioh3yk3BO4cFtoXnQg)" in content
        assert "数据中心-组织空间" in completion["message"]
        recipients = json.dumps(state["receivers"], ensure_ascii=False)
        assert "fixture-bi-group" in recipients and "fixture-bi-group-2" not in recipients
        assert not state.get("datasets")
    if scenario == "data":
        assert state["trigger"]["frequency"] == "weekly"
        assert state["trigger"]["dailyTimes"] == ["14:00"]
        assert state["trigger"]["weekDays"] == ["monday"]
        assert len(state["datasets"]) == 1
        query = state["datasets"][0]
        assert query["datasetUid"] == "orders" and query["datasetType"] == "widget"
        assert [field["key"] for field in query["dimensions"]] == ["manager", "store"]
        assert [field["key"] for field in query["metrics"]] == ["volume", "amount"]
        assert any(item["key"] == "date" and item.get("valueExp") == "last_day" for item in query["filterList"])
        tables = [item["config"] for item in state["sendContent"]["content"]["components"] if item["type"] == "data-table"]
        assert len(tables) == 1 and tables[0]["refDatasetId"] == query["id"]
        assert [column["key"] for column in tables[0]["columns"]] == ["manager", "store", "volume", "amount"]
    if scenario == "dynamic_alert":
        trigger = state["trigger"]
        assert trigger["frequency"] == "once" and trigger["executeTime"] == "11:00"
        assert trigger["onceDate"] == (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=1)).strftime("%Y-%m-%d")
        assert trigger["sendType"] == "conditional"
        query, = state["datasets"]
        assert query["datasetUid"] == "failed-orders"
        assert any(item["key"] == "date" and item.get("valueExp") == "last_day" for item in query["filterList"])
        assert any(item["key"] == "status" and item.get("value") == ["交易失败"] for item in query["filterList"])
        assert {"order", "store", "manager", "director"}.issubset({item["key"] for item in query["dimensions"]})
        row, = [row for group in state["conditions"]["groups"] for row in group["rows"]]
        assert row["operator"] == "gt" and row["value"] == [8000]
        assert state["conditions"]["dataset"] == query["id"]
        assert row["field"] == next(item["key"] for item in metric_keys[query["id"]] if item["metric"]["key"] == "amount")
        assert state["pushMode"]["mode"] == "record"
        fields = [item for item in state["receivers"].get("selectedReceiverOptions", []) if item.get("tab") == "dataOwner"]
        assert {item["fieldId"] for item in fields} == {"manager", "director"}
        assert all(item["employeeAccountType"] == "ob" and item["refDatasetId"] == query["id"] for item in fields)
        title = state["sendContent"]["title"]["main"]
        assert any(item["refType"] == "system_time" and item["refSubType"] == "yesterday" for item in title["bindings"])
        assert "$$" in title["template"]
        components = state["sendContent"]["content"]["components"]
        body = next(item["config"]["markdown"] for item in components if item["type"] == "richtext")
        assert "昨日未成交的大额订单如下：" in body and ("**" in body or "<b>" in body or "<strong>" in body)
        assert "#ff0000" in body.lower() or "rgba(255, 0, 0, 1)" in body
        table = next(item["config"] for item in components if item["type"] == "data-table")
        assert table["refDatasetId"] == query["id"]
        assert [column["key"] for column in table["columns"]] == ["order", "store", "manager", "amount"]
        assert any(event.get("type") == "space-navigation" and event["spaceName"] == "数据中心-组织空间" for event in native_events)


async def _verify_fixture(page, scenario):
    """Prove the resource fixture uses native lookup and starts unconfigured."""
    assert (await page.evaluate("window.nativeSnapshot()"))["lifecycle"] == "idle"
    context = await _execute(page, "space.message_rule.get_context", {})
    assert "activeDraft" not in context["data"]
    if scenario == "notification":
        assert context["data"]["scope"]["kind"] == "personal"
        listed = await _execute(page, "space.list", {})
        target, = [space for space in json.loads(listed["data"]["summary"])["spaces"] if space["name"] == "数据中心-组织空间"]
        await _execute(page, "space.open", {"spaceRef": target["spaceRef"], "destination": "subscription"})
        options = await _execute(page, "space.message_rule.search_options", {"kind": "recipient_group", "query": "BI测试群"})
        assert [item["label"] for item in options["data"]["results"]] == ["BI测试群", "BI测试群2"]
    else:
        options = await _execute(page, "space.message_rule.search_options", {"kind": "dataset", "query": ORDER_DATASET})
        assert len(options["data"]["results"]) == 1
        fields = await _execute(page, "space.message_rule.search_options", {
            "kind": "field", "datasetRef": options["data"]["results"][0]["ref"]})
        assert {item["label"] for item in fields["data"]["results"]} == {item[1] for item in ORDER_FIELDS}


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["notification", "data"])
async def test_natural_language_resource_fixture_is_native_and_unconfigured(tmp_path, monkeypatch, scenario):
    _fixture(tmp_path, monkeypatch, scenario)
    async with _native_page(tmp_path) as page:
        await _verify_fixture(page, scenario)


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("RUN_LIVE_SUBSCRIPTION_SCENARIOS") != "1",
                    reason="Set RUN_LIVE_SUBSCRIPTION_SCENARIOS=1 to call the configured model.")
@pytest.mark.parametrize("scenario", list(REQUESTS))
async def test_real_natural_language_reaches_native_completion(tmp_path, monkeypatch, scenario):
    import httpx
    from app.config import Settings

    model = _model()
    evidence = Path(os.environ.get("SUBSCRIPTION_LIVE_REPORT_DIR", str(tmp_path))) / scenario
    with SubscriptionLiveReport(evidence, scenario, model) as report:
        report.data.update(execution="real-model-real-host-real-native-controller-store",
            userUtterances=[REQUESTS[scenario]],
            limits={"hostTurns": MAX_HOST_TURNS, "modelTurns": MAX_MODEL_TURNS, "totalSeconds": MAX_TOTAL_SECONDS},
            fixtureNotes="Isolated resource metadata; native refs, Store, compilation and finish are production. "
                         "Named-space cases start in personal context and must navigate; widget fixture preserves an extra filter and historical query.")
        resources = _fixture(tmp_path, monkeypatch, scenario)
        workspaces = tmp_path / "workspaces"
        _write_isolated_workspace(workspaces, model)
        # Use the current managed guidance and actual Host tools. No remote MCP
        # connection is configured; this fixture exposes bounded native metadata.
        source_skill = ROOT / "workspaces/davinci-dashboard/.claude/skills/locate-data"
        shutil.copytree(source_skill, workspaces / "actual/.claude/skills/locate-data")
        workspace_file = workspaces / "actual/workspace.yaml"
        workspace_file.write_text(workspace_file.read_text().replace("  - configure-subscription-rule\n",
            "  - configure-subscription-rule\n  - locate-data\n"))
        report.data["sourceHashes"] = {
            "contract": hashlib.sha256(CONTRACT_PATH.with_name("davinci-agent-v2.json").read_bytes()).hexdigest(),
            "resources": hashlib.sha256(resources.read_bytes()).hexdigest(),
            "runtime": {name: hashlib.sha256((ROOT / "app/runtime" / name).read_bytes()).hexdigest()
                        for name in ("claude.py", "subscription_workflow.py", "subscription_model_context.py", "subscription_discovery.py")},
            "skills": {str(path.relative_to(workspaces / "actual")): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted((workspaces / "actual/.claude/skills").rglob("*.md"))},
        }
        report.flush()
        monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces))
        monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
        provider = _provider_settings()
        monkeypatch.setenv("ANTHROPIC_BASE_URL", provider["anthropic_base_url"])
        monkeypatch.setenv("ANTHROPIC_API_KEY", provider["anthropic_api_key"])
        from app.main import create_app

        settings = Settings(**provider, workspaces_root=workspaces, app_data_dir=tmp_path / "data",
            mock_personal_workspace_id="actual", mock_workspace_roles={"actual": "owner"},
            turn_timeout_seconds=190, subscription_model_decision_timeout_seconds=180,
            claude_model=model, claude_selectable_models=model)
        app = create_app(settings=settings)
        completion = None
        async with _native_page(tmp_path) as page, app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver", timeout=200) as client:
            session = (await client.post("/api/workspaces/actual/sessions")).json()
            messages = [{"id": "natural-language-request", "role": "user", "content": REQUESTS[scenario]}]
            started = time.perf_counter()
            try:
                async with asyncio.timeout(MAX_TOTAL_SECONDS):
                    for _ in range(MAX_HOST_TURNS):
                        body = await _body(page, session["id"], model, messages)
                        turn_started = time.perf_counter()
                        response = await client.post("/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body)
                        assert response.status_code == 200, "Host rejected the native integration request"
                        calls, assistant = _frontend_calls(response.text)
                        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                        errors = [item.get("code") for item in events if item.get("type") == "RUN_ERROR"]
                        report.data["rounds"].append({"elapsedSeconds": round(time.perf_counter()-turn_started, 3),
                            "tools": calls, "assistant": assistant, "errorCodes": errors})
                        report.data["finalAssistant"] = assistant
                        if errors:
                            report.data["terminal"] = "host-run-error"
                        report.flush()
                        assert not errors, "Host emitted RUN_ERROR; inspect sanitized scenario evidence"
                        if not calls:
                            report.data["terminal"] = "finished-unpersisted" if completion else "stopped-before-completion"
                            assert completion, "Model stopped without native completion"
                            break
                        messages = []
                        for call in calls:
                            result = await _execute(page, call["name"], call["arguments"])
                            call["nativeResult"] = result
                            native_time = round(time.perf_counter()-started, 3)
                            if any(op.get("operation") == "set_schedule" for op in call["arguments"].get("operations", [])):
                                report.data.setdefault("firstConfigurationSeconds", native_time)
                            completion = result.get("data", {}).get("completion") or completion
                            if completion:
                                report.data.setdefault("nativeCompletionSeconds", native_time)
                            messages.append({"id": str(uuid.uuid4()), "role": "tool", "toolCallId": call["id"],
                                             "content": json.dumps(result, ensure_ascii=False)})
                            report.flush()
                        await _capture_session_report(client, session["id"], report)
                        assert (report.data.get("modelTurns") or 0) <= MAX_MODEL_TURNS, "Model invocation budget exceeded"
                    else:
                        pytest.fail("Native continuation budget exhausted")
            finally:
                report.data["finalNativeSnapshot"] = await page.evaluate("window.nativeSnapshot()")
                report.data["nativeMetricKeys"] = await page.evaluate("window.nativeMetricKeys()")
                report.data["nativeEvents"] = await page.evaluate("window.nativeEvents")
                await _capture_session_report(client, session["id"], report)
            _assert_result(scenario, report.data["finalNativeSnapshot"], completion,
                           report.data["nativeMetricKeys"], report.data["nativeEvents"])
            assert "发送预览" in report.data["finalAssistant"] and "保存" in report.data["finalAssistant"]
