import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.data_mcp.service import DataAgentService
from app.errors import AppError
from app.sessions.locks import SessionLockRegistry


def context_service():
    service = object.__new__(DataAgentService)
    service.context_locks = SessionLockRegistry()
    service._require_enabled = lambda: None
    service.provider = SimpleNamespace(require_subject=lambda value: None)
    bindings = {key: SimpleNamespace(id=key, agent_id='agent', sqlbot_chat_id=None) for key in ['a', 'b']}
    async def get(key, subject):
        if subject != 'owner':
            raise AppError('data_agent_session_not_found', 'not found', 404)
        return bindings[key]
    async def set_chat(key, chat): bindings[key].sqlbot_chat_id = chat
    service.repository = SimpleNamespace(get_ask_session=get, set_sqlbot_chat=AsyncMock(side_effect=set_chat))
    counter = 0
    async def query(**kwargs):
        nonlocal counter
        binding = bindings[kwargs['host_session_key']]
        if binding.sqlbot_chat_id is None:
            await asyncio.sleep(0)
            counter += 1
            binding.sqlbot_chat_id = counter
        return SimpleNamespace(chat_id=binding.sqlbot_chat_id, evidence={})
    service._ask_in_context = query
    return service


@pytest.mark.asyncio
async def test_context_continues_resets_and_isolates_sessions():
    service = context_service()
    async def ask(key='a', **kw):
        return await service.ask(user_subject='owner', host_session_key=key, question='test', **kw)
    first, second = await asyncio.gather(ask(), ask())
    assert first.chat_id == second.chat_id
    assert first.evidence['contextMode'] == 'new'
    assert second.evidence['contextMode'] == 'continue'
    other = await ask('b')
    reset = await ask(context_mode='new')
    assert len({first.chat_id, other.chat_id, reset.chat_id}) == 3
    assert (await ask()).chat_id == reset.chat_id
    with pytest.raises(AppError):
        await ask(requested_agent_id='another', context_mode='new')
    assert (await ask()).chat_id == reset.chat_id
    with pytest.raises(AppError):
        await service.ask(user_subject='intruder', host_session_key='a', question='test')


@pytest.mark.asyncio
async def test_sdk_tool_returns_sql_rows_without_internal_presentation(monkeypatch):
    import json
    import app.runtime.claude as runtime
    from app.data_mcp.schemas import DataAskResult
    captured = {}
    def decorator(name, description, schema):
        captured['schema'] = schema
        def register(fn):
            captured['ask'] = fn
            return fn
        return register
    monkeypatch.setattr(runtime, 'tool', decorator)
    monkeypatch.setattr(runtime, 'create_sdk_mcp_server', lambda *a, **kw: {})
    service = SimpleNamespace(ask=AsyncMock(return_value=DataAskResult(
        resultId='result', agentId='agent', chatId=1, recordId=2, sql='SELECT 1',
        columns=['n'], rows=[{'n':1}], rowCount=1, truncated=False, chartHint={'type':'table'},
        evidence={}, fieldsUsed=[], presentation={'privatePrompt':'do not return'})))
    runtime.build_data_mcp_server(service, user_subject='owner', host_session_key='session')
    result = await captured['ask']({'question':'test', 'contextMode':'new'})
    body = json.loads(result['content'][0]['text'])
    assert body['sql'] == 'SELECT 1' and body['rows'] == [{'n':1}]
    assert 'presentation' not in body
    assert service.ask.await_args.kwargs['context_mode'] == 'new'
    assert service.ask.await_args.kwargs['host_session_key'] == 'session'
    assert set(captured['schema']['properties']) == {'question', 'contextMode'}


@pytest.mark.asyncio
async def test_context_route_checks_owner_before_read_or_write():
    from app.data_mcp.routes import session_data_agent, update_session_data_agent
    from app.data_mcp.schemas import DataAgentContextUpdate
    services = SimpleNamespace(workspace_access=SimpleNamespace(
        require_session_owner=AsyncMock(side_effect=AppError('forbidden', 'forbidden', 403))))
    with pytest.raises(AppError):
        await session_data_agent('foreign', services, object())
    with pytest.raises(AppError):
        await update_session_data_agent('foreign', DataAgentContextUpdate(agent_id='agent'), services, object())


@pytest.mark.asyncio
async def test_runner_relay_forwards_question_and_consumes_response(tmp_path):
    import json
    from app.runner.data_agent import RunnerDataAgentService
    queue = asyncio.Queue()
    service = RunnerDataAgentService(queue, tmp_path)
    pending = asyncio.create_task(service.ask(question='count', context_mode='new', user_subject='ignored', host_session_key='ignored'))
    event = await queue.get()
    assert set(event.payload) == {'request_id','question','contextMode'}
    path = tmp_path / f"data-response-{event.payload['request_id']}.json"
    path.write_text(json.dumps({'error': {'code':'sqlbot_model_quota_exceeded','message':'quota'}}))
    with pytest.raises(AppError, match='quota'):
        await pending
    assert not path.exists()


@pytest.mark.asyncio
async def test_worker_derives_identity_from_turn_owner_and_hides_presentation():
    import json
    from contextlib import asynccontextmanager
    from app.sandbox.worker import OpenSandboxExecutionWorker
    from app.data_mcp.schemas import DataAskResult
    worker = object.__new__(OpenSandboxExecutionWorker)
    worker.turns = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(session_id='bound-session')))
    worker._session_owner = AsyncMock(return_value=SimpleNamespace(id='bound-session',workspace_id='data-question',created_by='user'))
    @asynccontextmanager
    async def session():
        yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(external_subject='authenticated-subject')))
    worker.database = SimpleNamespace(session=session)
    worker.data_agent_service = SimpleNamespace(ask=AsyncMock(return_value=DataAskResult(
        resultId='r',agentId='a',chatId=1,recordId=2,sql='SELECT 1',columns=['n'],rows=[{'n':1}],
        rowCount=1,truncated=False,chartHint={'type':'table'},evidence={},fieldsUsed=[],presentation={'secret':'private'})))
    worker.sandbox = SimpleNamespace(write_data_response=AsyncMock())
    await worker._answer_data_request('turn','sandbox',{'request_id':'a'*32,'question':'count','contextMode':'continue'})
    assert worker.data_agent_service.ask.await_args.kwargs['user_subject'] == 'authenticated-subject'
    assert worker.data_agent_service.ask.await_args.kwargs['host_session_key'] == 'bound-session'
    body = json.loads(worker.sandbox.write_data_response.await_args.args[2])
    assert body['rows'] == [{'n':1}] and 'presentation' not in body
    worker._session_owner.return_value.workspace_id = 'foreign-workspace'
    worker.data_agent_service.ask.reset_mock()
    await worker._answer_data_request('turn','sandbox',{'request_id':'b'*32,'question':'count'})
    worker.data_agent_service.ask.assert_not_awaited()
