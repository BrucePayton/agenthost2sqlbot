# 线上报告功能基线（2026-09-21 更新版）

来源：《数巢问数智能体 · SQLBot 引擎化设计》  
地址：https://claude.ai/artifact/XRtov56xNhrWMRnBycJsBp  
状态：设计稿，不代表代码或线上服务已经实现。

本文件只冻结更新版报告的真实需求。实现判断见 `report-feature-assessment.md` 及三侧证据文件。

## 状态词

- `明确决策`：报告已经选定的产品或架构规则。
- `目标设计`：需要开发和验收，不能视为当前能力。
- `待验证假设`：必须用源码、接口、部署版本或运行结果确认。
- `暂缓项`：首版不做，但必须保留扩展边界。

## RPT-01 范围与职责

- 类型：明确决策。
- 前端：创建、发布、切换和结果展示。
- Agent Host：会话、澄清、问题改写、分析与图表编排；不持久化问数智能体对象。
- Data MCP：智能体领域、数据权限、Ticket、字段投影、SQLBot 编排、供数、缓存和审计。
- SQLBot：SQL 生成、校验、动态基础 SQL 替换、执行和基础图表建议。
- 成功标准：业务对象、凭据、权限判定和失败补偿只有一个权威责任方。

## RPT-02~05 创建、发布与开启

- 输入：名称、简介、1–5 个 `datasetRef`、可信创建者身份。
- 前端直接调用 Data MCP REST `POST /data-agents`，携带 Davinci 登录凭据；数据集选择器只展示当前用户有权资源。
- Data MCP 仍必须调用 `access.check_resources` 复核全部数据集，不能信任前端筛选结果。
- Data MCP 写入 `data_agent`、`data_agent_dataset`，并调用 SQLBot 管理 API 创建一对一的 `type=1` Assistant。
- 发布、禁用、删除、可见范围和 SQLBot Assistant 同步均由 Data MCP 负责。
- 开启时前端创建 Host Session。可信输入应以 `agentId` 为主；名称、简介和数据集摘要不得仅信任浏览器快照。
- 打开面板时不创建 SQLBot Chat；首次提问时创建并在后续提问中复用。
- 首版“已发布即全员可见”；精细使用范围暂缓，但 Data MCP 必须成为可见性权威。

## RPT-06 一次提问

- Host 根据对话上下文判断是否澄清，并把问题改写为可独立执行的问题。
- Host 调用 `data.ask(agentId, question, sessionKey)`。
- Data MCP 读取智能体记录，签发与用户、智能体、Host Session 和问题绑定的 Ticket。
- 首问调用 SQLBot `/api/v1/chat/assistant/start`，之后调用 `/api/v1/chat/question`；Data MCP 保存 `sessionKey -> sqlbotChatId`。
- SQLBot 每次请求使用动态 Certificate 回调 Data MCP，取得当前问题允许的数据集、字段、基础 SQL 和连接信息。
- SQLBot 完成 NL2SQL 和执行后，Data MCP 消费 SSE，并使用相同 Assistant 身份调用 `/api/v1/chat/record/{id}/data` 读取行数据。
- 终态包括成功、明确错误、取消、超时和 `cannot_generate`。

## RPT-07 数据集与字段选择

- 首版最多绑定五张表，不在提问前做表级裁剪；同一轮将全部绑定表的 Schema 交给 SQLBot。
- Data MCP 在授权字段集合内按问题相关性选取 Top 40 字段。
- 日期、粒度主键、必填筛选字段和命中业务术语的字段必须保留。
- 字段注释截断为 40 个汉字，单表 Schema 预算约 3,000 Token。
- SQLBot 无法生成时允许一次 Top 100 重试，仍失败则返回 `cannot_generate`。
- 权限裁剪必须先于相关性排序；重试不能扩大到未授权字段。
- 报告中的候选字段应记作 `fieldsExposed`；只有解析 SQL 实际引用列后才可记作 `fieldsUsed`。

## RPT-08 SQLBot 接口与模型调用

- 生产主路径使用 SQLBot Assistant REST，不使用 SQLBot 8001 MCP。
- 必要接口：Assistant CRUD、`assistant/start`、`question`、`record/{id}/data`，诊断时才使用 `record/{id}/log`。
- `/api/v1/mcp/mcp_assistant` 每次新建 Chat、固定虚拟身份且能力有限，只用于 Spike。
- SQLBot 模型调用可能包含关键词提取、数据源选择、SQL 生成、子查询替换和图表生成；只有一个数据源时跳过数据源选择。
- SSE 包含进度、SQL、`sql-data` 执行成功标记、图表和终态；行数据不在 SSE 中。

## RPT-09 分析与图表

- SQLBot 提供查询结果和图表配置/建议；Agent Host 负责业务分析。
- 前端通过稳定图表组件渲染，不把 SQLBot 内置 analysis/predict 作为报告主路径。
- 数据、SQL、口径和图表必须共享同一个执行记录与证据引用。

## RPT-10 权限与安全

- 用户身份只能来自可信登录态；不得采用浏览器或模型自报的 `obId`。
- Ticket 建议绑定 `user + agent + session + questionHash + resourceScope + projectionVersion`，TTL 60 秒，并允许覆盖 SQLBot 实际发生的 1–2 次回调。
- SQLBot 不持有用户 Token；Data MCP 是最终用户数据权限裁决者。
- Schema、字段、基础 SQL、行权限和执行权限是不同能力，必须分别验证。
- SQLBot 数据库账号、Assistant Token 签发密钥和管理服务账号必须最小权限、可轮换并脱敏审计。
- 下载和缓存必须重新鉴权，按用户、智能体、Session 和执行记录隔离。

## RPT-11 数据模型

- Data MCP：`data_agent`、`data_agent_dataset`、可见范围、Assistant 映射、`ask_session`、`ticket`、`result_cache`、同步/补偿状态。
- Agent Host：沿用 User、Workspace、Session、Turn；Session 保存或解析可信 `agentId`，不新增智能体主数据表。
- SQLBot：沿用 `sys_assistant`、Chat、ChatRecord、ChatLog，不持久化 Davinci 业务数据集主数据。
- 跨系统 ID 必须有唯一约束、幂等键、版本和删除/禁用规则。

## RPT-12 部署与实现位置

- 当前线上 Data MCP 是 Java 版 Office 服务；已验证实例只提供资产、目录、Schema 和权限类只读工具。
- Python `davinci_data_mcp` 代码不是当前线上运行实例，不能以其代码代表线上能力。
- 报告新增能力需决定扩展 Java 服务，或由 Java 网关承接外部认证并调用内部 Python Ask Service。
- SQLBot 生产只暴露 8000；8001 MCP 和前端端口不进入主业务暴露面。

## RPT-13~15 改动、风险与验收

- Data MCP 是主要开发面；前端和 Host 只实现其各自边界内的适配。
- SQLBot 目标保持源码不变，但必须以实际运行镜像版本验证，而不是只依赖本地源码。
- 当前 SQLBot SSE/ChatRecord 中的 SQL 是基础 SQL 替换前的 `logicalSql`；不能直接声称是最终 `executedSql`。
- 报告目标返回建议调整为 `{logicalSql, executedSql?, columns, rows, chartHint, evidence, fieldsExposed, fieldsUsed?}`。
- 生产验收必须覆盖无权、过期、跨用户、跨智能体、跨 Session、字段重试、断流、重放、补偿和审计读回。

## 总体验收效果

用户创建并发布问数智能体后，可以在 Host 会话中开启并提问；问题经过 Host 澄清，由 Data MCP 在当前实时权限内签发 Ticket、投影字段并编排 SQLBot。最终返回的数据、逻辑 SQL、可选实际执行 SQL、证据和图表来源可追溯。任一权限、回调、执行或流式步骤失败时都有明确终态，且设计稿、Mock、源码能力和线上已实现能力不会相互混淆。
