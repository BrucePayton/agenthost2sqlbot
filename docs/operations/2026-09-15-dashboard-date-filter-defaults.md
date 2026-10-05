# 默认日期筛选被误当必填：修复与验收

## 结论

问题客观存在。会话 `5a9782ca-a2a0-4c61-b1d7-3f5fd89d8d90` 的创建请求已包含“订单成交日期＝昨日”，但 Agent 前端编译器仍要求订单创建日期，返回 `REQUIRED_FILTER_MISSING(339)`。

这是工具对元数据标记的误读。原生仪表盘将 `requiredCondition` 用于默认带出筛选，字段级不可省略条件使用独立的 `required` 标记。有效时间约束要求一个符合业务口径的日期条件，不要求始终使用默认日期。

本地已修复编译器、catalog 映射与模型指引。未改数据集；线上发布与实际工具重放尚未验收。

## 会话证据

从实际工具回执提取的字段如下：

| 字段 | ID | required | requiredCondition |
| --- | --- | --- | --- |
| 订单创建日期 | 339 | false | true |
| 订单成交日期 | 408 | false | false |
| 成交订单金额 | 542 | false | false |
| 修正来源类型 | 11078 | false | false |

`09:51:27` 的 `dashboard.apply_widget_spec` 请求创建指标卡：指标 `542`，日期 `408 = last_day`，来源 `11078 = 官网`。拒绝回执为 `layer: page`，消息为“数据集要求必须提供筛选：订单创建日期(339)”。模型随后将其解释成后端硬性约束，但回执和代码均显示拦截发生在 Agent 前端编译器，尚未进入保存/查询阶段。

原生代码证据：

- `DashboardV2/components/ConfigPanel/index.tsx`：仅切换数据集时自动注入 `requiredCondition` 默认筛选；打开已有组件时保留已有配置。
- `DashboardPanel/autoFilterInjection.ts`：默认行不带 `required:true`。
- `DashboardV2/utils/requiredFieldFilters.ts`：真正的强制条件基于 `required:true`。
- `DashboardV2/utils/warehouseTopicValidation.ts`：主题等数据集有日期字段时，要求有效时间筛选，不锁定某个字段 ID。

额外添加“创建日期近 30/90 天”会排除较早创建、昨日才成交的订单，改变用户要求的成交金额统计范围。因此不应让用户用一个任意创建日期窗口来满足错误约束。

## 修复范围

1. **FE 编译器**：只对 `required=true` 校验字段级必填；保留 `requiredCondition` 原始标记。仍要求有效时间条件，允许业务日期替换默认日期。编译结果保留真正必填字段的 `required:true`，沿用原生必填值判定。
2. **Data MCP**：`requiredFilters` 改为依据 `required` 生成；原先错误地依据 `requiredCondition` 生成。默认项仍保留在字段摘要中，两类标记各自透传，不修改上游数据集。
3. **Host 契约与 Skill 引用文档**：明确默认与必填的区别；按成交/创建等业务语义选择日期，不额外叠加无关范围。与上一项枚举修复保持一致，来源枚举仍需要真实 value 回执。

无需新增工具、参数、服务或依赖。FE/Host 的唯一源契约已按现有流程重新生成，Host embed 已重建。

## 验收

在配套版本的新会话中输入“在当前仪表盘新增昨日官网成交回收金额”，若模型询问订单金额/物品金额，选择订单金额。

| 检查 | 预期 |
| --- | --- |
| catalog 中创建日期 339 | `required:false, requiredCondition:true`，不进入真正必填 `requiredFilters` |
| 指标卡配置 | 指标 542；日期筛选 408＝昨日；官网来源使用枚举接口返回的实际 value |
| 默认日期 | 不自动添加 339 的近 30/90 天条件，不追问无关创建日期范围 |
| 已有卡片更换日期 | 最终配置只保留本次指定日期，不残留被替换的默认日期 |
| 缺少所有日期条件 | 仍返回 `TIME_FILTER_REQUIRED` |
| 字段实际标记 `required:true` | 仍不能省略，未填写时返回 `REQUIRED_FILTER_MISSING`；合法数字 0 可通过 |

“官网”的实际生产存储值仍需登录态下的原生枚举回执确认，测试中的字符串是用于验证流程的 fixture。

## 本地验证记录

- 原会话参数回归先失败，精确复现 `REQUIRED_FILTER_MISSING(339)`；修复后创建成功且配置只含日期 408 与来源 11078。
- FE 编译、控制器、时间/必填守卫及上一项枚举回归：114 条通过。
- Data MCP 真实 HTTP 边界映射、相邻元数据、时间习惯与目录搜索回归：17 条通过。
- Host runtime 与契约回归：190 条通过。
- `check:agent-startup` 通过：FE 258 条、Host 90 条、真实双 Origin iframe 2 条；覆盖配对契约、实际 embed、Bridge READY 和工具回执。
- FE 受改 TypeScript 文件未新增类型或 TSLint 诊断，三个仓库 `git diff --check` 通过。
- 原生图表交互回归：113 条通过，1 条存量富文本测试因旧 jsdom 缺少 `ShadowRoot` 失败；联动与明细同时开启的菜单用例通过。该测试及渲染代码本次未修改。

日志：`/private/tmp/date-filter-incident-red.log`、`/private/tmp/date-filter-frontend.log`、`/private/tmp/date-filter-catalog-red.log`、`/private/tmp/date-filter-catalog.log`、`/private/tmp/date-filter-host.log`、`/private/tmp/date-filter-agent-startup.log`、`/private/tmp/date-filter-interaction.log`。

## 发布边界

需要配套发布 Davinci FE、Agent Host 及 Data MCP，使校验、目录回执和工具指引一致。使用数据库导入 Skill 的环境还需按现有流程更新对应引用文档；本次没有修改远端 Skill 快照。

本地验证没有使用真实用户登录态重放线上保存/查询，因此不能将本地测试通过视为生产数据查询已验收。
