"""Exercise task continuation, deterministic handoff gates and bounded inference."""

import asyncio
import json
from dataclasses import asdict

import pytest
from claude_agent_sdk import StreamEvent

from app.agui.deferred_tools import DeferredFrontendToolStore
from app.agui.tool_ledger import ThreadLedger, ToolOperation
from app.errors import AppError
from app.runtime.claude import ClaudeAgentRuntime, _bounded_subscription_messages
from app.runtime.contracts import RuntimeContextItem, RuntimeEvent, RuntimeToolResult
from app.runtime.subscription_workflow import (
    prepare_subscription_task, record_subscription_facts, subscription_completion,
    subscription_receipt_notice, subscription_receipt_notices,
)
from tests.test_claude_runtime import FakeClaudeSdkClient, _native_request
from tests.test_turns import build_turn_services


@pytest.mark.asyncio
async def test_clarification_restores_task_on_a_new_worker_without_repeating_opening(settings_factory, tmp_path):
    request = _native_request(tmp_path, 'space.message_rule.get_context', text='每天九点给我发仪表盘截图')
    request.page_state['page']['workflow'] = 'subscription'
    factory = lambda: ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'},
        deferred_frontend_tools=DeferredFrontendToolStore(), client_factory=lambda _: FakeClaudeSdkClient())
    first = [event async for event in factory().run(request, asyncio.Event())]
    checkpoint = [event.payload for event in first if event.type == 'subscription.task'][-1]
    assert checkpoint['intro_shown'] is True
    request.text, request.run_id = 'A，使用原生 AI 解读', 'clarification'
    request.metadata['subscription_task'] = checkpoint
    second = [event async for event in factory().run(request, asyncio.Event())]
    assert not [event for event in second if event.type == 'subscription.progress']
    resumed = [event.payload for event in second if event.type == 'subscription.task'][-1]
    assert resumed['original_request'] == '每天九点给我发仪表盘截图'
    assert resumed['amendments'] == ['A，使用原生 AI 解读']


def test_a_different_native_configuration_starts_a_new_task(tmp_path):
    request = _native_request(tmp_path, text='新建每周订阅')
    request.context_items = (RuntimeContextItem('订阅配置上下文', json.dumps({'taskId': 'new'})),)
    task = prepare_subscription_task(request, {'native_task_id': 'old', 'intro_shown': True,
                                             'original_request': '每天九点'})
    assert task['intro_shown'] is False
    assert task['original_request'] == '新建每周订阅'


def test_manual_edit_context_refreshes_only_presentation_revision(tmp_path):
    request = _native_request(tmp_path, text='继续找数')
    request.context_items = (RuntimeContextItem('订阅配置上下文', json.dumps({
        'taskId': 'same-draft', 'revision': 7, 'hasActiveDraft': True
    })),)
    task = prepare_subscription_task(request, {'native_task_id': 'same-draft',
        'revision': 3, 'original_request': '每天九点监测昨日订单'})
    assert task['revision'] == 7
    assert task['native_task_id'] == 'same-draft'
    assert task['original_request'] == '每天九点监测昨日订单'
    assert 'configuration' not in task


def test_clarification_retains_candidate_identity_as_evidence():
    task = {}
    record_subscription_facts(task, {'kind': 'dashboard', 'contextVersion': 3, 'results': [
        {'ref': 'personal-board', 'label': '订单仪表盘', 'sourceKind': 'personal'},
        {'ref': 'space-board', 'label': '订单仪表盘', 'sourceKind': 'space'},
    ]})
    task['last_response'] = 'A 个人空间；B 运营空间。'
    assert task['candidate_evidence'][0]['items'][1]['ref'] == 'space-board'
    assert task['candidate_evidence'][0]['context_version'] == 3


def test_candidate_evidence_retains_ai_semantics_parent_and_displayed_twenty_choices():
    task = {'native_task_id': 'current', 'scope_key': {'instance': 'page', 'space': 'space-a'}}
    record_subscription_facts(task, {'kind': 'widget', 'contextVersion': 4, 'results': [
        {'ref': f'widget-{index}', 'label': f'组件{index}', 'contentKind': 'ai_interpret'}
        for index in range(20)]}, arguments={'dashboardRef': 'template-dashboard'})
    evidence = task['candidate_evidence'][0]
    assert len(evidence['items']) == 20 and evidence['truncated'] is False
    assert evidence['items'][-1]['contentKind'] == 'ai_interpret'
    assert evidence['parentRef'] == 'template-dashboard'
    assert evidence['taskId'] == 'current' and evidence['scope_key']['space'] == 'space-a'


def test_successful_finish_is_current_configuration_evidence_not_business_plan(tmp_path):
    request, ledger, data = _finish_request_and_ledger(tmp_path)
    ledger.subscription_task['executor'] = {'intent': {'pendingRequirements': [{'key': 'old-gap'}]}, 'blocked': True}
    assert subscription_completion(request, ledger) == data['completion']
    assert ledger.subscription_task.get('status') != 'awaiting_manual_save'
    request.text = '时间改成晚上六点'
    assert subscription_completion(request, ledger) is None


def test_native_receipts_supply_identity_and_never_roll_back_manual_edits():
    task = {}
    record_subscription_facts(task, {'taskId': 'draft', 'revision': 3})
    assert task == {'native_task_id': 'draft', 'revision': 3}
    task['revision'] = 7
    record_subscription_facts(task, {'activeDraft': {'taskId': 'draft', 'revision': 4,
        'configurationSteps': [{'step': 'content', 'status': 'configured'}]}})
    assert task == {'native_task_id': 'draft', 'revision': 7}
    record_subscription_facts(task, {'taskId': 'other', 'revision': 9})
    assert task == {'native_task_id': 'draft', 'revision': 7}
    record_subscription_facts(task, {'taskId': 'created', 'revision': 1}, allow_task_change=True)
    assert task == {'native_task_id': 'created', 'revision': 1}


def test_configuration_receipt_is_kept_exactly_and_cannot_replace_newer_facts():
    task = {}
    data = {'taskId': 'draft', 'revision': 3, 'configuration': {
        'operations': [{'operation': 'set_schedule', 'times': ['10:00']},
                       {'operation': 'set_content', 'components': [{'type': 'rich-text', 'text': '昨日成交'}]}],
        'unresolved': [{'path': 'queries.source.filters', 'message': '待核验'}]}}
    record_subscription_facts(task, data)
    assert task['native_readback']['configuration'] == data['configuration']
    data['configuration']['operations'][0]['times'] = ['12:00']
    assert task['native_readback']['configuration']['operations'][0]['times'] == ['10:00']
    before = json.loads(json.dumps(task))
    for task_id, revision in [('draft', 2), ('other', 4), ('draft', True), ('draft', None)]:
        record_subscription_facts(task, {'taskId': task_id, 'revision': revision,
            'configuration': {'operations': [], 'unresolved': []}})
        assert task == before


def test_more_than_eight_amendments_and_long_body_survive_without_silent_loss(tmp_path):
    request = _native_request(tmp_path, text='每天发送通知')
    task = prepare_subscription_task(request, {})
    amendments = [f'第 {index} 次补答' for index in range(10)] + ['  ' + '正文原文\n' * 900 + '  ']
    for index, amendment in enumerate(amendments):
        request.run_id, request.text = f'amendment-{index}', amendment
        task = prepare_subscription_task(request, json.loads(json.dumps(task)))
        assert prepare_subscription_task(request, task)['amendments'] == task['amendments']
    assert task['amendments'] == amendments
    assert len(task['amendments'][-1]) > 4000
    request.run_id, request.text = 'restore-original', '每天发送通知'
    assert prepare_subscription_task(request, task)['amendments'][-1] == '每天发送通知'


def test_subscription_prompt_recovers_abbreviated_history_from_the_session_workspace(settings_factory, tmp_path):
    request = _native_request(tmp_path, 'space.message_rule.get_context', text='时间改成18点')
    request.page_state['page']['workflow'] = 'subscription'
    request.metadata['subscription_task'] = {'original_request': '发送长通知',
        'amendments': ['第一条正文\n' * 5000], 'native_task_id': 'draft', 'revision': 2}
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'},
        deferred_frontend_tools=DeferredFrontendToolStore(), client_factory=lambda _: FakeClaudeSdkClient())
    prompt = runtime.build_options(request).system_prompt['append']
    path = request.cwd / '.subscription-request-history.json'
    assert str(path) in prompt
    assert 'omittedAmendments' in prompt and '"complete": false' in prompt
    assert '第一条正文\n' * 5000 not in prompt
    entries = json.loads(path.read_text())['entries']
    assert [''.join(entry['textParts']) for entry in entries] == [
        '发送长通知', '第一条正文\n' * 5000, '时间改成18点']
    assert '不必填写 presentation' in prompt
    assert 'metricsMode=merge' not in prompt
    assert 'ready 只证明结构合法' in prompt


def test_first_native_identity_does_not_inherit_an_unscoped_revision(tmp_path):
    task = {'revision': 9}
    record_subscription_facts(task, {'taskId': 'draft', 'revision': 1})
    assert task == {'native_task_id': 'draft', 'revision': 1}
    request = _native_request(tmp_path, text='继续')
    request.context_items = (RuntimeContextItem('订阅配置上下文', '{"taskId":"draft","revision":1}'),)
    task = prepare_subscription_task(request, {'revision': 9})
    assert task['native_task_id'] == 'draft' and task['revision'] == 1


def test_draft_opening_keeps_requirement_but_drops_old_configuration_facts(tmp_path):
    request = _native_request(tmp_path, text='')
    request.context_items = (RuntimeContextItem('订阅配置上下文', json.dumps({'taskId': 'created', 'revision': 1})),)
    request.tool_results = (RuntimeToolResult('start', '{"status":"success"}'),)
    task = prepare_subscription_task(request, {'native_task_id': 'old-port', 'revision': 7,
        'original_request': '每天十点发订单表格', 'intro_shown': True, 'next_step': 'finalize'})
    assert task['native_task_id'] == 'created' and task['revision'] == 1
    assert task['original_request'] == '每天十点发订单表格'
    assert task['intro_shown'] is True and 'next_step' not in task


@pytest.mark.parametrize('task_id,revision', [('', 2), ('x' * 257, 2), (123, 2), ('valid', True), ('valid', 0)])
def test_context_identity_and_revision_are_validated(tmp_path, task_id, revision):
    request = _native_request(tmp_path, text='配置订阅')
    request.context_items = (RuntimeContextItem('订阅配置上下文', json.dumps({'taskId': task_id, 'revision': revision})),)
    task = prepare_subscription_task(request, {})
    if task_id == 'valid':
        assert task['native_task_id'] == 'valid' and 'revision' not in task
    else:
        assert 'native_task_id' not in task and 'revision' not in task


def _finish_request_and_ledger(tmp_path):
    request = _native_request(tmp_path, text='')
    request.context_items = (RuntimeContextItem('订阅配置上下文', json.dumps({'taskId': 'draft', 'revision': 3})),)
    operation = ToolOperation('finish', 'space.message_rule.apply_draft', 'hash', 'frontend', 2,
        execution_result='success', subscription_finish=True, subscription_origin_run='original-run',
        subscription_operations=('set_finalize',))
    ledger = ThreadLedger(operations=[operation], subscription_task={'native_task_id': 'draft', 'revision': 3})
    data = {'taskId': 'draft', 'revision': 3, 'completion': {'status': 'ready', 'revision': 3,
        'saved': False, 'dataVerified': False,
        'message': '当前配置已完成。请到「命名并保存」点击「发送预览」，检查后手动保存。'}}
    request.tool_results = (RuntimeToolResult('finish', json.dumps({'status': 'success', 'data': data})),)
    return request, ledger, data

def test_finish_progress_is_a_short_receipt_not_automatic_final_summary(tmp_path):
    request, ledger, data = _finish_request_and_ledger(tmp_path)
    notice = subscription_receipt_notice(ledger.subscription_task, ledger.operations[0],
        request.tool_results[0], run_id=request.run_id)
    assert notice and notice['phase'] == 'completed'
    assert notice['text'] != data['completion']['message']
    assert subscription_receipt_notice(ledger.subscription_task, ledger.operations[0],
        request.tool_results[0], run_id='reconnected') is None


@pytest.mark.parametrize('change', [
    {'revision': 2}, {'saved': True}, {'dataVerified': True}, {'message': ''}, {'status': 'incomplete'},
])
def test_invalid_or_stale_completion_does_not_claim_the_configuration_passed(tmp_path, change):
    request, ledger, data = _finish_request_and_ledger(tmp_path)
    data['completion'].update(change)
    receipt = RuntimeToolResult('finish', json.dumps({'status': 'success', 'data': data}))
    notice = subscription_receipt_notice(ledger.subscription_task, ledger.operations[0], receipt, run_id=request.run_id)
    assert notice is None or '通过配置检查' not in notice['text']
    assert ledger.subscription_task.get('status') != 'configuration_ready'


@pytest.mark.parametrize('change', ['missing_task', 'wrong_task', 'newer_form', 'newer_context', 'wrong_context',
    'pending_write', 'error_receipt', 'not_ready', 'wrong_revision', 'empty_message', 'new_instruction'])
def test_summary_requires_current_identified_fully_resolved_configuration(tmp_path, change):
    request, ledger, data = _finish_request_and_ledger(tmp_path)
    if change == 'missing_task':
        del data['taskId']
    elif change == 'wrong_task':
        data['taskId'] = 'other'
    elif change == 'newer_form':
        ledger.subscription_task['revision'] = 4
    elif change == 'newer_context':
        request.context_items = (RuntimeContextItem('订阅配置上下文', '{"taskId":"draft","revision":4}'),)
    elif change == 'wrong_context':
        request.context_items = (RuntimeContextItem('订阅配置上下文', '{"taskId":"other","revision":3}'),)
    elif change == 'pending_write':
        ledger.record_call(ToolOperation('pending', 'space.message_rule.apply_draft', 'hash', 'frontend', 3))
    elif change == 'not_ready':
        data['completion']['status'] = 'incomplete'
    elif change == 'wrong_revision':
        data['completion']['revision'] = 2
    elif change == 'empty_message':
        data['completion']['message'] = ' '
    elif change == 'new_instruction':
        request.text = '时间改为九点'
    request.tool_results = (RuntimeToolResult('finish', json.dumps({'status': 'success', 'data': data}),
        is_error=change == 'error_receipt'),)
    assert subscription_completion(request, ledger) is None


@pytest.mark.asyncio
async def test_continuous_thinking_has_a_separate_decision_budget():
    async def messages():
        """Emulate a live model that never reaches an action or answer."""
        while True:
            await asyncio.sleep(.005)
            yield StreamEvent(uuid='x', session_id='x', event={'type': 'content_block_delta',
                'delta': {'type': 'thinking_delta', 'thinking': '继续考虑'}})
    with pytest.raises(AppError) as caught:
        async for _ in _bounded_subscription_messages(messages(), 1, .035):
            pass
    assert caught.value.code == 'SUBSCRIPTION_DECISION_LIMIT'


@pytest.mark.asyncio
async def test_runtime_request_uses_server_checkpoint_not_client_supplied_task(settings_factory):
    (_settings, database, session, _sessions, _attachments, _runtime, _broker, turns) = await build_turn_services(settings_factory)
    first = await turns.start(session.id, '原始订阅需求', [], 'task-first')
    await turns.wait(first.id)
    await turns.append_runtime_event(first.id, RuntimeEvent('subscription.task', {
        'original_request': '原始订阅需求', 'intro_shown': True
    }, 'system'))
    second = await turns.start(session.id, 'A', [], 'task-second', runtime_metadata={
        'subscription_task': {'original_request': 'untrusted override'}
    })
    await turns.wait(second.id)
    request = await turns.build_runtime_request(second.id)
    assert request.metadata['subscription_task'] == {'original_request': '原始订阅需求', 'intro_shown': True}
    await turns.shutdown()
    await database.dispose()
