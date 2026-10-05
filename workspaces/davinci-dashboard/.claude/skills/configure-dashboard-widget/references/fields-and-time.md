# 字段、图表形状与时间

## 字段选择

- 用 datasetRef 锁定数据集，用 fieldRef 锁定字段；例如 `warehouseTopic:662/27068` 的 `fieldId` 是 `27068`。
- 指标优先比较定义、聚合口径、业务域和必选筛选条件，不仅比较名称。
- 维度必须能表达用户要求的分组粒度；时间字段必须符合口径（成交日期、创建日期、数据日期不可混用）。
- `required=true` 才是字段级必填；`requiredCondition=true` 表示默认带出筛选，可按业务口径替换。旧目录可能把默认项也放入 `requiredFilters`，应核对字段上的两个标记。
- 数据集要求有效时间筛选时，选择符合指标口径的日期。例如“昨日成交金额”用成交日期＝昨日，即可替换默认创建日期；不要额外添加创建日期近 30/90 天，这会改变统计范围。真正 `required=true` 的字段仍需有效条件。

## 查字段实际有哪些取值

目录只给字段的名称和描述，不给真实取值。原生枚举优先用 `dashboard.get_filter_field_options` 的 `datasetUid/datasetType/fieldId/query` 模式；使用 `enumValues.items` 中的真实 value 并保留类型，多个业务候选需澄清。它不占 catalog 额度，亦不要求已有组件。详见 locate-data 的枚举取值说明。

非枚举字段确需观测当前口径下实际取值时，才把字段放进 `dimensions` 用 `dryRun:true` 试跑：

可执行示例：假设最近 7 天做只读 dryRun 探针；正式写入前须由用户或工具确认实际时间范围和 source，不能直接去掉 dryRun。

```json
{"widgetId":"12945","dryRun":true,"spec":{
  "dataset":{"datasetUid":"483","datasetType":"warehouseTopic"},
  "metrics":[{"fieldId":"16198","agg":"sum"}],
  "dimensions":[{"fieldId":"23595"}],
  "filters":[{"fieldId":"16167","operator":"eq","source":"assumed","valueExp":"last_days","value":["7"]}]}}
```

回执 `probe.sampleRows` 按 fieldId 建键，每行就是该字段的一个实际取值（最多 20 行）；
`probe.truncated` 为 true 说明这不是完整枚举。dryRun 不写盘，但需要一个**已持久化的
组件**当载体，新建组件试跑会返回 `unavailableReason`。探针每轮有次数上限，用完会收到
`PROBE_BUDGET_EXCEEDED`——每次只改一个变量，别拿它穷举。

## 图表最小形状

| 图表 | chartType | 指标 | 维度 |
|---|---:|---:|---:|
| 指标卡 | 2001 | 1 | 0 |
| 柱状图 | 3001 | 至少 1；分组时恰好 1 | 1 个横轴；单指标可加第 2 维分组 |
| 折线图 | 4001 | 至少 1；分组时恰好 1 | 1 个横轴，通常为时间；单指标可加第 2 维分组 |
| 饼图 | 5001 | 1 | 1 |
| 表格 | 1001 | 指标或维度至少一项 | 可选 |

原生支持分组的柱/线/面积图中，`dimensions[0]` 是横轴，`dimensions[1]` 是分组系列。
例如近 30 天各战区成交额趋势：`metrics:[成交额]`、`dimensions:[订单创建日期,战区]`。
两个维度配多个指标、超过两个维度或不支持分组的非表格图表会返回 `CHART_SHAPE_INVALID`，
不会写入部分配置；表格和透视表保留原有多维列语义。分组图只能按横轴或指标排序，不能按
第 2 个分组维度排序。改回单维度会清除旧分组，不会继续按旧字段拆系列。

## 时间表达（valueExp 一律配 operator "eq"）

| 用户说法 | filters 写法 |
|---|---|
| 近 7 天 | `{"fieldId":"<日期字段>","operator":"eq","source":"assumed","valueExp":"last_days","value":["7"]}` |
| 近 30 天 | 同上，`value:["30"]` |
| 昨天 / 今天 | `valueExp` 用 `last_day` / `current_day`，`value` 留空 |
| 本周 / 上周 | `valueExp` 用 `this_week` / `last_week` |
| 本月 / 上月 | `valueExp` 用 `current_month` / `last_month` |
| 固定区间 | `{"operator":"between","value":["2026-07-01","2026-07-31"]}` |
| 某一天 | `{"operator":"eq","value":["2026-08-17"]}`（不带 `valueExp`） |

时间快捷方式由 Davinci 在运行时解释，不把客户端当前日期硬编码成范围，除非用户明确要求固定区间或某一天（此时用上表最后两行，直接写日期，不带 `valueExp`）。

## 同环比可用性

- 时间筛选为 `last_days`（最近 N 天）时：可用 日环比 `dayChain`、周同比 `weekSame`、年同比 `yearSame`；**月环比 `monthChain` 不可用**。
- 其他时间表达的可用集合以写入回执为准：用户点名的同环比直接提交一次写入，若不兼容，错误会列出当前可用口径（如「可用的是：日环比、周同比、年同比」），把列表转告用户选择即可，**不要在写入前长篇推演兼容性**。
- 用户只说「日环比/月环比」未指明差异率或差值时，默认差异率 `diffRate`，在完成报告中注明。
