# 《数巢问数智能体 · SQLBot 引擎化设计》功能链路 Draw.io 规划

## 0. 正式报告来源

- 在线报告：[数巢问数智能体 · SQLBot 引擎化设计](https://claude.ai/artifact/XRtov56xNhrWMRnBycJsBp)
- 报告日期：2026-09-21 更新版
- 报告状态：设计定稿，未实施
- 方案：方案 B，SQLBot 写 SQL
- 本轮已通过本机 Chrome 读取完整 Artifact；命令行直连仍会返回 HTTP 403，因此后续以浏览器可见的 live Artifact 为报告需求基线。
- 更新版报告明确边界：前端直接调用 Data MCP 创建和管理智能体；Agent Host 只负责会话、澄清、问题改写和分析；逻辑 Data MCP 负责智能体主数据、权限、ticket、字段投影、SQLBot 编排、供数和缓存；SQLBot 负责 SQL 生成、校验与执行。

## 1. 目标与约束

本轮目标是对线上报告要求的功能逐项做源码级和运行级核验，并最终形成一份多页 Draw.io，清楚表达每项功能的先后调用链、系统归属、数据对象、权限边界、成功回执和缺口。

硬约束：

- `/Users/a159358/Works/InternalProjects/DataAgent/data-agent-host/claude_workspace_mvp` 只读，不做任何修改。
- `/Users/a159358/Works/OpensourceProjects/SQLAgent/SQLBot` 只读，不做任何修改。
- 允许在 `/Users/a159358/Works/TestProjects/AgentHost2SQLbot` 内增加适配、Mock、测试、文档和 Draw.io。
- 报告中的 Data MCP 是逻辑业务域，不能归因给 Agent Host 或 SQLBot；当前线上实例是 Java 版只读 Asset Search MCP，不能与尚未实现的问数编排域混为一谈。
- SQLBot 保持源码不变；SQLBot 缺失能力必须单独、重点罗列，明确影响、替代路径和所需投入，但不得通过 Agent Host 适配代码或 Mock 伪装成 SQLBot 已具备。
- Data MCP 服务及 `table.search`、`table.describe`、`table.query` 已确认存在，按飞书配置文档接入真实生产网关；这三项不进入 Mock 范围。报告新增扩展能力须继续真实枚举，确认缺失且阻塞验证时才讨论对应 Mock。
- 第一阶段只完成报告 §5“问数智能体创建与开启”样板页。样式和表达经用户验收后，再统一绘制其余页面。

## 1.1 分析顺序（不可颠倒）

1. 逐章拆解线上报告，形成“真实功能点—业务输入—预期效果—成功标准—责任系统—依赖”的需求基线。
2. 对报告中的设计事实、目标能力和假设进行分栏，不能把“设计定稿”直接判为“已经实现”。
3. 完成需求基线后，才进入 Agent Host、SQLBot 和可用 Data MCP 的源码/API/运行核验。
4. 对每项能力给出 `已支持 / 部分支持 / 外部已支持 / 不支持 / 无法验证` 的真实结论。
5. 根据实际支持程度提出技术方案，并把现状、目标、缺口和方案放进对应 Draw.io TAB；不能先画方案再倒推需求。

## 2. 系统边界

后续图中固定使用六条泳道：

1. 用户 / Davinci 页面
2. Agent Host / Claude Agent SDK / AG-UI
3. 外部 Data MCP
4. SQLBot REST 问数引擎（8000）
5. davinci-api / DataMap / abauth
6. StarRocks、Host DB、缓存与审计

Ticket、供数回调、`data.ask` 和小助手同步属于外部 Data MCP 的职责，不另造“报告服务”。第 5、6 条泳道承载 Data MCP 的下游依赖；在取得接口契约、源码或运行证据前不得标记为“已支持”。SQLBot MCP 8001 只作为部署边界备注，不进入报告定义的主业务链。

## 3. 能力状态与绘图规范

每个功能节点必须包含：功能名称、执行系统、接口/工具名、关键输入、关键输出、权限依据和证据位置。

状态样式统一如下：

| 状态 | Draw.io 样式 | 含义 |
|---|---|---|
| 已实现且运行验证 | 绿色实线、浅绿底 | 源码和真实调用均通过 |
| 已实现但尚未联调 | 蓝色实线、浅蓝底 | 有源码/契约证据，缺真实环境验证 |
| 部分支持 | 橙色实线、浅橙底 | 只有部分步骤或契约不完整 |
| 外部能力待确认 | 灰色实线、浅灰底 | 明确属于外部服务，但未取得足够证据 |
| 当前不支持 | 红色粗虚线、浅红底 | `dashed=1;dashPattern=8 4;strokeWidth=3;strokeColor=#D32F2F` |
| Mock 占位 | 红色粗虚线、浅红底，左上角 `MOCK-ID` 与代码路径 | 可用于 Agent Host、Data MCP 或 SQLBot 的隔离验证，不得表示生产已支持 |

连线规范：

- 黑色实线：同步调用。
- 蓝色实线：流式事件、SSE 或 AG-UI 回传。
- 紫色实线：身份、ticket 或授权上下文传递。
- 绿色实线：成功回执及读回验证。
- 红色粗虚线：缺失步骤、不可落地链路或待开发接口。
- 每个写操作必须同时画出“写入请求”和“成功读回”；只有请求没有读回不能标记完成。

## 4. 功能判定方法

每个节点采用六级证据链：

1. 报告需求：确认报告要求的业务动作和成功定义。
2. 静态源码：定位路由、模型、Service、权限校验、状态机和持久化表。
3. API/MCP 契约：确认 operationId、输入输出 schema、错误码和传输类型。
4. 运行验证：使用当前 Agent Host、SQLBot 和可用外部服务执行真实请求。
5. 权限验证：至少验证正常用户、无权用户、过期 ticket 和伪造资源 ID。
6. 恢复验证：验证超时、重复提交、断线、取消和服务重启后的状态。

只有 1–6 中与该节点相关的证据全部通过，才能标记为绿色；仅存在代码不等于真正落地。

### 4.1 逐项核对模板

每个报告功能点在证据台账和 Draw.io 中使用同一条记录：

| 字段 | 内容 |
|---|---|
| 报告定位 | 章节、原始功能点、报告定义的责任系统 |
| 真实业务动作 | 谁在什么条件下执行什么动作 |
| 输入/输出 | API、MCP 工具、事件、数据对象和错误结果 |
| 预期效果 | 用户可观察结果与系统成功标准 |
| Agent Host 现状 | 源码位置、运行证据、支持程度 |
| SQLBot 现状 | 源码位置、运行证据、支持程度及缺失能力 |
| Data MCP 现状 | 真实工具/接口、权限和运行证据；设计目标另列 |
| 落地结论 | 可直接落地、需 Host 开发、需 Data MCP 开发、SQLBot 固有缺口或阻塞 |
| 技术方案 | 最小改造、接口契约、状态机、安全和验收用例 |

### 4.2 报告功能基线清单

在查源码前先完成以下需求拆解；每一组最终对应一个或多个 Draw.io TAB：

1. 总体定位与边界：方案 B、SQLBot 写 SQL；前端直接调用 Data MCP 创建/展示，Host 对话/澄清/分析，Data MCP 持有智能体/权限/ticket/供数/编排，SQLBot 生成/校验/执行 SQL。
2. 系统架构与部署：Host、外部 Data MCP、SQLBot 8000、SQLBot MCP 8001、davinci-api、StarRocks 的网络和信任边界。
3. 创建、发布与开启：1–5 个数据集、Data MCP REST 与持久化、创建者权限、小助手同步、草稿/发布、列表、Session 注入可信 `agentId`、首次提问再创建 Chat。
4. 一次提问：澄清和问题改写、`data.ask`、ticket、assistant/start/question、回调供数、虚拟表、SQL 生成校验执行、SSE、标准化结果和 evidence。
5. 分析与图表：Host 继续分析，SQLBot 只返回 `chartHint`，前端 `panel.render_chart` 展示；明确不使用 SQLBot 内置 analysis/predict 的目标效果。
6. 数据权限与供数：数据集/字段/行权限、基础 SQL、ticket 签发校验、用户/智能体/会话绑定、60 秒有效期、结果缓存和下载边界。
7. 权限与安全：用户令牌不下沉 SQLBot、服务账号边界、assistant token、StarRocks 临时凭据、SQL 只读、网络隔离、日志脱敏和审计。
8. 数据模型：Data MCP 的 `data_agent`/`data_agent_dataset`/session/ticket/cache，Host 的既有 Session/Turn 与可信 `agentId`，SQLBot 的 assistant/chat/record/log 以及跨系统 ID 映射。
9. 生命周期与一致性：修改、禁用、删除、双写、幂等、重试、补偿、超时、断线和恢复。
10. 改造范围与风险：SQLBot 零改码边界；Java Office MCP 与候选 Python Ask Service 的物理实现决策；Host、前端、davinci-api 各自改造项；Spike 和生产验收门槛。

每组先写“报告预期效果”，再写实现证据和技术方案；尚未核验的内容不得提前标为支持。

## 4.3 Data MCP 真实配置与验证基线

参考文档：[Codex 数仓单表 MCP 配置说明](https://atrenew.feishu.cn/wiki/GRMjweq1piRPJHkjnh4c4mBwnfd)。已通过 `lark-cli` 读取文档，后续按以下配置优先验证真实服务：

已确认事实：Data MCP 已存在且目前不需要启用 Mock；启动程序、生产网关、CAS 认证方式和三个公开工具均已有正式文档。运行验证的目的，是补齐当前机器的接入证据、返回 schema、权限行为和限制，不是重新判断服务是否存在。

- 安装：`npm install -g @aihuishoubi/mcp-proxy --registry=https://npm.aihuishou.com/`。
- 传输：Codex/客户端以 STDIO 启动 `mcp-proxy`，由代理连接 `https://bimcp-gateway.aihuishou.com`。
- 环境：`MCP_ENV=prod`。
- 用户缓存目录：`MCP_TOKEN_CACHE_DIR=/private/tmp/aihuishoubi-codex-149502`。
- 认证：首次启动通过公司 CAS 登录；不在配置中手工放 Token，不读取或提交缓存目录内容。
- 最小工具集：`table.search`、`table.describe`、`table.query`。
- 已知限制：仅单表、不接收原始 SQL、仅 `dw/dm/dim/rpt`、预览默认 10 行且最多 100 行、CSV 最多 100,000 行且保留 24 小时、单用户并发 10、查询最长 30 分钟。
- 权限语义：可查看表结构不代表拥有查询权限；敏感字段即使只参与筛选、分组或排序也必须授权。

验证顺序：安装与命令发现 → CAS 认证 → 三工具发现 → `search` → `describe` → 在授权范围内执行最小 `query` → 记录错误码、权限反馈和返回结构。

必须区分两类能力：上述文档已经证明的是“数仓单表 MCP”；报告目标中的 `data.ask`、ticket 签发、SQLBot 小助手同步、供数回调和结果缓存是否由同一 Data MCP 提供，仍需通过真实工具列表/API/schema 独立验证，不能由名称相似推定。

## 4.4 Asset Search MCP 运行验证

2026-09-21 新增端点：`https://asset-search-mcp-office.aihuishou.com/mcp`。

- MCP initialize 成功：`davinci-data-mcp 1.1.0`，协议 `2025-03-26`。
- `tools/list` 成功，共 12 个只读工具；覆盖资产搜索、数据集目录、Schema、字段、显式权限和需求解析。
- `search_data_assets` 使用 `obId=149502` 实测成功；其无权样本返回 `discoverable_but_unauthorized`，无权 Schema 返回 `RESOURCE_NOT_FOUND`。
- 改用 `obId=159358` 后，权限内搜索、单/双数据集 `authorized`、两个字段授权子集和 Schema 读取均验证成功。
- 三个授权样本的 `requiredFilters` 都为空；空集合契约已验证，非空必填筛选正样本仍待补充。
- 工具列表没有 `data.ask`、ticket、assistant 同步、SQLBot callback、SSE 归一化和结果缓存。
- 端点未发现独立 HTTP 鉴权，工具通过 `obId` 入参查询权限；必须由可信 Host/Data MCP 服务端代理并覆盖身份，禁止浏览器自报 `obId`。

该服务纳入架构的“资产发现与权限前置”位置，不把它标记为完整问数编排 Data MCP。详细证据见 `docs/evidence/asset-search-mcp-capability.md`。

## 5. 首个样板：问数智能体创建与开启

### 5.1 待验证的完整业务链

样板页按报告 §5 原始顺序绘制，并拆成“创建/发布”和“开启/进入会话”两条路径。

创建与发布：

1. 用户在数巢前端选择 1–5 个数据集，填写智能体名称和简介。
2. 前端调用 Data MCP `POST /data-agents`。
3. Data MCP 网关从可信 Davinci 登录态解析身份，并校验名称、简介、数据集数量和重复项。
4. Data MCP 调用现有权限能力复核创建者对每个数据集是否有权。
5. Data MCP 从 davinci-api 读取数据集授权；任一数据集无权则拒绝创建。
6. Data MCP 创建 `data_agent` 和 `data_agent_dataset` 草稿记录。
7. Data MCP 发起“小助手同步”，传递 `agentId` 和名称。
8. Data MCP 使用 SQLBot 工作空间管理员服务账号调用 `POST /api/v1/system/assistant`。
9. SQLBot 创建 `sys_assistant(type=1)`；configuration 只保存回调地址、超时、加密和凭证映射，不保存数据源。
10. SQLBot 返回 `assistantId`。
11. Data MCP 保存 `sqlbot_assistant_id` 以及固定回调映射，返回智能体草稿。
12. 用户点击发布；前端调用 Data MCP 发布接口。
13. Data MCP 将 `status` 从 `draft` 更新为 `published`。
14. 读回智能体详情，确认发布状态和 SQLBot assistant 映射后才报告完成。

开启与进入会话：

15. 用户在面板点击“切换智能体”。
16. 前端从 Data MCP 查询当前用户可见且已发布的问数智能体。
17. 用户选择目标智能体。
18. 前端新建 Agent Host Session，只把可信 `agentId` 写入 bootstrap/session context；其余元数据由 Host 服务端读取或校验签名快照。
19. Agent Host 装载问数 Workspace 模板、CLAUDE.md、问数 Skill及可信智能体摘要。
20. 面板进入可提问状态；SQLBot Chat 不在打开面板时创建，而在该 Host Session 首次 `data.ask` 时创建并复用。

相关生命周期分支也进入样板：

- 修改绑定数据集：更新 Data MCP 两张表，不修改 SQLBot Assistant 静态配置。
- 禁用：Data MCP `status=disabled` 后不得出现在可开启列表。
- 删除：Data MCP 删除智能体时级联调用 SQLBot 删除对应小助手；必须有双边回执和补偿状态。
- 第一版已发布即全员可见；精细使用范围字段属于后续缺口，用红色粗虚线表示。

### 5.2 当前初步判断

以下是制作样板前的假设，必须通过后续证据台账确认：

- Agent Host 已具备可信 Session、Workspace、AG-UI、页面导航和工具回执；更新版报告不再要求它持有 `data_agent`、`data_agent_dataset` 或 CRUD/发布接口。
- 当前 Davinci Workspace 是固定的 `davinci-dashboard`；报告要求新增“问数类型 Workspace 模板、CLAUDE.md 和问数 Skill”，尚未正式实现。
- 当前线上 Java Data MCP 只验证了资产、Schema、字段和显式权限；智能体 REST/持久化、小助手同步、逐题字段投影、`data.ask`、ticket、回调供数和结果缓存均未实现。
- SQLBot 已有 `sys_assistant(type=1)` 管理接口和高级小助手回调机制，计划保持 SQLBot 零改码。
- SQLBot 的 MCP 7 个工具不是报告主调用路径；报告主路径使用 SQLBot 8000 端口的 assistant/start、question 和回调接口，8001 MCP 不对外开放。
- “创建问数智能体”与“创建 SQLBot 小助手”必须一对一映射；双写一致性、重试、删除补偿和读回验证属于当前需要投入的能力。
- 第一版“发布即全员可见”是报告决策，不等于已经具备精细智能体使用权限。
- 因此样板图会把 Data MCP REST/数据模型/同步/状态机、Host 可信 bootstrap、精细使用范围和删除补偿等缺失节点放在实际主链位置，并画成红色粗虚线。

### 5.3 样板页布局

- 页面尺寸：A3 横向，六泳道。
- 左到右表示时间顺序，不使用交叉回线表达主流程。
- 上半部分画“创建与发布”，下半部分画“开启与进入会话”；公共的身份、数据集权限、SQLBot 服务账号和审计节点使用共享子流程。
- 每个节点右下角显示状态标签：`LIVE`、`CODE`、`PARTIAL`、`EXTERNAL`、`GAP` 或 `MOCK`。
- 页面右侧固定放置“是否真正落地”判定表，列出入口、接口、权限、持久化、读回、错误恢复和实测结果。
- 页面底部放置证据索引，使用 `AH-xxx`、`DM-xxx`、`SB-xxx`、`MOCK-xxx` 编号。

### 5.4 样板验收点

用户验收时只确认以下内容：

- 系统泳道是否符合真实架构。
- 创建与开启的顺序是否符合报告业务语义。
- 节点粒度是否足够具体。
- 颜色、虚线、字体和密度是否易读。
- “已有、部分、外部待确认、缺失、Mock”是否能一眼区分。
- 缺失能力是否位于正确的链路位置，而不是集中放在图外。
- 每个“完成”是否都具备真实成功回执和读回证据。

未获得样板验收前，不批量绘制后续页面。

## 6. 样板通过后的 Draw.io 页面规划

最终文件暂定为 `docs/agenthost-data-mcp-sqlbot-report-flows.drawio`：

1. `00-图例与系统边界`
2. `01-报告功能基线与预期效果`
3. `02-问数智能体创建与开启`（首个验收样板）
4. `02A-新MCP与动态Ticket验证`（2026-09-21 补充验证页）
5. `03-一次提问的完整取数链路`
6. `04-数据集与逐题字段投影`
7. `05-SQLBot接口-SSE-结果拉取`
8. `06-分析与图表组件链路`
9. `07-数据权限-Ticket-供数接口`
10. `08-权限与安全纵深`
11. `09-数据模型与对象映射`
12. `10-部署边界-Java与Python决策`
13. `11-错误-重试-补偿-恢复`
14. `12-能力矩阵与开发投入`
15. `13-条件式Mock与逐步替换地图`（覆盖 Agent Host、Data MCP、SQLBot）

每个业务 TAB 均分四层展示：`报告目标链路`、`当前真实链路`、`功能缺口`、`可落地技术方案`。SQLBot 缺失节点保留在原始时序位置并同步汇总到 TAB 10，不在 Host 或 Mock 中补画为已实现。

### 6.1 一次提问的取数链路

覆盖：用户问题、身份与上下文、意图/澄清、数据集发现、Schema/字段、权限、ticket、供数或 SQLBot 路由、SQLBot chat/token、NL2SQL、SQL 安全、查询执行、结果裁剪、证据、Host 汇总和终态。

必须明确报告规定的单一主路径：Agent Host 调用外部 Data MCP 的 `data.ask`，Data MCP 签发 ticket 并编排 SQLBot REST，SQLBot 再通过回调供数接口取得已裁决的数据范围。若当前代码还存在其他直连取数路径，只作为“现状旁路”单列，不能混入目标链路。

`datasetRef`、`agentId`、`assistantId`、固定回调数据源 ID 和 `chatId` 的映射关系必须逐一画出；缺失映射以红色粗虚线标记。

### 6.2 分析与图表组件

覆盖：结果数据资格、分析任务、图表推荐、图表 schema、页面组件创建、dataset/field 绑定、预览、保存确认、成功读回、快照、导出和异常/预测。

需要区分：

- SQLBot 生成的图表配置或图片。
- Davinci 页面原生 Widget。
- Agent Host Snapshot Artifact。
- Agent Host 最终回答中的表格、图表和 evidence 引用。

### 6.3 数据权限、Ticket 与供数接口

覆盖：用户身份、Workspace membership、资源 ACL、字段权限、行权限、ticket 签发/校验/过期/撤销、供数请求、限行/脱敏、水印、审计、缓存隔离和下载权限。

权限结论使用交集原则：

```text
最终允许 = Agent Host Workspace 权限
         ∩ 外部 Data MCP / Davinci ACL
         ∩ 问数智能体发布/可见状态
         ∩ 智能体绑定数据集范围
         ∩ Ticket 绑定的用户/智能体/会话/有效期
```

任何一层没有可验证授权时都不得降级为服务账号全权访问。

### 6.4 权限与安全

覆盖：身份来源、令牌保存位置、MCP header/参数注入、Host/CORS、SSRF、SQL 只读、危险函数、表/字段/行权限、工具 allowlist、工具预算、写操作确认、敏感日志脱敏、跨 Session 隔离和审计留存。

### 6.5 数据模型

至少绘制以下对象和映射：

- Agent Host：User、Workspace、Membership、Session、Turn、ToolCall、Attachment、Artifact、Memory。
- Data MCP/Davinci：datasetRef、fieldRef、dashboardRef、widgetId、pageInstanceId、ticket。
- 问数智能体域：`data_agent`、`data_agent_dataset`、`agentId`、`status`、`creator_ob_id`、`sqlbot_assistant_id`、`sqlbot_datasource_id`。
- Data MCP 运行态：`ask_session(session_key, agent_id, user_ob_id, sqlbot_chat_id)`、`ticket(token, user_ob_id, agent_id, session_key, expires_at)`、`result_cache(record_id, columns, rows, created_at)`。
- SQLBot：`sys_assistant(type=1)`、Chat、ChatRecord/ChatLog、Model、固定回调 Datasource；目标方案不创建 `core_datasource/core_table` 业务元数据。

不存在的跨系统主键映射必须以红色粗虚线对象表示。

## 7. SQLBot 缺失能力处理原则

- SQLBot 保持源码和数据库结构不变。
- 逐项核验 assistant 管理、Chat 生命周期、回调数据源、NL2SQL、SQL 校验/执行、SSE、chart hint、权限与审计等报告依赖。
- 不支持或部分支持的能力必须记录：报告要求、当前行为、源码/API 证据、阻断范围、是否存在 SQLBot 原生替代接口、生产风险和建议投入。
- SQLBot 缺口不得通过新增 Host 逻辑或 Mock 来“补齐”后改判为支持；若整体方案需要由其他系统承担，只能标记为架构职责调整，并保留“SQLBot 不支持”的原始结论。
- TAB `10-SQLBot能力与重点缺口` 使用红色粗虚线集中展示，其他业务 TAB 在对应时序位置重复引用缺口编号。

## 8. 跨系统条件式 Mock 策略

Mock 不是只针对 Data MCP；根据链路阻塞位置，可分别使用 `MOCK-AH-*`、`MOCK-DM-*`、`MOCK-SB-*`。默认优先验证真实实现，只有以下条件成立才启用对应 Mock：

1. Agent Host：目标功能尚未实现，但需要先冻结前端/API/状态机契约或验证后续链路；Mock 必须位于独立服务，正式实现仍落回 Host 对应模块。
2. Data MCP：`table.search/describe/query` 使用真实服务，不允许 Mock；仅当 `data.ask`、ticket、小助手同步、供数回调、session/cache 等报告扩展能力经真实枚举后确认缺失或不可用，才可 Mock 对应扩展契约。
3. SQLBot：真实服务不可达、需要稳定复现异常/SSE 场景，或需要展示报告能力缺口对整体链路的影响；Mock 不代表 SQLBot 原生支持，也不能用于规避 SQLBot 零改码约束。
4. 所有系统：启用前必须记录真实失败或缺失证据、Mock 目的、调用方、退出条件和契约测试。

只 Mock 被阻塞的最小系统边界。SQLBot 缺失能力即使存在 `MOCK-SB-*` 演示实现，仍必须保留对应 `SB-GAP-*` 和“不支持”结论；不得借 Mock 改写生产可落地判断。

Mock 仍须使用独立命名空间和端口、响应包含 `mock: true`、不保存凭据、不修改 SQLBot，并以红色粗虚线显示。验收结论分开记录“Mock 测试链路通过”和“生产能力未验证/未实现”。

### 8.1 可替换代码架构

若满足 Mock 启用条件，代码统一放在测试项目的明确边界内：

```text
app/
  integrations/
    data_mcp/
      contracts.py             # Host 侧稳定 Port、DTO、错误模型
      client.py                # Host 侧统一客户端；业务层只依赖这里
      transport.py             # STDIO mcp-proxy / HTTP 调用适配
      config.py                # DATA_MCP_MODE、command/base URL、超时
  data_agents/
    service.py                 # 问数智能体业务编排，不感知 real/mock
    repository.py              # data_agent/data_agent_dataset 持久化 Port
    ask_service.py             # data.ask 与 SQLBot 调用编排
mock_services/
  agent_host/                  # 独立 Host API/事件模拟器，不进入正式 app 包
    main.py
    data_agents.py
    sessions.py
    workspace_context.py
    analysis.py
    chart_events.py
    fixtures/
  data_mcp/                    # 独立进程，保持“外部 Data MCP”系统边界
    main.py                    # Mock MCP/HTTP 服务入口
    permissions.py             # 报告扩展的数据集权限检查契约
    assistant_sync.py          # 小助手同步契约
    ask.py                     # data.ask 编排契约
    ticket.py                  # ticket 签发与校验
    supply.py                  # SQLBot 回调供数接口
    session_store.py           # ask_session 状态
    result_cache.py            # 结果缓存
    mappings.py                # 身份与资源映射
    fixtures/                  # 脱敏表、字段、权限、结果和错误场景
  sqlbot/                      # 独立 SQLBot REST/SSE 模拟器，绝不修改 SQLBot 仓库
    main.py
    assistants.py
    chats.py
    questions.py
    callbacks.py
    sse.py
    fixtures/
tests/
  contract/agent_host/
  contract/data_mcp/           # 同一套契约测试同时验证 real 与 mock provider
  contract/sqlbot/
  integration/agent_host/
  integration/data_mcp/        # 端到端、权限、过期、失败恢复测试
  integration/sqlbot/
docs/evidence/
  mock-replacement-map.md      # MOCK-ID、代码位置、真实接口和替换状态台账
```

替换原则：三类 Mock 都必须作为独立进程存在。Host 正式业务包、外部 Data MCP、真实 SQLBot 与各自 Mock 通过稳定 API/MCP/SSE 契约对接。后续替换只切换 `AGENT_HOST_MODE`、`DATA_MCP_MODE`、`SQLBOT_MODE` 及对应 command/base URL，不改变业务顺序。若真实契约与 Mock 不同，先修正契约和测试，不能在调用方堆兼容分支。

### 8.2 Data MCP 扩展能力 Mock—代码位置—替换目标

`table.search`、`table.describe`、`table.query` 已存在，始终走真实 `mcp-proxy`，不为它们建立 Mock。Asset Search MCP 的资产、Schema、字段和显式权限工具也必须走真实服务。以下仅是报告额外要求的候选扩展；只有真实枚举确认对应能力缺失或不可用时才实现该行：

| Mock ID | 功能与稳定 Port | Mock 代码位置 | Fixture/状态位置 | 后续真实替换位置与目标 |
|---|---|---|---|---|
| `MOCK-DM-001` | 历史 `check_dataset_access(user_ob_id, dataset_refs)` | `mock_services/data_mcp/permissions.py` | `mock_services/data_mcp/fixtures/permissions.json` | 已由真实 `access.check_resources` 替代；禁止实现或启用 |
| `MOCK-DM-011` | `/data-agents` CRUD/发布/可见性 | `mock_services/data_mcp/data_agents.py` | `mock_services/data_mcp/fixtures/data_agents.json` | Java Office 网关领域模块或内部 Python Ask Service；Data MCP 为主数据权威 |
| `MOCK-DM-002` | `sync_assistant(agent_id, name, action)` | `mock_services/data_mcp/assistant_sync.py` | `mock_services/data_mcp/fixtures/assistants.json` | 切换至真实 Data MCP 小助手创建/更新/删除接口；最终调用 SQLBot REST |
| `MOCK-DM-003` | `ask(agent_id, session_key, user_ob_id, question)` | `mock_services/data_mcp/ask.py` | `mock_services/data_mcp/session_store.py` | 切换至真实 Data MCP `data.ask` 编排接口 |
| `MOCK-DM-004` | `issue_ticket(user, agent, session, question_hash, projection, ttl)` | `mock_services/data_mcp/ticket.py` | `mock_services/data_mcp/session_store.py` | 切换至真实 Ticket 签发服务，TTL 60 秒并绑定问题/投影版本 |
| `MOCK-DM-005` | `validate_ticket(token, bindings)` | `mock_services/data_mcp/ticket.py` | `mock_services/data_mcp/session_store.py` | 切换至真实 Data MCP ticket 校验/过期/绑定检查 |
| `MOCK-DM-006` | SQLBot 回调供数 `POST /data-supply` | `mock_services/data_mcp/supply.py` | `mock_services/data_mcp/fixtures/supply_payloads.json` | SQLBot 回调 URL 切至真实 Data MCP；Host 无路由改动 |
| `MOCK-DM-007` | `get_or_create_ask_session(...)` | `mock_services/data_mcp/session_store.py` | 同文件的可重置存储 | 切换至真实 Data MCP `ask_session` 存储与 SQLBot `chatId` 复用 |
| `MOCK-DM-008` | `put/get_result_cache(record_id)` | `mock_services/data_mcp/result_cache.py` | 同文件的可重置存储 | 切换至真实 Data MCP 结果缓存及用户/会话隔离实现 |
| `MOCK-DM-009` | `resolve_dataset_mapping(dataset_ref)` | `mock_services/data_mcp/mappings.py` | `mock_services/data_mcp/fixtures/resource_mappings.json` | 切换至真实 Data MCP 的 dataset、table、固定回调 datasource 映射 |
| `MOCK-DM-010` | `resolve_principal(host_user)` | `mock_services/data_mcp/mappings.py` | `mock_services/data_mcp/fixtures/identities.json` | 切换至真实身份/obId 映射或可信上游身份上下文 |
| `MOCK-DM-012` | Top40/Top100 字段投影与 `cannot_generate` | `mock_services/data_mcp/field_projection.py` | `mock_services/data_mcp/fixtures/field_projection.json` | Java Office 网关模块或内部 Python Ask Service 的授权后字段投影器 |

`MOCK-DM-002/003/006` 即使模拟了与 SQLBot 的交互，也只能表示 Data MCP 编排契约可测试；任何 SQLBot 原生缺口仍引用 `SB-GAP-*`，不得被这些 Mock ID 覆盖。

`MOCK-DM-001` 已被真实 `access.check_resources` 替代，状态为“禁止启用”；保留 ID 和代码位置仅用于历史替换台账。`MOCK-SB-004` 对应的请求级动态 certificate 与回调头透传已有源码证据，若后续实现 Mock，只能用于离线/故障注入，不再表示 SQLBot 生产缺口。

### 8.3 Agent Host Mock 功能—代码位置—替换目标

| Mock ID | 功能与稳定契约 | Mock 代码位置 | Fixture/状态位置 | 后续正式替换位置与目标 |
|---|---|---|---|---|
| `MOCK-AH-003` | Session bootstrap 注入 `agentId` | `mock_services/agent_host/sessions.py` | `mock_services/agent_host/fixtures/sessions.json` | `app/sessions/service.py`、bootstrap/schema 的正式扩展 |
| `MOCK-AH-004` | 装载问数 Workspace、CLAUDE.md、Skill 和数据集摘要 | `mock_services/agent_host/workspace_context.py` | `mock_services/agent_host/fixtures/workspace_context.json` | `app/workspaces/`、`app/instructions/`、`app/skills/` 正式实现 |
| `MOCK-AH-005` | 澄清与独立问题改写事件 | `mock_services/agent_host/analysis.py` | `mock_services/agent_host/fixtures/questions.json` | `app/runtime/` 与 `app/agui/` 中的正式 Agent/事件链路 |
| `MOCK-AH-006` | 结果标准化、evidence 与最终回答 | `mock_services/agent_host/analysis.py` | `mock_services/agent_host/fixtures/answers.json` | `app/runtime/`、`app/agui/` 的正式结果适配与消息输出 |
| `MOCK-AH-007` | `panel.render_chart`/图表事件契约 | `mock_services/agent_host/chart_events.py` | `mock_services/agent_host/fixtures/charts.json` | `app/agui/` 事件适配及 `web/` 正式图表组件 |

Agent Host Mock 只用于冻结接口、UI 和事件顺序；正式 Host 支持程度只由 `app/` 下源码与真实运行证据判定。

### 8.4 SQLBot Mock 功能—代码位置—替换目标

| Mock ID | 功能与稳定契约 | Mock 代码位置 | Fixture/状态位置 | 后续真实替换位置与目标 |
|---|---|---|---|---|
| `MOCK-SB-001` | assistant 创建/查询/更新/删除 REST | `mock_services/sqlbot/assistants.py` | `mock_services/sqlbot/fixtures/assistants.json` | 切换 `SQLBOT_BASE_URL` 至真实 `/api/v1/system/assistant` |
| `MOCK-SB-002` | assistant/start 创建并复用 Chat | `mock_services/sqlbot/chats.py` | `mock_services/sqlbot/fixtures/chats.json` | 切换至真实 SQLBot assistant/start 接口 |
| `MOCK-SB-003` | question 请求与 SSE 事件序列 | `mock_services/sqlbot/questions.py` + `sse.py` | `mock_services/sqlbot/fixtures/sse_events.json` | 切换至真实 SQLBot question/SSE 接口 |
| `MOCK-SB-004` | 携带 `X-Davinci-Ticket` 调用供数回调 | `mock_services/sqlbot/callbacks.py` | `mock_services/sqlbot/fixtures/callbacks.json` | 切换至真实高级小助手回调行为 |
| `MOCK-SB-005` | SQL 生成、校验、替换子查询和执行结果场景 | `mock_services/sqlbot/questions.py` | `mock_services/sqlbot/fixtures/sql_scenarios.json` | 切换真实 SQLBot；若真实能力缺失，继续保留 `SB-GAP-*` |
| `MOCK-SB-006` | `chartHint` 与结果元数据 | `mock_services/sqlbot/questions.py` | `mock_services/sqlbot/fixtures/chart_hints.json` | 切换真实 SQLBot 返回契约；不由 Host 推断为原生支持 |
| `MOCK-SB-007` | 超时、断流、错误、取消和重试场景 | `mock_services/sqlbot/sse.py` | `mock_services/sqlbot/fixtures/failures.json` | 用真实 SQLBot 故障注入/联调替换；用于恢复性验收 |

SQLBot Mock 不得复用真实 SQLBot 数据库、账号或源码。每个 `MOCK-SB-*` 必须绑定一个真实接口证据或 `SB-GAP-*`；前者表示可切换联调，后者表示仅供影响演示、不能生产落地。

### 8.5 Draw.io 与替换台账要求

- 每个 Mock 节点必须显示 `MOCK-ID`、Port 方法、上述代码路径和目标真实接口。
- Mock 节点旁必须有“替换开关”注释：配置键、当前 provider、目标 provider、契约测试编号。
- 同一 Mock 在多个 TAB 出现时复用相同 ID，禁止复制出无归属的匿名 Mock。
- ID 前缀固定表示所属系统：`MOCK-AH-*`、`MOCK-DM-*`、`MOCK-SB-*`。
- `docs/evidence/mock-replacement-map.md` 持续记录：启用原因、失败证据、调用方、Mock 路径、真实适配器路径、契约测试、替换负责人、替换状态和删除条件。
- 替换完成的 Mock 不立即删除：先让 real provider 通过相同 contract suite，再停用配置，最后删除 Mock 实现和 fixture；历史 Draw.io 节点改为灰色归档说明。

## 9. 分阶段计划

### Phase A：报告逐章功能基线（先于源码分析）

- 完整梳理更新版报告 15 个章节、每个流程和每项显式决策。
- 为每项功能写清用户场景、前置条件、输入、步骤、输出、预期效果、异常和成功标准。
- 标注报告中的“现状事实、目标设计、假设、暂缓项和风险”，防止混淆。
- 产出 `docs/evidence/report-requirements-baseline.md` 和 Draw.io TAB `01-报告功能基线与预期效果`。

### Phase B：真实 Data MCP 接入与能力盘点

- 将 Data MCP、生产网关、CAS 和三个单表工具登记为“文档确认已存在”，不规划对应 Mock。
- 保留飞书文档中的旧 `mcp-proxy` 配置作为单表 MCP 证据；报告主链优先以已真实连通的 Java Asset Search MCP 做能力盘点。
- 验证 `table.search`、`table.describe`、`table.query` 及文档声明的权限和限制。
- 进一步枚举是否存在报告需要的智能体 REST/持久化、字段投影、`data.ask`、ticket、供数回调、小助手同步和结果缓存契约。
- 形成 Data MCP 的“文档能力、实测能力、报告目标能力”三栏差异表。
- 只对报告扩展能力的真实缺口提交条件式 Mock 判断；三个单表工具始终使用真实服务。

### Phase C：Agent Host 与 SQLBot 逐项源码/运行核验

- 严格按 Phase A 的功能编号逐项检查，不以仓库现有模块反向定义需求。
- 深入 Agent Host 的身份、Session、Workspace、AG-UI、工具调用、SSE、数据模型、状态机和安全边界。
- 深入 SQLBot 的 assistant、ChatRecord/ChatLog、回调数据源、NL2SQL、执行、流式返回、chart hint、权限和数据模型。
- 对 SQLBot 缺失能力形成独立重点清单，不在 Host 或 Mock 中补位。
- 每项结论必须同时记录静态证据和可行的运行验证；无法运行则明确证据等级。

### Phase D：首个 Draw.io 样板

- 创建 `00-图例与系统边界`、`01-报告功能基线与预期效果` 和 `02-问数智能体创建与开启`。
- 将缺失节点按实际时序插入主链，并设置红色粗虚线。
- 同页分层展示目标、现状、缺口和技术方案，避免把规划链路当成现有链路。
- 输出 PNG/SVG 预览，检查字体、连线、分页和可读性。
- 提交用户验收，只针对样式、节点粒度和业务顺序迭代。

### Phase E：其余功能 TAB 与技术方案

- 按已验收样式绘制一次提问、分析图表、权限安全和数据模型等页面。
- 每页同步维护需求编号、证据台账、支持等级、SQLBot 缺口和验收用例。
- 技术方案以真实支持程度为前提，分别给出直接复用、Host 改造、Data MCP 扩展、前端改造和架构调整；不得隐式修改 SQLBot。
- 统一跨页对象名称、状态颜色和证据编号。

### Phase F：跨系统条件式 Mock（按真实缺口启用）

- 根据 Agent Host、Data MCP 扩展能力和 SQLBot 的真实缺口证据确定最小 Mock 范围。
- 按所属系统分配 `MOCK-AH-*`、`MOCK-DM-*` 或 `MOCK-SB-*`，落实 Port、代码位置、fixture 和真实替换目标。
- 先完成 Port 和 real/mock 共用 contract tests，再接入业务链路。
- Mock 只用于解除隔离验证、前后端并行或故障场景复现的阻塞。
- SQLBot 缺口保持原结论，不通过 Mock 或 Host 逻辑补位。

### Phase G：总体评审和落地结论

- 汇总当前可直接落地、部分支持、依赖外部服务、SQLBot 不支持和必须开发五类结论。
- 给出 Agent Host 改造项、Data MCP 扩展项、前端/davinci-api 项以及 SQLBot 零源码变更下的能力边界。
- 输出开发优先级、依赖关系、工作量和生产验收门槛。

## 10. 待办事项

### 当前：首个样板前

- [x] 确认线上报告正式地址并通过 Chrome 读取完整内容。
- [x] 确认 §5“创建与开启”指问数智能体，不是分析报告文件生命周期。
- [x] 通过 `lark-cli` 读取 Data MCP 配置文档；历史缓存目录使用 149502，授权正样本已按用户要求改用 159358 验证。
- [x] 完整建立 `docs/evidence/report-requirements-baseline.md`，逐章写清功能点和预期效果。
- [x] 为所有功能分配稳定需求编号，并区分设计目标、已知事实、假设和风险。
- [x] 完成新 Java Asset Search MCP 的直连 initialize、tools/list 和最小只读调用；旧 `mcp-proxy` CAS 路径不再作为本轮结论阻塞项。
- [ ] 实测三个单表工具、权限行为和限制；保存脱敏证据。
- [x] 对新 Asset Search MCP 完成 initialize、12 工具枚举、资产搜索、显式权限成功/拒绝、字段子集、Schema 与无权 Schema 隐藏验证。
- [x] 确认 Asset Search MCP 只覆盖资产/Schema/字段/显式权限，不包含 `data.ask`、ticket、供数回调、小助手同步和结果缓存。
- [ ] 枚举并核验报告目标的 `data.ask`、ticket、供数回调、小助手同步和结果缓存契约。（配置文档不能证明存在；待 CAS 后通过真实 `tools/list` 继续核验。）
- [x] 建立 `docs/evidence/data-agent-create-open.md` 证据台账。
- [x] 核验 Host 无智能体领域；更新版报告已将其归 Data MCP，因此不再作为 Host 开发项。
- [x] 核验 Session bootstrap 是否可安全注入并持久化 `agentId`。
- [x] 核验问数 Workspace 模板、CLAUDE.md 和问数 Skill 的现状。
- [ ] 核验外部 Data MCP 的 dataset、字段、查询、权限和 ticket 契约。
- [ ] 核验 Data MCP 是否已有小助手同步、创建者数据集权限校验、ticket、供数和 `data.ask`。
- [x] 核验 SQLBot `sys_assistant(type=1)` 创建、修改、删除与回调配置契约。
- [x] 核验 SQLBot 服务账号、工作空间管理员权限及凭据保存边界。
- [x] 建立 SQLBot 重点缺失能力清单，逐项记录影响和零改码条件下的真实结论。
- [x] 将草稿、Assistant 双写、读回、失败补偿和删除状态机改归 Data MCP。
- [x] 定义开启智能体、新建 Session、注入 `agentId` 和首次问数才创建 SQLBot Chat 的 schema。
- [x] 绘制首版 `00`、`01` 和 `02` 页面。
- [x] 重构 `02A-新MCP与动态Ticket验证` 页面：Agent Host 独立泳道呈现可信上下文、澄清改写、`data.ask`、业务分析和 AG-UI；修正 SQLBot 请求级 certificate 结论，标出 Data MCP 缺口并消除连线/步骤重叠。
- [x] 导出预览并完成布局自检。
- [ ] 提交用户验收并记录修改意见。

### 样板通过后

- [ ] 绘制一次提问取数链路。
- [ ] 绘制分析与图表组件链路。
- [ ] 绘制数据权限/ticket/供数接口链路。
- [ ] 绘制权限与安全纵深页。
- [ ] 绘制数据模型及跨系统映射页。
- [ ] 绘制错误、恢复和可观测页。
- [ ] 绘制 SQLBot 能力与重点缺口页，确保未被 Host/Mock 补位掩盖。
- [ ] 汇总红色虚线缺口、技术方案和开发优先级。
- [ ] 不为已存在的 `table.search/describe/query` 实现 Mock，始终通过真实 `mcp-proxy` 验证。
- [ ] 按真实缺口分别评审最小范围的 Agent Host、Data MCP 扩展或 SQLBot Mock。
- [ ] 若启用 Mock，创建 `docs/evidence/mock-replacement-map.md`，保证每个 Mock 都有代码位置、契约测试和真实替换目标。
- [ ] 若启用 Mock，按所属系统执行 `tests/contract/agent_host`、`tests/contract/data_mcp` 或 `tests/contract/sqlbot` 的 real/mock 共用契约测试。
- [ ] 完成最终 Draw.io、预览图和证据索引验收。

## 11. 首个样板启动门槛

线上报告和 Data MCP 配置文档已经可读，不再需要用户提供截图或原文。但在首个样板进入源码结论和方案层之前，必须先完成全报告功能基线，不能只凭 §5 局部内容开始反向推断。

外部 Data MCP 和三个单表工具已确认存在，先按真实配置完成接入及运行验证，不为其创建 Mock。报告扩展能力、Agent Host 或 SQLBot 若缺少源码或实时接口证据，相关节点仍按正确时序标记为灰色 `EXTERNAL` 或红色粗虚线 `GAP`。只有满足第 8 节对应系统的启用门槛时才出现 `MOCK-AH/DM/SB` 节点，且不把 Mock 当成生产实现。
