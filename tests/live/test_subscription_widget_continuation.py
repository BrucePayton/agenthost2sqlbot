"""Opt-in real-model acceptance against the real browser-owned draft engine.

No business endpoints are used. The fixture replaces resource loaders and server
validation only; parsing, opaque refs, mutations, readback and finish are native.
"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.test_subscription_agui_qwen import (
    SubscriptionLiveReport, _capture_session_report, _frontend_calls,
    _write_isolated_workspace,
)

ROOT = Path(__file__).resolve().parents[2]
REQUEST = (
    "数巢仪表盘模板中昨日成交量<10000且日环比<-0.1时，每天上午10点给我推送预警数据。"
    "内容里包含’昨日成交异常请关注。昨日成交量为【昨日成交量具体值】，"
    "日环比为【昨日成交量日环比具体值】"
)
MAX_MODEL_TURNS = 14
MAX_TOTAL_SECONDS = 900
MAX_INPUT_TOKENS = 750_000
MAX_OUTPUT_TOKENS = 30_000


def _scenario_request():
    """Select a raw-language regression; each process keeps its own native page."""
    scenario = os.environ.get("SUBSCRIPTION_LIVE_SCENARIO", "widget_alert")
    requests = {
        "widget_alert": REQUEST,
        "dashboard": "每天9点和18点给我推送数巢仪表盘模板截图、AI解读，并可以跳转该仪表盘。",
        "notification": "每月1号9点给我推送通知，内容为：7月数码3C标准更新-第四期：游戏机、游戏手柄与电子书。整段字体为斜体。",
    }
    assert scenario in requests
    return scenario, requests[scenario]


def _assert_simple_outcome(scenario, snapshot, completion, events):
    """Verify real native content and dates; a model saying done is insufficient."""
    state = snapshot["state"]
    assert snapshot["lifecycle"] == "active"
    assert completion and completion["status"] == "ready"
    assert completion["saved"] is False and completion["dataVerified"] is False
    assert completion["taskId"] == snapshot["taskId"] and completion["revision"] == snapshot["revision"]
    assert state["finalize"]["status"] == "disabled"
    components = state["sendContent"]["content"]["components"]
    if scenario == "dashboard":
        assert state["trigger"]["dailyTimes"] == ["09:00", "18:00"]
        assert [component["type"] for component in components] == ["dashboard-screenshot", "dashboard-chart", "button"]
        assert components[0]["config"]["dashboardId"] == components[1]["config"]["dashboardId"]
        assert str(components[1]["config"]["widgetId"]) == "-1"
    else:
        trigger = state["trigger"]
        assert trigger["frequency"] == "monthly"
        assert trigger["dailyTimes"] == ["09:00"]
        assert trigger["monthDays"] == ["1"]
        body = next(component["config"]["markdown"] for component in components if component["type"] == "richtext")
        assert "7月数码3C标准更新-第四期：游戏机、游戏手柄与电子书。" in body
        assert "*" in body or "_" in body or "<em>" in body
    assert not any(item.get("name", "").endswith("save_draft") or item.get("args", {}).get("includeDataCheck") for item in events)


@asynccontextmanager
async def _native_page(tmp_path):
    from playwright.async_api import async_playwright

    davinci = Path(os.environ.get("DAVINCI_REPO_ROOT", ROOT.parent / "davinci"))
    bundle = tmp_path / "native-parent.js"
    subprocess.run(
        ["node", str(Path(__file__).with_name("build_subscription_native_parent.cjs")),
         str(davinci), str(bundle)], cwd=ROOT, check=True, capture_output=True,
    )
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, **({"executable_path": executable} if executable else {}),
        )
        page = await browser.new_page()
        await page.route("**/*", lambda route: route.abort())
        await page.set_content("<!doctype html><html><body></body></html>")
        await page.add_script_tag(path=str(bundle))
        try:
            yield page
        finally:
            await browser.close()


async def _execute(page, name, arguments):
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    contract = registry.get(name)
    Draft202012Validator(dict(contract.input_schema)).validate(arguments)
    frontend_input = await page.evaluate("([name,value])=>window.nativeValidate(name,value)", [name, arguments])
    assert frontend_input["ok"], {"name": name, "frontendInputValidation": frontend_input}
    result = await page.evaluate("([name,args])=>window.nativeExecute(name,args)", [name, arguments])
    if result["status"] == "success":
        Draft202012Validator(dict(contract.output_schema)).validate(result["data"])
        frontend_output = await page.evaluate("([name,value])=>window.nativeValidate(name,value,true)", [name, result["data"]])
        assert frontend_output["ok"], {"name": name, "frontendOutputValidation": frontend_output}
    return result


def _model():
    import yaml

    model = yaml.safe_load((ROOT / "workspaces/davinci-dashboard/workspace.yaml").read_text())["model"]
    assert model.startswith("deepseek"), "This gate must exercise the managed DeepSeek model"
    return model


def _provider_settings():
    """Use the user's configured provider without logging credentials or URLs."""
    values = {}
    source = Path.home() / ".claude/settings.json"
    if source.exists():
        values = json.loads(source.read_text()).get("env", {})
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or values.get("ANTHROPIC_BASE_URL")
    token = (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
             or values.get("ANTHROPIC_API_KEY") or values.get("ANTHROPIC_AUTH_TOKEN"))
    assert base_url and token, "Existing provider configuration is required"
    return {"anthropic_base_url": base_url, "anthropic_api_key": token}


async def _body(page, session_id, model, messages):
    from app.agui.models import validate_native_page_state

    native = await page.evaluate("window.nativeRuntime()")
    validate_native_page_state(native["state"])
    return {
        "threadId": session_id, "runId": str(uuid.uuid4()), "state": native["state"],
        "context": native["context"], "tools": native["tools"], "messages": messages,
        "forwardedProps": {
            "workspaceId": "actual", "profile": "davinci-agui-native-v2", "profileId": "subscription",
            "model": model, "effort": "low", "catalogDigest": native["contractDigest"],
            "toolSetId": "subscription-native-continuation", "toolSetChanges": 0, "catalogDigestChanges": 0,
        },
    }


def _assert_outcome(snapshot, completion, metric_keys, native_events):
    """Assert business requirements only on the final native readback/state."""
    state = snapshot["state"]
    assert snapshot["scene"] == "data-alert"
    assert snapshot["lifecycle"] == "active"
    assert state["trigger"]["dailyTimes"] == ["10:00"]
    assert state["trigger"]["sendType"] == "conditional"
    datasets = state["datasets"]
    query = next(item for item in datasets if item["id"] == "query-sales")
    baseline = next(item for item in datasets if item["id"] == "query-baseline")
    assert any(item["key"] == "82" and item.get("valueExp") == "last_day" for item in query["filterList"])
    assert any(item["key"] == "region" and item["value"] == ["华东"] for item in query["filterList"])
    assert baseline["filterList"][0]["value"] == ["2026-07-01"]
    metrics = query["metrics"]
    assert any(item["key"] == "64" and item.get("dataType") == "val" for item in metrics)
    comparison = next(item for item in metrics if item["key"] == "64"
                      and item.get("contrastConfig", {}).get("calcMethod") == "dayChain"
                      and item["contrastConfig"].get("calcType") == "diffRate"
                      and item["contrastConfig"].get("valueExp") == "last_day")
    groups = state["conditions"]["groups"]
    assert len(groups) == 1 and len(groups[0]["rows"]) == 2
    assert {tuple(row["value"]) for row in groups[0]["rows"]} == {(10000,), (-0.1,)}
    assert all(row["operator"] == "lt" for row in groups[0]["rows"])
    assert state["conditions"]["dataset"] == query["id"]
    key_by_value = {tuple(row["value"]): row["field"] for row in groups[0]["rows"]}
    selected_keys = metric_keys[query["id"]]
    assert key_by_value[(10000,)] == next(item["key"] for item in selected_keys
                                          if item["metric"].get("dataType") == "val")
    assert key_by_value[(-0.1,)] == next(item["key"] for item in selected_keys
                                         if item["metric"].get("contrastConfig", {}).get("cmpId")
                                         == comparison["contrastConfig"]["cmpId"])
    components = state["sendContent"]["content"]["components"]
    bodies = [item["config"] for item in components if item["type"] == "richtext"]
    assert any("昨日成交异常请关注" in body.get("markdown", "") for body in bodies)
    columns = [column for body in bodies for binding in body.get("bindings", [])
               for column in binding.get("config", {}).get("columns", [])]
    assert all(binding["config"]["refDatasetId"] == query["id"] for body in bodies
               for binding in body.get("bindings", []) if binding.get("refType") == "dataset_field")
    assert any(column.get("dataType") == "val" and column["key"] == "64" for column in columns)
    assert any(column.get("cmpId") == comparison["contrastConfig"]["cmpId"] for column in columns)
    body = next(body for body in bodies if "昨日成交异常请关注" in body.get("markdown", ""))
    bindings = {item["key"]: item["config"] for item in body["bindings"] if item["refType"] == "dataset_field"}
    value_slot = re.search(r"昨日成交量为(?:\s|[*_])*\$\$(.*?)\$\$", body["markdown"])
    change_slot = re.search(r"日环比为(?:\s|[*_])*\$\$(.*?)\$\$", body["markdown"])
    assert value_slot and change_slot, "Requested body needs live value/comparison variables"
    assert bindings[value_slot.group(1)]["columns"][0]["dataType"] == "val"
    assert bindings[change_slot.group(1)]["columns"][0]["cmpId"] == comparison["contrastConfig"]["cmpId"]
    assert state["finalize"]["status"] == "disabled"
    assert completion and completion["status"] == "ready"
    assert completion["saved"] is False
    assert completion["dataVerified"] is False
    assert completion["revision"] == snapshot["revision"]
    searches = [item["args"].get("kind") for item in native_events
                if item.get("name") == "space.message_rule.search_options"]
    assert "field" not in searches and "dataset" not in searches
    assert not any(item.get("name", "").endswith("save_draft")
                   or item.get("args", {}).get("includeDataCheck") for item in native_events)


@pytest.mark.asyncio
async def test_native_widget_fixture_starts_with_fixed_date_and_real_refs(tmp_path):
    """A local smoke test proves the live fixture is a real unconfigured seed."""
    async with _native_page(tmp_path) as page:
        dashboard = await _execute(page, "space.message_rule.search_options", {"kind": "dashboard", "query": "数巢仪表盘模板"})
        widgets = await _execute(page, "space.message_rule.search_options", {
            "kind": "alert_widget", "dashboardRef": dashboard["data"]["results"][0]["ref"], "query": "昨日成交量",
        })
        result = await _execute(page, "space.message_rule.start_draft", {
            "mode": "widget", "widgetRef": widgets["data"]["results"][0]["ref"],
            "operations": [{"operation": "set_schedule", "frequency": "daily", "times": ["10:00"]}],
        })
        assert result["status"] == "success", result
        query = result["data"]["queries"][0]
        assert query["metadataReady"] is False
        assert len(query["outputs"]) == 1 and query["outputs"][0]["isComparison"] is False
        snapshot = await page.evaluate("window.nativeSnapshot()")
        assert snapshot["state"]["datasets"][0]["filterList"][0]["value"] == ["2026-08-01"]
        # Exercise the acceptance assertions through the same native harness,
        # without a model or any business query. This recipe is never in the
        # isolated model workspace, prompt, or model-facing fixture response.
        date = next(item for item in query["fields"] if item["label"] == "回收成交日期")
        config = next(item for item in result["data"]["configuration"]["operations"]
                      if item.get("queryRef") == query["queryRef"] and item["operation"] == "upsert_dataset_query")
        updated = await _execute(page, "space.message_rule.apply_draft", {
            "expectedRevision": result["data"]["revision"], "operations": [{
                "operation": "upsert_dataset_query", "queryRef": query["queryRef"], "metricsMode": "merge",
                "metrics": [{"fieldRef": query["outputs"][0]["fieldRef"], "contrast": {
                    "timeFieldRef": date["ref"], "valueExp": "last_day", "calcMethod": "dayChain", "calcType": "diffRate"}}],
                "filters": [{**item, "valueExp": "last_day", "values": []} if item["fieldRef"] == date["ref"]
                            else item for item in config["filters"]],
            }],
        })
        assert updated["status"] == "success", updated
        outputs = next(item for item in updated["data"]["queries"] if item["queryRef"] == query["queryRef"])["outputs"]
        value = next(item for item in outputs if not item["isComparison"])
        change = next(item for item in outputs if item["isComparison"])
        finished = await _execute(page, "space.message_rule.apply_draft", {
            "expectedRevision": updated["data"]["revision"], "finish": True, "operations": [
                {"operation": "set_trigger_conditions", "queryRef": query["queryRef"], "conditionGroups": [{"conditions": [
                    {"outputRef": value["outputRef"], "operator": "lt", "values": [10000]},
                    {"outputRef": change["outputRef"], "operator": "lt", "values": [-0.1]},
                ]}]},
                {"operation": "set_content", "components": [{"type": "richtext", "segments": [
                    {"type": "text", "text": "昨日成交异常请关注。昨日成交量为"},
                    {"type": "variable", "key": "value"}, {"type": "text", "text": "，日环比为"},
                    {"type": "variable", "key": "change"}], "bindings": [
                        {"key": key, "refType": "dataset_field", "refSubType": "field", "queryRef": query["queryRef"],
                         "outputRefs": [output["outputRef"]]} for key, output in [("value", value), ("change", change)]
                    ]}]},
                {"operation": "set_finalize", "ruleName": "昨日成交异常预警"},
            ],
        })
        assert finished["status"] == "success", finished
        actual = await page.evaluate("({snapshot:window.nativeSnapshot(),metricKeys:window.nativeMetricKeys(),events:window.nativeEvents})")
        _assert_outcome(actual["snapshot"], finished["data"].get("completion"), actual["metricKeys"], actual["events"])
        wrong = json.loads(json.dumps(actual["snapshot"]))
        rows = wrong["state"]["conditions"]["groups"][0]["rows"]
        rows[0]["value"], rows[1]["value"] = rows[1]["value"], rows[0]["value"]
        with pytest.raises(AssertionError):
            _assert_outcome(wrong, finished["data"]["completion"], actual["metricKeys"], actual["events"])
        wrong = json.loads(json.dumps(actual["snapshot"]))
        bindings = wrong["state"]["sendContent"]["content"]["components"][0]["config"]["bindings"]
        bindings[0]["config"], bindings[1]["config"] = bindings[1]["config"], bindings[0]["config"]
        with pytest.raises(AssertionError):
            _assert_outcome(wrong, finished["data"]["completion"], actual["metricKeys"], actual["events"])
        (tmp_path / "native-widget-smoke.json").write_text(
            json.dumps({**actual, "completion": finished["data"]["completion"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@pytest.mark.skipif(os.environ.get("RUN_LIVE_SUBSCRIPTION_WIDGET_CONTINUATION") != "1",
                    reason="Opt in to bounded real-model API use")
@pytest.mark.asyncio
async def test_real_model_finishes_widget_continuation_without_sending(tmp_path, monkeypatch):
    import httpx
    from app.config import Settings

    model = _model()
    scenario, request_text = _scenario_request()
    evidence = Path(os.environ.get("SUBSCRIPTION_LIVE_REPORT_DIR", str(tmp_path)))
    with SubscriptionLiveReport(evidence, scenario + "-incremental-continuation", model) as report:
        report.data.update(execution="real-model-real-host-real-native-controller-store", userUtterances=[request_text],
                           limits={"decisionSeconds": 180, "modelTurns": MAX_MODEL_TURNS,
                                   "totalSeconds": MAX_TOTAL_SECONDS, "inputTokens": MAX_INPUT_TOKENS,
                                   "outputTokens": MAX_OUTPUT_TOKENS})
        workspaces = tmp_path / "workspaces"
        _write_isolated_workspace(workspaces, model)
        skill = workspaces / "actual/.claude/skills/configure-subscription-rule/SKILL.md"
        report.data["skillSha256"] = hashlib.sha256(skill.read_bytes()).hexdigest()
        davinci = Path(os.environ.get("DAVINCI_REPO_ROOT", ROOT.parent / "davinci"))
        fixture = davinci / "webapp/share/containers/CollaborativeSpace/agent/__fixtures__/subscriptionWidgetComparison.json"
        report.data["fixtureNotes"] = "Fixed-date/raw-value seed plus a preserved regional filter and independent historical query; an extended regression fixture, not a byte-for-byte session replay."
        report.data["sourceHashes"] = {
            "contract": hashlib.sha256(CONTRACT_PATH.with_name("davinci-agent-v2.json").read_bytes()).hexdigest(),
            "fixture": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            **{str(path.relative_to(skill.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(skill.parent.rglob("*.md"))},
        }
        monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces))
        monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
        provider = _provider_settings()
        # app.main builds its module-level app at import time as well.
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
            messages = [{"id": "subscription-request", "role": "user", "content": request_text}]
            try:
                async with asyncio.timeout(MAX_TOTAL_SECONDS):
                    for turn in range(MAX_MODEL_TURNS):
                        body = await _body(page, session["id"], model, messages)
                        started = time.perf_counter()
                        response = await client.post("/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body)
                        if response.status_code != 200:
                            error = response.json().get("error", {})
                            report.data["httpFailure"] = {"status": response.status_code,
                                "code": error.get("code"), "message": error.get("message"),
                                "issues": [{key: issue[key] for key in ("path", "loc", "type", "msg") if key in issue}
                                           for issue in error.get("details", {}).get("issues", [])]}
                            report.flush()
                        assert response.status_code == 200
                        calls, assistant = _frontend_calls(response.text)
                        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                        errors = [{"code": item.get("code"), "message": item.get("message")} for item in events if item.get("type") == "RUN_ERROR"]
                        record = {"assistant": assistant, "elapsedSeconds": round(time.perf_counter()-started, 3), "tools": calls, "errors": errors}
                        report.data["rounds"].append(record)
                        report.data["finalAssistant"] = assistant
                        if errors:
                            report.data["terminal"] = "host-run-error"
                        report.flush()
                        assert not errors, "Host emitted RUN_ERROR; see isolated evidence"
                        if not calls:
                            report.data["terminal"] = "finished-unpersisted" if completion else "stopped-before-completion"
                            assert completion, "Model stopped without completing the draft"
                            break
                        messages = []
                        for call in calls:
                            result = await _execute(page, call["name"], call["arguments"])
                            call["nativeResult"] = result
                            completion = result.get("data", {}).get("completion") or completion
                            messages.append({"id": str(uuid.uuid4()), "role": "tool", "toolCallId": call["id"],
                                             "content": json.dumps(result, ensure_ascii=False)})
                            report.flush()
                        usage_events = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
                        usage = [item["payload"] for item in usage_events if item.get("event_type") == "usage.updated"]
                        report.data["usage"] = {"inputTokens": sum(item.get("total_input_tokens") or item.get("input_tokens") or 0 for item in usage),
                                                "outputTokens": sum(item.get("output_tokens") or 0 for item in usage)}
                        assert report.data["usage"]["inputTokens"] <= MAX_INPUT_TOKENS
                        assert report.data["usage"]["outputTokens"] <= MAX_OUTPUT_TOKENS
                    else:
                        pytest.fail("Bounded model turn budget exhausted")
            finally:
                report.data["finalNativeSnapshot"] = await page.evaluate("window.nativeSnapshot()")
                report.data["nativeMetricKeys"] = await page.evaluate("window.nativeMetricKeys()")
                report.data["nativeEvents"] = await page.evaluate("window.nativeEvents")
                await _capture_session_report(client, session["id"], report)
            if scenario == "widget_alert":
                _assert_outcome(report.data["finalNativeSnapshot"], completion,
                                report.data["nativeMetricKeys"], report.data["nativeEvents"])
            else:
                _assert_simple_outcome(scenario, report.data["finalNativeSnapshot"], completion,
                                       report.data["nativeEvents"])
            native_events = report.data["nativeEvents"]
            assert not any("configurationIntent" in event.get("args", {}) for event in native_events)
            time_index = next(index for index, event in enumerate(native_events)
                if any(op.get("operation") == "set_schedule" for op in event.get("args", {}).get("operations", [])))
            resource_indices = [index for index, event in enumerate(native_events)
                if event.get("name") == "space.message_rule.search_options"
                and event.get("args", {}).get("kind") in {"dashboard", "alert_widget", "widget", "dataset", "field"}]
            assert not resource_indices or time_index < min(resource_indices)
            assert "发送预览" in report.data["finalAssistant"]
            assert "保存" in report.data["finalAssistant"]
