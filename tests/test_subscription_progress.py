"""Progress is visible before configuration and factual after native receipts."""
import asyncio
import json

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from app.agui.adapter import AgUiEventMapper
from app.agui.claude_tools import native_frontend_sdk_name
from app.agui.deferred_tools import DeferredFrontendToolStore
from app.agui.tool_ledger import ThreadLedger, ToolOperation
from app.runtime.claude import ClaudeAgentRuntime
from app.runtime.contracts import RuntimeToolResult
from app.runtime.subscription_workflow import (
    subscription_presentation, subscription_receipt_notice, subscription_receipt_notices,
    subscription_recovery_notice, subscription_started_notice,
)
from tests.test_claude_runtime import FakeClaudeSdkClient, _native_request


@pytest.mark.parametrize('step,purpose', [
    ('receivers', 'BI测试群接收每月通知'),
    ('content', '数巢仪表盘模板截图、AI解读和跳转按钮'),
    ('datasets', '昨日门店成交量、区域及负责人账号和明细下载字段'),
    ('conditions', '昨日成交量＜10000且日环比＜−10%'),
])
def test_each_scenario_dispatch_preserves_business_goal(step, purpose):
    task = {'native_task_id': 'native-task', 'revision': 4}
    args = {'presentation': {'step': step, 'purpose': purpose}}
    notice = subscription_started_notice(task, None, run_id='run', tool_call_id='call',
        tool_name='space.message_rule.apply_draft', arguments=args)
    assert notice['phase'] == 'started' and notice['step'] == step
    assert purpose in notice['text'] and '已配置' not in notice['text']
    assert notice['taskId'] == 'native-task' and notice['revision'] == 4
    assert subscription_started_notice(task, None, run_id='run', tool_call_id='call',
        tool_name='space.message_rule.apply_draft', arguments=args) is None


def test_schedule_uses_parsed_arguments_without_another_model_request():
    notice = subscription_started_notice({}, None, run_id='run', tool_call_id='schedule',
        tool_name='space.message_rule.start_draft', arguments={'operations': [
            {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['09:00', '18:00']}
        ]})
    assert notice['step'] == 'trigger'
    assert '每天09:00、18:00执行' in notice['text']


def test_mixed_batch_uses_default_progress_and_reports_only_actual_configuration():
    operations = [
        {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']},
        {'operation': 'set_recipients', 'memberRefs': ['self']},
        {'operation': 'set_content', 'title': '订单通知'},
    ]
    task = {'native_task_id': 'draft', 'revision': 1}
    args = {'expectedRevision': 1, 'operations': operations}
    operation = ToolOperation('mixed', 'space.message_rule.apply_draft', 'hash', 'frontend', 1,
        subscription_operations=tuple(item['operation'] for item in operations),
        subscription_arguments=args, subscription_origin_run='run')
    started = subscription_started_notice(task, operation, run_id='run', tool_call_id='mixed',
        tool_name=operation.tool_name, arguments=args)
    assert started['step'] == 'trigger'
    assert started['text'] == '正在配置：每天10:00执行、接收人、消息标题。'
    operation.execution_result = 'success'
    actual = [{**operations[0], 'times': ['11:00']}, *operations[1:]]
    receipt = RuntimeToolResult('mixed', json.dumps({'status': 'success', 'data': {
        'taskId': 'draft', 'revision': 2, 'configuration': {'operations': actual}}}))
    completed = subscription_receipt_notice(task, operation, receipt, run_id='run')
    assert completed['text'] == '已设置：每天11:00执行、接收人、消息标题。'
    assert subscription_receipt_notice(task, operation, receipt, run_id='replay') is None


@pytest.mark.parametrize('schedule,expected', [
    ({'frequency': 'daily', 'dailyMode': 'workday', 'times': ['09:00']}, '每个工作日09:00'),
    ({'frequency': 'weekly', 'weekdays': [1, 3], 'times': ['10:00']}, '每周一、三10:00'),
    ({'frequency': 'monthly', 'monthDays': [1, 'eom'], 'times': ['09:00']}, '每月1日、最后一天09:00'),
    ({'frequency': 'once', 'executeAt': '2026-09-15 11:00'}, '2026-09-15 11:00单次'),
])
def test_schedule_progress_preserves_actual_day_and_frequency(schedule, expected):
    notice = subscription_started_notice({}, None, run_id='run', tool_call_id='schedule',
        tool_name='space.message_rule.apply_draft', arguments={'operations': [
            {'operation': 'set_schedule', **schedule}
        ]})
    assert expected in notice['text']


def test_dashboard_chart_lookup_is_content_discovery_without_explicit_presentation():
    notice = subscription_started_notice({}, None, run_id='run', tool_call_id='chart',
        tool_name='space.message_rule.search_options', arguments={'kind': 'widget', 'dashboardRef': 'ref'})
    assert notice['step'] == 'content'
    assert '数据查询' not in notice['text']


def test_alert_lookup_follows_known_data_phase_and_names_the_actual_resource():
    task = {'next_step': 'datasets'}
    board = subscription_started_notice(task, None, run_id='run', tool_call_id='board',
        tool_name='space.message_rule.search_options', arguments={'kind': 'dashboard', 'query': '订单模板'})
    assert board['step'] == 'datasets' and '订单模板' in board['text']
    widget = subscription_started_notice(task, None, run_id='run', tool_call_id='widget',
        tool_name='space.message_rule.search_options', arguments={'kind': 'alert_widget', 'dashboardRef': 'ref'})
    assert widget['step'] == 'datasets' and '可用于预警的图表' in widget['text']
    content = subscription_started_notice(task, None, run_id='run', tool_call_id='content',
        tool_name='space.message_rule.search_options', arguments={'kind': 'dashboard',
        'presentation': {'step': 'content', 'purpose': '消息中的仪表盘跳转'}})
    assert content['step'] == 'content'  # The current explicit action takes priority.


def test_schedule_and_send_rule_receipt_describe_distinct_written_settings(tmp_path):
    request = _native_request(tmp_path, text='')
    operation = ToolOperation('seed', 'space.message_rule.start_draft', 'hash', 'frontend', None,
        execution_result='success', subscription_operations=('set_schedule', 'set_send_rule'),
        subscription_presentation={'nextStep': 'datasets'})
    ledger = ThreadLedger(operations=[operation])
    request.tool_results = (RuntimeToolResult('seed', json.dumps({'status': 'success', 'data': {
        'revision': 1, 'configuration': {'operations': [
            {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']},
            {'operation': 'set_send_rule', 'sendRule': 'conditional'}
        ]}
    }})),)
    notice, = subscription_receipt_notices(request, ledger)
    assert notice['text'] == '已设置：每天10:00执行、满足条件时发送。'
    assert ledger.subscription_task['next_step'] == 'datasets'


def test_data_mcp_uses_confirmed_next_stage_once_without_changing_its_arguments():
    task = {'next_step': 'datasets', 'revision': 2}
    args = {'query': '昨日门店成交量'}
    first = subscription_started_notice(task, None, run_id='run', tool_call_id='find1',
        tool_name='mcp__davinci_data__catalog_search_datasets', arguments=args)
    assert first['step'] == 'datasets' and '昨日门店成交量' in first['text']
    assert args == {'query': '昨日门店成交量'}
    assert subscription_started_notice(task, None, run_id='run', tool_call_id='find2',
        tool_name='mcp__davinci_data__catalog_schema', arguments={}) is None


def test_completion_uses_actual_readback_and_replays_once(tmp_path):
    request = _native_request(tmp_path, text='')
    ledger = ThreadLedger(subscription_task={'revision': 3})
    operation = ToolOperation('write', 'space.message_rule.apply_draft', 'hash', 'frontend', 2,
        execution_result='success', subscription_operations=('set_schedule',),
        subscription_origin_run='original-run',
        subscription_presentation={'nextStep': 'datasets', 'purpose': '错误的完成文案'})
    ledger.record_call(operation)
    request.tool_results = (RuntimeToolResult('write', json.dumps({'status': 'success', 'data': {
        'revision': 3, 'configuration': {'operations': [
            {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']}
        ]}
    }})),)
    notice, = subscription_receipt_notices(request, ledger)
    assert notice['text'] == '已设置：每天10:00执行。'
    assert notice['noticeId'] == 'original-run:subscription:write:completed'
    assert 'step' not in notice  # Completion never starts the next phase/navigation.
    assert ledger.subscription_task['next_step'] == 'datasets'
    request.run_id = 'reconnected-run'
    assert subscription_receipt_notices(request, ledger) == []


def test_denied_call_and_failed_receipt_never_report_success(tmp_path):
    operation = ToolOperation('denied', 'space.message_rule.apply_draft', 'h', 'frontend', 1,
                             execution_result='denied')
    assert subscription_started_notice({}, operation, run_id='run', tool_call_id='denied',
        tool_name=operation.tool_name, arguments={'presentation': {'step': 'datasets'}}) is None
    ledger = ThreadLedger(operations=[operation])
    request = _native_request(tmp_path, text='')
    request.tool_results = (RuntimeToolResult('denied', '{"status":"error"}', is_error=True),)
    notice, = subscription_receipt_notices(request, ledger)
    assert notice['phase'] == 'failed' and '已设置' not in notice['text']
    assert subscription_receipt_notices(request, ledger) == []
    operation.execution_result = 'success'
    assert subscription_started_notice({}, operation, run_id='replay', tool_call_id='denied',
        tool_name=operation.tool_name, arguments={'presentation': {'step': 'datasets'}}) is None


def test_adapter_stable_notice_identity_and_live_navigation_sideband():
    mapper = AgUiEventMapper('session', 'run', bridge=None)
    payload = {'noticeId': 'stable-notice', 'phase': 'started', 'runId': 'run',
               'step': 'datasets', 'taskId': 'native', 'revision': 2, 'text': '正在查找昨日成交量。'}
    events = mapper.map('subscription.progress', payload)
    assert events[0].message_id == 'stable-notice'
    assert events[-1].name == 'workspace.subscription_progress'
    assert events[-1].value == payload
    assert mapper.map('subscription.progress', payload) == []


def test_progress_keeps_understanding_and_later_prose_in_chronological_order():
    mapper = AgUiEventMapper('session', 'run', bridge=None)
    intro = mapper.map('message.assistant.delta', {'text': '我理解你希望每天10点收到订单表格。'})
    progress = mapper.map('subscription.progress', {'noticeId': 'stage', 'text': '正在配置时间。'})
    later = mapper.map('message.assistant.delta', {'text': '这里需要明确订单日期口径。'})
    assert progress[0].type.value == 'TEXT_MESSAGE_END'
    assert progress[0].message_id == intro[0].message_id
    assert later[0].type.value == 'TEXT_MESSAGE_START'
    assert later[0].message_id != intro[0].message_id
    assert later[0].message_id != 'stage'


@pytest.mark.asyncio
async def test_same_model_response_understanding_then_stage_then_native_tool(settings_factory, tmp_path):
    action = 'space.message_rule.apply_draft'
    sdk_name = native_frontend_sdk_name(action)
    purpose = '每日10:00执行'
    args = {'presentation': {'step': 'trigger', 'purpose': purpose}, 'operations': [
        {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']}
    ]}

    class Client(FakeClaudeSdkClient):
        async def receive_response(self):
            yield AssistantMessage(model='test', content=[
                TextBlock(text='我理解你希望每天10:00收到昨日订单表格。'),
                ToolUseBlock(id='configure', name=sdk_name, input=args),
            ])
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id='test')

    request = _native_request(tmp_path, action, text='每天10点给我发昨日订单表格')
    request.page_state['page']['workflow'] = 'subscription'
    client = Client()
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'},
        deferred_frontend_tools=DeferredFrontendToolStore(), client_factory=lambda _: client)
    events = [event async for event in runtime.run(request, asyncio.Event())]
    types = [event.type for event in events]
    assert types.index('message.assistant.completed') < types.index('subscription.progress') < types.index('tool.started')
    progress, = [event.payload for event in events if event.type == 'subscription.progress']
    assert purpose in progress['text'] and progress['phase'] == 'started'
    assert '先在本次回复的聊天正文' in str(runtime.build_options(request).system_prompt)


def test_presentation_cannot_introduce_arbitrary_navigation_or_large_text():
    assert subscription_presentation({'presentation': {'step': '/admin', 'nextStep': 'datasets',
        'purpose': 'x' * 900, 'config': {'write': True}}}) == {'nextStep': 'datasets', 'purpose': 'x' * 400}


def test_native_dispatch_and_receipt_use_the_same_body_events():
    task = {'native_task_id': 'draft', 'revision': 2}
    operation = ToolOperation('native-write', 'space.message_rule.apply_draft', 'hash', 'frontend', 2,
        subscription_operations=('set_schedule',), subscription_origin_run='run')
    started = subscription_started_notice(task, operation, run_id='run',
        tool_call_id=operation.tool_use_id, tool_name=operation.tool_name,
        arguments={'expectedRevision': 2, 'operations': [
            {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']} ]})
    assert started['phase'] == 'started' and started['revision'] == 2
    operation.execution_result = 'success'
    receipt = RuntimeToolResult(operation.tool_use_id, json.dumps({'status': 'success', 'data': {
        'taskId': 'draft', 'revision': 3, 'configuration': {'operations': [
            {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']} ]}}}))
    completed = subscription_receipt_notice(task, operation, receipt, run_id='continued-run')
    assert completed['text'] == '已设置：每天10:00执行。'
    assert completed['taskId'] == 'draft' and completed['revision'] == 3
    mapper = AgUiEventMapper('session', 'run', bridge=None)
    for notice in (started, completed):
        events = mapper.map('subscription.progress', notice)
        assert events[1].type.value == 'TEXT_MESSAGE_CONTENT'
        assert events[1].delta == notice['text']
    assert subscription_receipt_notice(task, operation, receipt, run_id='replayed') is None


@pytest.mark.parametrize('error,phase,fragment', [
    ({'code': 'INVALID_ARGUMENT'}, 'failed', '参数未通过校验'),
    ({'code': 'STALE_CONTEXT'}, 'failed', '页面配置或引用已更新'),
    ({'code': 'INVALID_ARGUMENT', 'details': {'draftCode': 'OPTION_REF_STALE'}}, 'failed', '引用已更新'),
    ({'code': 'TOOL_NOT_AVAILABLE', 'details': {'draftCode': 'SECTION_BUSY'}}, 'waiting', '正在处理中'),
    ({'code': 'OUTPUT_SCHEMA_INVALID'}, 'failed', '回执未通过校验'),
    ({'code': 'TOOL_TIMEOUT'}, 'failed', '暂时无法确认'),
])
def test_failure_body_classifies_observed_result_once(tmp_path, error, phase, fragment):
    operation = ToolOperation('write', 'space.message_rule.apply_draft', 'hash', 'frontend', 1,
        execution_result='error', subscription_origin_run='original')
    request = _native_request(tmp_path, text='')
    request.tool_results = (RuntimeToolResult('write', json.dumps({'status': 'error', 'error': error}), is_error=True),)
    ledger = ThreadLedger(operations=[operation])
    assert fragment in subscription_recovery_notice(request, ledger)
    notice, = subscription_receipt_notices(request, ledger)
    assert notice['phase'] == phase and fragment in notice['text']
    assert 'step' not in notice and '已设置' not in notice['text']
    assert subscription_receipt_notices(request, ledger) == []


def test_stale_write_notice_cannot_borrow_the_newer_form_revision():
    notice = subscription_started_notice({'native_task_id': 'draft', 'revision': 7}, None,
        run_id='run', tool_call_id='stale', tool_name='space.message_rule.apply_draft',
        arguments={'expectedRevision': 3, 'presentation': {'step': 'datasets'}})
    assert notice['revision'] == 3  # The parent rejects navigation at this old revision.


@pytest.mark.parametrize('receipt_task,receipt_revision', [('other-draft', 7), ('draft', 3)])
def test_old_receipt_does_not_advance_or_describe_current_configuration(receipt_task, receipt_revision):
    task = {'native_task_id': 'draft', 'revision': 7, 'next_step': 'content'}
    operation = ToolOperation('old', 'space.message_rule.apply_draft', 'hash', 'frontend', 2,
        execution_result='success', subscription_operations=('set_schedule',),
        subscription_presentation={'nextStep': 'datasets'})
    receipt = RuntimeToolResult('old', json.dumps({'status': 'success', 'data': {
        'taskId': receipt_task, 'revision': receipt_revision, 'configuration': {'operations': [
            {'operation': 'set_schedule', 'frequency': 'daily', 'times': ['10:00']} ]}}}))
    assert subscription_receipt_notice(task, operation, receipt, run_id='run') is None
    assert task == {'native_task_id': 'draft', 'revision': 7, 'next_step': 'content'}
