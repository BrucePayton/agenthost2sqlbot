# 订阅配置工具契约

模型直接编排以下五个原生配置工具。它们分别负责读上下文、找候选、创建编辑态配置、原子修改、检查；不存在统包整条流程的入口。

配置使用 `space.message_rule.*` 五个配置工具；跨入口复用导航工具。用户指定空间时，若当前页没有 space.list，先 ui.open_space_page；随后 space.list → space.open({spaceRef,destination:"subscription"}) → get_context；不是默认打开该空间的仪表盘。身份、路由来自可信引用，不接受 spaceId、actor 或任意 URL。普通工具预算 15 秒，含精确绑定或模板导入的 start/apply 为 60 秒；这不是模型思考预算，也不是要求等待满时长。

## get_context

sourceBinding 是当前监测指标来源；contentBindings 记录实际内容组件的业务类型及父仪表盘。AI 可能以 dashboard-chart 原生类型展示，需按 contentKind=ai_interpret 与实际 widgetRef 判定。只读语义证据不作为可写配置或直接重放参数。

输入 `{}` 默认不加载规则列表；查看历史规则时才传 `includeRules:true` 或 query。结构化返回 scope、activeDraft（如果存在）、contextVersion；summary 仅供人阅读。activeDraft 包含 revision、lifecycle、editable、scene/source、sections、locks、queries/outputs 与 configuration。configuration 是当前配置的只读回显，未完成项可能省略；不能整包盲目回写，按用户要求挑 operation 修改。

仅需新增/修改比较且现有证据不足时传 includeComparisonCapabilities:true；默认上下文省略该能力表。comparisonCapabilities 来自原生同环比弹窗，包含评估期、每期合法比较方式、参数个数、均值周期范围、截至日期、差值/差异率和输出引用规则。按这些规则组装 contrast，不猜英文枚举。configuration.unresolved 表示已有配置的表达缺口；不能据此删除原配置或换来源。

saveOutcome 可为 pending（正在提交）、unknown（结果未确认）或 saved（已保存），不出现则尚未进入提交阶段。pending/unknown 均已越过确认环节，不要求再次确认。get_context 只读取事实，不会重发、解除未知状态或保证其自动恢复。

## search_options

kind：template、dataset、field、enum_value、recipient_member、recipient_group、dashboard、widget、alert_widget、tag。scene 只对 template 有效。field 需订阅 search_options(kind=dataset) 或当前 queries 返回的 datasetRef；catalog 返回的 datasetRef 只能放 bind_dataset_query.catalogDatasetRef，不能混用。enum_value 需 fieldRef，widget/alert_widget 需 dashboardRef。只消费当前结果的 ref；父级已确定后才检索子级。widget 用于正文嵌入；alert_widget 与原生数据预警铃铛使用相同的图表类型判定，包含指标卡，不能用 widget 空结果推断预警组件不存在。

输出有界候选、父级、是否截断与空结果说明；字段元数据区分指标/维度、参数、必填/默认/锁定/隐藏参数及员工账号。权限由页面实时校验，不从标签或姓名推 ID。字段候选不等于查询输出；后续条件和内容优先使用 activeDraft.queries[].outputs[].outputRef。

field/dashboard/widget/alert_widget 支持 offset；nextOffset 存在时保持原 kind、父引用和查询条件继续读取。matchedCount 是这次匹配候选集合的数量。无需知道名字即可分页；改变 query/role/父引用时从 offset=0 开始。field 可用 role=dimension/metric/parameter、isEmployeeAccount 过滤。sourceKind 区分 personal/space/template，不改变规则归属；缺少来源标记不能自行猜测。

filterCapabilities 来自原生字段约束：operators 是查询筛选允许值，dateExpressions 包含 value/label/valueCount；查询输出的 conditionOperators 用于预警条件，不能混用。日期范围参数仍须遵守原生正整数等要求，失败按工具的 path/message/allowedValues 修正。

动态员工资格看 isEmployeeAccount=true 且 employeeAccountType 为 oa/ob；该字段还必须加入目标查询的 dimensionRefs。通用目录的 varchar/名称不证明资格；不要求用户辨别存储账号类型。没有合格字段就保留其余配置，报告当前不支持及候选替代，不擅自更改收件人。

requiresTimeFilter=true 是数据集级约束：需选择时间维度和范围作为查询筛选，不代表每一个返回字段都必填。指标字段搜索也会携带这个约束；不必运行数据预览才能获知。

使用订阅 datasetRef 新增查询前须完成 field 检索；目录绑定 bind_dataset_query 自动完成精确来源及字段核验，无须再次搜索。已存在查询的局部修改沿用当前元数据，不必重复检索。

## start_draft

空白用 mode=blank 并明确 scene：纯通知 msg-notify、仪表盘内容 dashboard-push、数据规则 data-alert。scene 是原生分类，与 sendRule 分开；省略时原生默认 data-alert，不会由 sendRule 自动推断。订阅模板：`{"mode":"template","templateRef":"<ref>"}`；订阅中心选定预警组件：`{"mode":"widget","widgetRef":"<alert_widget ref>"}`。三者均可带与 apply 相同的完整 operations，1–32 项原子预填，模板锁仍有效。已知目录来源可以在同批绑定查询、设置条件、推送方式和正文，不必先建空草稿。成功表示原生编辑器已挂载并 ACK，初始 operations 已执行，不代表七步已完成。

mode=widget 导入原生动态模板，保留组件原有数据与同环比，规则归属遵循当前订阅中心；不搜索全局同名指标。在仪表盘页面应直接使用 open_data_alert_config，继承当前生效的全局筛选。已有草稿不调用 start；快捷入口打开后用 get_context 接续。模板未返回可用配置时报告具体缺口，不切换数据来源。

start/apply 的 readback=compact 只去除已被输出覆盖的重复源字段，完整保留 query upsert、filters、queryParameters、metrics.contrast。queries.metadataReady 表示整份数据集元数据是否已加载，不判断已有配置是否有效；filtersComplete 表示当前有序筛选是否完整回显。已有完整证据不重复读取。review 返回 configuration、queries、validationScope、dataChecked，用实际回显核对需求，不重复查询数据。

## apply_draft

工具支持 import_widget_source 时，向当前未绑定的数据预警草稿导入可信 alert_widget 引用，复用原生种子转换及来源策略；expectedRevision 与 taskId 仍须当前有效。它保留已填时间、收件人和独立内容，不用于覆盖已有查询或更换已经绑定的来源。导入后根据真实 queryRef/fieldRef/outputRef 设置所需滚动日期和比较。旧版缺该 operation 时不得自行发送。

输入 expectedRevision 与 1–32 个有序 operations，整批原子提交。同批支持 queryKey/outputKey 的 local: 引用；跨批以回执 queryRef/outputRef 为准。返回新 revision、配置回显、invalidated、warnings；出现失效项必须修复。参数见 [state-and-operations.md](state-and-operations.md)。

整批最终发送方式必须支持写入的步骤；例如添加数据查询时同批设置 scheduled_dataset 或 conditional，不能在 scheduled_no_dataset 下添加查询并宣称成功。

## review_draft

输入 expectedRevision、includeDataCheck（默认 false）。返回 complete、revision、七步 errors/warnings、dataChecks。默认使用实际 create 转换和 /message-rule/validateCreate 服务端核心校验，不执行数据查询；不渲染或试发。完整性不能用 summary 非空或打开面板判断。

## 完成交接

需求全部落实时，在最后一次 start_draft/apply_draft 加 finish=true；工具原子写入后直接调用原生配置校验，不再让模型单独调 review。无本次修改时可 review_draft({expectedRevision,finish:true})。finish 不和 includeDataCheck:true 合用。

completion.status=ready 表示该版本通过结构校验；模型仍须确认原需求全部落实后才能交付；configurationSteps 提供七步合法性及缺项，并不取代 Agent 对业务需求的核对。completion.saved=false、dataVerified=false；navigation=followed 才可说已切换到命名并保存，user_controlled 表示尊重用户当前页面。最终说明尚未保存，请用户点击该页右下角发送预览，核对实际数据和内容后手动保存。

## save_draft

save_draft 保留历史契约兼容，但配置 Agent 不暴露和执行此动作；直接调用也拒绝。用户原生预览、保存按钮不受影响。ready 后核对业务也满足即可结束；明确缺项只局部修正，不重复检索或检查，不试发或启用。
