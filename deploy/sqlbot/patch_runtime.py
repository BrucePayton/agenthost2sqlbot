"""Bounded compatibility patch for the local SQLBot v1.10.2 image.

Fail closed on changed upstream source. Never edits prompts or generated SQL.
"""
from pathlib import Path
import argparse


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"Unsupported SQLBot source: expected one occurrence of {old[:80]!r}")
    return text.replace(old, new, 1)


def patch_db(text: str) -> str:
    text = replace_once(text, "import json\n", "import json\nimport hashlib\n")
    text = replace_once(text,
        "    conf = DatasourceConf(**json.loads(aes_decrypt(ds.configuration)))\n    extra_config_dict = get_extra_config(conf)\n\n    pool_config",
        """    # Connection validation uses 10s and mutates the dynamic datasource.
    # Query connections must not inherit that health-check read timeout.
    dynamic_starrocks = isinstance(ds, AssistantOutDsSchema) and equals_ignore_case(ds.type, 'doris', 'starrocks')
    if dynamic_starrocks:
        ds = ds.model_copy(update={'configuration': get_out_ds_conf(ds, int(os.environ.get('SQLBOT_DYNAMIC_DB_READ_TIMEOUT', '30')))})
    conf = DatasourceConf(**json.loads(aes_decrypt(ds.configuration)))
    extra_config_dict = get_extra_config(conf)

    pool_config""")
    start = text.index("    elif equals_ignore_case(ds.type, 'doris', 'starrocks'):", text.index('def get_driver_connection('))
    end = text.index("    elif equals_ignore_case(ds.type, 'redshift'):", start)
    text = text[:start] + """    elif equals_ignore_case(ds.type, 'doris', 'starrocks'):
        ssl_args = {'ssl': {'ssl_mode': 'REQUIRE'}} if conf.ssl else {}
        timeouts = {
            'connect_timeout': int(os.environ.get('SQLBOT_DYNAMIC_DB_CONNECT_TIMEOUT', '10')) if dynamic_starrocks else conf.timeout,
            'read_timeout': conf.timeout,
        }
        # Explicit operation options (e.g. version probe) override defaults once.
        args = timeouts | conn_conf | ssl_args
        if not use_pool:
            conn = pymysql.connect(user=conf.username, passwd=conf.password, host=conf.host,
                                   port=conf.port, db=conf.database, **args)
        else:
            conn = PooledDB(creator=pymysql, user=conf.username, passwd=conf.password,
                            host=conf.host, port=conf.port, db=conf.database, **args)
""" + text[end:]
    text = replace_once(text,
        "def get_driver_pool(ds: CoreDatasource | AssistantOutDsSchema, db_config: dict = {}):\n    pool = driver_pool_manager.get_pool(ds, db_config)",
        """def get_driver_pool(ds: CoreDatasource | AssistantOutDsSchema, db_config: dict = {}):
    if isinstance(ds, AssistantOutDsSchema) and equals_ignore_case(ds.type, 'doris', 'starrocks'):
        ds = ds.model_copy(update={'configuration': get_out_ds_conf(ds, int(os.environ.get('SQLBOT_DYNAMIC_DB_READ_TIMEOUT', '30')))})
        db_config = {'connect_timeout': int(os.environ.get('SQLBOT_DYNAMIC_DB_CONNECT_TIMEOUT', '10')),
                     'read_timeout': int(os.environ.get('SQLBOT_DYNAMIC_DB_READ_TIMEOUT', '30'))} | db_config
    pool = driver_pool_manager.get_pool(ds, db_config)""")
    # Cache invalidation applies to effective configuration, not just datasource ID.
    start = text.index('class DriverConnectionPoolManager:')
    head, manager = text[:start], text[start:]
    manager = replace_once(manager, '        self._lock = threading.Lock()',
                           '        self._lock = threading.Lock()\n        self._signatures = {}')
    manager = replace_once(manager, '        with self._lock:\n            if ds.id:',
        """        signature = hashlib.sha256(json.dumps([json.loads(aes_decrypt(ds.configuration)), db_config], sort_keys=True).encode()).hexdigest()
        with self._lock:
            if ds.id in self._pools and self._signatures.get(ds.id) != signature:
                self._pools.pop(ds.id).close()
                self._signatures.pop(ds.id, None)
            if ds.id:""")
    manager = replace_once(manager, '                    oldest_pool.close()',
                           '                    self._signatures.pop(oldest_id, None)\n                    oldest_pool.close()')
    manager = replace_once(manager, '            self._pools[ds.id] = new_pool',
                           '            self._pools[ds.id] = new_pool\n            self._signatures[ds.id] = signature')
    manager = replace_once(manager, '                pool = self._pools.pop(datasource_id)',
                           '                pool = self._pools.pop(datasource_id)\n                self._signatures.pop(datasource_id, None)')
    manager = replace_once(manager, '            self._pools.clear()',
                           '            self._pools.clear()\n            self._signatures.clear()')
    return head + manager


def patch_model(text: str) -> str:
    return replace_once(text,
        "class ChatQuestionBase(BaseModel):\n    question:",
        "class ChatQuestionBase(BaseModel):\n    generate_chart: bool = Body(default=True, description='Generate chart configuration after querying data')\n    question:")


def patch_api(text: str) -> str:
    text = replace_once(text,
        '    return await question_answer_inner(session, current_user, question, current_assistant, embedding=True)',
        '''    finish_step = ChatFinishStep.GENERATE_CHART if request_question.generate_chart else ChatFinishStep.QUERY_DATA
    return await question_answer_inner(session, current_user, question, current_assistant,
                                       embedding=True, finish_step=finish_step)''')
    return replace_once(text,
        '        if not record.chart:\n            raise Exception(',
        "        if not record.chart and not (action_type == 'analysis' and record.data):\n            raise Exception(")


def patch_llm(text: str) -> str:
    text = replace_once(text,
        '    def get_fields_from_chart(self, _session: Session):\n        chart_info = get_chart_config(_session, self.record.id)',
        '''    def get_fields_from_chart(self, _session: Session):
        if not self.record.chart:
            return get_chat_chart_data(_session, self.record.id).get('fields', [])
        chart_info = get_chart_config(_session, self.record.id)''')
    return replace_once(text,
        '            if finish_step.value <= ChatFinishStep.QUERY_DATA.value:\n                if stream:',
        '''            if finish_step.value <= ChatFinishStep.QUERY_DATA.value:
                # Reuse SQL generation's type; no chart configuration/model call.
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'chart-type', 'content': chart_type}).decode() + '\\n\\n'
                if not stream:
                    json_result['chart_type'] = chart_type
                if stream:''')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/opt/sqlbot/app'))
    args = parser.parse_args()
    patches = {'apps/db/db.py': patch_db, 'apps/chat/models/chat_model.py': patch_model,
               'apps/chat/api/chat.py': patch_api, 'apps/chat/task/llm.py': patch_llm}
    # Validate all replacements and syntax before writing any file.
    outputs = {}
    for name, patch in patches.items():
        path = args.root / name
        outputs[path] = patch(path.read_text())
        compile(outputs[path], str(path), 'exec')
    for path, value in outputs.items():
        path.write_text(value)


if __name__ == '__main__':
    main()
