# SQLBot 能力核验

源码：`/Users/a159358/Works/OpensourceProjects/SQLAgent/SQLBot`（只读）  
运行服务：`http://127.0.0.1:18000`

## 运行验证

- 管理员登录成功，未在文档中保存 Token。
- 携带 `X-SQLBOT-TOKEN: Bearer <token>` 可读取真实 OpenAPI。
- `GET /api/v1/system/assistant` 返回成功；当前 assistant 数量为 0。
- OpenAPI 确认存在：
  - `POST/GET/PUT /api/v1/system/assistant`
  - `DELETE /api/v1/system/assistant/{id}`
  - `POST /api/v1/chat/assistant/start`
  - `POST /api/v1/chat/question`
- 8001 的 `GET /mcp` 返回 SSE endpoint 事件；根、`/sse` 均 404，属于 `/mcp` 入口的 legacy SSE 形态。

## 能力矩阵

| 报告依赖 | 状态 | 源码/运行证据 | 结论 |
|---|---|---|---|
| `sys_assistant(type=1)` | 支持 | `AssistantModel`、assistant CRUD、Swagger type 描述 | 可直接使用 |
| assistant 管理权限 | 支持 | CRUD 使用 `ws_admin` 权限 | 适合受控服务账号 |
| assistant/start | 支持 | `backend/apps/chat/api/chat.py:169` | 可创建 Chat |
| question SSE | 支持 | `chat.py:250`、`StreamingResponse(text/event-stream)` | 可使用但需适配事件 |
| 高级助手外部数据源回调 | 支持 | `AssistantOutDs.get_ds_from_api()` GET endpoint | 可返回动态数据源/schema/凭据 |
| SQL 生成/执行 | 支持 | `LLMService` 的 SQL、执行和图表阶段 | 核心能力存在 |
| ChatRecord/ChatLog | 支持 | chat 模型与记录接口 | 可审计 SQLBot 内部问答 |
| 内置分析/预测 | 支持但目标不用 | record analysis/predict SSE | 报告主链排除 |
| MCP 8001 | 存在 | `FastApiMCP` 暴露 7 个 operation；运行 `/mcp` 为 SSE | 不进入报告主链，生产应关闭 |

## SQLBot 重点能力与缺口

### SB-CAP-001 每题动态 ticket 透传已有源码路径

报告要求每题 ticket 绑定用户、智能体和 Session，并由 SQLBot 以 `X-Davinci-Ticket` 回调。复核源码后确认，调用方可以在每次 `assistant/start` 和 `question` 请求中传入 `X-SQLBOT-ASSISTANT-CERTIFICATE`。`get_current_assistant()` 会用其 Base64 解码后的 certificate JSON 覆盖当前请求的小助手 certificate；`AssistantOutDs.get_ds_from_api()` 再把其中的 header/cookie/param 原样写入回调请求。

推荐 certificate 内容为 `[{"target":"header","key":"X-Davinci-Ticket","value":"<ticket>"}]`。Data MCP 必须在首次 `assistant/start` 和每次 `question` 都传入新的动态 certificate。该路径证明 SQLBot 零改码具备源码基础；尚需真实 callback 验证编码、60 秒过期、多次回调、并发隔离和拒绝路径。

### SB-GAP-002 目标标准结果契约

SQLBot SSE 输出内部事件，如 `id`、`sql-result`、`sql`、`sql-data`、`chart-result`、`chart`、`finish`、`error`；没有原生 `{sql, columns, rows, chartHint, evidence}` 单一对象。

影响：Data MCP 必须解析并归一化；其中 `evidence` 需要额外来源，不能假定 SQLBot 生成。

### SB-GAP-003 chartHint 语义不一致

SQLBot 会生成完整 chart JSON/图片相关结果，而报告要求 SQLBot 只给轻量 `chartHint`，Host/前端完成分析和渲染。

影响：需定义从 SQLBot chart/SSE 到目标 hint 的裁剪规则；不能直接声称原生支持 `chartHint`。

### SB-GAP-004 外部用户权限不可见

assistant token 解析后使用通用 `sqlbot-inner-assistant` 身份并继承 assistant 的 oid；SQLBot 不知道 Host 最终用户、智能体绑定数据集或 Davinci 行列权限。

影响：权限必须在 Data MCP 回调前完成并通过 ticket 绑定；SQLBot 自身不能成为最终授权点。

### SB-GAP-005 回调协议与供数接口需精确对齐

当前高级助手在创建 Chat 时同步 GET assistant endpoint，并期待数据源列表/字段/连接参数。报告描述的“每题回调供数、基础 SQL、临时凭据”需要与 `AssistantOutDsSchema` 和后续执行阶段逐字段对齐。

影响：若真实供数接口只返回结果数据而不是 SQLBot 期待的数据源 schema/连接信息，则无法零改码落地。

### SB-GAP-006 MCP 暴露安全

8001 `/mcp` 当前可建立 SSE 会话；MCP operation 中包含 access_token、账号密码或 token 参数型工具。报告已经决定不对外暴露 8001。

影响：部署层必须关闭/隔离端口；不能把它当成 Host→SQLBot 的生产主路径。

## 总结

SQLBot 具备“高级助手 + 请求级动态 certificate + 外部数据源 + Chat + NL2SQL + 执行 + SSE”的核心引擎能力，零源码修改具有较强基础。当前决定性缺口已经从“SQLBot 是否能透传 ticket”转移为“Data MCP 是否实现 ticket、callback、供数模型和 SSE 归一化”；端到端 Spike 仍是生产门槛。
