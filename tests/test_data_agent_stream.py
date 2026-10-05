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
