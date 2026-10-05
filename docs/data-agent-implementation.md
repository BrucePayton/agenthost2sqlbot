# Data Agent × SQLBot 高级小助手实现说明

状态日期：2026-09-22。实现仅落在 `AgentHost2SQLbot` 测试项目；参考 Agent Host 与 SQLBot 仓库未修改。

## 架构结论

报告中的 Data MCP 是“问数编排逻辑域”，不是当前线上某个端点的同义词。线上 `davinci-data-mcp`（本项目称外部 Asset ACL MCP）是该逻辑域依赖的资产发现、授权与 Schema 服务；它已提供 `access.check_resources`、`catalog.get_dataset_schema`、`catalog.search_datasets`，但不提供 `data.ask`、Ticket、SQLBot Assistant 生命周期、动态供数回调、SQLBot SSE 归一化或结果缓存。

本 POC 将逻辑 Data MCP 作为 `app/data_mcp` 模块与 Agent Host 同进程部署，以便在不修改参考仓库和 SQLBot 源码的前提下验证完整契约。模块边界独立，可后续拆为单独服务：

- `app/data_mcp/asset_mcp.py`：外部 Asset ACL MCP 客户端，确定性调用权限与 Schema 工具。
- `app/data_mcp/service.py`：Agent 生命周期、Ticket、SQLBot 编排、回调、缓存与审计。
- `app/data_mcp/providers.py`：外部 assetRef 到物理 MySQL 表、字段白名单和受控 `tables[].sql` 的映射。
- `app/sqlbot/client.py`：SQLBot 管理 API、高级小助手 JWT、SSE 与 Record Data 适配。
- `app/runtime/claude.py`：Agent Host 进程内 `mcp__data_mcp__ask` 工具；用户身份和 Session 由 Host 注入。

## 已实现链路

```text
创建/发布
  前端 -> POST /api/data-agents
  -> Asset MCP access.check_resources（可信 obId）
  -> catalog.get_dataset_schema（授权字段/requiredFilters/metadataVersion）
  -> 同源字段校验，失败关闭
  -> 保存 Data Agent 草稿与授权快照
  -> SQLBot 管理 API 创建或更新 type=1 Assistant
  -> 保存 assistantId 并发布

开启/提问
  前端 -> POST /api/data-agents/{id}/open
  -> Host 创建 data-question Session 并绑定 user/agent/session
  -> ClaudeAgentRuntime 调用进程内 data.ask
  -> Data Agent 签发 60 秒 Ticket（库内只存 SHA-256）
  -> SQLBot assistant/start 或 question，Certificate 仅携带本次 Ticket
  -> SQLBot GET /api/sqlbot/datasources
  -> 原子消费 Ticket并再次调用 Asset MCP 复核权限与 Schema
  -> 同源校验通过后返回 DB 连接、tables[].sql 和授权 fields
  -> SQLBot NL2SQL、子查询替换并连接数据库执行
  -> Data Agent 消费 SSE，再读取 /record/{id}/data
  -> 首屏最多 200 行；结果缓存最多 1000 行、默认 10 分钟 TTL
  -> 前端展示表格、图表建议、逻辑 SQL、fieldsUsed 与 evidence
```

SQLBot 不是“永不连接数据库”。准确边界是：SQLBot 在动态回调完成 Ticket 与 ACL 校验之前拿不到连接信息；校验通过后，由 SQLBot 使用回调载荷连接数据库并执行 SQL。Data Agent 不代替 SQLBot 执行 SQL。

## 已落地的安全控制

- 浏览器不能提交可信 user、Host Session、Ticket、数据库连接或 SQLBot 密钥。
- Ticket 绑定 user、agent、session、questionHash、问题与字段投影上限；默认 60 秒、最多两次回调，问答结束立即完成并清空短期问题。
- Ticket 次数通过条件更新原子递增，过期、完成和超次数请求均失败关闭。
- 创建、发布和每次 SQLBot 回调都会重新检查外部 ACL；任何数据集未授权即整体拒绝。
- 外部 Schema 与配置的物理字段不一致时拒绝；出现非空 `requiredFilters` 时也拒绝，因为当前 MySQL POC 尚不能正确执行行权限谓词。
- 只有全部校验通过后才构造数据库连接载荷；审计不记录 Ticket、Token、密码等秘密。
- `DATA_AGENT_CALLBACK_CIDRS` 可限制回调来源；不信任 `X-Forwarded-For`，只检查直连地址。
- `tables[].sql` 与物理列白名单均由服务端生成，浏览器不能注入 `rule` 或 SQL。
- 仅当 SQLBot 明确返回 `cannot_generate` 时进行一次 Top100 重试，授权字段集合不扩大。
- 结果接口按用户和 Agent 再鉴权，缓存过期返回 410。

## 运行配置与真实门禁

Agent Host 的模型调用支持两种互斥凭证方式：标准代理使用
`ANTHROPIC_API_KEY`，要求 `Authorization: Bearer` 的 Anthropic 兼容网关使用
`ANTHROPIC_AUTH_TOKEN`。当前阿里云兼容网关已按后一种方式配置；模型为
`deepseek-v4-pro`，流式空闲超时为 600000 毫秒。凭证仅进入 Claude 子进程，日志与
配置摘要只显示脱敏值。

关键配置见 `.env.example`：

```env
DATA_AGENT_ENABLED=true
DATA_AGENT_SUBJECT=159358
DATA_AGENT_PUBLIC_BASE_URL=http://host-reachable-ip:18080
DATA_AGENT_RUNTIME_ASSET_REF=warehouseTable:the-same-physical-asset
ASSET_MCP_URL=https://asset-search-mcp-office.aihuishou.com/mcp
SQLBOT_BASE_URL=http://localhost:18000
SQLBOT_ADMIN_ACCOUNT=admin
SQLBOT_ADMIN_PASSWORD=...
SQLBOT_SECRET_KEY=...
MYSQL_HOST=...
MYSQL_USER=...
MYSQL_PASSWORD=...
MYSQL_DATABASE=knowledge
DATA_AGENT_CALLBACK_CIDRS=10.193.65.41/32
```

已实测本机 SQLBot 管理登录及 OpenAPI 可用，客户端使用的 Assistant、start、question、record-data 路由与服务契约一致。Host 使用的部署级 Secret 已与正在运行的 `sqlbot` 容器实际 `SECRET_KEY` 对齐；临时创建 type=1 Assistant 后，Assistant JWT 鉴权通过，并成功进入动态回调。验证使用故意无效的 Ticket，回调返回 401、SQLBot 下游返回 500，证明鉴权通过且供数失败关闭；临时 Assistant 已删除。

用户提供的 Access Key 与所谓 `SQLBOT_SECRET_KEY` 已通过 `/api/v1/system/apikey` 验证为一对启用的 API Key。该 Secret 用于签发 `X-SQLBOT-ASK-TOKEN`，不等于高级小助手 JWT 使用的部署级 `settings.SECRET_KEY`，因此没有写入 `SQLBOT_SECRET_KEY` 配置，也不参与本链路。

当前环境仍不能宣称真实端到端通过：

1. 尚未取得能证明与 `knowledge.sync_job` 同一物理来源的 Asset MCP `datasetRef`，所以 `DATA_AGENT_RUNTIME_ASSET_REF` 保持为空。已有的 `159358` 授权样本属于其他数据集，不能冒充同源资产。
2. 本地 `.env` 已按确认配置给定的高权限 MySQL 账号，仅用于本次测试；生产必须换为限定表、限定来源的 SELECT-only 账号，并在验收后轮换测试凭证。
3. 外部 MCP 当前返回的已验证样本均没有非空 `requiredFilters`。本 POC 对非空值主动拒绝，不能声称已落地行级权限。
4. 本机 Docker 回调实测在 Host 侧来源为 `127.0.0.1`，本地 `.env` 已收紧为 `127.0.0.1/32`。迁移到其他主机或集群时必须按真实代理/NAT 拓扑重新配置，且不能直接信任 `X-Forwarded-For`。

`DATA_AGENT_ALLOW_UNVERIFIED_LOCAL_DATASET=true` 只供自动化测试夹具使用，真实联调禁止开启，也不会作为上述缺口的 Mock 补位。

## 验证

```bash
uv sync --frozen
uv run pytest tests/test_asset_mcp.py tests/test_data_agent.py tests/test_config.py tests/test_bootstrap.py -q
uv run ruff check app/data_mcp app/sqlbot app/config.py tests/test_asset_mcp.py tests/test_data_agent.py
python scripts/generate_agenthost_sqlbot_drawio.py
```

Drawio 权威图位于 `docs/agenthost-data-mcp-sqlbot-report-flows.drawio`。红色加粗虚线只保留真实未满足项；已实现但尚受运行配置阻塞的能力标为蓝色或橙色，并在节点中说明门禁。
