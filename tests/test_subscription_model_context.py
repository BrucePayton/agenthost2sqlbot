"""Read-only business facts survive turns without a parallel executable plan."""
from copy import deepcopy
import json
import stat

import pytest

from app.runtime.subscription_model_context import (
    REQUEST_HISTORY_PROMPT_BUDGET, compact_subscription_task, subscription_resource_facts,
)


def source_task():
    return {"original_request": "每天推送昨日成交", "amendments": ["来源选A", "改成18点"],
        "native_task_id": "draft", "revision": 5, "status": "configuring",
        "last_response": "A个人来源，B模板来源", "candidate_evidence": [{"kind": "dashboard", "items": [
            {"ref": "personal-board", "label": "成交看板", "sourceKind": "personal"},
            {"ref": "template-board", "label": "成交看板", "sourceKind": "template"}]}],
        "confirmed_selections": {"dashboard": {"ref": "personal-board", "sourceKind": "personal"}},
        "operations": [{"tool_call_id": "private-ledger"}], "sdk_pending_results": [{"secret": "ledger"}],
        "native_readback": {"taskId": "draft", "revision": 5, "summary": "每天18点",
            "configuration": {"operations": [
                {"operation": "set_schedule", "frequency": "daily", "times": ["18:00"]},
                {"operation": "upsert_dataset_query", "queryRef": "query-sales", "filters": [
                    {"fieldRef": "date", "valueExp": "last_day", "values": []}],
                    "queryParameters": [{"fieldRef": "tenant", "values": ["confirmed"]}]},
                {"operation": "set_trigger_conditions", "queryRef": "query-sales", "conditionGroups": [
                    {"conditions": [{"outputRef": "original", "operator": "lt", "values": [10000]},
                                    {"outputRef": "rate", "operator": "lt", "values": [-0.1]}]}]},
                {"operation": "set_push_mode", "mode": "group", "queryRef": "query-sales", "fieldRef": "region"},
                {"operation": "set_recipients", "fieldRecipients": [{"queryRef": "query-sales", "fieldRef": "owner"}]},
                {"operation": "set_content", "components": [{"type": "rich-text", "text": {
                    "template": "成交量 ${volume}", "bindings": [{"name": "volume", "outputRef": "original"}]}}]}
            ], "unresolved": [{"path": "queries.query-sales.fields", "message": "字段目录未完全返回"}]},
            "queries": [{"queryRef": "query-sales", "datasetRef": "native-source", "fields": [
                {"ref": "volume", "label": "成交量", "role": "metric"}], "outputs": [
                {"outputRef": "original", "fieldRef": "volume", "isComparison": False},
                {"outputRef": "rate", "fieldRef": "volume", "isComparison": True}]}],
            "contentBindings": [{"componentRef": "component-ai", "type": "dashboard-chart",
                "contentKind": "ai_interpret", "widgetRef": "widget-ai", "dashboardRef": "personal-board"}]}}


def test_projection_retains_requirements_choices_and_current_native_facts_without_mutation():
    task = source_task()
    before = deepcopy(task)
    result = compact_subscription_task(task)
    assert task == before
    assert result["original_request"] == task["original_request"]
    assert result["amendments"] == task["amendments"]
    assert result["confirmed_selections"] == task["confirmed_selections"]
    assert result["last_response"] == task["last_response"]
    assert result["candidate_evidence"] == task["candidate_evidence"]
    assert result["current_configuration"]["summary"] == "每天18点"
    assert result["current_configuration"]["configuration"] == task["native_readback"]["configuration"]
    assert result["field_evidence"]["configuredQueries"] == task["native_readback"]["queries"]
    assert "queries" not in result["current_configuration"]
    assert "native_readback" not in result
    assert not {"executor", "operations", "sdk_pending_results", "requirements", "consumed_requirements"} & result.keys()
    assert "不是待重放操作" in result["notice"]
    assert "ready" in result["notice"]


def test_short_clarification_keeps_displayed_candidate_identity():
    task = source_task()
    task["amendments"].append("A")
    compact = compact_subscription_task(task)
    assert compact["amendments"][-1] == "A"
    assert compact["last_response"] == "A个人来源，B模板来源"
    assert compact["candidate_evidence"][0]["items"][0]["ref"] == "personal-board"


def test_actual_ai_identity_is_bound_to_native_task_and_revision():
    task = source_task()
    fact, = subscription_resource_facts(task)
    assert fact == {**task["native_readback"]["contentBindings"][0],
                   "purpose": "content", "status": "bound", "taskId": "draft", "revision": 5}
    task["native_readback"]["sourceBinding"] = {"kind": "widget", "widgetRef": "monitor-widget",
        "dashboardRef": "personal-board", "private": "not exposed"}
    source, content = subscription_resource_facts(task)
    assert source["purpose"] == "monitoring" and content["purpose"] == "content"
    assert "private" not in source


@pytest.mark.parametrize("identity", [{"taskId": "other", "revision": 5},
                                      {"taskId": "draft", "revision": 4},
                                      {"taskId": "draft", "revision": True}])
def test_stale_readback_cannot_claim_configuration_or_bound_components(identity):
    task = source_task()
    task["native_readback"].update(identity)
    compact = compact_subscription_task(task)
    assert "resource_facts" not in compact and "current_configuration" not in compact
    assert not compact.get("field_evidence", {}).get("configuredQueries")
    assert compact["confirmed_selections"] == task["confirmed_selections"]


def test_selected_candidate_does_not_prove_a_component_was_written():
    task = source_task()
    task["native_readback"]["contentBindings"] = []
    assert subscription_resource_facts(task) == []
    assert compact_subscription_task(task)["confirmed_selections"]["dashboard"]["ref"] == "personal-board"


def test_field_inventory_preserves_original_and_comparison_output_identity():
    task = source_task()
    query = task["native_readback"]["queries"][0]
    query["fields"] += [{"ref": f"field-{index}", "label": f"列{index}"} for index in range(80)]
    query["metadataReady"] = False
    compact = compact_subscription_task(task)
    actual, = compact["field_evidence"]["configuredQueries"]
    assert len(actual["fields"]) == 81
    assert actual["metadataReady"] is False
    assert [item["outputRef"] for item in actual["outputs"]] == ["original", "rate"]
    assert actual["outputs"][0]["fieldRef"] == actual["outputs"][1]["fieldRef"]


def test_short_request_history_does_not_create_a_file(tmp_path):
    path = tmp_path / '.subscription-request-history.json'
    task = source_task()
    result = compact_subscription_task(task, history_path=path)
    assert result['amendments'] == task['amendments']
    assert 'request_history' not in result
    assert not path.exists()


@pytest.mark.parametrize('body', ['完整正文\n' * 4000, '\x01' * 24000, '长正文无换行' * 6000])
def test_overflow_history_is_explicit_bounded_and_readable_without_losing_text(tmp_path, body):
    task = source_task()
    task['original_request'] = '  请每天发送以下正文：' + body + '  '
    task['amendments'] = [f'第 {index} 次确认，保留前述要求' for index in range(12)] + [body]
    before = deepcopy(task)
    path = tmp_path / '.subscription-request-history.json'
    compact = compact_subscription_task(task, history_path=path)
    assert task == before
    assert len(json.dumps({key: compact[key] for key in ('original_request', 'amendments')},
                          ensure_ascii=False)) <= REQUEST_HISTORY_PROMPT_BUDGET
    recovery = compact['request_history']
    assert recovery['complete'] is False and recovery['path'] == str(path)
    assert recovery['omittedAmendments'] == {'from': 1, 'to': 12}
    assert recovery['visibleAmendmentsStart'] == 13
    assert recovery['partialAmendments'][0]['number'] == 13
    assert recovery['originalRequestIncludedCharacters'] < recovery['originalRequestCharacters']
    source = json.loads(path.read_text())
    assert source['nativeTaskId'] == 'draft'
    assert [''.join(entry['textParts']) for entry in source['entries']] == [
        task['original_request'], *task['amendments']]
    assert max(map(len, path.read_text().splitlines())) < 2000
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_history_artifacts_are_session_local_rebuildable_and_reject_symlinks(tmp_path):
    task = source_task()
    task['amendments'] = ['第一会话原文' * 4000]
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    path = first / '.subscription-request-history.json'
    compact_subscription_task(task, history_path=path)
    saved = path.read_text()
    path.unlink()
    compact_subscription_task(json.loads(json.dumps(task)), history_path=path)
    assert path.read_text() == saved
    other = deepcopy(task)
    other['amendments'] = ['第二会话原文' * 4000]
    other_path = second / path.name
    compact_subscription_task(other, history_path=other_path)
    assert path.read_text() == saved and other_path.read_text() != saved
    other_path.unlink()
    other_path.symlink_to(path)
    from app.errors import AppError
    with pytest.raises(AppError, match='无法提供订阅要求原文'):
        compact_subscription_task(other, history_path=other_path)
    assert path.read_text() == saved


def test_omitted_history_is_not_abbreviated_without_a_recoverable_path():
    task = source_task()
    task['amendments'] = ['完整原文' * 4000]
    with pytest.raises(ValueError, match='session-local history path'):
        compact_subscription_task(task)
