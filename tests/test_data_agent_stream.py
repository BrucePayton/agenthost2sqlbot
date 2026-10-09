import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.data_mcp.routes import _event_response
from app.errors import AppError
from app.sqlbot.client import SQLBotClient


@pytest.mark.asyncio
async def test_sqlbot_events_forwarded_before_record_and_log_fetch(settings_factory):
    seen = []
    def handle(request):
        path = request.url.path
        if path.endswith('/question'):
            assert json.loads(request.content)["generate_chart"] is True
            return httpx.Response(200, text='data: {"type":"id","id":9}\n\ndata: {"type":"sql-result","content":"生成中"}\n\ndata: {"type":"sql","content":"SELECT 1"}\n\n')
        assert seen  # live chunks must arrive before completion is assembled
        if path.endswith('/chat/8'):
            return httpx.Response(200, json={'code':0,'data':{'records':[{'id':9,'finish':True,'sql':'SELECT 1','sql_answer':'说明'}]}})
        if path.endswith('/record/9/log'):
            return httpx.Response(200,json={'code':0,'data':{'steps':[{'operate':'EXECUTE_SQL','message':{'password':'do-not-export'},'duration':1}]}})
        raise AssertionError(path)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://sqlbot.test')
    client = SQLBotClient(settings_factory(sqlbot_secret_key=SecretStr('configured-secret')),http)
    async def emit(event): seen.append(event)
    try:
        result=await client.ask_question(assistant_token='test-token',ticket='short-ticket',chat_id=8,question='test',on_event=emit)
        assert [e['type'] for e in seen]==['id','sql-result','sql']
        assert result['presentation']['record']['sql_answer']=='说明'
        assert 'do-not-export' not in json.dumps(result['presentation'])
        assert client.public_details({'content':'configured-secret short-ticket'},('short-ticket',))=={'content':'[redacted] [redacted]'}
    finally: await http.aclose()


@pytest.mark.asyncio
async def test_legacy_chart_type_receipt_remains_readable(settings_factory):
    def handle(request):
        if request.url.path.endswith('/question'):
            return httpx.Response(200, text='data: {"type":"id","id":9}\n\n'
                'data: {"type":"sql","content":"SELECT 1"}\n\n'
                'data: {"type":"sql-data","content":"execute-success"}\n\n'
                'data: {"type":"chart-type","content":"bar"}\n\n')
        if request.url.path.endswith('/chat/8'):
            return httpx.Response(200, json={'code':0,'data':{'records':[{'id':9,'finish':True,'sql':'SELECT 1','chart':None}]}})
        return httpx.Response(200, json={'code':0,'data':{'steps':[]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://sqlbot.test') as http:
        result = await SQLBotClient(settings_factory(), http).ask_question(
            assistant_token='token',ticket='ticket',chat_id=8,question='test')
    assert result['chart_hint'] == {'type':'bar'}
    assert result['presentation']['record']['chart'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('upstream,code', [
    ("(2013, 'Lost connection to MySQL server during query (timed out)')", 'sqlbot_database_timeout'),
    ("Error code: 429 - insufficient_quota", 'sqlbot_model_quota_exceeded'),
    ("(1064, 'Unknown column')", 'sqlbot_sql_execution_failed'),
])
async def test_error_classification_retains_record_and_redacts_details(settings_factory, upstream, code):
    events = []
    payload = json.dumps({'message':upstream+' configured-secret', 'traceback':'private stack'})
    wire = ''.join('data: '+json.dumps(e)+'\n\n' for e in [
        {'type':'id','id':9}, {'type':'sql','content':'SELECT 1'}, {'type':'error','content':payload}])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,text=wire)),base_url='http://sqlbot.test') as http:
        client = SQLBotClient(settings_factory(sqlbot_secret_key=SecretStr('configured-secret')), http)
        async def emit(event): events.append(event)
        with pytest.raises(AppError) as caught:
            await client.ask_question(assistant_token='token',ticket='ticket',chat_id=8,question='test',on_event=emit)
    assert caught.value.code == code
    assert caught.value.details['record_id'] == 9
    assert caught.value.details['stage'] == 'sql_execution'
    assert 'configured-secret' not in json.dumps(caught.value.details)
    assert 'private stack' not in json.dumps(caught.value.details)
    assert [e['type'] for e in events] == ['id','sql']  # one classified terminal error comes from Host


@pytest.mark.asyncio
async def test_stream_failure_has_error_and_terminal_event():
    async def run(emit):
        await emit({'type':'sql','content':'SELECT 1'})
        raise AppError('query_failed','查询失败',502)
    chunks=[chunk async for chunk in _event_response(run).body_iterator]
    events=[json.loads(c.removeprefix('data: ')) for c in chunks]
    assert [e['type'] for e in events]==['sql','error','done']
    assert events[1]['code']=='query_failed'


@pytest.mark.asyncio
async def test_stream_disconnect_cancels_producer_and_runs_cleanup():
    cleaned=asyncio.Event()
    async def run(emit):
        try:
            await emit({'type':'started'})
            await asyncio.Event().wait()
        finally: cleaned.set()
    response=_event_response(run)
    await anext(response.body_iterator)
    await response.body_iterator.aclose()
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_completed_data_stream_does_not_reload_dynamic_chat_metadata(settings_factory):
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/question'):
            events = [{'type':'id','id':9}, {'type':'sql','content':'SELECT 1'},
                      {'type':'sql-data','content':'execute-success'},
                      {'type':'chart','content':json.dumps({'type':'table','columns':[{'name':'值','value':'n'}]})}, {'type':'finish'}]
            return httpx.Response(200, text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
        assert request.url.path.endswith('/record/9/log'), 'Do not spend a third datasource callback on chat history'
        return httpx.Response(200, json={'code':0,'data':{'steps':[]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://sqlbot.test') as http:
        result = await SQLBotClient(settings_factory(), http).ask_question(
            assistant_token='token',ticket='ticket',chat_id=8,question='follow-up')
    assert result['sql'] == 'SELECT 1'
    assert result['presentation']['record']['finish'] is True
    assert result['chart_hint'] == {'type':'table','columns':[{'name':'值','value':'n'}]}
    assert json.loads(result['presentation']['record']['chart']) == result['chart_hint']
    assert '/api/v1/chat/8' not in calls


@pytest.mark.asyncio
async def test_native_chart_axes_and_title_preserved_without_extra_callback(settings_factory):
    config = {'type':'column','title':'质检分布','axis':{'x':{'name':'质检项','value':'item'},
        'y':[{'name':'正常','value':'normal'},{'name':'异常','value':'abnormal'}],
        'multi-quota':{'name':'质检结果','value':['normal','abnormal']}}}
    def handle(request):
        if request.url.path.endswith('/question'):
            assert json.loads(request.content)['generate_chart'] is True
            events=[{'type':'id','id':9},{'type':'sql','content':'SELECT item, normal, abnormal FROM sample'},
                    {'type':'sql-data','content':'execute-success'},
                    {'type':'chart','content':json.dumps(config)}, {'type':'finish'}]
            return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
        assert request.url.path.endswith('/record/9/log')
        return httpx.Response(200,json={'code':0,'data':{'steps':[]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle),base_url='http://sqlbot.test') as http:
        result=await SQLBotClient(settings_factory(),http).ask_question(assistant_token='token',ticket='ticket',chat_id=8,question='test')
    assert result['chart_hint'] == config
