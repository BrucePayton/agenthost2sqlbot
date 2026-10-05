# 错误处理

数据检索类错误（`INTENT_MODEL_UNAVAILABLE`、resolve `not_found`、结果超长等）见 locate-data Skill 的 errors.md。

| 错误/现象 | 下一步 |
|---|---|
| `INVALID_ARGUMENT` 且 `details.path` 指向 `$.spec.…`（如 `$.spec.filters[0].name` "is not allowed"） | 把读回 `effectiveSpec` 里的只读字段（`name`/`type`）拷进了写入 spec。删掉这些键后重试一次写入。 |
| `DATASET_NOT_AVAILABLE` | 先确认 datasetType/datasetUid；再做目录权限校验并重试 `dashboard.apply_widget_spec`。不要用空的预加载选项列表下结论。 |
| `FIELD_NOT_FOUND` | 用同一 datasetRef 搜字段，核对传入的是 fieldId 而非完整 fieldRef/名称。 |
| `FIELD_ROLE_MISMATCH` | 按 metric/dimension/date 角色重新选字段；不要强行绑定。 |
| `REQUIRED_FILTER_MISSING` | 核对原生元数据中 `required=true` 的字段并补充有效 filter；`requiredCondition` 只是默认带出，不能要求用户为默认创建日期再选一个无关范围。 |
| `TIME_FILTER_REQUIRED` | 选择符合指标口径的有效日期筛选。成交日期＝昨日可以替换默认创建日期；不要同时补一个创建日期窗口来凑约束。 |
| `CHART_SHAPE_INVALID` | 按图表最小形状补齐/减少指标和维度。 |
| `CONTEXT_STALE` 或版本冲突 | 重新读取页面上下文/Widget 配置，用新 revision 重新编译一次。 |
| 写结果无 `persisted:true` | 除非同时是 `status:success, persisted:false, state:unchanged` 且读回预期匹配的 `data.effectiveSpec`（already satisfied，不要重试），否则视为未完成；读取错误并决定修正或停止。 |
| 写成功但数据不可用 | 用 `dashboard.get_widget_config` 的 `data.effectiveSpec` 验证配置；如配置正确，明确报告数据查询错误。 |
| `network error` | 不重复所有前置工具；在页面恢复后只重试最后一个必要的读回工具一次。 |
| `FRONTEND_TOOL_SERIALIZED` | 同一条回复里已经有一个 `davinci_ui` 前端工具在执行。立即结束这条回复，不要同轮重试；等上一个工具的结果返回后，在下一条回复里再调用。 |
| `PAGE_TOOL_IN_FLIGHT` | 页面工具已在本条回复里挂起，它之后的任何调用（`Read`/`Grep`/数据检索都算）都会被拒。立即结束这条回复；下一条回复先做完其他调用，页面工具放最末尾。 |
| `PROBE_BUDGET_EXCEEDED` | dryRun 探针次数用完。停止探测，把每次探针改了哪个变量、各自查到多少行，连同当前口径一起汇报给用户，由用户决定改法。 |
| `COMPARISON_NOT_SUPPORTED` | 目标 Widget 不是指标卡，`apply_widget_spec` 的 `comparison` 不适用；去掉 `comparison` 或改用指标卡。 |
| `COMPARISON_TIME_FILTER_INVALID` | `comparison.timeFieldId` 引用的筛选不是同环比可用的时间筛选；核对 filters 里的时间字段和 valueExp 是否符合[时间表达](fields-and-time.md)。 |
| `COMPARISON_METHOD_UNAVAILABLE` | 该时间表达不支持所选 `method`（如非 `last_days` 场景下用了不兼容口径）；按时间表达重新选可用的 method 或去掉该组。 |
| `ELIGIBLE_DATE_FILTER_REQUIRED` | **人工/旧编辑路径**（`dashboard.apply_widget_edits` 的能力）返回；改用 `dashboard.apply_widget_spec` 的 `comparison` 可直接避开此错误。 |
| `LIMIT_NOT_SUPPORTED` | 该图表类型没有 Top N（只有排行榜 `11001` 有）。去掉 `limit`，向用户说明；不要换成 maxRows 或排序来“模拟”。 |
| 写结果 `status:success, persisted:false, state:unchanged` | 配置与已保存内容相同、本次未写入。不是失败，不要重试；直接读回预期匹配的 `data.effectiveSpec`，并如实报告“already satisfied / no write needed”。 |
| `PUBLISH_REQUIRES_USER_REQUEST` | 本轮用户没说要发布（或说了不要）。停止发布；不要要求用户"再说一遍发布"，如实说明即可。 |
| `CAPABILITY_CONTRACT_MISMATCH`（报告标签，不是工具错误码） | 写回执与 `data.effectiveSpec` 矛盾。最多再读一次；仍不一致就停止并如实报告，不 refresh/publish/重复写，不猜测其他编辑者。 |
| `MESSAGE_WIDGET_NOT_QUERYABLE` | 停止查询该消息 Widget，并如实报告当前只能由原生消息卡读取。 |
| `DASHBOARD_AMBIGUOUS` | 请用户按空间名和 Dashboard 名选择一个可信目录目标。 |
| `CHART_TYPE_NOT_AVAILABLE` | 改用能力列表中当前可用的创建入口；消息类型不要走 `apply_widget_spec(create)`。 |
| `TOO_MANY_TARGETS` | 把目标按每批不超过 50 个 Widget 拆分后再执行。 |
| `CREATE_FAILED` | 重新读取 Dashboard 结构，确认补偿后没有残留再决定后续操作。 |
| `CREATE_ROLLBACK_FAILED` | 立即停止写入，报告返回的真实残留 Widget ID 并交由人工核对；禁止盲重试。 |
| `PUBLISH_FAILED` | 立即停止发布并报告服务端结果未知；当前会话禁止盲重试。 |
| `EMPTY_READBACK_NEEDS_USER` | 该组件上次读回 0 行，平台拦下了继续改写。不要换个写工具绕行；用 `dryRun:true` 探针每次只改一个变量定位，再把口径与探针结果交给用户决定。 |
| `WRITE_BUDGET_EXCEEDED` | 本轮写入次数已达上限。停止写入，汇报已完成与未完成的部分及卡点，由用户决定下一步。 |
| `REPEATED_CALL_BLOCKED` | 相同参数的调用已执行过，拒绝文案里附了上次的结果。直接用那个结果；确需重查先改变参数并说明原因。 |
| `QUERY_NOT_DISPATCHED` | 该组件的查询**从未发起**（`retryable:false`）：写入只落配置、不触发查询，新建组件尤其如此。调一次 `dashboard.refresh_widget` 再读；重试 `get_widget_data` 或加长 `timeoutMs` 都不会有结果。 |
| `WAIT_TIMEOUT` | 查询确实在跑、只是没在等待窗口内结束（`retryable:true`），可以再等一次。与 `QUERY_NOT_DISPATCHED` 的分界就是「有没有在跑」，两者处理方式不能混用。 |

## 通用错误码（所有页面工具）

- `EXECUTION_FAILED`：处理器抛出的未分类失败，原因在 `message`；若 `details.failureCode` 存在（如 `DUPLICATE_SPACE_NAME`、`USER_CANCELLED`、`DASHBOARD_NOT_FOUND`），以它为准处置，不要重试同一输入。
- `INVALID_ARGUMENT`：`details.path` 指出出错字段（如 `$.candidateRefs[1]`）；ref 类字段失效就重新读取再传，不改别的参数盲试。
- `PERMISSION_DENIED`：当前身份/视角/页面状态不允许，如实告知，不换工具绕过。
- `STALE_CONTEXT` / `PAGE_INSTANCE_CHANGED` / `RESOURCE_CHANGED`：页面或资源在执行中变了，重新读取上下文后再决定，不原样重发。
- `HANDLER_NOT_READY`：工具在目录里但当前页面没挂载它（如个人工作台的消息中心 tab 上调订阅工具）；只允许在页面仍在加载时重试一次，否则先导航到支持该工具的页面。
- `TOOL_NOT_AVAILABLE`：当前实时门禁不允许（忙、快照、代查、无权），是终态。
- `PERSISTENCE_OUTCOME_UNKNOWN` / `PERSISTED_NAVIGATION_FAILED`：见 manage-space 的空间工具约定；前者按 `requiredAction` 读回一次，后者写入已完成、禁止重做。
- `ACTION_REPLACED`：改用错误里的 `requiredAction`。
- `SPACE_CONTEXT_REQUIRED`：先 `space.list` 再 `space.open`。
- `NEEDS_USER_CLARIFICATION` / `CATALOG_SEARCH_EXHAUSTED` / `PROBE_BUDGET_EXCEEDED` / `WRITE_BUDGET_EXCEEDED`：运行时护栏，拒绝消息里已写明出路；照做并结束本轮。
- `DATASET_NOT_FOUND`：`datasetRef` 未命中（已删除、不在当前用户可见范围或格式有误）；用 `dataset.marketplace.search` 按业务词重新搜索，不要凭旧 ref 重试。
- `EDITOR_ALREADY_OPEN`：已有一个数据集编辑器草稿在打开中，同时只能开一个；先完成或关闭当前草稿再开新的。
- `NOT_DATASET_OWNER`：当前用户不是该自建数据集的创建者或所有者，不能编辑；如实告知，不要绕过权限重试。
