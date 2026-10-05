# Asset Search MCP 真实能力证据

验证日期：2026-09-21  
端点：`https://asset-search-mcp-office.aihuishou.com/mcp`

## 协议与服务

| 检查项 | 结果 | 证据 |
|---|---|---|
| GET `/mcp` | 405 | 端点存在，但不是 GET/SSE 入口 |
| MCP initialize | 通过 | HTTP 200；协议 `2025-03-26` |
| 服务身份 | 通过 | `davinci-data-mcp` `1.1.0` |
| 服务说明 | 通过 | “查询报表、主题、指标、维度和数据集资产，仅提供只读能力” |
| `tools/list` | 通过 | 返回 12 个工具，全部 `readOnlyHint=true` |
| 传输鉴权 | 未发现独立 HTTP 鉴权 | 未携带 Cookie、Bearer 或 CAS Token 即可 initialize、list 和 call；权限工具依赖调用参数 `obId` |

该端点是 Streamable HTTP 风格的 MCP POST 端点，不是旧式 SSE `/sse` 端点。

## 真实工具

资产搜索：

- `aggregate_data_assets`
- `get_data_asset`
- `search_data_assets`

数据集目录、字段和权限：

- `access.check_resources`
- `access.get_apply_plan`
- `catalog.get_dataset_schema`
- `catalog.list_dataset_usages`
- `catalog.match_dataset_fields`
- `catalog.search_datasets`
- `catalog.search_fields`
- `catalog.search_semantic_assets`
- `analytics.resolve_data_requirements`

## 运行验证

先使用 `obId=149502` 验证无权拒绝路径；随后按用户要求改用 `obId=159358` 验证授权成功路径。验证只读取元数据，不查询业务数据行。

| 调用 | 结果 | 结论 |
|---|---|---|
| `search_data_assets` | 成功，返回稳定 `assetRef` | 支持资产发现；发现结果不等于有使用权限 |
| `access.check_resources` | 成功返回 `discoverable_but_unauthorized` | 数据集权限拒绝路径有效 |
| `catalog.get_dataset_schema` | 对无权资源返回 `RESOURCE_NOT_FOUND` | Schema 不向无权用户泄露 |
| `catalog.search_datasets(scope=authorized)` | 返回结构化 `not_found/clarifications/issues` | 支持权限内搜索与业务歧义提示 |
| 合成无效 obId 的权限检查 | 返回无权，不返回 Schema | 未发现无效身份可越权读取 |
| `catalog.search_datasets`（159358） | 返回多个 `permission=authorized` 候选 | 已取得真实有权数据集样本 |
| `access.check_resources`（159358） | 单数据集及两个数据集批量检查均返回 `authorized` | 创建阶段 1–5 数据集权限检查具备真实接口基础 |
| 字段权限子集（159358） | 请求两个 fieldRef，两个均进入 `authorizedFieldRefs`，无 unauthorized 字段 | 字段级授权成功路径通过 |
| `catalog.get_dataset_schema`（159358） | 成功返回完整字段；指定两个 fieldRef 时仅返回两个字段 | Schema 和字段子集读取成功 |
| 必填筛选（159358） | 三个授权样本均明确返回 `requiredFilters=[]` | 空集合契约通过；尚未取得非空必填筛选正样本 |

可复现样本之一：`warehouseTable:rpt.rpt_source_trade_recycle_3c_order_analysis_mock_new`。该样本 Schema 返回 13 个字段，`truncated=false`；状态为 `partial` 的原因仅是 `relation_summary_unavailable`，不是权限失败。

## 与报告目标的差异

12 个工具中没有以下能力：

- `data.ask`
- Ticket 签发、校验、撤销和消费
- SQLBot assistant 创建/同步
- SQLBot 回调供数接口
- Davinci 基础 SQL/行权限谓词供给
- `ask_session` 与 SQLBot Chat 映射
- SQLBot SSE 解析与标准结果归一化
- `result_cache` 和按用户授权的数据拉取

因此该服务的准确定位是“线上 Java 版资产发现、Schema、字段和显式权限前置 MCP”，不能替代报告中的逻辑“问数编排/供数 Data MCP”。更新版报告还要求 Data MCP 持有 `data_agent`、发布/可见性、逐题字段投影和 SQLBot Assistant 生命周期；这些能力同样未出现在真实工具列表中。

Python `davinci_data_mcp` 代码不是这个线上实例。后续不得用 Python 仓库存在的类或接口，推断 Java Office MCP 已经支持同名能力。

## 安全约束

- Agent Host 必须从可信登录态取得用户身份，并由服务端覆盖 `obId`；禁止直接采用浏览器请求体中的 `obId`。
- Office MCP 只能位于内网信任边界，不应由浏览器或公网直接访问。
- `search_data_assets` 可返回可发现但无权的资产；真正绑定或供数前必须调用 `access.check_resources`。
- Schema、基础 SQL、行权限和执行权限是不同能力，不能用其中任何一个推定其他能力已经具备。
