---
name: configure-dashboard-widget
description: Create or configure Widgets after data selection; inspect, repair and style existing Widgets.
---

# Configure Davinci Dashboard Widget

## 最短路径

- 只改标题、布局或样式：按目标页结构确定组件，用对应编辑工具；样式能力缺失时才查 `get_widget_edit_capabilities`。不加载 locate-data、不读取数据集 schema。
- 新建看板并加组件：先完成必要业务澄清，再创建容器；用户明确先建空看板时按其顺序做。协同空间用 `space.menu.get_context` 定位唯一分组后 `space.dashboard.create_and_open`；个人空间用 `workspace.dashboard.create_and_open`。位置不明先问，成功容器沿用。

## 消息与预警组件

- 预警：`dashboard.open_data_alert_config {widgetId}` → `configure-subscription-rule` 续改；均归个人，不改图表。
- 消息 Widget 不可查询，返回 `MESSAGE_WIDGET_NOT_QUERYABLE`；Agent 刷新只处理普通 Widget，消息项进入 `unavailableWidgetIds`。

## 工作流

1. **核对已有组件。** 新建未绑定组件直接第 2 步，不读无关组件。仅既有组件核对目标页：已在目标页复用有效上下文；否则用 `workspace.list_dashboards` 定位后 `ui.open_dashboard`，回执后才读组件。优先用目标页的 `dashboard_structure`，缺失或 resourceRevision 变化才 `dashboard.get_structure`；再 `get_widget_config` 确认配置。禁用跨看板配置和 widgetId。不存在就询问，不检索数据。只检查已有配置时读回即完成；仅解释口径时用配置 refs 定向查定义。
2. **澄清。** 新增/改变数据需求用 `Skill(locate-data)`；口径检查点要求用户选择时，给齐数据集、指标和真实定义后立即提问，先不规划图表或创建容器。合并询问本次必需的已知日期/筛选缺口。条件明确后复用绑定/配置；新增组件仅目标页不同时导航，新建容器按上方路径。
3. **校验访问权限。** 同任务同资源/字段复用 locate-data 刚验证的权限，否则 `access.check_resources`；写入实时 ACL 保留。无权时调用 `access.get_apply_plan` 并停止写入。
4. **一次提交完整配置。** 调用 `dashboard.apply_widget_spec`：已有 Widget 传 `widgetId`；新建传 `create:{chartType,title}`。传 dataset、metrics、dimensions、filters、sort（若需要）。**`limit` 只有排行榜（`11001`）支持，其余图表传了返回 `LIMIT_NOT_SUPPORTED`**；新建时间趋势默认时间升序，排行榜默认主指标降序；显式排序优先。修改已有图表未要求排序时省略 sort，保留原排序；其他图表不自行加 sort，未要求时不加 limit。字段只传 fieldRef 最后一段 fieldId。每个 filter 必须带 `source` 声明取值依据：`user_stated`（用户原话）/`user_confirmed`（用户确认）/`tool_verified`（工具证实）；拿不出依据先问用户，标 `assumed` 的写入会被 `NEEDS_USER_CLARIFICATION` 拒回（dryRun 探针不受限）。
   提交只用写入 schema 的字段；`effectiveSpec` 是读回对象，不可直接作为输入，尤其 `filters` 不接受 `name`/`type`，每项的 `source` 必须保留真实依据。
   按某维度拆成多系列时，原生支持分组的柱/线/面积图用 `dimensions:[横轴字段,分组字段]`，且只能有一个指标；具体形状和排序限制见 [fields-and-time.md](references/fields-and-time.md)。输入不传 `group`。
   指标卡同次带 `comparison:{timeFieldId, methods:[{method,calcType}]}`，最多 3 组。
   口径映射：日环比=dayChain、**周同比=weekSame（“跟上周同期比”也用它，回复仍称周同比）**、月环比=monthChain、年同比=yearSame；
   calcType：差异率=diffRate、差值=diff。
5. **读回验证。** 成功回执的 `appliedSpec` 是生效配置；要求分组多系列时，必须核对 `group.enabled:true` 和 `group.fieldId` 等于所需分组字段。`group:null` 是未配置，`enabled:false` 是未开启；字段缺失也不证明已分组。`dimensions` 含两个字段或 `probe.rowCount>0` 只证明维度/取数，不能替代分组核对。分组缺失或不匹配按下方回执不一致处理。`probe.rowCount>0` 证明写后取数可用；`rowCount:0` 按下方空结果处理。仅 partial、warnings、回执不一致或核对他人改动时再读配置。用户要看具体行才 `get_widget_data`；返回 `QUERY_NOT_DISPATCHED` 时先 `refresh_widget` 再读。

## 约束

- 改已有组件的查询语义前先 `dryRun:true` 试跑：`rowCount` 为 0 即该口径查不到数据，先问用户；字段放进 `dimensions` 试跑，`sampleRows` 即其实际取值。新建组件的 dryRun 只校验结构不探数（`probe.rowCount` 恒 0，不代表查不到数），数据验证见第 5 步。
- dryRun 成功后输入未变，原 spec 只改 dryRun 和 expectedResourceRevision 正式提交；不把 appliedSpec 摘要作输入而丢失 filter.source。
- 读回 `state:empty` 且无错误：这是数据的答案，不是配置故障。如实报告当前口径与空结果并让用户定夺，不要自行改时间、指标、维度或数据集重写。

- 图表类型：指标卡 `2001`、柱状图 `3001`、折线图 `4001`、饼图 `5001`、表格 `1001`、排行榜 `11001`。
- 时间快捷方式优先用 `valueExp`；最近 N 天使用 `last_days` 和 `value:["N"]`。
- 不用 `dashboard.set_widget_dataset` 加多次字段编辑拼装一个新图表。
- 不把仅创建空卡、打开配置面板、重命名或导航描述为需求完成。
- 只有 `status:success, persisted:true` 加读回证据，或 `status:success, persisted:false, state:unchanged` 加预期匹配的 `data.effectiveSpec`，才能宣称成功；后者是 already satisfied，不要重试。
- 用户本轮明确要求发布（消息里出现"发布/上线/publish"且没说"不要发布"）时直接调用 `dashboard.publish`（可先 `get_publish_readiness`），不再二次确认；用户没要求时不发布，也不用发布来验证写入。
- 写入成功但读回 `data.effectiveSpec` 与预期不一致时：最多重读一次；仍不一致就以 `CAPABILITY_CONTRACT_MISMATCH` 如实报告"工具回执与页面不一致"并停止，不要 refresh、重开仪表盘、重复写入或归因"其他人在编辑"（没有 actor 证据）。

按需读：参数查 [tools.md](references/tools.md)，图表与时间查 [fields-and-time.md](references/fields-and-time.md)，错误码查 [errors.md](references/errors.md)。
