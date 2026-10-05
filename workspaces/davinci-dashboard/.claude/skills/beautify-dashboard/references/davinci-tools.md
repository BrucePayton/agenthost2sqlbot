# Davinci 工具适配

以当前运行时公布的 schema 和能力为准；下面是项目现有接口，不能臆造全局主题、圆角或图表内边距接口。

新组外框优先独占 24 列整行，只有连续两个各不超过 2 张卡片且全部为指标卡或排行榜的窄卡分组才尝试 12+12；该规则只选择外框宽度，不限制语义分组成员数量或按固定张数拆组。容器背景根据画布与全部可见子卡的实际背景色选择差异足够大的白色或非灰低饱和浅底；所有候选都冲突时仅允许调整冲突的指标卡浅色背景。

## 读取

- `dashboard.get_structure {"pageSize":100}`，有 `nextCursor` 继续分页；同一快照的 `resourceRevision` 应一致。根节点及容器全部子节点都要计入组件清单，包括隐藏 Tab 页；用实际 chartType 识别容器和卡片，不能用标题替代类型。
- `dashboard.get_widget_config {"widgetIds":[...]}`：每批最多 30，结果为 `data.widgets[]`。仅在结构及能力返回值不够时使用。
- `dashboard.get_widget_edit_capabilities {"widgetIds":[...],"sections":["appearance","metric"]}`：只改背景时省略 metric；读取各目标组件的真实支持能力、值域、当前值，不从一张卡推断所有图表支持相同字段。

已存在的展示 capabilityId 包括 `appearance.background.color`、`appearance.background.opacity`、`metric.value.color`、`metric.value.fontSize`、`metric.description.fontSize`。只有本次能力响应列出且允许写的项才可提交。`metric` 同时含数值格式等字段，美化时不要修改它们。

## 写入

样式预置由 `dashboard.apply_widget_edits` 过滤未改变和不支持的字段并一次保存：A 素雅风使用 `{"preset":{"theme":"plain","scope":"background"}}`；B1 指标卡统一颜色风使用 `{"preset":{"theme":"colorful","variant":"metric-unified","scope":"colors"}}`；B2 指标卡多彩风使用 `{"preset":{"theme":"colorful","variant":"metric-multicolor","scope":"colors"}}`。只说“颜色 1B”时追问 B1 还是 B2。B2 至少需要 2 张适用指标卡；只有 1 张时如实报告无变更、不保存，并建议改用 B1。

B1/B2 只为指标卡分配浅色背景，非指标分析卡统一为白色背景；保留指标卡已有业务语义色，不改字号和透明度。指标卡文字固定为深色，因此拒绝深色背景。重复请求 B1 或 B2 时仍调用同一预置，由前端推进到下一套排序色板。显式颜色使用 `colors:[...]` 数组：B1 显式单色使用 `{"preset":{"theme":"colorful","variant":"metric-unified","scope":"colors","colors":["#EAF2FF"]}}`；B2 显式有序多色使用 `{"preset":{"theme":"colorful","variant":"metric-multicolor","scope":"colors","colors":["#EAF2FF","#E8F7F2"]}}`，均绕过内置色板。没有适用指标卡时如实报告无变更，不保存。写入超时或结果不确定时先检查实际结果，不盲目重放。仅非 B1/B2 的自定义局部组件或特殊字段才读能力并改用 `operations:[{widgetId,edits:[{capabilityId,value}]}]`。

布局预置：`dashboard.set_widget_layout` 接收 `preset:{mode:"compact",sizing:"content"}`，最多 200 个根组件。前端按内容尺寸生成根布局并校验，成功最多保存一次。已有平铺及 Tab 的归属和子组件几何保持不变，不创建分组，不宣称组内已优化。用户明确禁止改尺寸用 `preset:{mode:"align",sizing:"preserve"}`。一键整理仪表盘直接使用 `{"preset":{"mode":"organize","sizing":"content"}}`，优先对齐并减少可避免留白，不追求全局最优；工具限制候选规模并保留可用保底布局，搜索预算耗尽时也优先返回当前合法方案。

根业务排序：选择 3A/3B/3C 即授权全量重新编排，不再询问保留旧平铺还是全部重新分组，也不展示候选方案等待二次确认。一次读取完整结构后，把所有现有原生平铺 Widget ID 放入 `regroup.containerWidgetIds`，不得放入 Tab Widget ID；释放出的平铺子卡与当前根卡一起单遍生成完整顺序和新 groups。用户明确“只排序、不分组”时才提交非破坏性 `preset:{mode:"reorder",orderedWidgetIds:[完整当前根组件ID顺序],sizing:"auto"}`。失败如实报告，不自动删减成员重试。检查 requestedOrderApplied、persisted、preview。

旧版显式根布局接收 `items:[{widgetId,x,y,width,height}]`、`strategy:"preserve"|"compact"` 和 `expectedResourceRevision`，每批最多 30 项。只在用户指定精确位置或当前契约不支持预置时使用；普通美化不降级到该路径。

当前 V2 为 24 列、行高 30px、间距 10px；运行时有约束时优先采用运行时约束。当前指标栅格技术下限为 1×1，实际可读尺寸由内容试算决定；普通图表最小 4×5、表格最小 6×5，其他类型有各自下限；这只是校验下限，不保证内容可读。未知类型保留尺寸，不能套用普通图表下限。

### 内容尺寸与预览

`sizing:"content"` 已内置批量内容尺寸脚本。指标卡读取已渲染标题、数值、比较值、字号和内边距；普通图表使用集中维护的类型目标，不逐卡查询 DOM。指标使用隐藏 DOM 副本和渲染器字号规则试算完整内容，候选及共同高度均以可完整显示内容为前提，不再强制 3×3 下限。

此行回退仅适用于普通 compact/reorder 的自动 content 最终根行，align 保留原列。先走现有混排，先尝试用有业务用途且兼容的其他卡片填补空白，保持阅读顺序和分组边界；只有没有合适的其他卡片可填补时，孤立行中至少 2 张自动指标卡才按 24 列整数栅格分配整行可用宽度：可整除时等宽，不可整除时近等分、最多相差 1 格（例如 5 张可为 5、5、5、5、4 格，不用 4.8 格）。同行等高，取实际分配宽度下有限探测中实测可容纳全部内容的最小共同高度，不得超过原紧凑行最大高度；不承诺全局最小高度，界内没有可行候选则保留紧凑行，不额外增高。须实测分配宽度和共同高度，保留卡间间距及内边距。单指标保持紧凑，回退不改动单指标及显式尺寸；preserve 和显式尺寸锁优先，排行榜保持窄而高，ID、数据和非布局配置不变。此根行回退在同次布局调用内完成，不新增参数或模型手算坐标，不跨组或突破受保护范围；无法实测内容适配或与锁冲突时保留约束并如实说明。

获批分组的临时根尺寸预览（capturePlan）禁用此回退，同时关闭根级宽度/高度填空（width/height gapfill）与指标行扩展（metricrowjustify）；capturePlan 不先扩展根行尺寸，根级填空尺寸不传入 group，避免先在根层扩卡后再使分组二次膨胀。group 使用内容尺寸与内部实时实测，这不表示禁用获批新组的内部候选规划；capturePlan 是内部路径，不是工具参数。

未获批移除的既有分组仍受保护：不改变其容器尺寸、成员归属或内部子卡几何，隐藏 Tab 页也不例外。获批完整重组只处理精确移除清单和成员提案中的范围，不把新组内部优化权限扩展到其他既有组。

获批新组内部遵循相同业务区域原则：在获批成员与顺序内优先用有业务用途且兼容的卡片填补空白，协调同行尺寸、区块边界和间距，不为填空混入无关卡片或压扁排行榜。工具进行多宽度与指标卡尺寸候选联合比较，并在保存前对最终候选执行精确内部 DOM 验证，而非仅将原根卡紧凑尺寸换算为内部栅格。同行协调须以实际候选及验证证据为准，不能仅凭单卡测量或已生成候选宣称协调完成。显式 preserve 和尺寸锁优先，ID、数据和非布局配置不变；模型不算内部坐标、不新增工具参数。分组候选缺少必要测量或最终验证证据时不写入，如实报告限制，不自动删减成员重试。

表格保持单表，透视表同样保持单表；自动 content 允许横向扩展，其最窄宽度依据整屏可用宽度的一半与当前容器可用宽度，取两者较小值，并由工具按实际像素换算本层整数栅格。容器本身不超过半屏时，表格占满该容器，不能再缩成容器的一半。保持当前高度，在可用空间与既有布局约束内利用横向空位；显式 width 锁与 preserve 优先，其中 `sizing:"preserve"` 保留原尺寸。不拆表、不减少数据行数，不改变数据和非布局配置。是否实际扩宽以本次回执为准，尺寸策略不等于表头、内容或滚动区域已经通过渲染验收。

试算数量及缺失证据见 summary.contentFit，具体限制见 issues。加载中、隐藏或不可测的内容，以及受保护的既有容器和未知类型保留；不能据此把所有表格归为尺寸不变。类型目标不等于平台最小尺寸，模型不换算像素或猜测图内留白。

用户明确要求预览时可用 `dryRun:true`；选择 3A/3B/3C 足以设置 groupingConfirmed/regroup.confirmed，无需先展示精确移除清单和成员提案。预览不等于保存，之后写入仍须用户明确要求保存。

### 两个布局陷阱

1. 预置 `compact` 使用当前阅读顺序生成完整布局，再经平台引擎投影与校验。单纯紧凑不询问业务先后原则；只有 `reorder` 接受模型决定的完整业务顺序，不能给 compact/align 附加 orderedWidgetIds。混合高度下以回执 orderPreserved 为准，不无条件声称顺序、齐底均已满足。
2. 仅旧版显式 `items` 受 30 项上限和分批碰撞问题影响。预置路径生成全量候选、统一校验并一次持久化；不要主动拆批或降级。

每次布局回执都可能包含其他归一化变化。读取这些变化，下一批使用真实位置和最新版本；回执明细可能截断（最多 200 项），按计数和 `readbackAction` 决定是否重新分页。

### 容器

没有获批 regroup 时，不要将容器子组件直接提交到根布局工具。`dashboard.set_layout_child_order {layoutWidgetId,orderedWidgetIds,expectedResourceRevision}` 用于受支持且保留的容器子项排序；使用前确认当前 schema、实际 chartType 及完整子项集合。它不等于任意子图尺寸编辑，也不能替代原子重组。没有对应能力时保留子布局并说明限制。

`dashboard.set_widget_container` 与 add/delete/copy 工具不能用来逐卡重建、搬出子卡或串联建组，也不得整页覆盖。3A/3B/3C 的 `set_widget_layout` reorder preset 携带 `groups:[{title,widgetIds}]`、`groupingConfirmed:true`、`regroup:{containerWidgetIds:[全部旧平铺Widget ID],confirmed:true}`、`sizing:"content"` 和完整有效根 orderedWidgetIds，外层带 expectedResourceRevision。成员全局唯一且在完整顺序中连续；Tab 及其子项保持归属和几何。

3A/3B/3C 重组直接传 `regroup:{containerWidgetIds:[全部现有原生平铺Widget ID],confirmed:true}`，1-200 个唯一字符串 ID，每个 1-100 字符；不得包含 Tab。它要求 groups、groupingConfirmed:true、mode:reorder、expectedResourceRevision。有效根列表包含全部被释放的平铺子卡、当前非容器根卡和保留的 Tab 根，不含被移除平铺；所有卡片 ID、数据和非布局配置保持不变。旧平铺移除、子卡提升、新组创建、功能 2 紧凑布局及归属变更只能一次原子事务保存。

当前 schema 未暴露 groups/regroup 时如实说明不支持对应操作，不猜参数或假装能力存在；不擅自替换用户选择的模式。失败后不自动排除旧容器、受保护子卡或隐藏子卡再调用。按 [beautification.md](beautification.md#可选平铺分组与降级) 处理确认和结果。

## 验证

普通布局核对前后组件 ID 集合和归属未变；3A/3B/3C 允许移除全部旧平铺并创建替代平铺，但必须保留全部非容器卡片及 Tab 的 ID、数据和非布局配置。检查 `data.grouping.groups` 与 `removedContainerWidgetIds`，后者必须与调用时全部旧平铺 ID 一致且不得含 Tab；dryRun 时仅表示拟移除清单。各坐标系同级矩形合法且不重叠，阅读顺序符合本次单遍结果。

仅结构不能证明文字没被裁切或内部已紧凑。有可用页面预览时按 Skill 执行“一次整体诊断、一次整体复查、最多一轮定向修正”；没有则记录“配置校验，未做页面视觉验证”。纠正尺寸时只提交相关展示字段并携带最新版本，不用旧快照覆盖整个看板。写入失败、部分成功或能力缺失都如实报告实际范围。
