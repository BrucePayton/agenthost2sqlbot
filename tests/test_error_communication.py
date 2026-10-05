"""All runtime tool modes share the business-facing failure policy."""

import asyncio
import json

import pytest

from tests.test_runtime_events import runtime_request


@pytest.mark.parametrize("mode", ["default", "native", "legacy"])
def test_business_error_guidance_is_global(settings_factory, tmp_path, mode):
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.agui.deferred_tools import DeferredFrontendToolStore
    from app.runtime.claude import ClaudeAgentRuntime
    from app.runtime.contracts import RuntimeFrontendTool
    from app.runtime.error_communication import BUSINESS_ERROR_GUIDANCE
    from tests.agui_helpers import dashboard_context, dashboard_tools

    request = runtime_request(tmp_path)
    registry = FrontendToolBridgeRegistry()
    if mode == "native":
        request.frontend_tools = (
            RuntimeFrontendTool(
                name="page.get_context", description="Read page state.",
                parameters={"type": "object"},
            ),
        )
    elif mode == "legacy":
        registry.register(
            request.platform_session_id, "run-1", dashboard_context(), dashboard_tools()
        )
    options = ClaudeAgentRuntime(
        settings_factory(), environ={"PATH": "/usr/bin"},
        frontend_tool_bridges=registry,
        deferred_frontend_tools=DeferredFrontendToolStore(),
    ).build_options(request)

    assert options.system_prompt["append"].count(BUSINESS_ERROR_GUIDANCE) == 1


def test_failure_policy_requires_evidence_and_safe_recovery():
    from app.runtime.error_communication import BUSINESS_ERROR_GUIDANCE as policy

    for requirement in (
        "卡片标题", "只读", "不要猜测", "未找到符合条件的结果", "日期范围和筛选条件",
        "不能仅凭", "尚未查明", "管理员", "没有保存", "结果待确认", "不要重复提交",
        "retryable", "不自动跳过", "用户明确要求技术详情",
    ):
        assert requirement in policy


def test_layout_policy_does_not_equate_bounded_validation_with_global_optimality():
    from app.runtime.error_communication import BUSINESS_ERROR_GUIDANCE as policy

    for requirement in ('有限候选', '当前已验证尺寸', '不能据此宣称全局最优或全局最小', '没有改动不等于已经最优'):
        assert requirement in policy


def test_layout_policy_presents_fallback_receipts_as_completed_work():
    from app.runtime.error_communication import BUSINESS_ERROR_GUIDANCE as policy

    for requirement in (
        'CONTENT_SIZE_FALLBACK', 'LAYOUT_LINEAR_FALLBACK', '兜底尺寸',
        '卡片类型、标题、原因和尺寸', '不得说成“布局失败”',
        '日期范围和筛选条件', '数据负责人',
    ):
        assert requirement in policy


def test_empty_canvas_success_is_not_presented_as_a_layout_failure():
    from app.runtime.error_communication import layout_measurement_reply

    assert layout_measurement_reply({
        'status': 'success',
        'data': {'persisted': False, 'layoutChanges': []},
        'issues': [{'code': 'LAYOUT_EMPTY_CANVAS', 'severity': 'info'}],
    }) is None


def test_error_only_layout_receipt_surfaces_direct_prewrite_failure() -> None:
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error',
        'error': {
            'code': 'EXECUTION_FAILED',
            'message': 'Content measurement stalled before flat-group planning',
            'layer': 'frontend',
            'phase': 'grouping_preflight',
            'lastStage': 'content_measurement',
            'retryable': False,
        },
        'diagnostics': {'writeDispatched': False},
        'issues': [],
    })

    assert '未发出保存请求' in text
    assert 'Content measurement stalled before flat-group planning' in text
    assert 'content\\_measurement' in text
    assert '保存结果待确认' not in text


def test_structured_bridge_failure_surfaces_bounded_direct_message() -> None:
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error',
        'error': {
            'code': 'EXECUTION_FAILED',
            'message': 'remote solver unavailable',
            'layer': 'page',
            'retryable': False,
            'details': {
                'code': 'LAYOUT_SOLVER_UNAVAILABLE',
                'lastStage': 'solveLayout',
                'writeDispatched': False,
            },
        },
        'issues': [{
            'code': 'LAYOUT_SOLVER_UNAVAILABLE',
            'message': 'remote solver unavailable',
            'retryable': False,
            'constraints': {
                'solverFinal': True,
                'lastStage': 'solveLayout',
                'writeDispatched': False,
            },
        }],
    })

    assert '未发出保存请求' in text
    assert 'remote solver unavailable' in text
    assert '具体原因尚未查明' not in text


def test_rejected_grouping_before_save_is_reported_as_definitely_unsaved():
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error',
        'error': {'code': 'INVALID_ARGUMENT'},
        'issues': [{
            'code': 'GROUPING_INVALID_PROPOSAL',
            'constraints': {'solverFinal': True, 'writeDispatched': False},
        }],
    })

    assert '没有保存任何布局改动' in text
    assert '保存结果待确认' not in text


def test_grouping_preflight_error_is_shown_directly_in_terminal_reply():
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error',
        'error': {'code': 'EXECUTION_FAILED'},
        'issues': [{
            'code': 'GROUPING_PENDING_WRITES_FAILED',
            'message': '平铺分组保存前失败：GROUPING_PENDING_WRITES_FAILED',
            'constraints': {
                'solverFinal': True,
                'writeDispatched': False,
                'preflightStage': 'pending_writes',
                'preflightError': 'GROUPING_PENDING_WRITES_FAILED',
            },
        }],
    })

    assert r'保存前错误：GROUPING\_PENDING\_WRITES\_FAILED' in text
    assert '具体原因尚未查明' not in text
    assert '未发出保存请求' in text


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['terminal', 'user_message', 'other_tool'])
@pytest.mark.parametrize('issue_code', [
    'LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE',
    'LAYOUT_NO_READABLE_CANDIDATES',
    'FUTURE_LAYOUT_FAILURE',
])
async def test_terminal_measurement_reply_uses_receipt_identity_not_model_prose(
    settings_factory, tmp_path, mode, issue_code,
):
    from app.agui.deferred_tools import DeferredFrontendToolStore
    from app.agui.tool_ledger import ToolOperation
    from app.runtime.claude import ClaudeAgentRuntime
    from app.runtime.contracts import RuntimeToolResult
    from tests.test_claude_runtime import FakeClaudeSdkClient, _native_request

    request = _native_request(tmp_path, 'dashboard.set_widget_layout', text='')
    client = FakeClaudeSdkClient()  # Deliberately replies "hello", ignoring the receipt.
    runtime = ClaudeAgentRuntime(settings_factory(), environ={'PATH': '/usr/bin'},
                                client_factory=lambda options: client,
                                deferred_frontend_tools=DeferredFrontendToolStore())
    runtime.tool_ledger.get(request.platform_session_id).record_call(ToolOperation(
        tool_use_id='layout', tool_name='dashboard.get_structure' if mode == 'other_tool' else 'dashboard.set_widget_layout',
        arguments_hash='hash', kind='frontend', revision_before=None,
    ))
    payload = {'status': 'error', 'error': {'code': 'INVALID_ARGUMENT'}, 'issues': [{
        'code': issue_code, 'widgetIds': ['15666'],
        'constraints': {'solverFinal': True, 'writeDispatched': False,
                        'widgetType': '指标卡', 'widgetTitle': '近30天成交金额'},
    }]}
    raw = json.dumps(payload)
    request.tool_results = (RuntimeToolResult('layout', raw, is_error=True),)
    if mode == 'user_message':
        request.text = '请解释技术详情'
    events = [event async for event in runtime.run(request, asyncio.Event())]
    visible = [event for event in events if event.type.startswith('message.assistant.')]
    if mode != 'terminal':
        assert ''.join(event.payload['text'] for event in visible) == 'hello'
        return
    assert len(visible) == 1
    assert visible[0].type == 'message.assistant.completed'
    text = visible[0].payload['text']
    assert '指标卡「近30天成交金额」' in text
    assert '没有保存' in text
    assert '管理员' in text
    for forbidden in ('hello', '15666', 'LAYOUT_', 'retryable', 'resourceRevision'):
        assert forbidden not in text
    assert client.queried  # Keep the SDK tool continuation/session intact.
    assert any(event.type == 'runtime.result' for event in events)
    assert any(event.type == 'usage.updated' for event in events)
    completed = next(event for event in events if event.type == 'tool.completed')
    assert issue_code in completed.payload['output_preview']
    assert request.tool_results[0].content == raw


def test_no_save_statement_requires_confirmed_prewrite_evidence():
    from app.runtime.error_communication import layout_measurement_reply

    payload = {'status': 'error', 'issues': [{
        'code': 'LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE',
        'constraints': {'solverFinal': True, 'writeDispatched': False,
                        'widgetType': '排行榜', 'widgetTitle': '门店排行'},
    }]}
    assert '排行榜「门店排行」' in layout_measurement_reply(payload)
    payload['issues'][0]['constraints']['writeDispatched'] = True
    assert '结果待确认' in layout_measurement_reply(payload)
    assert '没有保存' not in layout_measurement_reply(payload)
    payload['issues'][0]['constraints'].pop('writeDispatched')
    assert '结果待确认' in layout_measurement_reply(payload)
    assert '没有保存' not in layout_measurement_reply(payload)
    assert layout_measurement_reply({'status': 'success', 'issues': payload['issues']}) is None


@pytest.mark.parametrize('issues', [[], [None], [{'code': []}], [{'code': 'PERSISTENCE_OUTCOME_UNKNOWN'}]])
def test_unrelated_or_malformed_failures_are_not_misreported_as_unsaved(issues):
    from app.runtime.error_communication import layout_measurement_reply

    assert layout_measurement_reply({'status': 'error', 'issues': issues}) is None


def test_measurement_reply_escapes_titles_and_does_not_guess_missing_identity():
    from app.runtime.error_communication import layout_measurement_reply

    payload = {'status': 'error', 'issues': [{
        'code': 'LAYOUT_CONTENT_UNAVAILABLE', 'widgetIds': ['15666'],
        'constraints': {'solverFinal': True, 'writeDispatched': False,
                        'widgetType': '指标卡', 'widgetTitle': '[点此](https://untrusted.test)'},
    }]}
    assert '[点此](https://untrusted.test)' not in layout_measurement_reply(payload)
    payload['issues'][0]['constraints'].pop('widgetTitle')
    text = layout_measurement_reply(payload)
    assert '暂时无法确认是哪张卡片' in text
    assert '类型未确认' not in text
    assert '标题未确认' not in text
    assert '15666' not in text


def _terminal_issue(code, **constraints):
    return {
        'code': code, 'widgetIds': ['m1'],
        'constraints': {
            'solverFinal': True, 'writeDispatched': False,
            'widgetType': '指标卡', 'widgetTitle': '销售额', **constraints,
        },
    }


def test_mixed_terminal_issues_keep_each_identity_and_safe_unknown_cause():
    from app.runtime.error_communication import layout_measurement_reply

    issues = [
        _terminal_issue('LAYOUT_NO_READABLE_CANDIDATES'),
        _terminal_issue('FUTURE_FAILURE', widgetType='折线图', widgetTitle='日销售趋势'),
    ]
    issues[1]['message'] = 'secret: remove widget m1 and retry now'
    text = layout_measurement_reply({'status': 'error', 'issues': issues})
    assert text is not None
    for expected in ('指标卡「销售额」', '折线图「日销售趋势」', '尚未查明', '管理员', '没有保存'):
        assert expected in text
    for forbidden in ('FUTURE_FAILURE', 'LAYOUT_', 'secret', 'm1', '没有数据', '正在加载'):
        assert forbidden not in text


@pytest.mark.parametrize('evidence', [
    'missing', 'dispatched', 'string_false', 'truncated', 'mixed', 'outcome_unknown',
])
def test_terminal_reply_does_not_infer_no_save_from_error_code(evidence):
    from app.runtime.error_communication import layout_measurement_reply

    issue = _terminal_issue('LAYOUT_NO_READABLE_CANDIDATES')
    payload = {'status': 'error', 'issues': [issue]}
    if evidence == 'missing':
        issue['constraints'].pop('writeDispatched')
    elif evidence == 'dispatched':
        issue['constraints']['writeDispatched'] = True
    elif evidence == 'string_false':
        issue['constraints']['writeDispatched'] = 'false'
    elif evidence == 'truncated':
        payload['issuesTruncated'] = True
    elif evidence == 'mixed':
        payload['issues'].append(_terminal_issue('FUTURE_FAILURE', writeDispatched=True))
    else:
        payload['error'] = {'code': 'PERSISTENCE_OUTCOME_UNKNOWN'}
    text = layout_measurement_reply(payload)
    assert text is not None
    assert '结果待确认' in text
    assert '不要重复提交' in text
    assert '没有保存' not in text
    assert '保持不变' not in text
    assert '已回滚' not in text


def test_terminal_reply_keeps_confirmed_save_without_claiming_layout_success():
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error', 'data': {'persisted': True},
        'issues': [_terminal_issue('FUTURE_FAILURE', writeDispatched=True)],
    })
    assert text is not None
    assert '已发生保存' in text
    assert '未全部完成' in text
    assert '没有保存' not in text


def test_measurement_budget_exhaustion_does_not_claim_no_readable_size():
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error', 'issues': [_terminal_issue('LAYOUT_MEASUREMENT_LIMIT')],
    })
    assert '检查额度' in text
    assert '找不到' not in text


@pytest.mark.parametrize('code,expected,forbidden', [
    ('LAYOUT_CANCELLED', ('无需排查卡片',), ('需要检查', '加载', '管理员', '重新发起')),
    ('LAYOUT_CANVAS_CHANGED', ('保持窗口大小稳定', '重新发起'), ('加载', '锁定')),
    ('LAYOUT_CONTENT_CHANGED', ('筛选条件和数据查询稳定', '重新发起'), ('加载提示', '锁定')),
    ('LAYOUT_CONTENT_OVERFLOW', ('是否锁定', '若没有锁定', '管理员'), ('加载', '解除锁定', '重新发起')),
    ('LAYOUT_NO_READABLE_CANDIDATES', ('是否锁定', '若没有锁定', '管理员'), ('加载', '解除锁定', '重新发起')),
    ('FUTURE_FAILURE', ('尚未查明', '管理员'), ('刷新', '加载', '重新发起', '锁定')),
])
def test_terminal_actions_follow_the_actual_issue(code, expected, forbidden):
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({'status': 'error', 'issues': [_terminal_issue(code)]})
    for word in expected:
        assert word in text
    for word in forbidden:
        assert word not in text


@pytest.mark.parametrize('code', ['LAYOUT_CANVAS_CHANGED', 'LAYOUT_CONTENT_CHANGED', 'LAYOUT_CANCELLED', 'FUTURE_FAILURE'])
def test_unknown_save_action_precedes_any_new_request(code):
    from app.runtime.error_communication import layout_measurement_reply

    text = layout_measurement_reply({
        'status': 'error', 'issues': [_terminal_issue(code, writeDispatched=None)],
    })
    assert '保存结果待确认' in text
    assert '先核对' in text
    assert '不要重复提交' in text
    for forbidden in ('重新发起', '再次尝试', '重试即可', '没有保存', '已发生保存'):
        assert forbidden not in text


@pytest.mark.parametrize('location', ['data', 'top', 'error', 'constraints'])
@pytest.mark.parametrize('field', ['committed', 'persisted', 'writeDispatched'])
def test_positive_write_evidence_at_any_supported_level_blocks_no_save(location, field):
    from app.runtime.error_communication import layout_measurement_reply

    issue = _terminal_issue('LAYOUT_NO_READABLE_CANDIDATES')
    payload = {'status': 'error', 'issues': [issue]}
    scopes = {'top': payload, 'constraints': issue['constraints']}
    scope = scopes.get(location)
    if scope is None:
        scope = payload.setdefault(location, {})
    scope[field] = True
    text = layout_measurement_reply(payload)
    assert '保存结果待确认' in text
    assert '没有保存' not in text
    assert '已发生保存' not in text
    assert '不要重复提交' in text


@pytest.mark.parametrize('conflict', [
    'prewrite', 'data_committed_false', 'top_committed_false', 'error_committed_false',
    'issue_persisted_false', 'truncated', 'data_truncated', 'invalid_boolean',
])
def test_conflicting_or_truncated_receipt_does_not_claim_save_success(conflict):
    from app.runtime.error_communication import layout_measurement_reply

    issue = _terminal_issue('FUTURE_FAILURE', writeDispatched=True)
    payload = {'status': 'error', 'data': {'persisted': True}, 'issues': [issue]}
    if conflict == 'prewrite':
        issue['constraints']['writeDispatched'] = False
    elif conflict == 'data_committed_false':
        payload['data']['committed'] = False
    elif conflict == 'top_committed_false':
        payload['committed'] = False
    elif conflict == 'error_committed_false':
        payload['error'] = {'committed': False}
    elif conflict == 'issue_persisted_false':
        issue['constraints']['persisted'] = False
    elif conflict == 'invalid_boolean':
        issue['constraints']['committed'] = 'false'
    elif conflict == 'data_truncated':
        payload['data']['issuesTruncated'] = True
    else:
        payload['issuesTruncated'] = True
    text = layout_measurement_reply(payload)
    assert '保存结果待确认' in text
    assert '没有保存' not in text
    assert '已发生保存' not in text


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [
    'connect', 'stream', 'result', 'rate_limit', 'empty', 'factory', 'disconnect', 'protected_error',
    'receipt_prepare', 'runtime_config',
])
@pytest.mark.parametrize('write_dispatched', [False, None])
async def test_known_terminal_facts_survive_sdk_failure(
    settings_factory, tmp_path, monkeypatch, failure, write_dispatched,
):
    from claude_agent_sdk import ResultMessage

    from app.agui.deferred_tools import DeferredFrontendToolStore
    from app.agui.tool_ledger import ToolOperation
    from app.errors import AppError
    from app.runtime.claude import ClaudeAgentRuntime
    from app.runtime.contracts import RuntimeToolResult
    from tests.test_claude_runtime import FakeClaudeSdkClient, _native_request

    protection_details = {'retryable': False, 'committed': True}

    class FailingClient(FakeClaudeSdkClient):
        async def connect(self):
            if failure == 'connect':
                raise RuntimeError('connection unavailable')

        async def receive_response(self):
            if failure == 'empty':
                return
            if failure == 'stream':
                raise RuntimeError('stream interrupted')
            if failure == 'protected_error':
                raise AppError('TOOL_RESULT_CONFLICT', 'Result conflict.', 409, protection_details)
            if failure == 'disconnect':
                async for message in super().receive_response():
                    yield message
                return
            yield ResultMessage(
                subtype='error_during_execution', duration_ms=1, duration_api_ms=1,
                is_error=True, num_turns=1, session_id='sdk-session',
                api_error_status=429 if failure == 'rate_limit' else 500,
                errors=['rate limit' if failure == 'rate_limit' else 'execution failed'],
                result='generic model error',
            )

        async def disconnect(self):
            if failure == 'disconnect':
                raise RuntimeError('disconnect failed; try again later')

    client = FailingClient()

    def client_factory(options):
        if failure == 'factory':
            raise RuntimeError('setup failed; try again later')
        return client

    runtime = ClaudeAgentRuntime(
        settings_factory(), environ={'PATH': '/usr/bin'},
        client_factory=client_factory,
        deferred_frontend_tools=DeferredFrontendToolStore(),
    )
    request = _native_request(tmp_path, 'dashboard.set_widget_layout', text='')
    if failure == 'runtime_config':
        request.workspace_snapshot['instructions'] = None
    elif failure == 'receipt_prepare':
        def fail_receipt_preparation(content):
            raise RuntimeError('receipt event preparation failed')

        monkeypatch.setattr('app.runtime.claude._tool_result_fields', fail_receipt_preparation)
    runtime.tool_ledger.get(request.platform_session_id).record_call(ToolOperation(
        tool_use_id='layout', tool_name='dashboard.set_widget_layout',
        arguments_hash='hash', kind='frontend', revision_before=None,
    ))
    raw = json.dumps({'status': 'error', 'issues': [
        _terminal_issue('LAYOUT_NO_READABLE_CANDIDATES', writeDispatched=write_dispatched),
    ]})
    request.tool_results = (RuntimeToolResult('layout', raw, is_error=True),)
    events = []
    with pytest.raises(AppError) as caught:
        async for event in runtime.run(request, asyncio.Event()):
            events.append(event)
    assert caught.value.code == {
        'rate_limit': 'claude_rate_limited', 'protected_error': 'TOOL_RESULT_CONFLICT',
    }.get(failure, 'claude_unavailable')
    if failure == 'protected_error':
        assert caught.value.status_code == 409
        assert caught.value.details == protection_details
    assert '布局结果以上方业务摘要为准' in caught.value.message
    assert '不要重复提交' in caught.value.message
    for forbidden in ('Try again', 'try again', '重试', '没有保存', '已发生保存'):
        assert forbidden not in caught.value.message
    visible = [event for event in events if event.type.startswith('message.assistant.')]
    assert len(visible) == 1
    assert visible[0].type == 'message.assistant.completed'
    assert events[0] is visible[0]
    assert '指标卡「销售额」' in visible[0].payload['text']
    if write_dispatched is False:
        assert '没有保存' in visible[0].payload['text']
    else:
        assert '没有保存' not in visible[0].payload['text']
        assert '结果待确认' in visible[0].payload['text']
    assert 'generic model error' not in visible[0].payload['text']
    assert request.tool_results[0].content == raw
    assert not any(event.type == 'frontend_tool.deferred' for event in events)

    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper(request.platform_session_id, 'run-1', bridge=None)
    mapped = []
    for event in events:
        mapped.extend(mapper.map(event.type, event.payload))
    mapped.extend(mapper.map('turn.failed', {
        'code': caught.value.code, 'message': caught.value.message,
    }))
    contents = [event.delta for event in mapped if event.type.value == 'TEXT_MESSAGE_CONTENT']
    assert contents == [visible[0].payload['text']]
    assert mapped[-1].type.value == 'RUN_ERROR'
    assert mapped[-1].message == caught.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_at', ['before_run', 'after_summary'])
async def test_terminal_summary_respects_cancellation(settings_factory, tmp_path, cancel_at):
    from app.agui.tool_ledger import ToolOperation
    from app.runtime.contracts import RuntimeCancelled, RuntimeToolResult
    from tests.test_claude_runtime import _native_request, _runtime

    runtime = _runtime(settings_factory)
    request = _native_request(tmp_path, 'dashboard.set_widget_layout', text='')
    runtime.tool_ledger.get(request.platform_session_id).record_call(ToolOperation(
        tool_use_id='layout', tool_name='dashboard.set_widget_layout',
        arguments_hash='hash', kind='frontend', revision_before=None,
    ))
    request.tool_results = (RuntimeToolResult('layout', json.dumps({
        'status': 'error', 'issues': [_terminal_issue('LAYOUT_NO_READABLE_CANDIDATES')],
    }), is_error=True),)
    cancel_event = asyncio.Event()
    events = runtime.run(request, cancel_event)
    try:
        if cancel_at == 'after_summary':
            first = await anext(events)
            assert first.type == 'message.assistant.completed'
        cancel_event.set()
        with pytest.raises(RuntimeCancelled):
            await anext(events)
    finally:
        await events.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['LAYOUT_NO_READABLE_CANDIDATES', 'FUTURE_FAILURE'])
async def test_readable_fallback_does_not_authorize_followup_reads_or_writes(
    settings_factory, tmp_path, code,
):
    from app.agui.claude_tools import native_frontend_sdk_name
    from app.agui.tool_ledger import ToolOperation
    from app.runtime.contracts import RuntimeToolResult
    from tests.test_claude_runtime import _native_request, _runtime

    runtime = _runtime(settings_factory)
    request = _native_request(tmp_path, 'dashboard.set_widget_layout', text='')
    runtime.tool_ledger.get(request.platform_session_id).record_call(ToolOperation(
        tool_use_id='layout', tool_name='dashboard.set_widget_layout',
        arguments_hash='hash', kind='frontend', revision_before=None,
    ))
    request.tool_results = (RuntimeToolResult('layout', json.dumps({
        'status': 'error', 'issues': [dict(_terminal_issue(code), retryable=False)],
    }), is_error=True),)
    gate = runtime.build_options(request).hooks['PreToolUse'][0].hooks[0]
    for tool_name in ('dashboard.get_structure', 'dashboard.set_widget_layout', 'dashboard.publish'):
        decision = await gate({
            'tool_name': native_frontend_sdk_name(tool_name), 'tool_input': {},
        }, 'new-call', None)
        assert decision['hookSpecificOutput']['permissionDecision'] == 'deny'
        assert 'LAYOUT_SOLVER_FINISHED' in decision['hookSpecificOutput']['permissionDecisionReason']
