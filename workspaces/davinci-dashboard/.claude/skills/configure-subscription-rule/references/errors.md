# 错误恢复

模型按原生工具回执修正同一任务，Host 只保留真实事实及通用的超时、重复失败、权限和未知结果保护。

先读 code、reason、currentRevision 和真实回执；不以相同参数自动重复写入。

- OUTPUT_SCHEMA_INVALID（旧版可能为 EXECUTION_FAILED 且 message 指明 output schema）：创建/修改可能已经执行。保留当前来源，只读取一次 get_context；不能据此断言组件或草稿损坏。
- configuration.unresolved 的 READBACK_SCHEMA_INVALID：原生草稿未被删除，只有对应配置项无法完整回读。可填其他明确配置，不覆盖缺失项、不保存、不更换数据源。页面修正该项后重新读取，使用新 revision 继续。
- SUBSCRIPTION_RECOVERY_BLOCKED / SUBSCRIPTION_NO_PROGRESS：停止技术重试和推演，简短报告已知状态与具体阻塞。不是业务歧义，不要求用户重新描述口径、换数据集或清空草稿。
- 等待模型响应超时仅代表自动配置中断，不证明尚未保存、已经启用或已有内容写入。面向用户说明“自动配置暂时中断，请查看当前订阅设置和启用状态”；用户要求继续时，先 get_context 核对实际状态，不重复新建或保存。

- DRAFT_NOT_FOUND：get_context 核对当前页；仅用户仍要新建配置时 start。若只是保存、用户刚手动保存或原提交结果未知，不得重建。
- DRAFT_ALREADY_ACTIVE / TASK_TARGET_CONFLICT：先核对本次新建/继续/修改意图及 taskId。确为同一任务才续改；新需求面对无关旧稿保留现场，让用户处理这个对象冲突，不能直接覆盖或擅自再起一份。
- CONTEXT_STALE：仅草稿 revision 冲突时重读 configuration，保留手动编辑后有界重规划；身份/归属/页面变化先停止旧请求。
- SECTION_BUSY：等用户完成原生抽屉编辑后再读新 revision，不抢写。
- FIELD_LOCKED：保留模板及个人接收人锁，说明限制，不换 ref 绕过。
- OPTION_REF_STALE：先读 reason、expectedKind、path、requiredAction。unknown_reference 不代表来源不存在；kind_mismatch 表示引用类型不符，stale_scope 表示引用不属于当前页面/身份。field 查询的 datasetRef 须来自订阅候选或当前查询；目录证据应继续 bind_dataset_query，不必重新搜索同一来源。其余仅对失效候选及必要父级定向重搜。
- filter_readback_incomplete：按 requiredAction 重读当前配置；filtersComplete 仍为 false 时不按索引修改或重建整组筛选，报告具体回读缺口。
- SPACE_DIRECTORY_INCOMPLETE：目录页或总数不完整是技术缺口，由依据返回游标继续分页或报告缺口，不能缩短空间全名、伪装成业务歧义或改建个人规则。
- DRAFT_INVALID：修复 errors 指向的步骤/字段，重新静态 review；数据查询超时不等于口径错误。
- USER_CANCELLED：本次保存结束，保留未保存草稿，不自动再次确认。
- EXECUTION_FAILED 且页面恢复 editable：说明服务端明确拒绝的原因，修改后按新指令保存；不误报为仍等待确认。
- TOOL_TIMEOUT/TOOL_CANCELLED：未派发则未执行；已派发且无确定回执则结果未知，不重发 create。
- TOOL_NOT_AVAILABLE/HANDLER_NOT_READY：只按当前目录与 requiredAction 做一次有依据的页面接续，权限不绕过。
- PERSISTED_NAVIGATION_FAILED、committed:true：已创建但返回页面失败；报告两者，不再保存。
- 持久化结果未知或缺真实规则回执：保留未知状态，提示通过原生规则列表核对；名称相同不证明是本次创建，不能凭猜测重新提交。
- saveOutcome=unknown：get_context 只能观察，不能恢复或承诺十几秒后自动解锁；没有原请求的唯一证据时如实说明待核对，不建议刷新后重建。

后续操作必须有新事实或用户新指令；不要用反复 preview、start 或 save 探测错误是否消失。

CANDIDATE_SEARCH_STALLED 表示同一对象和角色连续多次关键词搜索为空。可以停止猜词、无关键词浏览或按真实字段角色/资格检索；仍缺口径时一次说明缺口。不能转用工作台菜单冒充订阅候选，也不能为了通过校验删除原需求中的条件。

- SUBSCRIPTION_SESSION_UPGRADE_REQUIRED：当前会话冻结的是旧订阅 Skill/执行协议。保留页面当前配置，告知需使用新版 Skill 新会话，并先读取现有配置再继续；不把旧计划重放到页面，也不假装当前已加载新 Skill。
- 表格快捷参数冲突：新正文使用 components；只修改具体内容 operation，不浅合并两套互斥列引用。不以删除表格规避错误。
- no-op 与 all：revision 未增长不等于写入失败；all 没有 queryRef 不等于绑定丢失。读取实际输出与状态即可，不循环“修复”。

- 名称冲突：服务端 validateCreate 会核验规则名唯一性；默认名也可能重名。只按真实名称错误调整 ruleName 后 finish，用户指定的名称冲突说明并澄清替代名；保留全部其他配置，不重新找数或起稿。
