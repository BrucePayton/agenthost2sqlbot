# 订阅推送全链路调整与本地验证（2026-09-10）

## 范围与边界

目标是把用户需求快速变成原生表单中可检查、可修改的配置，覆盖个人/协同空间订阅中心、同步空间中心以及全部仪表盘推送/预警快捷入口。候选修复只是其中一部分。

- 保留 `space.message_rule.*` 六个工具，不新增状态存储或第二套领域表单。
- 规则归属与数据来源分开：空间订阅中心建空间规则；所有仪表盘快捷入口建个人规则。
- 用户和 Agent 共用原生 Store；切步不作为业务修改，后续操作读取用户当前编辑结果。
- 配置默认不保存、不取数、不试发。原生手动新建保存启用；Agent 保存经过原生确认、默认停用。未知保存结果不重试创建。
- 代码涉及 `davinci` 的 `codex/collaborative-space-agui-implementation` 与 `claude_workspace_mvp` 的 `main`；未修改数据 MCP，未提交/推送，未操作 UAT 数据。

## 实际调整

| 层次 | 调整 | 复用点 |
| --- | --- | --- |
| 七步模型 | 根据发送方式决定步骤；初始场景不再阻止后来补入查询、预警条件、分发步骤 | 原生 STEP_DEFS、步骤裁剪与 DraftOperations |
| 字段契约 | 字段返回筛选操作符、动态日期表达式和参数个数；查询输出返回条件操作符。错误包含位置及允许值 | FilterConditionEditor 与原生条件字段计算 |
| 候选 | 仪表盘过滤嵌入网页/删除对象，保留个人/空间/模板来源；字段/仪表盘/图表可分页；字段支持角色及员工资格筛选 | 原订阅候选接口和原生图表/AI 解读资格函数 |
| 初始配置 | start_draft 可同时填入已知时间、发送方式、名称；依赖查询输出的操作仍在拿到引用后 apply | 相同原子操作器，模板锁保持有效 |
| 人机接续 | 写回执省略重复查询描述；get_context 保留完整现状，review 返回实际配置与查询以核对业务 | 原生 Store 和既有引用注册表，不新增“影子草稿” |
| 执行效率 | 30 秒、按用户/数据集隔离的有限字段元数据缓存；同一范围连续三次空搜索后阻止继续猜词，但仍允许浏览和资格筛选 | 原 loader 与 Host tool ledger |
| 思考预算 | 隐私脱敏后保留 subscription 工作流标记，使空间页用户接续仍能识别订阅预算；适配器异常不阻止启动 | 原预算选择与运行上下文，不暴露空间 ID |
| 契约校验 | 前端执行已有 not/pattern 约束，禁止互斥输入混填、非法链接和按钮宽度；读回兼容原生未完成/历史值，新增写入受可执行词表约束 | 既有 FrontendToolRegistry |
| Skill | 已有表单、当前来源、未知业务来源分路径；集中搜字段、合并已知操作、默认静态复核，按实际回显逐项核对原需求 | 原 configure-subscription-rule 及引用文档 |

查询过滤的动态日期使用原生六个表达式。例如“昨天”为 last_day、“最近 N 天”为 last_days；比较指标 contrast 的表达式是另一套契约，不混为一谈。修正了测试样例把 last_week 当查询过滤表达式的问题，并明确样例统计最近 7 天。

正式契约、Davinci 生成物、Host 生成物与实际提供的 embed 已同步。最终摘要：`8e58b50d277f4cd98862c523844e9063697207c1ae1abe0863f57677d3265651`。

## 本地验证

- 前端：134 个测试套件、1,424 项通过。包括六工具链路、共享表单、人工修改接续、条件及分发、内容、个人/空间归属、保存边界、字段能力、分页、权限引用、运行时和契约校验。
- Host：219 项通过（runtime、ledger、正式契约、订阅模拟链路、Skill 发布、工具目录与动态加载）。
- 后端：MessageRuleControllerDashboardsTest 4 项通过；同时编译 1,960 个生产 Java 文件与 332 个测试文件。
- 启动专项：契约同步检查、15 项同步脚本测试、7 项 bootstrap 测试通过；Host `build:agui` 通过。
- Skill 结构检查与两个工程的 `git diff --check` 通过。
- 针对变更生产 TypeScript 文件的语义诊断，与 HEAD 基线比较未新增诊断；基线 16 项，当前 15 项（修正了 widgets loader 的第三参数签名）。这不是完整前端类型检查通过。

前端复现命令（webapp 目录）：

```sh
./node_modules/.bin/jest --runInBand --no-coverage --silent 'share/containers/CollaborativeSpace/agent|share/containers/CollaborativeSpace/pages/SubscriptionConfigDetail|share/containers/WorkBenchNew/agent/runtime|share/containers/WorkBenchNew/agent/contracts' --testPathIgnorePatterns '/node_modules/' 'markdown/extensions/.*test' 'markdown/MarkdownToolbar.test'
npm run check:agent-startup
```

## 尚未证明的内容

1. 首次扩大回归时，7 个富文本测试因本地缺失 `@tiptap/core` 无法启动；上述绿色前端批次排除了这些测试，不能算作它们通过。没有为本次任务升级或修改依赖。
2. 按用户此前决定，没有真实模型调用测试凭据，跳过真实 Agent 测试。以上使用当前代码、原生状态/操作器与受控服务桩，不是 UAT 验收，也不能证明真实模型遵守预算或达到某个秒级 SLA。
3. 性能机制已落地，收益仍需真实会话测量：首份有业务内容的可编辑配置时间、最终复核时间、模型思考时间、工具/业务请求时间、重复搜索数量，以及是否完整保留用户要求。不能用空白页打开时间或单元测试耗时代替这些指标。
4. 发布时需要配套更新 Davinci 前后端与 Host 契约/embed；Skill 仍需按既有发布流程上线。不能只替换一侧契约。
