# 更新版报告功能真实支持度与技术判断

> 本文的“当前支持度”只评价改造前的两个参考仓库与线上 Java ACL MCP。测试项目随后
> 实现的 POC 能力及其仍未通过的真实联调项见
> [data-agent-implementation.md](data-agent-implementation.md)。POC 不反向证明线上 Data MCP
> 已具备这些能力。

结论日期：2026-09-21。判断对象为只读 Agent Host、只读 SQLBot、本次真实验证的 Java Asset Search MCP，以及更新后的线上设计报告。测试项目和候选 Mock 不计入生产支持度。

## 总结

更新版方案在职责上更集中，但尚不能生产落地。它把智能体主数据、权限、Ticket、逐题字段投影、SQLBot 编排和结果缓存全部放入 Data MCP；当前线上 Java MCP 只验证了资产搜索、Schema、字段子集和显式权限，远未达到该职责。Agent Host 和 SQLBot 均有可复用底座，但不能补足 Data MCP 缺口。

| 功能域 | Agent Host | 当前 Java Data MCP | SQLBot | 结论 |
|---|---|---|---|---|
| 创建/发布/开启 | 有 Session/Workspace；不应持久化智能体 | 无智能体 REST、表和生命周期 | type=1 Assistant CRUD 存在 | 端到端不支持 |
| 数据集/字段选择 | 可承载交互，不是权限权威 | ACL、Schema、字段子集已实测；无 Top40/100 | 可消费动态 Schema | 部分支持 |
| 一次提问 | 有对话、澄清、改写和 AG-UI；无 `data.ask` 适配 | 无 ask/session/Ticket/SSE 编排 | start/question/SSE/record data 存在 | 部分支持 |
| Ticket/Callback/供数 | 可携带可信会话身份 | 全部缺失 | 请求级 Certificate 和回调头透传有源码证据 | Data MCP 阻断 |
| 分析/图表 | 有 Agent/Artifact/前端工具底座 | 无标准化结果层 | 有 chart 事件，不等于目标 chartHint | 部分支持 |
| 权限/安全 | 身份覆盖和工具治理基础存在 | 当前 MCP 接受 `obId` 参数且未发现独立 HTTP 鉴权 | Assistant Token 与服务凭据为强信任 | 不能直接开放浏览器链路 |
| 数据模型 | User/Workspace/Session/Turn | 报告新增全部持久化尚不存在 | Assistant/Chat/Record/Log 已有 | 跨系统模型不成立 |

## 1. 创建与开启

旧分析把 `data_agent` 和 `data_agent_dataset` 放在 Host，已与更新版报告冲突，应删除。新目标链为：前端 → Data MCP REST → `access.check_resources` 复核 → Data MCP DB → SQLBot Assistant → 发布；开启时前端只把可信 `agentId` 交给 Host Session。

前端直接传 Davinci Token 给 Data MCP 是新的外部安全边界，必须补齐认证、CORS、CSRF、限流和租户隔离。Host 不应相信浏览器传来的名称、简介和数据集列表，应按 `agentId` 从可信接口读取或验证签名快照。

## 2. 数据集与逐题字段投影

当前 Java MCP 已证明：`159358` 可取得授权数据集、批量授权、字段授权子集和 Schema；`149502` 无权样本会被拒绝并隐藏 Schema。它没有实现 Top 40/100、字段注释预算、业务术语命中或 `cannot_generate`。

字段投影的正确顺序是：身份覆盖 → 数据集 ACL → 字段 ACL → 必填筛选/日期/粒度保留 → 相关性排序 → Token 预算。`fieldsExposed` 是传给 SQLBot 的候选字段，不能冒充实际 SQL 引用的 `fieldsUsed`。

## 3. 一次提问、Ticket 与回调

SQLBot 的 `X-SQLBOT-ASSISTANT-CERTIFICATE` 可以按请求覆盖 Assistant 固定凭据，回调会把配置中的 Header 原样转发。动态 Ticket 在 SQLBot 侧具有实现基础，但 Data MCP 尚无签发、校验、消费、撤销、重放控制和 Callback 接口。

`assistant/start` 会初始化动态数据源，但 `CreateChat.question` 缺省会被时间戳代替。若字段投影依赖当前问题，首问必须明确采用“带问题创建 Chat”或“start 返回安全最小 Schema、question 再投影”的契约，否则首次回调无法得到可靠的问题上下文。

## 4. SQLBot 结果语义

- `question` SSE 有 `sql`、`sql-data`、chart、finish/error 等事件。
- `sql-data` 是执行成功标记，不包含完整行数据。
- 行数据需以相同 Assistant 身份调用 `/api/v1/chat/record/{id}/data`；源码按 `create_by` 隔离。
- SQLBot 先保存并输出模型生成的逻辑 SQL，之后才把授权基础 SQL 替换成真实子查询并执行。
- 实际执行 SQL只进入执行日志的 `full_message`；普通 SSE 和 ChatRecord SQL不能证明最终 SQL。

因此报告应公开 `logicalSql`，把 `executedSql` 设为可选、待可靠来源验证的字段。直接使用 `/record/{id}/log` 作为业务接口会带来日志结构不稳定和敏感信息泄露风险。

## 5. 权限与安全

- UI 只展示有权数据集不构成安全控制，持久化前仍须服务端复核。
- 当前 Java MCP 未发现独立 HTTP 鉴权且接受调用方提供的 `obId`，只能位于可信服务端边界。
- Ticket 应绑定问题哈希与投影版本；明文问题需加密或短期保存。
- Top100 重试必须保持同一授权快照，不能扩大未授权字段。
- 宽权限 StarRocks/ETL 账号意味着 SQLBot 服务失陷时可能绕过用户范围；需受限账号、查询代理或明确把 SQLBot 定义为可信数据面。
- Data MCP 若持有 SQLBot `SECRET_KEY`，就拥有完整 Assistant Token 签发权，必须按强信任密钥发行方部署和审计。

## 6. 物理实现边界

线上运行的是 Java 版 Office MCP；Python `davinci_data_mcp` 不是线上实例。规划中的“Data MCP”是逻辑业务域，不等于现有端点已经实现全部能力。

实施决策规则：若 Java 服务能承载持久化、SSE 客户端、SQLBot 管理和运维要求，则在 Java 内扩展；否则由 Java 网关继续承担外部认证与权限入口，调用内部 Python Ask Service。不得让浏览器绕过网关直连无独立认证的 MCP/Ask 服务。

## 7. SQLBot 零改码边界

本地源码版本为 `v1.10.1-48-g60e2b022`，报告写 `1.10.2`，必须核对运行镜像 digest。零改码可覆盖动态 Assistant、回调数据源、NL2SQL、执行、SSE 和数据读取；不能自动满足稳定的最终 SQL、标准化 evidence、逐题字段投影策略或 Data MCP 生命周期。

`/api/v1/mcp/mcp_assistant` 使用固定虚拟用户、每次新建 Chat，适合 Spike，不适合作为正式多轮主链。

## 8. Mock 结论

当前不启用 Mock。真实 Java MCP 能力保持绿色；经枚举确认缺失的能力保持红色粗虚线。后续若为契约冻结或故障注入启用 Mock，必须登记到 `evidence/mock-replacement-map.md`，并明确 Java 网关模块或内部 Python Ask Service 的正式替换位置。
