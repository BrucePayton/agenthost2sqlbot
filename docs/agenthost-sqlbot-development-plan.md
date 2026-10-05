# Agent Host × SQLBot 落地分析与开发计划

> 历史基线：本文记录早期“直接连接 SQLBot SSE MCP”的验证。当前按更新报告实施的
> `Data MCP + SQLBot 高级小助手 + 动态 Ticket` 主链见
> [data-agent-implementation.md](data-agent-implementation.md)，两者不可混作同一生产方案。

## 1. 结论

Agent Host 应作为上层对话、规划、会话、记忆、Skills、工具治理和报告编排层；SQLBot 应作为受治理的 NL2SQL 执行引擎。两者最合适的关系不是替换或复制，而是 Agent Host 通过 MCP 调用 SQLBot，SQLBot 继续负责数据源选择、Schema/RAG、SQL 生成、安全校验、权限重写、执行和基础图表结果。

SQLBot 当前源码无需改动即可提供这些业务能力。它的 MCP 服务位于独立端口（本机为 `http://127.0.0.1:8001/mcp`），采用旧式 HTTP+SSE 传输。原始 Agent Host 清单仅接受 `http`（Streamable HTTP）和 `stdio`，所以不能只加一段原始配置直接连接；本测试副本增加 `sse` 传输后，Claude Agent SDK 已成功连接并发现 7 个 SQLBot 工具。

## 2. 已验证的调用关系

```text
用户 / Agent Host Web / AG-UI
  -> Agent Host Turn、Session、Workspace、Memory、Skill、权限与工具预算
  -> Claude Agent SDK MCP Client（SSE）
  -> SQLBot MCP Server :8001/mcp
  -> mcp_start（登录/恢复令牌并创建 SQLBot chat_id）
  -> mcp_ws_list / mcp_datasource_list / mcp_model_list（按需发现）
  -> mcp_question（question + chat_id + token + datasource/model options）
  -> SQLBot 固定问数链
       数据源选择 -> 术语/Schema/样例召回 -> SQL 生成
       -> SQLGlot/只读/表权限/字段权限/行权限
       -> 查询执行 -> 数据/SQL/图表/日志
  -> MCP 工具结果
  -> Agent Host 汇总、追问、规划、报告或继续调用工具
```

已验证事实：

- SQLBot MCP 初始化成功，协议握手可用。
- Claude Agent SDK 以 `sse` 传输连接成功，发现 `access_token`、`mcp_start`、`mcp_ws_list`、`mcp_datasource_list`、`mcp_model_list`、`mcp_question`、`mcp_assistant` 七个工具。
- 使用管理员账号创建了 SQLBot 会话，读取到“默认工作空间”和“飞书同步OSS情况数据”数据源；验证输出未打印 token 或密码。
- Agent Host 健康检查、OpenAPI 和 Swagger 可用；`sqlbot` Workspace 配置校验通过。

## 3. 当前兼容性判断

| 项目 | 当前状态 | 判断 |
|---|---|---|
| SQLBot 作为 MCP Server | 已有，旧式 SSE | 可复用，不改 SQLBot |
| SQLBot 问数工具 | 会话、空间、数据源、模型、问数、助手 | 满足基础 NL2SQL 调用 |
| Agent Host MCP Client | 原始源码支持 Streamable HTTP、stdio | 与 SQLBot SSE 传输不直接匹配 |
| Claude Agent SDK | 原生支持 `sse` | Host 小改即可直连 |
| 身份凭证 | SQLBot 工具参数携带账号/密码或 token | POC 可用，生产不合格 |
| 会话关系 | Agent Host session 与 SQLBot chat_id 相互独立 | 需持久化映射和恢复策略 |
| 流式协议 | Host 使用自身 SSE/AG-UI；SQLBot 问数可流式 | 需定义事件转换与终态语义 |
| 错误契约 | SQLBot MCP 输出 schema 过宽，部分业务错误可能包在成功响应 | 需适配层归一化 |

因此：

- “SQLBot 不改源码能否支持”：能支持业务调用和 MCP 工具调用。
- “原始 Agent Host 不改源码能否直接连接”：不能，传输类型校验缺少 `sse`。
- “只改 Agent Host/部署配置、不改 SQLBot 能否落地”：能。本 POC 已验证此路径。

## 4. 落地所需基础能力

### Agent Host

1. MCP SSE 传输：在 Workspace manifest、URL 安全校验和运行时解析中支持 `type: sse`。
2. 凭证代理：凭证只存服务端密钥库；模型、Prompt、SQLite、日志和前端均不可见密码/token。
3. 会话映射：持久化 `host_session_id -> sqlbot_chat_id + workspace + datasource + model`，支持恢复、过期和重新登录。
4. 工具治理：按 Workspace/角色限制 SQLBot 工具，设置超时、并发、重试和每轮调用预算。
5. 事件适配：把 SQLBot 流式步骤、SQL、数据、图表、错误和终态转换为统一的 Host/AG-UI 事件。
6. 受控 Planner：复杂问题拆成澄清、取数、校验、分析、报告；SQL 生成和执行仍交给 SQLBot。
7. 可观测性：贯通 request_id/trace_id，记录阶段耗时、工具错误、SQLBot chat_id、token 用量和结果规模，敏感字段脱敏。
8. 结果与报告：保存 SQL、数据证据、图表引用、口径和生成报告的血缘；支持可恢复的长任务。

### SQLBot

1. 稳定的 MCP operationId、输入/输出 JSON Schema 和版本声明。
2. 面向服务集成的认证方式；至少支持短期 token 刷新，推荐服务账号/OAuth 客户端凭证或受限 API Key，避免工具参数传密码。
3. 明确的会话生命周期、超时、取消、幂等 request_id 和重复提交行为。
4. 稳定的非流式结构化结果，以及有版本的流式事件 schema。
5. 标准错误码与 HTTP/MCP error 对齐，不能把失败混入 HTTP 200 的普通文本。
6. capability/readiness 接口，覆盖主 API、MCP、PostgreSQL、模型、Embedding 和图表服务。
7. 返回可引用证据：最终 SQL、字段定义、数据截断信息、数据源/模型/Prompt/Schema 版本、权限处理摘要。
8. Web、导出和 MCP 之间一致的数据权限与审计策略。

## 5. 推荐开发计划

### Phase 0：契约与安全基线（1–2 周）

- 固化 Agent Host ↔ SQLBot MCP 版本、7 个工具 schema 和错误样例。
- 把本 POC 的 SSE 支持产品化并补齐回归测试。
- 实现服务端 CredentialProvider，禁止账号、密码和 token 进入模型上下文。
- 定义 Host session 与 SQLBot chat 的映射、过期、重建和清理策略。
- 建立 `/ready` 聚合检查和最小端到端 smoke test。

出口：连接、登录、列空间/数据源、创建会话、非流式问数全部自动化通过；日志中无凭证；SQLBot 不改源码。

### Phase 1：稳定问数闭环（2–4 周）

- 实现 SQLBot MCP 结果适配器，统一成功、业务失败、传输失败和超时。
- 打通 Host SSE/AG-UI 的阶段事件、取消和断线恢复。
- 增加 request_id、最大结果行数/字节数、超时、并发和重试预算。
- 建立单表、多表、权限拒绝、错误 SQL、长查询、token 过期回归集。
- 对 SQLBot 的执行错误执行最多两次受控重试；仅在错误可修复且不扩大数据权限时进行。

出口：核心集执行正确率可度量；重复请求不产生不可控重复任务；取消和超时可回收；错误不以成功终态隐藏。

### Phase 2：分析编排与澄清（4–8 周）

- 在 Agent Host 增加 intent/slot、歧义候选和用户确认状态机。
- 引入有预算的 Planner/任务 DAG，限定只能使用已授权 SQLBot 工具和只读分析工具。
- 建立指标语义层引用方式，避免 Host 与 SQLBot 各自维护冲突口径。
- 支持多次取数后的交叉校验、异常/归因/下钻和证据化结论。
- 增加离线金标评测：数据源、表、字段召回，SQL 执行正确率，回答证据一致性。

出口：复杂问题可展示计划；缺槽时先澄清；结论可回溯到 SQL 和数据；工具预算可审计。

### Phase 3：报告与生产治理（8–12 周）

- 实现叙事报告模板、图表和数据快照、审批/人工确认点。
- 贯通分布式 trace、P50/P95、成本、成功率、SQL 耗时和容量看板。
- 完成密钥轮换、最小权限、审计留存、备份恢复和升级兼容测试。
- 仅在单 Agent 编排稳定后，再评估规划、取数、分析、审核、报告多 Agent 拆分；不把多 Agent 作为第一阶段目标。

出口：主要旅程可重复自动验收；发布有质量/时延/成本/安全门禁；报告中的事实、推断和假设清晰分离。

## 6. 验收用例

1. MCP 握手与工具发现：必须连接成功且工具集合符合锁定快照。
2. 认证安全：模型输入、事件、日志、数据库均不出现 SQLBot 密码和 token。
3. Workspace/数据源权限：不同用户只能看到其 SQLBot 权限允许的资源。
4. 单表与多表问数：返回可执行 SQL、结构化数据和明确终态。
5. 高风险 SQL：写操作和越权访问 100% 被 SQLBot 拦截，Host 不绕过。
6. 错误与重试：区分可修复 SQL 错误、权限错误、模型错误、超时和传输错误。
7. 多轮恢复：Agent Host 重启后可恢复映射；SQLBot token/chat 失效时可安全重建。
8. 大结果与取消：触发限制时给出可理解提示；取消后释放 SQLBot 和 Host 资源。
9. 证据一致性：报告结论能定位 SQL、数据快照、时间范围和指标口径。
10. 性能：分别记录 Host 编排、MCP、SQLBot 生成、SQL 执行和报告阶段的 P50/P95。

## 7. POC 运行说明

- Agent Host：`http://127.0.0.1:18080`
- Swagger：`http://127.0.0.1:18080/docs`
- SQLBot Web/REST：`http://127.0.0.1:18000`
- SQLBot MCP：`http://127.0.0.1:8001/mcp`

当前 `.env` 中的大模型地址和密钥是不可调用的占位值，因此服务、Swagger、Workspace 和 MCP 连接可验证，但完整自然语言 Agent turn 需要替换为真实的 Anthropic-compatible endpoint、API Key 和对应模型名。
