# 数据检索参数速查

MCP 参数是平铺对象，不要额外包一层 `request`。

- `analytics.resolve_data_requirements`: `{"rawQuery":"近30天各城市成交金额","hints":{"metrics":[{"term":"成交金额","aggregation":"sum"}],"dimensions":[{"term":"城市"}],"time":{"term":"近30天","rangeExpression":"last_days:30"},"isComplete":true}}`——hints 完整并设 `isComplete:true` 可跳过服务端意图模型，避免其超时
- `catalog.search_datasets`: 首次补选择证据用 `{"query":"成交金额","scope":"authorized","limit":3,"maxMatchedFields":3}`，先比较 matchedFields 的定义；口径未定不加入一批拟选维度扩检索。
- 分页：有 `nextCursor` 时原参数不变传 `cursor`；`coverage` 是该关键词的权限范围、上游页码/总数与是否到末页，不是全部资源覆盖。组合词 fallback 仍有界，注意 issues。`CURSOR_STALE` 表示结果/权限变化，可重新从首页查询一次并去重。
- `catalog.list_dataset_usages`: `{"datasetRef":"warehouseTopic:10","limit":10}`。只回当前可访问的 CUSTOM/SPACE 关联候选及 dashboardId、spaceUid；`evidence:dataset_association` 不证明字段被使用。检查 truncated/issues/coverageComplete；V1 等覆盖未建立。不得将关联当作“确认包含指标”。
- 确认指标后：`analytics.resolve_data_requirements` 的 `hints.metrics` 可传 `{"term":"成交回收额","fieldRef":"warehouseTopic:10/12285"}`；完整选择会直接经 ACL 和 schema 验证，未绑定需求继续定位，不因同名字段重新代选。
- 已知组件查定义：`dashboard.get_widget_config {"widgetId":"13090"}` → 复用 `data.refs.datasetRef` 和 `data.refs.fieldRefs` 中 role=metric 的非空 fieldRef → `catalog.get_dataset_schema {"datasetRef":"warehouseTopic:10","fieldRefs":["warehouseTopic:10/12285"]}`。`unresolvedReason` 必须如实报告，不自行拼造派生字段引用。纯定义查询不需要 resolver、查数或日期澄清。
- `catalog.search_fields`: `{"query":"成交金额","datasetRefs":["warehouseTopic:662"],"roles":["metric"],"limit":6}`。澄清补查时 datasetRefs 最多两个已命中数据集；一次调用会读取各自 schema，不能借批量参数隐藏广泛扫描。
- `catalog.get_dataset_schema`（看必填筛选 / 指定字段）: `{"datasetRef":"warehouseTopic:307","fieldRefs":["warehouseTopic:307/12826","warehouseTopic:307/22563"]}`；按角色看时间字段 `{"datasetRef":"warehouseTopic:307","roles":["time"]}`；也可 `"query":"成交订单金额"` + `"limit":10`。默认只回 ≤50 个字段（必填筛选/粒度/时间字段优先）+ 完整 `requiredFilters`、`grainFieldRefs`；响应里 `fieldCount` 是授权字段总数、`truncated` 表示 `fields` 被裁。`required=true` 才是字段级必填，`requiredCondition=true` 只是可替换的默认筛选；旧目录的 `requiredFilters` 可能混入默认项，应核对字段标记，不叠加无关日期。不要无参数调用它来"看全貌"——跨数据集找字段用 `catalog.search_fields`。
- `access.check_resources`: `{"datasetRefs":["warehouseTopic:662"],"fieldRefs":["warehouseTopic:662/27068","warehouseTopic:662/27119","warehouseTopic:662/27121"]}`
- `access.get_apply_plan`: `{"datasetRef":"warehouseTopic:662"}`

## 查询两个数据集的关联维度与关系

AG-UI `dataset.marketplace.get_join_relations`：`{"mainDatasetName":"回收订单分析","auxDatasetName":"上门战区月目标"}`。市场和编辑器页面均可用；只读，不修改草稿。返回名称候选、主表维度、`analysis.uniquePairs/ambiguousGroups/joinKeys/relation` 及字段基数证据。

同名候选按真实名称/来源向用户确认后附 `mainDatasetRef/auxDatasetRef` 重试。默认全部无歧义配对组成一个组合键；要查询指定维度组合，用候选返回的字段 ID 传 `joinKeys:[{mainFieldId,auxFieldId}]`。默认检查完整主表维度（最多 1000），可用 `mainFieldIds` 收窄至最多 100 个。证据列表最多展示 100 项，resolved 的计算范围以 summary 为准；不得把截断当作无公共维度。`analysis.issues` 提示缺少查询变量时可传 `mainFilters/auxFilters:[{fieldId:"$变量名$",varName:"变量名",isQueryVar:true,operator:"eq",value:"实际值"}]`；不猜值，不传普通行筛选。关系是主→辅方向下的原生字段粒度，`unknown` 不能补写为 1:1。

## 想知道某个维度字段实际有哪些取值

已确定字段且元数据为枚举时，优先 `dashboard.get_filter_field_options {"datasetUid":"10","datasetType":"warehouseTopic","fieldId":"410","query":"上门","limit":20}`。`enumValues.items` 是该字段的真实 label/value，写入 value 并保留字符串或数字类型；`query` 可用业务简称检索候选，不自动把简称当存储值。它独立于 catalog 额度，也不要求已有组件。多个合理候选需用户选定；`enumValues.nextCursor` 只翻本次查询返回的候选，`exhaustive:false` 不证明全量覆盖，无匹配或失败时不得猜值。

`FIELD_NOT_ENUM` 表示此字段没有原生枚举语义。只有口径确认后且确需观测实际值，才将维度放入已有组件 `dashboard.apply_widget_spec` 的 `dimensions` 用 `dryRun:true`，读取 `probe.sampleRows`（最多 20 行）。这只是当前查询样本，不是枚举字典；新建组件 dryRun 不探数。

## 知识层回执（data-MCP 蒸馏知识，默认开启）

- `catalog.search_datasets`：候选已按社区用量在 150 条深池上重排，首查 `limit:3` 即可拿到主力表，不必翻页。`candidates[].evidence[]` 中 `kind:"community_usage"`、`source:"distilled_usage"` 只有一句「平台常用数据集，主时间字段 订单创建日期」，不带人数、部门和百分比：用量数据只在服务端排序，不给用户看。同名指标跨多张表时按次序和口径列 A/B/C 让用户选。`clarifications[]`（`{term,definition,options[]}`，issues 含 `AMBIGUOUS_BUSINESS_TERM`）是检索词里的歧义业务词（如 GMV 对应三种口径），提问直接用 options，定义可用 `catalog.search_semantic_assets` 补。
- `analytics.resolve_data_requirements`：`termClarifications[]`（`{term,definition,options[]}`）按整句识别歧义词（如「城市」四种口径），与字段绑定的 `clarification` 合并成一轮提问。
- `catalog.get_dataset_schema`：`dataset.evidence[]` 中 `kind:"community_time_habit"` 形如「主时间字段 订单创建日期，常用窗口 近30天；与 订单成交日期 在现有看板里从不同时筛选」。把它作为默认写进提问让用户确认，不替用户决定；「从不同时筛选」的两个日期不要同时加。
- 知识是 2026-09-11 的快照；没有用量记录的数据集只按文本相关性排，证据缺失不等于没人用。回答里不出现任何人数、部门或占比。

## 交回给调用方的最小结果

定位结果保留 `datasetRef`、完整 `fieldRef`、字段名与真实定义、时间依据和 requiredFilters，供续跑复用。只有写工具参数要求 fieldId 时才取最后一段；不得因转成裸 ID 丢失其所属数据集。


## 数据集关系检查

查询数据集关联维度及关系：进入数据集市场后调用 `dataset.marketplace.get_join_relations`，传两个数据集名称即可；市场和编辑器均可调用，不需要为查询打开编辑器。同名候选先确认 datasetRef；维度歧义需明确 joinKeys。回报关联键组合、主→辅方向、mainFieldIds 对应主表维度与辅表全部维度的计算范围；`unknown` 不能补写成 1:1，同名字段也不能当作公共维度证据。`status=resolved` 时关系已按 summary 的完整粒度计算，truncated 仅说明展示证据被截断；其余情况不得声称覆盖完整。只应用 widget 查询变量，普通行筛选和原始明细唯一性不在本工具计算范围内。用户要求创建关联时再打开编辑器并使用原生草稿工具。**跨数据集计算指标（A 表指标 ÷ B 表指标）做不到**，粒度不一致需说明限制。

## 订阅检索预算

按独立查询规划读取并复用已知绑定；Host 工具回执的本轮总上限仍优先，不能靠换词或换工具规避。订阅 bind_dataset_query 的精确来源核验不再发起 catalog 名称搜索。总预算不足时交代已查范围和未完成项，不能伪装成用户缺业务条件。生产读取和耗时预算需结合实际部署基线再校准。
