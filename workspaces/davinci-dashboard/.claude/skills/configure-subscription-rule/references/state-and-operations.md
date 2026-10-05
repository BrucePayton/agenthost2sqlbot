# 七步配置操作

模型直接调用原生工具；参数校验、引用转换、原子写入、版本检查和回读在工具内部完成。原生 Store 是唯一可写配置。

复用原生表单，不创建另一份规则模型。场景与发送类型分别读取：定时无数据 scheduled_no_dataset、定时查数据 scheduled_dataset、条件触发 conditional；数据预警仍需执行时间。

新建先按 [执行流程](workflow.md) 选择纯通知、仪表盘内容、定时数据或条件预警路径。先核实归属/权限，首批填写已明确独立的时间/发送类型并打开配置；模板/图表预警保留真实种子。随后按完整需求解决依赖，不因先填时间而推迟考虑动态接收人和内容字段。

业务核对：查询决定取哪些记录；条件决定何时/哪些记录命中；推送方式决定卡片粒度；接收人映射决定每人收到哪些卡片；正文决定展示哪些列。“逐条订单超过一万元”需要订单标识等明细维度与提交金额输出，不能只按区经聚合，也不能把订单在多条商品明细上的重复金额直接累加。查“昨日”使用原生相对日期，不能冻结成当天算出的绝对日期。数据表和仪表盘可以在同一正文组合，配置完成前逐项对照原需求。

## 1 什么时候发

时间 times 为 HH:mm；executeAt/effectiveStart/effectiveEnd 为 YYYY-MM-DD HH:mm（可带 :00 秒）。频率专属字段不能混传，星期 1–7，单次日期必须是真实日期；有效期缺口由 review 返回。

set_schedule：hourly/daily/weekly/monthly/once；dailyMode everyday/workday、times、weekdays、monthDays（含 eom）、hourlyMinute 0/15/30/45、hourWindowStart/End、executeAt；effectiveMode long/range，有界时段用 effectiveStart/End。只修改用户指定项。set_send_rule 选择上述三类发送方式，检查步骤显隐导致的失效引用。

## 2 用哪些数据

### 查询差异与证据

对照用户要求和当前查询，复用原查询与字段，只更新明确差异。显式要求“昨日/上月”等滚动范围时，将目标查询中已核验的日期筛选改为原生相对日期，保留其他筛选和参数；用户指定固定日期或要求沿用原配置时不改。只凭图表标题不能猜日期字段；锁定项或实际业务口径冲突需澄清。

| 当前查询证据 | 操作 |
| --- | --- |
| 完整选中指标及 outputs 已含所需比较 | 复用对应 outputRef，不重复添加 |
| 完整选中指标及 outputs 一致表明缺少所需比较 | 用已有 queryRef、原指标及日期字段，merge 添加所需比较；不声称原图表没有比较 |
| 选中指标/输出缺失或该查询有读取错误 | 定向补读该项，再判断；不重新搜索来源 |

metadataReady=false 是全源目录未全载，不影响已有有效引用及能力；conditions 未填完整不等于查询读取失败。不要用单个 comparison=null 判断有无比较。原值的日期筛选限定取数范围；contrast.valueExp 确定比较评估期，calcMethod 确定比较基准，calcType 决定差值或差异率。例如昨日原值筛选和昨日的日环比评估期都可用 last_day，后者比较前一天，并非再次向前移动原值日期。用已返回能力核验组合，参数见下文。

有足够证据就先提交确定的查询修改，再用新 outputRef 完成条件/正文；可使用同批 local 引用时合并。只在存在具体技术缺口时读取对应参考或元数据，不重复规划同一已明确方案。

### 查询参数

bind_dataset_query 接收通用目录 catalogDatasetRef 和完整 fieldRef，工具精确核验来源及字段，直接转换为订阅引用，支持相同的查询参数。upsert_dataset_query 新建用 datasetRef，修改用 queryRef（可省 datasetRef）；name、dimensionRefs、metricRefs、filters 可独立修改。多次引用同一个 dataset 也是不同 query，不能按数据集去重。

filters 是 AND 筛选；传入时替换整组，不传保留。局部修改已有查询优先用 filterEdits，前提是当前回读 queries.filtersComplete=true。每项 {action:"replace"|"remove",filterIndex,fieldRef,filter?}，索引来自 configuration 对应查询的 filters，fieldRef 必须与该项匹配；replace 的 filter 为完整新 operator/values/valueRefs/valueExp，remove 不带 filter。缺省的新值不继承旧值；values 与 valueRefs 互斥，valueExp 不带 valueRefs。必须带 queryRef，不能同时传 datasetRef/filters；同批不能对同一查询再做其他筛选、换源或删除操作，防止索引漂移。其他筛选、参数及输出保持不变。operator 字符串、values 或已验证 valueRefs、相对日期 valueExp。将固定日期改为昨日可用 `{fieldRef,operator:"eq",valueExp:"last_day",values:[]}` 清除旧日期值；“最近30天”用 `{fieldRef,operator:"eq",valueExp:"last_days",values:["30"]}`。不要把数值填进 operator。

同环比用 metrics 替代 metricRefs：每项 fieldRef，可带 contrast:{timeFieldRef,calcMethod,calcType,valueExp,values?,cutoffDate?} 与 format（普通指标 decimal 0–3）。使用原生比较方式与时间字段能力；差值 diff、差异率 diffRate；工具返回不同 outputRef 区分原值和比较值，不编造 cmpId。

contrast 的合法组合按需读取 get_context({includeComparisonCapabilities:true}).comparisonCapabilities，工具 schema 同时校验。截图所示“回收成交日期、昨天、日环比、差异率”应组装为 `{timeFieldRef:<该日期字段引用>,valueExp:"last_day",calcMethod:"dayChain",calcType:"diffRate"}`，无需 values。差异率是原始比率，下降 10% 用 -0.1；百分号是展示格式。均值环比需要一个 1–366 的整数参数；其他周期参数个数和可用基准按回执，不混用日期筛选的字符串参数格式。

新增同环比设置 metricsMode=merge，保留原值和其他比较输出。metricsMode=replace 或省略时，已传入的 metrics/metricRefs 是完整替换列表；未传列表则保留现有指标。merge 不用于删除指标。原值和比较值需各自 outputKey/outputRef，条件和正文引用各自对应输出；是否需要添加按上面的当前查询证据判断。

参数用 queryParameters:[{fieldRef,operator?,values?,valueRefs?,valueExp?}]，按参数逐项更新；[] 不清空未列出的参数。清除可选参数值需该条 values:[]。字段须为真实参数，operator 沿用原生参数；保留默认值、锁定/隐藏参数，缺必填参数必须补齐。remove_dataset_query 仅按 queryRef 删除，检查下游引用。

## 3 满足什么条件

set_trigger_conditions：queryRef 选一份已配置查询；conditionGroups:[{conditions:[...]}] 表示组间 OR、组内 AND。leaf 用 fieldRef 或 outputRef 二选一，加 operator 与值。旧 conditions+logicalOperator 支持纯 AND/OR，但不能和 conditionGroups 同传。

hitRecordLimit 默认 200、范围 1–1000，超过上限自动暂停并告警；不是预览行数或数据表行数。条件命中取决于这份查询的输出，不能引用另一查询的同名字段。

## 4 数据怎么推送

set_push_mode：all/record/group，record/group 指定主 queryRef，group 指定 groupByFieldRef。条件模式主查询是触发查询。每个附带查询用 mappings:[{queryRef,fieldRef?}] 配置；条目省略 fieldRef 表示该查询整份数据，整个 mappings 省略则保留原配置。不按同名字段自动关联。

按业务对象发送前核验实际查询粒度；一条小订单有多行时，不能把 record 当作每订单一卡。all 回读省略 queryRef 是合法归一化，正文表格仍引用各自查询。all 是全部适用数据生成一张卡片，不由“只要一个数据表”推断。条件、分组、动态员工、正文和下载需使用一致的适用卡片范围。

## 5 发给谁

set_recipients：memberRefs、groupRefs、fieldRecipients:[{queryRef,fieldRef}]。动态员工字段必须 isEmployeeAccount=true、employeeAccountType=oa/ob，且已选为该查询输出维度；同源多查询不可只给裸 fieldRef。旧 fieldRefs 仅在查询归属唯一时可用。某一类别省略则保留，空数组清空该类别。

个人规则固定本人；空间成员/群组按原生权限检查。固定接收人收到适用卡片；动态员工按实际查询及映射分发。发送前的数据一致性校验比较接收人与创建人的查询结果，不是比较员工字段名称。配置阶段不必实际查询全量员工结果。

## 6 发什么内容

set_content 新正文统一使用有序 components；title、subtitle、titleColor 可独立补丁，省略正文时保留现有内容。旧 richTextMarkdown、includeDataTable、dataTableQueryRef、dataTableFieldRefs/dataTableOutputRefs、dashboardRefs、widgetRefs 仅兼容；不与 components 混用，列引用二选一，表格参数必须显式 includeDataTable:true，richTextBindings 必须同时带 richTextMarkdown。patch_content.edits 用 componentRefs 增删改：仅 component 新增，componentRef+component 替换，仅 componentRef 删除，componentRef+position 移动到从 0 开始的位置。组件引用来自回显，与 components 同序；局部操作不重建其他块。

新正文传有序 components，表示明确替换整段正文（不与上述正文快捷字段混传）：

- richtext：markdown 或 segments（二选一），以及 bindings。segments 是有序片段：text/link 携 text，可选 bold/italic/color（hex 或 rgba），link 另需 url；mention-all 生成真实提及；newline 换行；variable 用 key 对应 bindings。示例：`[{"type":"text","text":"昨日未成交的大额订单如下：","bold":true,"color":"#ff0000"}]`。原生 Markdown 仍可表达列表等编辑器能力，变量写 `$$key$$`。
- data-table：queryRef、fieldRefs 或 outputRefs，合计最多 50 列；每卡最多 5 张表。下载最多 100 个选中输出。
- dashboard-screenshot：dashboardRef。
- dashboard-chart：widgetRef。
- image：imageRef，只能用当前草稿已上传资源；新图需在原生编辑器上传，不编造图片 key。
- button：text、actionType link/download/dashboard；链接用用户指定的 http(s) url，下载用 queryRef 与列引用，仪表盘跳转用 dashboardRef。
- button-group：items，为上述按钮配置。

titleBindings/subtitleBindings/richTextBindings 与富文本 bindings 使用 key 对应模板变量；系统时间 refType=system_time，refSubType=trigger_datetime/trigger_date/today/yesterday。数据变量 refType=dataset_field，带 queryRef、fieldRefs 或 outputRefs，refSubType=field/employee_name/mention/table。员工姓名和 @ 仅作用于员工账号输出；普通变量单输出，table 可多列。record/group 的标题或正文必须实际引用主查询，不能只配置分组不引用数据。

标题“昨日”需要日期变量时使用 yesterday 绑定，不能只写固定“昨日”冒充变量；该日期应与查询的业务范围一致。富文本保留指定文字、格式和顺序；不以 Markdown 表格或纯文本 @ 代替原生表格/提及。截图、AI 组件、图片、链接、仪表盘跳转和下载各有用途，不能因候选缺失静默互换。

## 7 命名并保存

set_finalize：ruleName 最多 30 字、tagRefs；兼容可选 desiredStatus=disabled，只改命名配置。Agent 不保存或启用；最后一批 operations 加 finish=true，原生校验后交付用户发送预览并手动保存。

## 批次与续改

同批 queryKey 提供 local:key 查询引用；metrics.outputKey 提供 local:key/outputKey 原值或比较输出。后续 operation 可直接消费，减少往返；批次外只用回执的真实引用。start/apply 共用完整操作能力，每批 1–32 项、失败不提交任何修改。手动编辑与 Agent 共用同一 Store/revision；冲突重读，忙碌不覆盖，模板锁不绕过。配置回显中缺失字段表示未完成，不代表可跳过对应校验。

正文进度与页面定位由工具及实际 operations 推导，模型不规划 presentation；快速配置合并批次，实际写入回执才证明已完成。

首次配置的必填标题/名称由工具补齐，用户指定及模板已有值优先，后续人工清空不自动覆盖。重复相同操作是成功 no-op，revision 可以不变；以当前回读判断，无需为凑新版本再写入。
