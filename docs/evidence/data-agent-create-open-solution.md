# 问数智能体创建与开启技术方案（更新版）

本方案对应 Draw.io TAB `02-问数智能体创建与开启`。SQLBot 和 Agent Host 原仓库保持不变；本文描述后续正式开发边界。

## 1. Data MCP 对外 REST

| 方法 | 路径 | 作用 | 成功条件 |
|---|---|---|---|
| POST | `/data-agents` | 创建草稿并启动 Assistant 同步 | 全部数据集重新授权；草稿和绑定已写入；返回同步状态 |
| GET | `/data-agents` | 列出当前用户可见对象 | 只返回调用者可见且符合状态的对象 |
| GET | `/data-agents/{id}` | 详情读回 | 重新校验可见权限 |
| PUT | `/data-agents/{id}` | 修改名称、简介和数据集 | 重新验证全部数据集，使用版本锁 |
| POST | `/data-agents/{id}/publish` | 发布 | 仅 Assistant 同步成功对象可发布，返回读回对象 |
| POST | `/data-agents/{id}/disable` | 禁用 | 立即从可见列表移除并阻止新问数 |
| DELETE | `/data-agents/{id}` | 级联删除 | 先 tombstone，再删除 SQLBot Assistant，最终完成 |

所有写接口使用可信 Davinci 登录态和幂等键，不接受请求体传入创建者 `obId`。浏览器不能直接访问当前无独立 HTTP 鉴权的 MCP 工具端点。

## 2. Data MCP 数据模型

```text
data_agent
  id UUID PK
  name / intro
  status draft|published|disabled|deleting|delete_failed
  creator_ob_id
  sqlbot_assistant_id BIGINT NULL UNIQUE
  sync_state pending|ready|failed
  visibility_policy
  version
  created_at / updated_at

data_agent_dataset
  agent_id UUID FK
  dataset_ref
  display_name / table_name / sort_order
  UNIQUE(agent_id, dataset_ref)

data_agent_outbox
  id / agent_id / operation / idempotency_key
  status / attempts / last_error / next_retry_at
```

Host 不创建上述表。它只保留已有 Session/Turn，并在会话上下文中保存或解析可信 `agentId`。

## 3. 创建状态机

```text
request
  -> authenticate_and_validate
  -> permission_denied [终态，无写入]
  -> draft + sync_pending [Data MCP 本地事务]
  -> sync_assistant
       -> ready [保存 assistantId]
       -> sync_failed [保留草稿，可重试]
  -> publish
       -> published [读回确认]
```

先写 Data MCP 草稿和 outbox，再同步 SQLBot，避免 SQLBot Assistant 成功但业务对象丢失。幂等键由 `agentId + operation + version` 派生。

## 4. SQLBot Assistant 同步

- 使用目标 Workspace 的受控管理员服务账号。
- 创建 `type=1` Assistant；配置只保存 Callback、超时和凭证映射，不保存业务数据集主数据。
- 创建后读回 `id/type/name/configuration`。
- 删除时 404 视为幂等成功，其他错误进入重试和人工对账。
- SQLBot 源码不做任何修改。

## 5. 开启与 Host Session

前端创建 Session 时只提交 `agentId`。Host 服务端从 Data MCP 读取智能体快照，或校验由 Data MCP 签发的快照；不得信任浏览器提交的名称、数据集、Assistant ID 或可见范围。

```json
{
  "agentId": "uuid",
  "dataAgentVersion": 3,
  "workspaceKind": "data-question"
}
```

Host 装载问数 Workspace、指令和 Skill 后进入可提问状态。SQLBot Chat 在第一次 `data.ask` 时由 Data MCP 创建并保存 `sessionKey -> chatId`，打开面板不创建空 Chat。

## 6. 权限

- 创建和修改：当前用户必须拥有全部 `datasetRef`，持久化前重新检查。
- 查看和开启：以 Data MCP 可见性结果为准，开启时再次检查 `published`。
- 数据查询：每次提问实时裁决，不能沿用创建时权限快照。
- 已发布智能体可以对用户可见，但不得泄露用户无权查看的数据集和字段元数据。

## 7. 物理实现位置

当前线上 Java Office MCP 只提供资产和权限工具。正式实现必须先按以下规则决策：

- Java 服务具备持久化、SSE 客户端、SQLBot 管理和生产运维能力：直接在 Java 网关扩展。
- 否则：Java 网关继续负责外部认证和身份覆盖，内部调用新的 Python Ask Service。
- Python `davinci_data_mcp` 不是线上实例，不能直接当作正式替换目标。

## 8. Mock 替换位

- Data MCP REST/状态机：`mock_services/data_mcp/data_agents.py` → Java Office 网关模块或内部 Python Ask Service。
- Assistant 同步：`mock_services/data_mcp/sqlbot_sync.py` → 同一 Data MCP 实现边界。
- Host agentId/bootstrap：`mock_services/agent_host/session_context.py` → Host sessions/runtime 的正式适配。
- SQLBot 只允许故障注入 Mock；正常联调使用真实 18000 服务。

当前均未启用。真实 `access.check_resources`、Schema 和字段工具禁止 Mock。

## 9. 验收用例

1. 1 个和 5 个有权数据集创建成功；0 个、6 个和重复项拒绝。
2. 任一数据集无权时不写草稿、不调用 SQLBot。
3. 同一幂等键重试不产生第二个 Assistant。
4. SQLBot 超时后草稿为 `sync_failed`，可重试且可读回。
5. `sync_failed` 不能发布；禁用对象不能开启。
6. Host 只接受可信 agentId，伪造快照或越权 agentId 被拒绝。
7. 打开面板不创建 SQLBot Chat；首次 ask 只创建一次。
8. 删除失败保留 tombstone 和映射，补偿成功后才最终清理。
