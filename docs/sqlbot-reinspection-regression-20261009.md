# 复检结果变化问数回归（2026-10-09）

> 后续状态：按用户要求恢复无专项提示的历史基线，下面记录的提示 ID 1、2 已备份后删除。
> 当前部署与 10 题回归结果以 [10 题优化记录](sqlbot-regression-10-20261009.md) 为准。

问题：最近一周复检时，哪些质检项的结果和之前不一样？各有多少台？

## 历史与现状

历史 Excel「运营中心Top50_500题_助手问答阶段性结果.xlsx」测试结果第25行 T018，对应 SQLBot record 57，2026-09-30 使用 qwen3.8-max 完成查询。源记录在 `.runtime/batch-top50-20260930/results/T018.json`。

失败 record 1414 实际 GENERATE_SQL 日志仍是 qwen3.7-flash。管理接口显示 name 为 `[service2] qwen3.8-max`，但 base_model 为 `qwen3.7-flash`。因此显示名不能证明实际切换。该会话还包含 record 1412 的失败回答。历史与当前输入中均存在 old_property_value_name（上一次质检属性值）、new_inspection_value_name（本次质检属性值）；不是缺失授权表或字段。

修正 model 7512712873869578240 的 base_model 为 qwen3.8-max，保留 endpoint、密钥、模型ID和其他配置。新会话 record 1415 确认实际调用 Max，但错误地自连接并引用明细表不存在的 type_inspection_rank，故仅切换模型不足以稳定复现历史结果。

## 已部署修复

通过 SQLBot 原生 `/api/v1/system/custom_prompt` API 创建 GENERATE_SQL 提示，分别限定 DW 助手 7511012753000108032（提示ID 1）和三源助手 7510943314162487296（提示ID 2），没有修改 DM/RPT 助手。

规则来源为真实字段注释和历史 T018：

- 在同一行比较 old_property_value_name 与 new_inspection_value_name，默认排除 NULL，无须自连接。
- 按 inspection_property_name 分组，各项 COUNT(DISTINCT product_no)，不能跨项加总当作总设备数。
- 最近一周沿用历史最近7天口径：inspection_dt >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)。
- 不臆造 type_inspection_rank 或 inspection_type_name='复检' 枚举。此规则表示已有上次结果的前后变化；若用户明确指定业务质检类型，仍须按真实类型规则过滤。
- StarRocks 三段式表名分别引用：`hive`.`dw`.`dw_centre_inspection_report_item_info`。

配置保存在本地 SQLBot 数据库，正常重启保留；重新创建空数据库后需恢复提示。提示不把历史答案数值写入模型，结果始终查询当前数据。运行脚本和回归响应保存在 `.runtime/reinspection-analysis/`，原工作簿未修改。

## 真实回归

新会话使用未经附加说明的原问题，record 1416：

- EXTRACT_KEYWORDS、FILTER_CUSTOM_PROMPT、GENERATE_SQL、EXECUTE_SQL 全部成功。
- 实际模型日志为 qwen3.8-max。
- SQL 使用正确明细表、前后值比较和按物品去重，与历史 T018 口径一致。
- 后续 GENERATE_CHART 失败，模型服务返回 429 insufficient_quota / Workspace allocated quota exceeded。

因此 SQL 生成和执行已验证修复；图表及最终回答尚受模型工作空间额度阻塞，不能宣称端到端成功。三源助手已配置同一规则，但未另行消耗额度回归。解决额度后，在新会话中重试原问题并核对实际 GENERATE_SQL 模型及最终回答。
