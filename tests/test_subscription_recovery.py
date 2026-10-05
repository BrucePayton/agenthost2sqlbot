"""Regression for a mutated draft whose public readback fails (session 6fa0b280)."""
import asyncio
import json

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolResultBlock, ToolUseBlock, UserMessage

from app.agui.adapter import AgUiEventMapper
from app.agui.deferred_tools import DeferredFrontendToolStore
from app.agui.tool_ledger import ThreadLedger, ToolOperation
from app.errors import AppError
from app.runtime.claude import ClaudeAgentRuntime, _bounded_subscription_messages, _effective_thinking_config, _subscription_receipt_stage
from app.runtime.contracts import RuntimeToolResult
from tests.test_claude_runtime import FakeClaudeSdkClient, _native_request


def receipt(ledger, action, payload, success=True):
    """Resolve a frontend call as the real deferred-result path does."""
    call_id = str(len(ledger.operations))
    ledger.record_call(ToolOperation(call_id, 'space.message_rule.' + action, call_id, 'frontend', None))
    ledger.resolve(call_id, success=success, revision_after=None, write_receipt=False)
    content = json.dumps(payload)
    ledger.record_subscription_result(call_id, content)
    return RuntimeToolResult(call_id, content, is_error=not success)


def broken_receipt(ledger, action='start_draft'):
    """Mirror the old output-schema error, without relying on the new error code."""
    return receipt(ledger, action, {'status': 'error', 'error': {
        'code': 'EXECUTION_FAILED', 'message': 'Frontend Tool output failed its declared schema.'
    }}, False)


@pytest.mark.parametrize('name,args', [
    ('space.message_rule.start_draft', {'mode': 'blank', 'scene': 'data-alert'}),
    ('space.message_rule.search_options', {'kind': 'dataset', 'query': '成交量'}),
    ('mcp__davinci_data__catalog_search_datasets', {'query': '成交量'}),
    ('space.message_rule.apply_draft', {'operations': [{'operation': 'bind_dataset_query', 'catalogDatasetRef': 'wrong-source'}]}),
])
def test_readback_failure_blocks_source_substitution_even_after_successful_search(name, args):
    ledger = ThreadLedger()
    broken_receipt(ledger)
    receipt(ledger, 'search_options', {'status': 'success', 'data': {'results': [{'ref': 'other'}]}})
    assert ledger.subscription_recovery_denial(name, args)
    assert ledger.subscription_recovery_denial('space.message_rule.get_context', {}) is None
    assert ledger.subscription_recovery_denial('space.message_rule.apply_draft', {
        'operations': [{'operation': 'set_schedule', 'times': ['10:00']}]
    }) is None


def test_failed_recovery_read_stops_loop_but_verified_read_or_user_turn_can_resume():
    ledger = ThreadLedger()
    broken_receipt(ledger)
    broken_receipt(ledger, 'get_context')
    assert ledger.subscription_recovery_denial('space.message_rule.get_context', {})
    receipt(ledger, 'get_context', {'status': 'success', 'data': {'activeDraft': {
        'revision': 3, 'configuration': {'operations': [], 'unresolved': []}
    }}})
    assert ledger.subscription_recovery is None
    broken_receipt(ledger)
    ledger.start_user_turn('我已在页面修复了按钮，继续配置')
    assert ledger.subscription_recovery is None


def test_partial_readback_keeps_recovery_without_blocking_independent_configuration():
    ledger = ThreadLedger()
    receipt(ledger, 'get_context', {'status': 'success', 'data': {'activeDraft': {
        'configuration': {'unresolved': [{'message': 'READBACK_SCHEMA_INVALID:set_content'}]}
    }}})
    assert ledger.subscription_recovery == 'partial'
    assert ledger.subscription_recovery_denial('space.message_rule.search_options', {'kind': 'recipient_group'}) is None


@pytest.mark.asyncio
async def test_runtime_preserves_source_during_recovery_with_business_reasoning_available(settings_factory, tmp_path):
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'}, deferred_frontend_tools=DeferredFrontendToolStore())
    request = _native_request(tmp_path, 'space.message_rule.start_draft', 'space.message_rule.get_context', text='')
    request.workspace_snapshot.setdefault('allowed_tools', []).append('mcp__davinci_data__catalog_search_datasets')
    ledger = runtime.tool_ledger.get(request.platform_session_id)
    request.tool_results = (broken_receipt(ledger),)
    assert _subscription_receipt_stage(request, ledger) == 'recovery'
    assert _effective_thinking_config(request, settings_factory(), ledger) == {'type': 'enabled', 'budget_tokens': 2048}
    options = runtime.build_options(request)
    hook = options.hooks['PreToolUse'][0].hooks[0]
    output = await hook({'tool_name': 'mcp__davinci_ui__space__message_rule__start_draft',
                         'tool_input': {'mode': 'blank', 'scene': 'data-alert'}}, 'retry', {})
    assert output['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert 'SUBSCRIPTION_RECOVERY_REQUIRED' in str(output)
    catalog = await hook({'tool_name': 'mcp__davinci_data__catalog_search_datasets',
                          'tool_input': {'query': '成交量'}}, 'fallback', {})
    assert 'SUBSCRIPTION_SOURCE_PRESERVED' in str(catalog)


@pytest.mark.asyncio
@pytest.mark.parametrize('delta', [
    {'type': 'thinking_delta', 'thinking': '正在核对配置'},
    {'type': 'text_delta', 'text': '已找到需要的数据'},
    {'type': 'input_json_delta', 'partial_json': '{"operations":['},
])
async def test_live_output_can_exceed_response_timeout_and_reach_tool_call(delta):
    """Replay slow streaming from 2b1a4a12/c374b4e1 without a wall-time abort."""
    async def messages():
        for _ in range(20):
            await asyncio.sleep(0.005)
            yield StreamEvent(uuid='x', session_id='x', event={'type': 'content_block_delta', 'delta': delta})
        yield AssistantMessage(model='test', content=[ToolUseBlock(id='configure', name='apply_draft', input={})])
        yield UserMessage(content=[ToolResultBlock(tool_use_id='configure', content='configured')])
        yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id='test')
    result = [message async for message in _bounded_subscription_messages(messages(), 0.05)]
    assert isinstance(result[-1], ResultMessage)
    assert result[-3].content[0].id == 'configure'


@pytest.mark.asyncio
@pytest.mark.parametrize('event', [
    {'type': 'ping'},
    {'type': 'content_block_delta', 'delta': {'type': 'thinking_delta', 'thinking': ''}},
])
async def test_keepalives_and_empty_deltas_do_not_hide_response_stall(event):
    """A connected but unresponsive stream still terminates."""
    async def messages():
        while True:
            await asyncio.sleep(0.005)
            yield StreamEvent(uuid='x', session_id='x', event=event)
    with pytest.raises(AppError) as error:
        _ = [event async for event in _bounded_subscription_messages(messages(), 0.03)]
    assert error.value.code == 'SUBSCRIPTION_NO_PROGRESS'
    assert '草稿' not in error.value.message
    assert '已写入' not in error.value.message


@pytest.mark.asyncio
async def test_runtime_interrupts_silent_stream_without_claiming_saved_settings(settings_factory, tmp_path):
    """Exercise cancellation and the actual failure message after a partial response."""
    class StalledClient(FakeClaudeSdkClient):
        interrupted = False
        disconnected = False

        async def receive_response(self):
            yield StreamEvent(uuid='x', session_id='x', event={
                'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '正在查找数据'}
            })
            await asyncio.sleep(1)

        async def interrupt(self):
            self.interrupted = True

        async def disconnect(self):
            self.disconnected = True

    request = _native_request(tmp_path, 'space.message_rule.get_context')
    request.page_state['page']['workflow'] = 'subscription'
    client = StalledClient()
    settings = settings_factory()
    settings.subscription_model_step_timeout_seconds = 0.03
    runtime = ClaudeAgentRuntime(settings, environ={'PATH': '/usr/bin'}, deferred_frontend_tools=DeferredFrontendToolStore(), client_factory=lambda _: client)
    with pytest.raises(AppError) as error:
        _ = [event async for event in runtime.run(request, asyncio.Event())]
    assert error.value.code == 'SUBSCRIPTION_NO_PROGRESS'
    assert client.interrupted and client.disconnected
    assert '已写入' not in error.value.message
    assert '启用状态' in error.value.message


@pytest.mark.asyncio
async def test_real_tool_execution_is_excluded_from_model_deadline():
    async def messages():
        """Tool execution may take longer than a model planning interval."""
        yield AssistantMessage(model='test', content=[ToolUseBlock(id='lookup', name='Read', input={})])
        await asyncio.sleep(0.06)
        yield UserMessage(content=[ToolResultBlock(tool_use_id='lookup', content='ready')])
        yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id='test')
    result = [message async for message in _bounded_subscription_messages(messages(), 0.03)]
    assert len(result) == 3


@pytest.mark.asyncio
async def test_subscription_uses_model_understanding_without_generic_opening(settings_factory, tmp_path):
    request = _native_request(tmp_path, 'space.message_rule.get_context')
    request.page_state['page']['workflow'] = 'subscription'
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'}, deferred_frontend_tools=DeferredFrontendToolStore(), client_factory=lambda _: FakeClaudeSdkClient())
    events = [event async for event in runtime.run(request, asyncio.Event())]
    types = [event.type for event in events]
    assert 'subscription.progress' not in types
    assert 'message.assistant.delta' in types
    mapper = AgUiEventMapper('session', 'turn', bridge=None)
    notice = mapper.map('subscription.progress', {'text': '已打开订阅设置'})
    answer = mapper.map('message.assistant.completed', {'text': '还需选择仪表盘来源'})
    assert [event.type.value for event in notice] == ['TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END', 'CUSTOM']
    assert notice[0].message_id != answer[0].message_id
    assert answer[1].delta == '还需选择仪表盘来源'


def test_subscription_tools_alone_do_not_turn_dashboard_work_into_subscription(settings_factory, tmp_path):
    """Dashboard pages can expose alert tools while the user asks for unrelated work."""
    request = _native_request(tmp_path, 'space.message_rule.get_context', text='解释这个仪表盘')
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'}, deferred_frontend_tools=DeferredFrontendToolStore())
    options = runtime.build_options(request)
    assert '订阅配置：先用中文聊天正文' not in str(options.system_prompt)


@pytest.mark.parametrize('action,data', [
    ('get_context', {'scope': {'kind': 'personal'}}),
    ('search_options', {'kind': 'alert_widget', 'results': [{'ref': 'metric-card'}]}),
    ('start_draft', {'readback': 'compact', 'queries': [{'outputs': [{'outputRef': 'value'}]}]}),
])
def test_intermediate_configuration_receipts_allow_the_next_business_decision(settings_factory, tmp_path, action, data):
    """Only terminal finish uses receipt thinking; searches still need business reasoning."""
    request = _native_request(tmp_path, 'space.message_rule.' + action, text='')
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'}, deferred_frontend_tools=DeferredFrontendToolStore())
    ledger = runtime.tool_ledger.get(request.platform_session_id)
    request.tool_results = (receipt(ledger, action, {'status': 'success', 'data': data}),)
    assert runtime.build_options(request).thinking == {'type': 'enabled', 'budget_tokens': 2048}
    assert _effective_thinking_config(request, settings_factory(subscription_receipt_thinking_budget_tokens=4096), ledger) == {
        'type': 'enabled', 'budget_tokens': 2048
    }
    assert _effective_thinking_config(request, settings_factory(claude_thinking_budget_tokens=4096), ledger) == {
        'type': 'enabled', 'budget_tokens': 4096
    }
