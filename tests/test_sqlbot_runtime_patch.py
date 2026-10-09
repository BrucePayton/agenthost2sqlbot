"""Verify against extracted, unmodified SQLBot v1.10.2 source before deploying.

Set SQLBOT_REGRESSION_SOURCE_ROOT to the backup directory containing
db.original.py, chat-api.original.py, chat-model.original.py, llm.original.py.
"""
import ast
import hashlib
import importlib.util
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def patched():
    root = os.environ.get('SQLBOT_REGRESSION_SOURCE_ROOT')
    if not root:
        pytest.skip('Requires extracted SQLBot source; see module docstring')
    path = Path(__file__).parents[1] / 'deploy/sqlbot/patch_runtime.py'
    spec = importlib.util.spec_from_file_location('patch_runtime', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = {}
    for name, fn in [('db',module.patch_db),('chat-api',module.patch_api),
                     ('chat-model',module.patch_model),('llm',module.patch_llm)]:
        original = (Path(root)/f'{name}.original.py').read_text()
        result[name] = fn(original)
        compile(result[name], name, 'exec')
        with pytest.raises(ValueError):
            fn(result[name])  # never silently apply twice to unknown source
    return result


def extract(source, names, namespace):
    nodes = [n for n in ast.parse(source).body if getattr(n,'name',None) in names]
    for node in nodes:
        node.decorator_list = []
    code = ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*nodes],type_ignores=[])
    exec(compile(ast.fix_missing_locations(code),'<sqlbot>','exec'),namespace)


class Dynamic:
    id = 1
    type = 'starrocks'
    configuration = '{"timeout":10}'

    def model_copy(self,update):
        copied = Dynamic()
        copied.__dict__.update(self.__dict__)
        copied.__dict__.update(update)
        return copied


def test_health_check_timeout_not_inherited_and_overrides_not_duplicated(patched, monkeypatch):
    monkeypatch.setenv('SQLBOT_DYNAMIC_DB_READ_TIMEOUT','30')
    monkeypatch.setenv('SQLBOT_DYNAMIC_DB_CONNECT_TIMEOUT','10')
    ns = dict(os=os,json=json,AssistantOutDsSchema=Dynamic,
        equals_ignore_case=lambda value,*options:value in options,aes_decrypt=lambda x:x,
        get_out_ds_conf=lambda ds,timeout:json.dumps({'timeout':timeout}),
        DatasourceConf=lambda **kw:SimpleNamespace(**kw,host='test',port=9030,username='u',
            password='p',database='hive.dw',poolSize=1,ssl=False),
        get_extra_config=lambda conf:{}, PooledDB=lambda **kw:kw,
        pymysql=SimpleNamespace(connect=lambda **kw:kw))
    extract(patched['db'], {'get_driver_connection'}, ns)
    ds=Dynamic()
    pooled=ns['get_driver_connection'](ds, use_pool=True)
    assert (pooled['connect_timeout'],pooled['read_timeout']) == (10,30)
    assert json.loads(ds.configuration)['timeout'] == 10  # copy, not caller mutation
    probe=ns['get_driver_connection'](ds, {'read_timeout':10,'connect_timeout':10})
    assert probe['read_timeout'] == 10


def test_pool_invalidates_when_effective_config_changes(patched):
    pools=[]
    class Pool:
        closed=False
        def close(self): self.closed=True
    def create(*_,**__):
        pool=Pool();pools.append(pool);return pool
    ns=dict(json=json,hashlib=hashlib,threading=threading,OrderedDict=OrderedDict,
            aes_decrypt=lambda x:x,get_driver_connection=create)
    extract(patched['db'], {'DriverConnectionPoolManager'}, ns)
    manager=ns['DriverConnectionPoolManager'](max_pools=2)
    ds=Dynamic()
    first=manager.get_pool(ds,{'read_timeout':10})
    assert manager.get_pool(ds,{'read_timeout':10}) is first
    second=manager.get_pool(ds,{'read_timeout':30})
    assert second is not first and first.closed
    ds.configuration='{"timeout":30,"password":"changed"}'
    third=manager.get_pool(ds,{'read_timeout':30})
    assert third is not second and second.closed
    manager.remove_pool(ds.id)
    assert third.closed and not manager._signatures


@pytest.mark.asyncio
@pytest.mark.parametrize('generate,expected',[(False,2),(True,3)])
async def test_api_selects_native_query_data_finish_step(patched,generate,expected):
    seen={}
    async def inner(*_,**kw): seen.update(kw);return 'stream'
    ns=dict(ChatQuestion=lambda **kw:kw,ChatFinishStep=SimpleNamespace(QUERY_DATA=2,GENERATE_CHART=3),question_answer_inner=inner)
    extract(patched['chat-api'],{'question_answer'},ns)
    assert await ns['question_answer'](None,None,SimpleNamespace(chat_id=1,question='unchanged',generate_chart=generate),None)=='stream'
    assert seen['finish_step']==expected
