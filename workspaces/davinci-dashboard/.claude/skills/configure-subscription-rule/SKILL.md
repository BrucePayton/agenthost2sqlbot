---
name: configure-subscription-rule
description: Configure editable subscriptions, notifications and alerts using native space.message_rule tools; the user previews and saves.
---

# 订阅配置

先在聊天正文简短回显时间、归属、来源、条件、接收人及内容，再执行确定项。理解依赖后合并证据已齐的配置；业务歧义才提问。

## 执行

1. **确定对象与归属。** 复用可信 Page State；指定空间未确认时，若当前页没有 `space.list`，先 `ui.open_space_page` 到空间入口，再 `space.list` → `space.open({spaceRef,destination:"subscription"})`。来源不等于归属。`space.message_rule.get_context` 读取实际配置；已有本次草稿直接续改，无关草稿先处理对象冲突。
2. **提交证据已齐的配置。** 新建用 `start_draft` 携带已知时间、发送方式等 operations，续改用 `apply_draft`；纯通知/仪表盘内容用 scheduled_no_dataset，定时数据用 scheduled_dataset，预警用 conditional。仅 mode=blank 传 scene（msg-notify/dashboard-push/data-alert）；template/widget 用对应来源 ref，不传 scene，保留种子口径与锁。工具自动补首次缺少的消息标题和规则名，用户指定值优先。
3. **定向找资源。** 已确认 ref 沿用。指定数据集直接定位，并一次考虑日期、指标、筛选、分组、动态员工及内容字段；未知来源按 `locate-data` 补业务证据。已有通用目录证据用 `bind_dataset_query` 精确绑定；原生候选用 `upsert_dataset_query`。预警指标用 `alert_widget`；正文组件用 `widget`。截图、AI、跳转复用同一父仪表盘，AI 以真实 contentKind=ai_interpret 为准。
4. **按依赖成批配置。** 直接调用原生工具，不另建计划协议。已确定的关联动作合并进一次 start/apply；所需引用已齐可一次写完并 finish；仍需找数或澄清时才先提交独立项。同批可用 queryKey/outputKey，跨批用回执 queryRef/outputRef。新增比较用 metricsMode=merge 保留原值；条件、变量和表格优先用真实 outputRef。
5. **内容以 components 为主。** 七类有序组件：富文本、数据表、仪表盘截图、仪表盘图表、图片、按钮、按钮组，含 AI 组件、变量、提及、格式、链接及下载。只改标题用 title 补丁，局部正文用 patch_content，不重建未要求改变的内容。
6. **一次核对后交接。** 原需求及用户补答与当前实际配置逐项对照；全部落实后最后一批加 finish=true。没有新写入时用 review_draft({expectedRevision,finish:true})。ready 只证明结构检查通过；不得把“配置合法”当作业务要求全部实现。no-op 的 revision 可不变，all 推送不需要 queryRef，均不当作失败。

工具参数按需阅读 [工具](references/tools.md)、[七步操作](references/state-and-operations.md)；业务分流和页面进度见 [流程](references/workflow.md)，完整参数示例见 [示例](references/examples.md)。不要为已知参数反复读全部参考。

## 业务判断

- 已配置事实以 configuration 为准；metadataReady=false 不等于未配置。已有查询改日期，确认 filtersComplete=true 后用 filterEdits 按当前索引及 fieldRef 定向替换，保留其他筛选/参数/输出；完整重设才传 filters。昨日/上月用滚动日期，日期字段从实际配置或定义核验。下降10%的比率阈值为 -0.1。AND/OR、大于/大于等于保持原义。
- all/record/group 按用户业务粒度选择；“一个表格”不代表整体一张卡。小订单可能有商品明细行，金额不能重复累加；条件、卡片、负责人、正文和下载范围须一致。
- 动态员工字段须有原生 isEmployeeAccount 与 oa/ob 资格，并加入相应查询维度。不让用户解释内部账号类型；全体空间成员接收与正文 @所有人不同，不互换。
- 用户只补答口径或纠正一项时，沿用已确认来源和成功配置。改变日期检查比较/标题，改变粒度检查映射/内容；修改前使用最新 revision，保留手动编辑。
- 技术报错按 [错误恢复](references/errors.md) 定向修正，不能转为业务澄清、重新找数或重建草稿。不要为了通过校验删除原需求。

## 展示与完成

Host 根据实际工具及 operations 在聊天正文显示进度，原生页面展示实际结果；模型不重复同一进度，不把过程放在“已工作”折叠区。用户手动切页时尊重其选择。

完成后基于最新回读总结适用项：名称、时间、来源及口径、条件、卡片范围、接收人、标题与内容。明确尚未保存、实际数据待验证，并提示：请在“命名并保存”页面右下角点击“发送预览”，检查实际数据和消息展示效果。确认符合预期后，再保存；需要调整也可以继续告诉我。若用户已手动切页，请其打开该页。

Agent 不调用数据预览、发送预览、试发、保存、启用或发送。结果不明确就说明具体未完成项，不以页面打开或文字摘要代替执行成功。
