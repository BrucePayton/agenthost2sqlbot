# Agent Host 能力核验

核验基线：原始仓库 `/Users/a159358/Works/InternalProjects/DataAgent/data-agent-host/claude_workspace_mvp` 只读；测试副本单独记录 POC 差异。

## 能力矩阵

| 报告能力 | 原始 Host | 证据 | 结论 |
|---|---|---|---|
| Workspace 模板/快照 | 已有 | `app/workspaces/registry.py`、`WorkspaceRecord`、Session 的 `workspace_snapshot_json/hash` | 可复用 |
| Session/Turn/Message | 已有 | `app/db/models.py:29` 起 | 可复用 |
| 身份与 Workspace membership | 已有 | `app/auth/`、`WorkspaceMemberRecord` | 部分复用，需对接 obId/数据权限 |
| AG-UI 与流式事件 | 已有 | `/api/ag-ui`、Turn SSE、heartbeat | 可复用 |
| MCP HTTP | 已有 | 原始 `McpHttpServerManifest`、`_resolve_http_mcp` | 已支持 |
| MCP STDIO | 已有 | 原始 `McpStdioServerManifest`、`_resolve_stdio_mcp` | 已支持 |
| MCP SSE | 原始无 | 原始模型 union 只有 HTTP/STDIO；resolver 只识别二者 | `AH-GAP-001` |
| 测试副本 SSE POC | 有 | 测试副本增加 `McpSseServerManifest`、registry 校验和 `_resolve_sse_mcp` | 仅 POC，不能代表原始项目 |
| `data_agent`/`data_agent_dataset` | 无 | DB 模型与迁移中无对应表 | `AH-GAP-002` |
| `/agent-api/data-agents` CRUD/发布 | 无 | 更新版报告已将该职责移到 Data MCP | Host 非缺口，不应实现 |
| Session 持久化 `agentId` | 无 | `SessionRecord` 无字段，创建 schema 无该上下文 | `AH-GAP-004` |
| 问数 Workspace/CLAUDE.md/Skill | 无 | 原始 workspaces 未提供报告定义的问数模板 | `AH-GAP-005` |
| `data.ask` 调用与结果归一化 | 无 | OpenAPI、runtime、tools 中无目标契约 | `AH-GAP-006` |
| `panel.render_chart` | 无目标实现 | 现有 Davinci 工具/Artifact 不等同该契约 | `AH-GAP-007` |
| 智能体生命周期双写/补偿 | 无 | 无领域模型与服务 | `AH-GAP-008` |

## 运行证据

- 测试副本 Host 运行于 `127.0.0.1:18080`，Swagger/OpenAPI 可读取。
- 已有路径：`/agent-api/session/bootstrap`、`/api/ag-ui`、Workspace/Session/Skill/Instruction API。
- 缺失但仍属 Host 边界的路径：可信 `agentId` bootstrap 和调用 Data MCP `data.ask` 的工具/适配器。智能体 CRUD 已不属 Host 边界。

## 落地判断

原始 Host 不是、也不应成为问数智能体管理平台。它提供会话、Workspace、工具、AG-UI、身份和事件基础设施；更新版报告只要求它补充可信 `agentId` Session/bootstrap、`data.ask` 调用契约和分析/图表适配。

SSE MCP 需要特别区分：测试副本已有可验证 POC，原始 Host 仍不支持。若正式方案采用飞书文档给出的 STDIO `mcp-proxy`，创建与开启样板不依赖 SSE MCP；若采用远程 SSE，再将 POC 回迁原项目并完整测试。
