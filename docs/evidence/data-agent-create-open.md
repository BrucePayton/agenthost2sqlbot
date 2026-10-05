# 问数智能体创建与开启证据台账

范围：线上报告 2026-09-21 更新版“创建与开启”；这是首个 Draw.io 验收样板。

| ID | 时序功能 | 主责 | 目标接口/对象 | 当前证据 | 支持度 | 技术方案/缺口 |
|---|---|---|---|---|---|---|
| CO-01 | 选择 1–5 个数据集、填写名称简介 | 前端 | 创建表单 | 前端仓库不在本轮代码范围 | 未验证 | 保持外部边界，不用 Host Mock 代替 |
| CO-02 | 创建智能体 | Data MCP | `POST /data-agents` | Java MCP `tools/list` 无 REST/智能体工具证据 | 不支持 | `DM-GAP`；外部认证网关、参数校验、幂等 |
| CO-03 | 获取可信创建者 | Data MCP 网关 | Davinci 登录态/obId | 当前 MCP 仅接受工具参数 `obId`，未发现独立 HTTP 鉴权 | 不支持生产直连 | 禁止浏览器自报 obId；网关解析并覆盖身份 |
| CO-04 | 校验全部数据集权限 | Java ACL MCP | `access.check_resources` | `159358` 单/双数据集和字段子集成功；`149502` 拒绝并隐藏 Schema | 已验证 | 非空 `requiredFilters` 正样本仍待补充 |
| CO-05 | 写草稿和绑定 | Data MCP | `data_agent`、`data_agent_dataset` | 当前工具和两侧仓库均无实现证据 | 不支持 | `DM-GAP`；表、事务、唯一约束和审计 |
| CO-06 | 同步 SQLBot Assistant | Data MCP | Assistant 管理客户端 | 当前 Java MCP 工具列表无此能力 | 不支持 | `DM-GAP`；幂等、重试和补偿 |
| CO-07 | 创建 type=1 Assistant | SQLBot | `POST /api/v1/system/assistant` | 源码、OpenAPI 和登录后列表验证通过 | 代码支持 | 使用受控 Workspace 管理服务账号 |
| CO-08 | 保存 Assistant 映射 | Data MCP | `sqlbot_assistant_id` | 无目标存储 | 不支持 | 与草稿状态机同事务/补偿边界设计 |
| CO-09 | 发布/禁用/删除 | Data MCP | 生命周期 REST | 无领域状态机/API | 不支持 | 红色粗虚线；删除需 tombstone 和对账 |
| CO-10 | 读回与可见列表 | Data MCP | detail/list-visible | 无 API | 不支持 | 发布成功必须读回；Data MCP 是可见性权威 |
| CO-11 | 选择智能体并新建 Session | 前端/Host | `agentId` + Session | Host Session 已支持 | 部分支持 | 前端只提交 agentId，不提交可信业务快照 |
| CO-12 | 可信 bootstrap | Host | agentId/context | Session 模型没有目标 agentId 契约 | 不支持 | `AH-GAP`；服务端读取或校验签名快照 |
| CO-13 | 装载问数 Workspace/Skill | Host | Workspace/CLAUDE.md/Skill | 基础设施存在，目标模板不存在 | 部分支持 | 只做 Host 边界内的会话适配 |
| CO-14 | 首问才创建 SQLBot Chat | Data MCP/SQLBot | `assistant/start` | SQLBot 支持；Data MCP Chat 映射不存在 | 部分支持 | 打开阶段不得调用 SQLBot |

## 当前落地结论

创建与开启不能在现有 Host、SQLBot 和当前 Java MCP 中端到端落地。SQLBot Assistant 管理接口可复用；智能体 REST、主数据、生命周期、可见范围和 Assistant 同步属于 Data MCP 的新增职责。Host 不应新增智能体主数据表，只需要可信 `agentId` Session/bootstrap 与问数上下文适配。

## 样板图规则

- 真实 Java MCP 与逻辑 Data MCP 使用不同泳道或明确的物理边界。
- 已运行验证为绿色；源码支持为蓝色；部分支持为橙色。
- 缺失能力在实际时序位置使用红色加粗虚线。
- Mock 未启用，不作为运行节点，也不提高生产支持度。
