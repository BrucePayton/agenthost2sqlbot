# 数巢问数：切换取数方式

在“数巢问数”会话顶部的“取数方式”选择：

- **SQLBot（远程服务 2）**：保留已发布 Data Agent、SQLBot 历史上下文与图表流程。
- **MCP 工具**：模型直接使用公司 MCP 的 `table.search`、`table.describe`、`table.query`，无需创建或发布 SQLBot 助手，不通过 Service-2 目录或 SQLBot 取数。工具失败不会回退。

选择保存在服务端，每个会话独立，刷新页面后仍保留。切换会清除模型续接 ID 和 SQLBot 上下文，历史消息仍显示；新问题应包含完整条件。正在回答或已有排队请求时禁止切换。MCP 的“重置问数上下文”同样重建模型上下文。

## 连接配置

先安装公司 `@aihuishoubi/mcp-proxy`。在 Host 和执行 Worker 的环境中设置 `.env.example` 中的 `DATA_MCP_*` 项：

```dotenv
DATA_MCP_COMMAND=/absolute/path/to/node
DATA_MCP_ARGS=["/absolute/path/to/mcp-proxy"]
DATA_MCP_TOKEN_CACHE_DIR=/absolute/path/to/agenthost-only-cache
DATA_MCP_SUBJECT=159358
DATA_MCP_GATEWAY_URL=https://bimcp-gateway.aihuishou.com
DATA_MCP_TIMEOUT_SECONDS=60
```

路径必须按部署机器填写。预先创建缓存目录（建议权限 0700），通过该缓存目录完成公司 CAS 登录；不要把缓存提交到仓库。`DATA_MCP_SUBJECT` 必须对应缓存中登录者在 AgentHost 的已认证用户标识。当前实现是单用户显式绑定，不把同一登录缓存开放给其他用户。默认未配置时 MCP 选项不可用，原 SQLBot 行为不变。

在容器部署时，Node、mcp-proxy 和缓存都必须在 Host / Worker 容器内可用，不能填宿主机 macOS 路径。不要把缓存或以上配置放入 Runner。无浏览器的服务器应先在可完成 CAS 的环境中取得该账号的有效缓存，再通过部署方的凭证管理方式提供给服务。登录过期时需要重新认证。

应用启动会执行 0017 数据库迁移，给历史会话补上默认 SQLBot 模式。保存 MCP 模式前通过真实 `tools/list` 验证三个工具并保存其 schema；工具变更时重新选择 MCP 以刷新。工具名在模型内映射为 `mcp__data_mcp__table_search` / `table_describe` / `table_query`，上游仍使用点号名称。

Host 使用项目已有的 Python MCP SDK；隔离执行时复用现有 Runner/Worker 控制通道，Worker 根据当前 turn 的会话和用户决定权限。只传递工具定义、参数及结果，不传代理路径或认证缓存。现有工具过程展示可查看 MCP 参数与回执，回答由模型基于返回数据生成；MCP 模式不提供 SQLBot 的专用图表和分析按钮。

## 本次验证（2026-10-09）

- Python：379 项通过，覆盖后端选择保存、失败回滚、会话隔离、运行中禁止切换、SDK 工具白名单、Runner/Worker 转发、旧 SQLBot 流程、数据库升级及 API 权限回归。
- JavaScript：221 项通过，包含 MCP 模式不加载 SQLBot 助手、切换保存与失败恢复、运行中禁用选择器。
- 实际公司网关：使用 AgentHost 独立缓存完成认证，读取到三个工具；真实搜索“回收质检”返回 208 个候选，partial=false，首项为 `dm.dm_platform_trade_order_recycle_inspection_detail`。
- 实际 Claude 运行时：隔离临时会话使用当前 MCP schema，模型只调用一次 `mcp__data_mcp__table_search`，上游 `table.search` 成功并生成答复，约 13.5 秒。没有查询业务明细，没有调用 SQLBot。
- 未重启已有服务或部署远端。已有实例需重启后加载配置、0017 迁移和前端改动；OpenSandbox 的真实容器运行未在本次启动，仅验证其协议和转发测试。
