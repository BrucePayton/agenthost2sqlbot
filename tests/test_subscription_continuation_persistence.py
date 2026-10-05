"""Drain genuine legacy SDK receipts without resuming retired business plans."""
from copy import deepcopy
from dataclasses import asdict
import json
import uuid

import pytest

from app.agui.deferred_tools import DeferredFrontendToolStore
from app.runner.protocol import RunnerRequest
from app.sandbox.repository import SandboxRepository
from app.sandbox.worker import OpenSandboxExecutionWorker
from app.runtime.contracts import RuntimeContextItem, RuntimeToolResult
from app.runtime.claude import build_user_message
from app.runtime.subscription_continuation import acknowledge_subscription_sdk_results, subscription_sdk_results
from app.runtime.subscription_workflow import prepare_subscription_task
from app.runtime.subscription_model_context import compact_subscription_task
from tests.test_agui_api import _client, _native_body
from tests.test_claude_runtime import _native_request
from tests.test_sandbox_worker import FakeSandboxPort


def test_legacy_checkpoint_discards_plan_but_keeps_actual_pending_receipts_and_selection(tmp_path):
    sdk_result = RuntimeToolResult("sdk-read", '{"status":"success","data":{}}')
    task = {"native_task_id": "draft", "revision": 2, "original_request": "每天十点发成交预警",
        "sdk_pending_results": [asdict(sdk_result)], "last_response": "A个人看板，B模板看板",
        "executor": {"intent": {"operations": [{"operation": "set_schedule", "times": ["10:00"]}]},
            "pending": {"tool_name": "space.message_rule.apply_draft"},
            "confirmed_selections": {"dashboard": {"ref": "chosen-board"}},
            "last_readback": {"taskId": "draft", "revision": 2, "summary": "已填写时间", "configuration": {"operations": []}}}}
    request = _native_request(tmp_path, text="A")
    request.context_items = (RuntimeContextItem("订阅配置上下文", '{"taskId":"draft","revision":2}'),)
    original = deepcopy(task)
    restored = prepare_subscription_task(request, task)
    assert task == original and "executor" not in restored
    assert restored["original_request"] == task["original_request"]
    assert restored["confirmed_selections"]["dashboard"]["ref"] == "chosen-board"
    assert restored["native_readback"]["summary"] == "已填写时间"
    assert restored["amendments"][-1] == "A"
    assert subscription_sdk_results(request, restored) == (sdk_result,)


def test_task_switch_cannot_replay_old_edits_or_lose_deferred_sdk_result(tmp_path):
    result = RuntimeToolResult("sdk-old", '{"status":"success","data":{}}')
    request = _native_request(tmp_path, text="新建另一条通知")
    request.context_items = (RuntimeContextItem("订阅配置上下文", '{"taskId":"new-draft","revision":1}'),)
    restored = prepare_subscription_task(request, {"native_task_id": "old-draft", "revision": 9,
        "original_request": "旧需求", "sdk_pending_results": [asdict(result)],
        "executor": {"intent": {"operations": [{"operation": "set_content", "title": "旧内容"}]}}})
    assert "executor" not in restored and "native_readback" not in restored
    assert restored["native_task_id"] == "new-draft" and restored["revision"] == 1
    assert restored["original_request"] == "新建另一条通知"
    assert subscription_sdk_results(request, restored) == (result,)


def test_manual_revision_and_late_receipt_do_not_restore_outdated_configuration(tmp_path):
    from app.runtime.subscription_workflow import record_subscription_facts

    request = _native_request(tmp_path, text='正文补充处理建议')
    request.context_items = (RuntimeContextItem('订阅配置上下文', '{"taskId":"draft","revision":4}'),)
    task = {'native_task_id': 'draft', 'revision': 2, 'original_request': '昨日成交预警',
        'amendments': [f'补答 {index}' for index in range(12)],
        'native_readback': {'taskId': 'draft', 'revision': 2, 'configuration': {
            'operations': [{'operation': 'set_content', 'title': '旧标题'}], 'unresolved': []}}}
    restored = prepare_subscription_task(request, json.loads(json.dumps(task)))
    assert len(restored['amendments']) == 13
    assert 'current_configuration' not in compact_subscription_task(restored)
    record_subscription_facts(restored, {'taskId': 'draft', 'revision': 3,
        'configuration': {'operations': [{'operation': 'set_content', 'title': '迟到标题'}]}})
    assert restored['revision'] == 4
    assert 'current_configuration' not in compact_subscription_task(restored)
    current = {'operations': [{'operation': 'set_content', 'title': '人工编辑标题'}], 'unresolved': []}
    record_subscription_facts(restored, {'taskId': 'draft', 'revision': 4, 'configuration': current})
    assert compact_subscription_task(restored)['current_configuration']['configuration'] == current


def test_only_real_model_receipts_enter_sdk_and_acknowledgement_is_selective(tmp_path):
    old = RuntimeToolResult("old-model", "old")
    fresh = RuntimeToolResult("new-model", "new")
    legacy_program = RuntimeToolResult("old-program", "program", origin="program")
    request = _native_request(tmp_path, text="", tool_results=(fresh, legacy_program))
    task = {"sdk_pending_results": [asdict(old), asdict(legacy_program)]}
    assert subscription_sdk_results(request, task) == (old, fresh)
    acknowledge_subscription_sdk_results(task, (old,))
    request.tool_results = ()
    assert subscription_sdk_results(request, task) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["program", "model"])
async def test_durable_origin_survives_route_repository_service_and_runner_roundtrip(settings_factory, origin):
    async with _client(settings_factory()) as (client, app):
        session_id = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        first_id, continued_id = str(uuid.uuid4()), str(uuid.uuid4())
        response = await client.post("/api/ag-ui", json=_native_body(session_id, first_id))
        assert response.status_code == 200
        services = app.state.services
        await services.turns.repository.append_event(first_id, "frontend_tool.deferred", "assistant", {
            "tool_use_id": "durable-call", "name": "space.message_rule.apply_draft",
            "arguments": {}, "origin_run_id": first_id, "origin": origin,
            "requires_restatement": origin == "model"})
        sdk_result = RuntimeToolResult("sdk-original", '{"status":"success","data":{}}')
        checkpoint = {
            "sdk_pending_results": [asdict(sdk_result)], "native_task_id": "old-draft", "revision": 2,
            "original_request": "原始通知正文\n" * 1000,
            "amendments": [f"补答 {index}" for index in range(12)] + ["完整修改正文\n" * 1000],
            "native_readback": {"taskId": "old-draft", "revision": 2, "configuration": {
                "operations": [{"operation": "set_content", "title": "已确认标题"}], "unresolved": []}}}
        await services.turns.repository.append_event(first_id, "subscription.task", "system", checkpoint)
        services.deferred_frontend_tools = DeferredFrontendToolStore(max_entries=1)
        recovered, = await services.turns.repository.frontend_tool_recovery(session_id)
        assert recovered["call"].origin == origin
        body = _native_body(session_id, continued_id)
        body["messages"] = [{"id": "receipt", "role": "tool", "toolCallId": "durable-call",
            "content": '{"status":"success","data":{},"issues":[]}',
            "origin": "model" if origin == "program" else "program"}]
        response = await client.post("/api/ag-ui", json=body)
        assert response.status_code == 200, response.text
        request = await services.turns._runtime_request(continued_id)
        assert request.tool_results[0].origin == origin  # Never trust client-supplied origin.
        recovery, = await services.turns.repository.frontend_tool_recovery(session_id)
        assert recovery["tool_result"]["origin"] == origin
        worker = OpenSandboxExecutionWorker(database=services.database, turns=services.turns,
            repository=SandboxRepository(services.database), sandbox=FakeSandboxPort(services.database),
            runtime_cohort="subscription-test", runner_image="runner@sha256:" + "a" * 64,
            allowed_hosts=(), sandbox_timeout_seconds=900, memory_lease_seconds=60, runner_runtime="fake")
        owner = await worker._session_owner(session_id)
        runner = await worker._runner_request(continued_id, "test-attempt", 1, request, owner)
        assert runner.subscription_task == request.metadata["subscription_task"]
        assert runner.tool_results[0].origin == origin
        runtime = RunnerRequest.model_validate_json(runner.to_bytes()).to_runtime_request()
        assert runtime.tool_results[0].origin == origin
        assert set(runtime.metadata) == {"subscription_task"}
        task = runtime.metadata["subscription_task"]
        assert task == checkpoint
        results = subscription_sdk_results(runtime, task)
        assert {result.tool_call_id for result in results} == (
            {"sdk-original"} if origin == "program" else {"sdk-original", "durable-call"})
        message = await build_user_message(runtime, sdk_tool_results=results)
        blocks = message["message"]["content"]
        assert {block["tool_use_id"] for block in blocks if block["type"] == "tool_result"} == {
            result.tool_call_id for result in results}
        task["sdk_pending_results"] = [asdict(result) for result in results]
        # A worker restart before an SDK response retains exactly these real results.
        restored = json.loads(json.dumps(task))
        runtime.tool_results = ()
        assert subscription_sdk_results(runtime, restored) == results
        acknowledge_subscription_sdk_results(restored, results)
        assert subscription_sdk_results(runtime, restored) == ()
        services.deferred_frontend_tools = DeferredFrontendToolStore(max_entries=1)
        assert (await client.post("/api/ag-ui", json=body)).status_code == 200
        next_id = str(uuid.uuid4())
        amended = _native_body(session_id, next_id)
        amended["messages"] = [{"id": "amendment", "role": "user", "content": "发送时间改为十一点"}]
        assert (await client.post("/api/ag-ui", json=amended)).status_code == 200
        assert (await services.turns._runtime_request(next_id)).text == "发送时间改为十一点"
