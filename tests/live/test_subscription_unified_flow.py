"""Direct native tool regressions; no Host business plan or model replies are mocked.

Only upstream resources/validators are fixtures. References, Controller, Store,
normalization, readback and completion are production code. The browser rejects
network, data preview, persistence and sending.
"""
import json

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.test_subscription_natural_language import ORDER_DATASET, _assert_result, _fixture
from tests.live.test_subscription_widget_continuation import _execute, _native_page


async def native_call(page, action, **arguments):
    result = await _execute(page, "space.message_rule." + action, arguments)
    assert result["status"] == "success", result
    return result["data"]


async def open_named_space(page, name):
    """Choose only a real opaque reference emitted by the native space controller."""
    listed = await _execute(page, "space.list", {})
    assert listed["status"] == "success", listed
    directory = json.loads(listed["data"]["summary"])
    target, = [space for space in directory["spaces"] if space["name"] == name]
    opened = await _execute(page, "space.open", {"spaceRef": target["spaceRef"], "destination": "subscription"})
    assert opened["status"] == "success", opened
    context = await native_call(page, "get_context")
    assert context["scope"]["spaceName"] == name
    return directory


async def start_known_time(page, *, scene="data-alert", time="09:00"):
    return await native_call(page, "start_draft", mode="blank", scene=scene, operations=[
        {"operation": "set_schedule", "frequency": "daily", "times": [time]}])


async def find_order_fields(page):
    datasets = await native_call(page, "search_options", kind="dataset", query=ORDER_DATASET)
    dataset, = datasets["results"]
    fields = await native_call(page, "search_options", kind="field", datasetRef=dataset["ref"])
    return dataset["ref"], {field["label"]: field["ref"] for field in fields["results"]}


async def configure_table(page):
    draft = await start_known_time(page, scene="data-alert", time="14:00")
    source, fields = await find_order_fields(page)
    query = await native_call(page, "apply_draft", expectedRevision=draft["revision"], operations=[
        {"operation": "set_schedule", "frequency": "weekly", "weekdays": [1], "times": ["14:00"]},
        {"operation": "set_send_rule", "sendRule": "scheduled_dataset"},
        {"operation": "upsert_dataset_query", "datasetRef": source,
         "dimensionRefs": [fields[label] for label in ["区域经理", "门店名称"]],
         "metricRefs": [fields[label] for label in ["总成交订单量", "总成交订单金额"]],
         "filters": [{"fieldRef": fields["回收成交日期"], "operator": "eq", "valueExp": "last_day", "values": []}]}])
    current, = query["queries"]
    outputs = {output["fieldRef"]: output["outputRef"] for output in current["outputs"]}
    finished = await native_call(page, "apply_draft", expectedRevision=query["revision"], finish=True, operations=[
        {"operation": "set_push_mode", "mode": "all"},
        {"operation": "set_content", "title": "昨日订单日报", "components": [{"type": "data-table",
         "queryRef": current["queryRef"], "outputRefs": [outputs[fields[label]] for label in
             ["区域经理", "门店名称", "总成交订单量", "总成交订单金额"]]}]},
        {"operation": "set_finalize", "ruleName": "昨日订单日报"}])
    return finished, source, fields


@pytest.mark.asyncio
async def test_native_table_uses_actual_outputs_and_finishes_after_early_time_write(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, "data")
    async with _native_page(tmp_path) as page:
        finished, _, _ = await configure_table(page)
        actual = await page.evaluate("({snapshot:window.nativeSnapshot(),metricKeys:window.nativeMetricKeys(),events:window.nativeEvents})")
        _assert_result("data", actual["snapshot"], finished["completion"], actual["metricKeys"], actual["events"])
        tools = [event for event in actual["events"] if event.get("type") == "tool"]
        assert tools[0]["name"].endswith("start_draft")
        assert any(op["operation"] == "set_schedule" for op in tools[0]["args"]["operations"])
        assert not any("configurationIntent" in event["args"] for event in tools)


@pytest.mark.asyncio
async def test_manual_title_survives_time_change_and_identical_all_write_is_success(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, "data")
    async with _native_page(tmp_path) as page:
        finished, _, _ = await configure_table(page)
        before = await page.evaluate("window.nativeSnapshot()")
        manual = await native_call(page, "apply_draft", expectedRevision=finished["revision"], operations=[
            {"operation": "set_content", "title": "人工维护的标题"}])
        amended = await native_call(page, "apply_draft", expectedRevision=manual["revision"], operations=[
            {"operation": "set_schedule", "frequency": "weekly", "weekdays": [1], "times": ["18:00"]}], finish=True)
        repeated = await native_call(page, "apply_draft", expectedRevision=amended["revision"], operations=[
            {"operation": "set_push_mode", "mode": "all"}], finish=True)
        assert repeated["revision"] == amended["revision"]
        mode = next(op for op in repeated["configuration"]["operations"] if op["operation"] == "set_push_mode")
        assert mode["mode"] == "all" and "queryRef" not in mode
        after = await page.evaluate("window.nativeSnapshot()")
        assert after["state"]["trigger"]["dailyTimes"] == ["18:00"]
        assert after["state"]["sendContent"]["title"]["main"]["template"] == "人工维护的标题"
        assert after["state"]["sendContent"]["content"] == before["state"]["sendContent"]["content"]
        assert repeated["completion"]["status"] == "ready" and not repeated["completion"]["saved"]
        stale = await _execute(page, "space.message_rule.apply_draft", {
            "expectedRevision": before["revision"], "operations": [{"operation": "set_content", "title": "过期覆盖"}]})
        assert stale["status"] == "error"
        assert await page.evaluate("window.nativeSnapshot()") == after


@pytest.mark.asyncio
async def test_same_dataset_queries_keep_separate_dates_and_output_tables(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, "data")
    async with _native_page(tmp_path) as page:
        first, source, fields = await configure_table(page)
        second = await native_call(page, "apply_draft", expectedRevision=first["revision"], operations=[
            {"operation": "upsert_dataset_query", "datasetRef": source, "name": "上月对照",
             "dimensionRefs": [fields["门店名称"]], "metricRefs": [fields["总成交订单量"]],
             "filters": [{"fieldRef": fields["回收成交日期"], "operator": "eq", "valueExp": "last_month", "values": []}]}])
        assert len(second["queries"]) == 2
        old_ref = first["queries"][0]["queryRef"]
        new = next(query for query in second["queries"] if query["queryRef"] != old_ref)
        result = await native_call(page, "apply_draft", expectedRevision=second["revision"], finish=True, operations=[
            {"operation": "patch_content", "edits": [{"component": {
                "type": "data-table", "queryRef": new["queryRef"],
                "outputRefs": [output["outputRef"] for output in new["outputs"]]}}]}])
        snapshot = await page.evaluate("window.nativeSnapshot()")
        queries = snapshot["state"]["datasets"]
        assert len({query["id"] for query in queries}) == 2
        assert {query["datasetUid"] for query in queries} == {"orders"}
        assert [query["filterList"][0]["valueExp"] for query in queries] == ["last_day", "last_month"]
        tables = [component["config"] for component in snapshot["state"]["sendContent"]["content"]["components"]]
        assert [table["refDatasetId"] for table in tables] == [query["id"] for query in queries]
        assert result["completion"]["status"] == "ready"


@pytest.mark.asyncio
async def test_notification_crosses_real_space_lookup_and_navigation_before_configuration(tmp_path, monkeypatch):
    path = _fixture(tmp_path, monkeypatch, "notification")
    catalog = json.loads(path.read_text())
    catalog["spaceTotal"] = 11  # Upstream may omit inaccessible records; not a malformed directory.
    path.write_text(json.dumps(catalog, ensure_ascii=False))
    async with _native_page(tmp_path) as page:
        before = await native_call(page, "get_context")
        assert before["scope"]["kind"] == "personal"
        directory = await open_named_space(page, "数据中心-组织空间")
        assert directory["total"] == 11 and len(directory["spaces"]) < directory["total"]
        draft = await start_known_time(page, scene="msg-notify")
        groups = await native_call(page, "search_options", kind="recipient_group", query="BI测试群")
        group, = [item for item in groups["results"] if item["label"] == "BI测试群"]
        finished = await native_call(page, "apply_draft", expectedRevision=draft["revision"], finish=True, operations=[
            {"operation": "set_schedule", "frequency": "monthly", "monthDays": [1], "times": ["09:00"]},
            {"operation": "set_send_rule", "sendRule": "scheduled_no_dataset"},
            {"operation": "set_recipients", "groupRefs": [group["ref"]]},
            {"operation": "set_content", "components": [{"type": "richtext", "segments": [
                {"type": "mention-all"}, {"type": "newline"},
                {"type": "text", "text": "7月数码3C标准更新-第四期：游戏机、游戏手柄与电子书。", "italic": True},
                {"type": "newline"}, {"type": "link", "text": "7月数码3C标准更新-第四期",
                 "url": "https://atrenew.feishu.cn/wiki/KHSow4bRBioh3yk3BO4cFtoXnQg"}]}]},
            {"operation": "set_finalize", "ruleName": "7月标准更新通知"}])
        snapshot = await page.evaluate("window.nativeSnapshot()")
        assert snapshot["state"]["sendContent"]["title"]["main"]["template"], "Native title default must be real"
        events = await page.evaluate("window.nativeEvents")
        _assert_result("notification", snapshot, finished["completion"], {}, events)
        assert not any(item.get("args", {}).get("kind") in {"dataset", "field"} for item in events)


@pytest.mark.asyncio
async def test_native_53_columns_month_filters_and_download_share_one_atomic_batch(tmp_path, monkeypatch):
    dimensions = ["month", "C2", "C3", "C4"] + [f"dimension-{index}" for index in range(13)]
    metrics = [f"metric-{index}" for index in range(36)]
    keys = dimensions + metrics
    fields = [{"fieldId": key, "fieldName": key,
               "fieldDataType": "date" if key == "month" else "varchar" if key in dimensions else "number",
               "fieldType": "dimension" if key in dimensions else "metrics",
               "sourceUid": "attendance", "sourceType": "widget"} for key in keys]
    fixture = tmp_path / "attendance.json"
    fixture.write_text(json.dumps({"datasets": [{"datasetUid": "attendance", "datasetType": "widget",
                                                "datasetName": "考勤月汇总"}], "fields": fields}))
    monkeypatch.setenv("SUBSCRIPTION_NATIVE_CATALOG_FIXTURE", str(fixture))
    ref = lambda key: f"widget:attendance/{key}"
    async with _native_page(tmp_path) as page:
        draft = await start_known_time(page)
        result = await native_call(page, "apply_draft", expectedRevision=draft["revision"], finish=True, operations=[
            {"operation": "set_send_rule", "sendRule": "scheduled_dataset"},
            {"operation": "bind_dataset_query", "catalogDatasetRef": "widget:attendance", "queryKey": "attendance",
             "name": "当月考勤", "dimensionRefs": list(map(ref, dimensions)),
             "metrics": [{"fieldRef": ref(key), "outputKey": key} for key in metrics],
             "filters": [{"fieldRef": ref("month"), "operator": "eq", "valueExp": "current_month", "values": []},
                         *[{"fieldRef": ref(key), "operator": "in", "values": ["yes"]} for key in ["C2", "C3", "C4"]]]},
            {"operation": "set_push_mode", "mode": "all"},
            {"operation": "set_content", "title": "当月考勤明细", "components": [{"type": "button", "text": "下载考勤",
             "actionType": "download", "queryRef": "local:attendance", "fieldRefs": list(map(ref, keys))}]},
            {"operation": "set_finalize", "ruleName": "考勤月汇总订阅"}])
        snapshot = await page.evaluate("window.nativeSnapshot()")
        query, = snapshot["state"]["datasets"]
        assert [field["key"] for field in query["dimensions"]] == dimensions
        assert [field["key"] for field in query["metrics"]] == metrics
        assert len(query["filterList"]) == 4
        assert query["filterList"][0]["valueExp"] == "current_month"
        button = snapshot["state"]["sendContent"]["content"]["components"][0]["config"]
        assert button["actionType"] == "download" and button["refDatasetId"] == query["id"]
        assert [column["key"] for column in button["columns"]] == keys
        assert result["completion"]["status"] == "ready" and not result["completion"]["saved"]


@pytest.mark.asyncio
async def test_invalid_long_schedule_identifies_failed_operation_without_starting_draft(tmp_path):
    args = {"mode": "blank", "scene": "msg-notify", "operations": [
        {"operation": "set_content", "title": "通知", "components": [{"type": "richtext", "markdown": "内容"}]},
        {"operation": "set_schedule", "frequency": "monthly", "monthDays": [1], "times": ["09:00"],
         "effectiveMode": "long", "effectiveStart": "2026-09-01 00:00:00", "effectiveEnd": "2099-12-31 23:59:59"}]}
    contract = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json")).get("space.message_rule.start_draft")
    assert not Draft202012Validator(dict(contract.input_schema)).is_valid(args)
    async with _native_page(tmp_path) as page:
        before = await page.evaluate("window.nativeSnapshot()")
        result = await page.evaluate("args=>window.nativeExecute('space.message_rule.start_draft',args)", args)
        assert result["status"] == "error", result
        assert result["error"]["details"] == {
            "draftCode": "INVALID_ARGUMENT", "path": "trigger.effectiveStart", "reason": "long_effective_mode_has_boundaries",
            "message": "长期生效不能同时设置生效起止日期；请删除日期或使用范围生效。",
            "failedOpIndex": 1, "failedOperation": "set_schedule", "appliedOps": 0, "committed": False}
        assert await page.evaluate("window.nativeSnapshot()") == before


@pytest.mark.asyncio
async def test_dynamic_alert_binds_actual_employee_outputs_and_abnormal_record_content(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    _fixture(tmp_path, monkeypatch, "dynamic_alert")
    tomorrow = (datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(days=1)).strftime("%Y-%m-%d")
    async with _native_page(tmp_path) as page:
        await open_named_space(page, "数据中心-组织空间")
        draft = await native_call(page, "start_draft", mode="blank", scene="data-alert", operations=[
            {"operation": "set_schedule", "frequency": "once", "executeAt": tomorrow + " 11:00:00"}])
        datasets = await native_call(page, "search_options", kind="dataset", query="数巢订单明细模板")
        source, = datasets["results"]
        options = await native_call(page, "search_options", kind="field", datasetRef=source["ref"])
        fields = {field["label"]: field["ref"] for field in options["results"]}
        query_result = await native_call(page, "apply_draft", expectedRevision=draft["revision"], operations=[
            {"operation": "set_send_rule", "sendRule": "conditional"},
            {"operation": "upsert_dataset_query", "datasetRef": source["ref"],
             "dimensionRefs": [fields[label] for label in ["小订单号", "门店", "区经", "战区负责人"]],
             "metricRefs": [fields["提交订单额"]], "filters": [
                 {"fieldRef": fields["订单日期"], "operator": "eq", "valueExp": "last_day", "values": []},
                 {"fieldRef": fields["订单状态"], "operator": "in", "values": ["交易失败"]}]}])
        query, = query_result["queries"]
        outputs = {output["fieldRef"]: output["outputRef"] for output in query["outputs"]}
        finished = await native_call(page, "apply_draft", expectedRevision=query_result["revision"], finish=True, operations=[
            {"operation": "set_trigger_conditions", "queryRef": query["queryRef"], "conditions": [
                {"outputRef": outputs[fields["提交订单额"]], "operator": "gt", "values": [8000]}]},
            {"operation": "set_push_mode", "mode": "record", "queryRef": query["queryRef"]},
            {"operation": "set_recipients", "fieldRecipients": [
                {"queryRef": query["queryRef"], "fieldRef": fields[label]} for label in ["区经", "战区负责人"]]},
            {"operation": "set_content", "title": "$$yesterday$$大额失败订单", "titleBindings": [
                {"key": "yesterday", "refType": "system_time", "refSubType": "yesterday"}], "components": [
                    {"type": "richtext", "segments": [{"type": "text", "text": "昨日未成交的大额订单如下：", "bold": True, "color": "#ff0000"}]},
                    {"type": "data-table", "queryRef": query["queryRef"], "outputRefs": [
                        outputs[fields[label]] for label in ["小订单号", "门店", "区经", "提交订单额"]]}]},
            {"operation": "set_finalize", "ruleName": "昨日大额失败订单预警"}])
        actual = await page.evaluate("({snapshot:window.nativeSnapshot(),metricKeys:window.nativeMetricKeys(),events:window.nativeEvents})")
        _assert_result("dynamic_alert", actual["snapshot"], finished["completion"], actual["metricKeys"], actual["events"])


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_name", ["upsert_dataset_query", "bind_dataset_query"])
async def test_metric_output_alias_without_query_alias_is_rejected_before_native_commit(tmp_path, monkeypatch, operation_name):
    """A stale client cannot bypass the schema or partially write the query."""
    _fixture(tmp_path, monkeypatch, "data")
    async with _native_page(tmp_path) as page:
        draft = await start_known_time(page)
        source, fields = await find_order_fields(page)
        if operation_name == "upsert_dataset_query":
            operation = {"operation": operation_name, "datasetRef": source,
                         "metrics": [{"fieldRef": fields["总成交订单量"], "outputKey": "volume"}]}
        else:
            operation = {"operation": operation_name, "catalogDatasetRef": "widget:orders",
                         "metrics": [{"fieldRef": "widget:orders/volume", "outputKey": "volume"}]}
        args = {"expectedRevision": draft["revision"], "operations": [operation]}
        schema = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json")).get("space.message_rule.apply_draft").input_schema
        assert not Draft202012Validator(dict(schema)).is_valid(args)
        frontend_validation = await page.evaluate("args=>window.nativeValidate('space.message_rule.apply_draft',args)", args)
        assert frontend_validation["ok"] is False
        before = await page.evaluate("window.nativeSnapshot()")
        result = await page.evaluate("args=>window.nativeExecute('space.message_rule.apply_draft',args)", args)
        assert result["status"] == "error", result
        assert await page.evaluate("window.nativeSnapshot()") == before
