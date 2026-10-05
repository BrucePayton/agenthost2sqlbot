---
name: locate-data
description: Find datasets, metric locations, field definitions, enum values and data access. Does not write configuration or interpret numbers.
---

# Locate Davinci Data

用常驻 davinci_data 的 catalog/analytics/access；不搜工具、不编造口径。

## 发现位置

1. `catalog.search_datasets` 用用户业务词搜索，复用 matchedFields 的名称、定义和 fieldRef。要全量时按 nextCursor 翻页，参数保持一致；coverage 只代表该关键词和权限范围，检查 issues。businessDomains 只接受组织系统名，不填业务品类。
2. 对命中数据集用 `catalog.list_dataset_usages` 读关联报表。它是数据集级候选，不证明某字段被使用；只有真实组件配置中的 datasetRef + fieldRef 匹配，才能说“确认包含指标”。不要切换页面逐个搜索。
3. 报告核实绑定、关联候选和未查范围；多个匹配直接列出，不先选表、不询问查数日期，不声称全局覆盖。

## 查看口径

1. 已知组件先 `dashboard.get_widget_config`，再用返回的 `refs.datasetRef`、`refs.fieldRefs[].fieldRef` 定向 `catalog.get_dataset_schema`。已有当前有效配置则复用；无法解析的派生字段照实说明，不猜 ref。不调用 resolver、不查数值。
2. 无绑定才搜索；复用 matchedFields.description，缺字段才补 schema/search_fields，不读整表。
3. 精确匹配或可信别名才解释；歧义进入口径检查点。组件时间/筛选与固有定义分开。

## 查询或配置

1. 指定数据集直接定位，集中核验字段用途；小表可读完整 schema，大表按角色/名称/fieldRefs 补查，不换来源。无来源才 `catalog.search_datasets(limit:3,maxMatchedFields:3)`，工具补最多两个 schema 定义；community_usage 不代替口径。开放搜索优先一次搜索，必要时一次定向 schema/search_fields，datasetRefs 最多两个已命中数据集；指定来源按实际缺口补查。truncated=false 仅代表本次过滤范围，不能证明全表缺少其他字段；relation_summary_unavailable 不否定已返回字段。
2. 证据足够就执行或给组合选项提问。查不到可靠候选，说明关键词/权限范围，问有帮助的业务范围或已有看板线索；未定口径不调用 resolver 规划维度/关联，不查数或 dryRun 猜口径，不写依赖该口径的配置；订阅中独立明确的时间或文案可以先准备。
3. 复用已确认的 datasetRef + fieldRef；需要组装需求才调用 `analytics.resolve_data_requirements`。hints 写已识别条件，完整则 isComplete:true；已选指标回传 `metrics[].fieldRef`。同一输入不要再调用 resolve；用户选择或补条件后用新 hints 续跑。
4. `resolved` 用 plan；`permission_required` 按候选调 `access.get_apply_plan`。`needs_relation` / `partial` 保留 plans 与限制；无 plans 如实报告，不擅自换指标。`need_clarification` 复用已有候选直接提问，定义缺口仍计入上方补查额度。日期/requiredFilters 确实缺失且已知时合并问；某候选独有条件选定后问。回执 clarifications / termClarifications 的选项直接用于提问。时间字段区分成交、创建、数据日期；有 community_time_habit 时把社区主时间字段和常用窗口作为默认写进提问让用户确认，从不同筛的日期不同时加。

## 口径检查点

选择单元是数据集 + 指标字段；同表不同口径、跨表同名保留。给中文名和真实定义；FIELD_DEFINITIONS_NOT_RETURNED 表示工具补查后仍缺，按失败或上限说明未取得；不重复补读、不从名称生成口径。

唯一近似候选仍需确认；可信当前绑定或精确业务证据才直接用。仅回答同环比选项不等于确认指标。选项只用 A/B/C，回复冲突只问冲突；不能压掉合理候选或补写未维护定义。选择后保留精确 fieldRef，禁止另选同名字段。

元数据 ACL 可复用，写入仍校验；无权提供申请路径。

## 检索边界

本用户轮次 catalog 共享总预算以运行时公布的余额为准（默认 4 次，含 schema/字段/数据集检索），不为用完额度继续探索。用满仍不完整就交付已查结果和未查范围，不把截断当不存在，不伪装成用户缺条件。相同条件不重复调用。

口径确认后指标与维度分散时：一次 search_fields 验证已有候选是否单表可用。订阅可多查询并列、按已证实字段映射；Widget 不支持跨表。

关系查询用 `dataset.marketplace.get_join_relations`，细节见 [tools.md](references/tools.md)。同名不证明关系，unknown 不补成 1:1；跨数据集计算指标（A 表指标 ÷ B 表指标）做不到。

不写入配置、不解读数值；引用原样保留，不索要内部 ID。

## 创建交接

确认创建后交给编辑器工具：先读 get_context 保留手改，回执以 committed 与 validation.valid 为准，打开保存窗口不等于已保存。参数见 [tools.md](references/tools.md)，错误见 [errors.md](references/errors.md)。

## 交给订阅配置

订阅直接用 `space.message_rule.*`，集中核验字段用途、沿用确认引用。目录证据齐备用 bind_dataset_query 精确绑定；已有查询按 queryRef 局部修改，不再找同名来源。各查询时间独立，员工/分组/内容范围须一致。元数据不是查数；多个 Resolver plans 可能是候选或部分覆盖，不能盲目全选，证据齐备不再调用 Resolver。
