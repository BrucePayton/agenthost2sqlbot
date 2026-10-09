# Local SQLBot compatibility patch

Targets the tested `registry.cn-qingdao.aliyuncs.com/dataease/sqlbot:v1.10.2` image.
`patch_runtime.py` checks each source replacement and compiles all outputs before
writing them. An upstream source change fails the build instead of silently
applying an incomplete patch.

## Behavior

- Dynamic StarRocks/Doris query connections use a 10-second connection timeout
  and a separate 30-second read timeout. SQLBot's connection check previously
  wrote a 10-second timeout into the datasource used for subsequent queries.
- `SQLBOT_DYNAMIC_DB_CONNECT_TIMEOUT` and `SQLBOT_DYNAMIC_DB_READ_TIMEOUT` control
  these values in the SQLBot container. They do not change StarRocks server settings.
- Driver pools are invalidated when effective connection configuration changes.
  Configuration fingerprints are computed from decrypted values so randomized
  encryption does not trigger needless rebuilds; credentials are not logged.
- `POST /api/v1/chat/question` accepts `generate_chart` (default `true`, retaining
  SQLBot's original UI behavior). Host sends `false` to reuse the native
  `QUERY_DATA` finish step. The SSE `chart-type` event contains the SQL-generation
  model's suggested type, or `null` when unavailable. No chart model is called.
- Data-only records can still request text analysis; field names come from the
  query result. Prediction retains SQLBot's existing chart requirement.
- Host returns only `chartHint.type` and displays it alongside the data table.
  No chart canvas or G2 dependency is loaded by the Data Agent page.

## Build, deploy and roll back

```sh
docker build -t sqlbot-confirm:agenthost-query-data-20261009 deploy/sqlbot
```

For the existing local SQLBot deployment, use its original compose file plus
`deploy/sqlbot/compose.override.yaml`. The override changes only the image and
two timeout settings, retaining the original persistent volumes and credentials.
The deployed copy is at the paired SQLBot deployment's `compose.override.yaml`.
Recreate its `sqlbot` service to install the image and clear the old process pools.

To roll back, remove only that override and recreate the service from its original
compose file. Retain the data volumes. Revert Host's `generate_chart: false` behavior
as well if returning to an unpatched SQLBot image; old SQLBot ignores the new field.

## Verification

Extract the original image's four patched Python files using `docker cp` and name
them `db.original.py`, `chat-api.original.py`, `chat-model.original.py`, and
`llm.original.py`. Run:

```sh
SQLBOT_REGRESSION_SOURCE_ROOT=/path/to/source-backup \
  .venv/bin/python -m pytest tests/test_sqlbot_runtime_patch.py tests/test_data_agent_stream.py -q
```

The vendor tests require that original source snapshot and explicitly skip if it
is absent. Also run `node --test tests/js/data-agent-chat.cjs` and the real 10-case
regression described in `docs/sqlbot-regression-10-20261009.md`.
