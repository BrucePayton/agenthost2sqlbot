import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.data_mcp.table_mcp import TABLE_TOOLS, TableMcpClient
from app.errors import AppError

DEFINITIONS = [{'name': name, 'description': name, 'inputSchema': {'type': 'object', 'properties': {}}}
               for name in sorted(TABLE_TOOLS)]


def test_mcp_requires_configuration_and_matching_identity(settings_factory, tmp_path):
    with pytest.raises(AppError, match='尚未配置'):
        TableMcpClient(settings_factory()).require_subject('owner')
    settings = settings_factory(DATA_MCP_COMMAND='/usr/bin/node', DATA_MCP_SUBJECT='owner',
                                DATA_MCP_TOKEN_CACHE_DIR=tmp_path)
    client = TableMcpClient(settings)
    client.require_subject('owner')
    with pytest.raises(AppError, match='未绑定'):
        client.require_subject('intruder')


@pytest.mark.asyncio
async def test_mcp_allowlist_checked_before_connect(settings_factory):
    client = TableMcpClient(settings_factory())
    with pytest.raises(AppError) as error:
        await client.call_tool('owner', 'arbitrary.write', {})
    assert error.value.code == 'table_mcp_tool_forbidden'


@pytest.mark.asyncio
async def test_table_sdk_forwards_exact_schema_and_preserves_error(monkeypatch):
    import app.runtime.claude as runtime
    captured = {}
    def decorator(name, description, schema):
        def register(fn):
            captured[name] = (fn, schema)
            return fn
        return register
    monkeypatch.setattr(runtime, 'tool', decorator)
    monkeypatch.setattr(runtime, 'create_sdk_mcp_server', lambda *a, **kw: {})
    service = SimpleNamespace(call_table_tool=AsyncMock(return_value={'content': [], 'is_error': True}))
    _, names = runtime.build_table_mcp_server(service, host_session_key='owned', definitions=DEFINITIONS)
    assert set(names) == {'mcp__data_mcp__'+n.replace('.', '_') for n in TABLE_TOOLS}
    assert 'ask' not in captured
    result = await captured['table_search'][0]({'keyword': '质检'})
    assert result['is_error']
    service.call_table_tool.assert_awaited_once_with(host_session_key='owned', name='table.search', arguments={'keyword':'质检'})
    service.call_table_tool.side_effect = AppError('denied', 'no permission', 403)
    assert (await captured['table_query'][0]({}))['is_error']
    with pytest.raises(AppError):
        runtime.build_table_mcp_server(service, host_session_key='owned', definitions=DEFINITIONS[:1])


def test_runtime_exposes_only_selected_backend(settings_factory, tmp_path):
    from app.runtime.claude import ClaudeAgentRuntime
    from tests.test_runtime_events import runtime_request
    request = runtime_request(tmp_path)
    request.workspace_snapshot = {'id': 'data-question', 'skills': [], 'allowed_tools': ['mcp__data_mcp__ask'], 'mcp_servers': {}}
    request.metadata = {'data_backend':'mcp', 'data_mcp_tools':DEFINITIONS}
    runtime = ClaudeAgentRuntime(settings_factory(), data_agent_service=SimpleNamespace())
    options = runtime.build_options(request)
    assert 'mcp__data_mcp__ask' not in options.allowed_tools
    assert {n for n in options.allowed_tools if n.startswith('mcp__data_mcp__')} == {'mcp__data_mcp__'+n.replace('.', '_') for n in TABLE_TOOLS}
    request.metadata = {'data_backend':'sqlbot'}
    options = runtime.build_options(request)
    assert 'mcp__data_mcp__ask' in options.allowed_tools
    assert 'mcp__data_mcp__table_query' not in options.allowed_tools


@pytest.mark.asyncio
async def test_table_relay_keeps_host_identity_out_of_payload(tmp_path):
    from app.runner.data_agent import RunnerDataAgentService
    queue = asyncio.Queue()
    service = RunnerDataAgentService(queue, tmp_path)
    task = asyncio.create_task(service.call_table_tool(host_session_key='ignored', name='table.search', arguments={'keyword':'质检'}))
    event = await queue.get()
    assert event.type == 'data.table.request'
    assert set(event.payload) == {'request_id','name','arguments'}
    body = {'content': [{'type':'text','text':'{"total":1}'}], 'is_error': False}
    (tmp_path / f"data-response-{event.payload['request_id']}.json").write_text(json.dumps(body))
    assert await task == body


@pytest.mark.asyncio
async def test_worker_table_request_uses_turn_session_only():
    from app.sandbox.worker import OpenSandboxExecutionWorker
    worker = object.__new__(OpenSandboxExecutionWorker)
    worker.turns = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(session_id='owned')))
    worker._session_owner = AsyncMock(return_value=SimpleNamespace(id='owned',workspace_id='data-question',created_by='owner'))
    worker.data_agent_service = SimpleNamespace(call_table_tool=AsyncMock(return_value={'content': [], 'is_error':False}), ask=AsyncMock())
    worker.sandbox = SimpleNamespace(write_data_response=AsyncMock())
    await worker._answer_data_request('turn','sandbox',{'request_id':'a'*32,'name':'table.search','arguments':{'keyword':'质检'}, 'host_session_key':'foreign'}, table_request=True)
    worker.data_agent_service.call_table_tool.assert_awaited_once_with(host_session_key='owned', name='table.search', arguments={'keyword':'质检'})
    worker.data_agent_service.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_backend_api_persists_isolates_and_rejects_busy_or_failed_switch(settings_factory, tmp_path):
    import httpx

    from app.db.models import SessionRecord, TurnRecord
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    settings = settings_factory(MOCK_PERSONAL_WORKSPACE_ID='data-question',
        MOCK_WORKSPACE_ROLES={'data-question':'owner'}, DATA_AGENT_ENABLED=False,
        DATA_MCP_COMMAND='/usr/bin/node', DATA_MCP_TOKEN_CACHE_DIR=tmp_path,
        DATA_MCP_SUBJECT='mock-user')
    write_workspace(settings.workspaces_root, 'data-question', skills=())
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app):
        services = app.state.services
        services.data_agents.table_mcp.list_tools = AsyncMock(return_value=DEFINITIONS)
        services.data_agents.table_mcp.call_tool = AsyncMock(return_value={'content': [], 'is_error':False})
        services.data_agents.sqlbot.start_chat = AsyncMock(side_effect=AssertionError('SQLBot must not run'))
        services.data_agents.sqlbot.datasource_tables = AsyncMock(side_effect=AssertionError('Service-2 must not run'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            created = await client.post('/api/workspaces/data-question/sessions')
            assert created.status_code == 201, created.text
            sid = created.json()['id']
            second = (await client.post('/api/workspaces/data-question/sessions')).json()['id']
            url = f'/api/sessions/{sid}/data-agent'
            assert (await client.get(url)).json()['backend'] == 'sqlbot'
            async with services.database.session() as db:
                session = await db.get(SessionRecord, sid)
                session.claude_session_id = 'previous-model-session'
                await db.commit()
            response = await client.put(url,json={'backend':'mcp'})
            assert response.status_code == 200, response.text
            assert response.json()['backend'] == 'mcp' and response.json()['agent_id'] is None
            assert (await client.get(url)).json()['backend'] == 'mcp'
            assert (await client.get(f'/api/sessions/{second}/data-agent')).json()['backend'] == 'sqlbot'
            async with services.database.session() as db:
                session = await db.get(SessionRecord, sid)
                assert session.claude_session_id is None
                assert json.loads(session.data_mcp_tools_json) == DEFINITIONS
                db.add(TurnRecord(id='queued', session_id=sid,client_request_id='queued',status='queued',input_text='find tables'))
                await db.commit()
            assert (await client.put(url,json={'backend':'sqlbot'})).status_code == 409
            await services.data_agents.call_table_tool(host_session_key=sid,name='table.search',arguments={'keyword':'质检'})
            services.data_agents.table_mcp.call_tool.assert_awaited_once_with('mock-user','table.search',{'keyword':'质检'})
            with pytest.raises(AppError):
                await services.data_agents.call_table_tool(host_session_key=second,name='table.search',arguments={})
            async with services.database.session() as db:
                turn = await db.get(TurnRecord,'queued'); turn.status='completed'
                await db.commit()
            assert (await client.put(url,json={'backend':'sqlbot'})).status_code == 200
            services.data_agents.table_mcp.list_tools.side_effect = AppError('table_mcp_unavailable','unavailable',503)
            assert (await client.put(url,json={'backend':'mcp'})).status_code == 503
            assert (await client.get(url)).json()['backend'] == 'sqlbot'
            services.data_agents.sqlbot.start_chat.assert_not_awaited()
            services.data_agents.sqlbot.datasource_tables.assert_not_awaited()
