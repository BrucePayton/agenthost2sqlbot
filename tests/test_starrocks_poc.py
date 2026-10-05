import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import httpx
import jwt
import pytest
from pydantic import SecretStr

from app.auth.models import IdentityContext
from app.data_mcp.schemas import DataAgentCreate
from app.data_mcp.service import DataAgentService
from app.data_mcp.starrocks import StarRocksPocProvider
from app.db.base import Database
from app.db.models import DataAgentTicketRecord, UserRecord
from app.errors import AppError
from app.sqlbot.client import SQLBotClient
from tests.test_data_agent import FakeSQLBot


class LocalSQLBotTransport(httpx.AsyncBaseTransport):
    """Exercise the 07B wire contract with a separate mock SQLBot secret."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.service: DataAgentService | None = None
        self.callbacks: list[list[dict]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == '/api/v1/system/assistant' and request.method == 'POST':
            return httpx.Response(200, json={'code': 0, 'data': {'id': 7001}})
        token_header = request.headers['X-SQLBOT-ASSISTANT-TOKEN']
        scheme, token = token_header.split(' ', 1)
        assert scheme == 'Assistant'
        assert jwt.decode(token, self.secret, algorithms=['HS256'])['assistant_id'] == 7001
        certificate = json.loads(unquote(base64.b64decode(
            request.headers['X-SQLBOT-ASSISTANT-CERTIFICATE']).decode()))
        assert len(certificate) == 1 and certificate[0]['key'] == 'X-Davinci-Ticket'
        assert certificate[0]['target'] == 'header'
        assert self.service is not None
        if path.endswith(('/assistant/start', '/question')):
            self.callbacks.append(await self.service.callback_payload(certificate[0]['value']))
        if path.endswith('/assistant/start'):
            return httpx.Response(200, json={'code': 0, 'data': {'id': 8001}})
        if path.endswith('/question'):
            return httpx.Response(200, text='data: {"type":"id","id":9001}\n\n')
        if path.endswith('/chat/8001'):
            return httpx.Response(200, json={'code': 0, 'data': {'records': [
                {'id': 9001, 'finish': True, 'sql': 'SELECT COUNT(*) AS total FROM dw_test'}]}})
        if path.endswith('/record/9001/log'):
            return httpx.Response(200, json={'code': 0, 'data': {'duration': 1, 'total_tokens': 10, 'steps': [{'operate': 'EXECUTE_SQL', 'duration': 1, 'message': {'count': 1}}]}})
        if path.endswith('/record/9001/data'):
            return httpx.Response(200, json={'code': 0, 'data': {
                'fields': ['total'], 'data': [{'total': 1}]}})
        raise AssertionError(f'Unexpected SQLBot request: {request.method} {path}')


def poc_settings(settings_factory, tmp_path, **overrides):
    path = tmp_path / 'starrocks.json'
    path.write_text(json.dumps({'datasources': [
        {'dataBase': f'hive.{g}', 'name': g, 'description': f'{g} test', 'tables': [
            {'name': f'{g}_test', 'comment': '测试表', 'fields': [
                {'name': 'id', 'type': 'bigint', 'comment': '编号'},
                {'name': 'dt', 'type': 'date', 'comment': '日期'},
            ]},
        ]} for g in ['dw', 'dm', 'rpt']]}))
    return settings_factory(data_agent_provider='starrocks_poc', data_agent_starrocks_manifest=path,
                            data_agent_enabled=True, data_agent_subject='159358',
                            starrocks_host='db.test', starrocks_user='test', starrocks_password=SecretStr('db-secret'),
                            sqlbot_secret_key=SecretStr('jwt-secret'), **overrides)


def test_poc_catalog_and_scope(settings_factory, tmp_path):
    provider = StarRocksPocProvider(poc_settings(settings_factory, tmp_path))
    assert len(provider.list_authorized('159358')) == 3
    payload = provider.callback_payload('159358', ['starrocks:hive.dm'])
    assert len(payload) == 1 and payload[0]['dataBase'] == 'hive.dm'
    assert payload[0]['tables'][0]['fields'][1]['comment'] == '日期'
    assert payload[0]['password'] == 'db-secret'
    assert not any('password' in d.model_dump() for d in provider.list_authorized('159358'))
    with pytest.raises(AppError):
        provider.callback_payload('other', ['starrocks:hive.dm'])
    with pytest.raises(AppError):
        provider.callback_payload('159358', ['starrocks:hive.other'])
    settings = poc_settings(settings_factory, tmp_path)
    settings.app_env = 'production'
    with pytest.raises(ValueError):
        StarRocksPocProvider(settings)


@pytest.mark.asyncio
async def test_starrocks_ticket_publish_ask_replay_and_expiry(settings_factory, tmp_path):
    settings = poc_settings(settings_factory, tmp_path)
    db = Database(settings.resolved_database_url)
    await db.initialize()
    identity = IdentityContext('owner', '159358', 'Owner')
    async with db.session() as session:
        session.add(UserRecord(id='owner', external_subject='159358', display_name='Owner', provider='mock'))
        await session.commit()
    fake = FakeSQLBot()
    service = DataAgentService(db, settings, fake)
    fake.service = service
    try:
        agent = await service.create_agent(DataAgentCreate(name='StarRocks test', dataset_refs=['starrocks:hive.dw', 'starrocks:hive.dm', 'starrocks:hive.rpt']), identity)
        await service.publish_agent(agent.id, identity)
        await service.bind_host_session(agent_id=agent.id, host_session_key='session-1', identity=identity)
        result = await service.ask(user_subject='159358', host_session_key='session-1', question='统计测试')
        assert result.record_id == 9001
        assert len(fake.callback_payloads[0]) == 3
        with pytest.raises(AppError):
            await service.callback_payload(fake.tickets[0])
        with pytest.raises(AppError):
            await service.ask(user_subject='159358', host_session_key='session-1', question='test', requested_agent_id='another')
        token, digest = await service._issue_ticket(agent_id=agent.id, user_subject='159358', host_session_key='session-1', question='test', projection_limit=40)
        await service.callback_payload(token)
        await service.callback_payload(token)
        with pytest.raises(AppError) as replay:
            await service.callback_payload(token)
        assert replay.value.code == 'data_ticket_replayed'
        async with db.session() as session:
            ticket = await session.get(DataAgentTicketRecord, digest)
            ticket.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        with pytest.raises(AppError) as expired:
            await service.callback_payload(token)
        assert expired.value.code == 'invalid_data_ticket'
    finally:
        await service.aclose()
        await db.dispose()


@pytest.mark.asyncio
async def test_07b_local_mock_signed_assistant_and_dynamic_callback(settings_factory, tmp_path):
    settings = poc_settings(settings_factory, tmp_path)
    mock_secret = 'local-mock-sqlbot-secret-for-07b-testing'
    settings.sqlbot_secret_key = SecretStr(mock_secret)
    db = Database(settings.resolved_database_url)
    await db.initialize()
    identity = IdentityContext('owner', '159358', 'Owner')
    async with db.session() as session:
        session.add(UserRecord(id='owner', external_subject='159358', display_name='Owner', provider='mock'))
        await session.commit()
    transport = LocalSQLBotTransport(mock_secret)
    http = httpx.AsyncClient(transport=transport, base_url='http://mock-sqlbot.test')
    client = SQLBotClient(settings, http)
    client._management_token = 'local-mock-admin-token'
    service = DataAgentService(db, settings, client)
    transport.service = service
    try:
        agent = await service.create_agent(DataAgentCreate(
            name='07B local mock', dataset_refs=['starrocks:hive.dw', 'starrocks:hive.dm', 'starrocks:hive.rpt']), identity)
        await service.publish_agent(agent.id, identity)
        await service.bind_host_session(agent_id=agent.id, host_session_key='session-07b', identity=identity)
        result = await service.ask(user_subject='159358', host_session_key='session-07b', question='统计作业数量')
        assert result.record_id == 9001 and result.rows == [{'total': 1}]
        assert len(transport.callbacks) == 2
        assert all(len(payload) == 3 for payload in transport.callbacks)
        assert all({source['dataBase'] for source in payload} == {'hive.dw', 'hive.dm', 'hive.rpt'}
                   for payload in transport.callbacks)
        assert all(source['password'] == 'db-secret' for payload in transport.callbacks for source in payload)
    finally:
        await service.aclose()
        await http.aclose()
        await db.dispose()


@pytest.mark.asyncio
async def test_publish_model_and_browser_origin(settings_factory, tmp_path):
    settings = poc_settings(settings_factory, tmp_path, sqlbot_custom_model='7510520007692914688',
                            data_agent_browser_origin='http://127.0.0.1:8765', data_agent_public_base_url='http://callback.test:8766')
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'code': 0, 'data': {'id': 77}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://sqlbot.test') as http:
        client = SQLBotClient(settings, http)
        client._management_token = 'test-management-token'
        assert await client.ensure_assistant(assistant_id=None, agent_id='agent', name='name', description='description') == 77
    payload = captured[0]
    assert payload['type'] == 1 and payload['custom_model'] == settings.sqlbot_custom_model
    assert payload['enable_custom_model'] is True
    assert payload['domain'] == 'http://127.0.0.1:8765'
    config = json.loads(payload['configuration'])
    assert config['endpoint'] == 'http://callback.test:8766/api/sqlbot/datasources'
    assert 'password' not in payload['configuration']


@pytest.mark.asyncio
async def test_service2_catalog_exposes_selected_schema_and_comments_only(settings_factory, tmp_path):
    settings = poc_settings(settings_factory, tmp_path)
    settings.data_agent_sqlbot_catalog_enabled = True
    settings.data_agent_sqlbot_source_ids = {'dw': 4, 'dm': 5, 'rpt': 6}
    settings.data_agent_enabled = False
    replies = {
        ('GET', '/api/v1/datasource/list'): [
            {'id': i, 'name': f'{g}', 'type': 'starrocks', 'configuration': 'encrypted-secret'}
            for i, g in [(4, 'dw'), (5, 'dm'), (6, 'rpt')]],
        ('POST', '/api/v1/datasource/tableList/4'): [
            {'id': 10, 'table_name': 'dw_test', 'checked': True, 'custom_comment': '业务表'},
            {'id': 11, 'table_name': 'not_selected', 'checked': False, 'custom_comment': '隐藏'}],
        ('POST', '/api/v1/datasource/fieldList/4/10'): [
            {'field_name': 'id', 'field_type': 'bigint', 'checked': True, 'custom_comment': '主键'},
            {'field_name': 'dt', 'field_type': 'date', 'checked': True, 'custom_comment': '日期'}],
        ('POST', '/api/v1/datasource/getFields/4/dw_test'): [
            {'fieldName': 'id', 'fieldType': 'bigint', 'fieldComment': ''},
            {'fieldName': 'dt', 'fieldType': 'date', 'fieldComment': ''}],
        ('GET', '/api/v1/datasource/check/4'): True,
    }
    def handler(request):
        key = request.method, request.url.path
        assert key in replies
        return httpx.Response(200, json={'code': 0, 'data': replies[key]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://sqlbot.test') as http:
        client = SQLBotClient(settings, http)
        client._management_token = 'mock-admin-token'
        service = DataAgentService(Database(settings.resolved_database_url), settings, client)
        who = IdentityContext('owner', '159358', 'Owner')
        tables = await service.sqlbot_catalog_tables(who, 'dw')
        assert [table['name'] for table in tables] == ['dw_test']
        detail = await service.sqlbot_catalog_table_schema(who, 'dw', 'dw_test')
        assert [field['comment'] for field in detail['fields']] == ['主键', '日期']
        assert all(field['in_live_schema'] for field in detail['fields'])
        assert (await service.sqlbot_catalog_connection(who, 'dw'))['connected'] is True
        assert 'encrypted-secret' not in json.dumps([tables, detail])
        with pytest.raises(AppError):
            await service.sqlbot_catalog_tables(who, 'other')


@pytest.mark.asyncio
async def test_service2_metadata_drives_local_sqlbot_callback_and_result(settings_factory, tmp_path):
    settings = poc_settings(settings_factory, tmp_path)
    settings.data_agent_starrocks_metadata_source = 'service2'
    settings.data_agent_sqlbot_source_ids = {'dw': 4, 'dm': 5, 'rpt': 6}
    settings.sqlbot_catalog_base_url = 'http://service2.test'
    settings.sqlbot_catalog_admin_password = SecretStr('catalog-admin')
    settings.sqlbot_secret_key = SecretStr('local-test-secret-at-least-32-bytes-long')
    db = Database(settings.resolved_database_url)
    await db.initialize()
    identity = IdentityContext('owner', '159358', 'Owner')
    async with db.session() as session:
        session.add(UserRecord(id='owner', external_subject='159358', display_name='Owner', provider='mock'))
        await session.commit()
    transport = LocalSQLBotTransport('local-test-secret-at-least-32-bytes-long')
    local_http = httpx.AsyncClient(transport=transport, base_url='http://local-sqlbot.test')
    local_sqlbot = SQLBotClient(settings, local_http)
    local_sqlbot._management_token = 'local-admin'
    selected = {'dw': True, 'dm': True, 'rpt': True}
    requests_seen = []

    def catalog_handler(request):
        requests_seen.append(request.url.path)
        path = request.url.path
        if path == '/api/v1/datasource/list':
            data = [{'id': i, 'name': g, 'type': 'starrocks', 'configuration': 'encrypted-secret'}
                    for i, g in [(4, 'dw'), (5, 'dm'), (6, 'rpt')]]
        elif '/tableList/' in path:
            group = {4: 'dw', 5: 'dm', 6: 'rpt'}[int(path.rsplit('/', 1)[1])]
            data = [{'id': int(path.rsplit('/', 1)[1]) * 10, 'table_name': f'{group}_test',
                     'checked': selected[group], 'custom_comment': f'{group} remote table'}]
        elif '/fieldList/' in path:
            data = [{'field_name': 'id', 'field_type': 'bigint', 'checked': True,
                     'custom_comment': 'remote identifier'},
                    {'field_name': 'dt', 'field_type': 'date', 'checked': True,
                     'custom_comment': 'remote date'}]
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={'code': 0, 'data': data})

    catalog_http = httpx.AsyncClient(transport=httpx.MockTransport(catalog_handler),
                                     base_url='http://service2.test')
    service = DataAgentService(db, settings, local_sqlbot)
    await service.catalog_sqlbot.aclose()
    service.catalog_sqlbot = SQLBotClient(settings, catalog_http)
    service.catalog_sqlbot._management_token = 'service2-admin'
    transport.service = service
    try:
        agent = await service.create_agent(DataAgentCreate(
            name='07D local SQLBot', dataset_refs=['starrocks:hive.dw', 'starrocks:hive.dm', 'starrocks:hive.rpt']), identity)
        await service.publish_agent(agent.id, identity)
        await service.bind_host_session(agent_id=agent.id, host_session_key='session-07d', identity=identity)
        result = await service.ask(user_subject='159358', host_session_key='session-07d', question='统计作业数量')
        assert result.rows == [{'total': 1}] and result.record_id == 9001
        assert len(transport.callbacks) == 2
        assert len(requests_seen) >= 12
        for callback in transport.callbacks:
            assert len(callback) == 3
            for source in callback:
                group = source['dataBase'].split('.')[-1]
                assert source['tables'][0]['comment'] == f'{group} remote table'
                assert source['tables'][0]['fields'][0]['comment'] == 'remote identifier'
                assert source['tables'][0]['sql'] == ''
                assert source['password'] == 'db-secret'
                assert 'encrypted-secret' not in json.dumps(source)
        selected['dw'] = False
        token, digest = await service._issue_ticket(agent_id=agent.id, user_subject='159358',
                                                    host_session_key='session-07d', question='drift', projection_limit=40)
        with pytest.raises(AppError) as drift:
            await service.callback_payload(token)
        assert drift.value.code == 'sqlbot_catalog_mismatch'
        await service.repository.complete_ticket(digest)
    finally:
        await service.aclose()
        await local_http.aclose()
        await catalog_http.aclose()
        await db.dispose()
