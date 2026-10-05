# 数据检索错误处理

| 错误/现象 | 下一步 |
|---|---|
| `EDITOR_ALREADY_OPEN` | 读取 `dataset.editor.get_context` 继续当前草稿；不要为消除错误而关闭或丢弃未保存修改。 |
| 日期范围缺失 / 查询变量参数无效 | 普通日期字段使用普通筛选，完整区间由用户提供；变量只使用当前 `get_source_fields.queryVars` 中的标识。参数错误不是账号缺权的证据。 |
| `apply_draft` 返回 `committed:false` | 本次替换未生效，不能说请求中的字段已选中或校验通过。修正参数后再提交；状态不确定先回读 `get_context`，不可重试相同错误输入。 |
| `INTENT_MODEL_UNAVAILABLE` | 用 `catalog.search_datasets` → `catalog.search_fields`/schema 的确定性回退继续，不把它当整项任务失败。 |
| `analytics.resolve_data_requirements` 返回 `not_found` / `NO_MATCHING_DATASET`，或 `need_clarification` | 不重试 resolve；复用有效候选与 clarification 提问，缺定义且还有补查额度才定向补证据。无候选如实说明本次范围与限制，不将未找到或工具失败冒充用户缺条件。 |
| `partial` 且 `plans=[]` | 保留 issues 指出的失败或缺证据状态；有可信线索最多补一条定向查证，仍未解决就提问或报告卡点，不更换业务概念。 |
| `CANDIDATES_TRUNCATED` / `SEARCH_SCOPE_LIMITED` | 按 nextCursor 在预算内继续；无下一页仍需检查范围限制，不能当全目录完成。组合词 fallback 只覆盖有界候选。 |
| `CURSOR_INVALID` / `CURSOR_STALE` | 保持原查询参数；结果变化时重新首页一次并去重。不要伪造 cursor。 |
| `RESOURCE_TYPE_COVERAGE_LIMITED` / `FIELD_BINDINGS_NOT_CHECKED` / `ASSOCIATIONS_TRUNCATED` | 只交付已返回的关联候选；未核实具体字段及未覆盖类型如实说明。 |
| `PERSISTENCE_OUTCOME_UNKNOWN` / `TOOL_CONTINUATION_REQUIRED` | 按恢复入口核对或补传原结果，不能换词后重做原写入。 |
| 工具结果提示 `exceeds maximum allowed tokens`（结果被写进文件） | 不要用 Grep/Read 去翻那个文件。用收敛参数重调同一工具：`get_dataset_schema` 传 `fieldRefs`/`roles`/`query`（或降低 `limit`）；`search_datasets` 降低 `limit`/`maxMatchedFields`。 |
| `permission_required` / `access.check_resources` 返回无权 | 说明权限状态并调用 `access.get_apply_plan` 给出申请路径，停止后续写入；不要改用 `search_datasets` 复查——它会滤掉「可见但未授权」的数据集，让你误报「没有数据集」。 |
| 搜不到指标字段 | 先确认用的是用户的业务名词而不是猜测的技术名；在已确认的同业务域数据集上 `catalog.search_fields`；仍无命中就如实报告缺失，不要用近似字段替代。 |
| 多个合理指标口径 | 先给数据集、指标、真实定义；只缺定义时定向补查一次，仍缺明确标记，再合并提问。发现位置任务直接列出结果。 |
| 单表无法同时满足指标与维度 | 先用指标词+维度词做一次 `catalog.search_fields` 找宽表；仍不满足时按 SKILL「单表不满足时」的判定给出可行性与替代方案，不要许诺跨表计算。 |
| `access.get_apply_plan` 返回 `UNSUPPORTED_CAPABILITY` | 该数据集缺权威权限映射（多见于没经过目录搜索的冷调用）。不要重试；直接引导用户到数据集市页面申请权限。 |
