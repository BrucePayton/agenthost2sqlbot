---
name: manage-space
description: Use when a user asks to open or create a collaborative space, rename, transfer ownership, delete or upgrade a space, respond to invitations, manage members and roles, or edit the space business menu (业务目录/功能页). Not for subscription push rules and not for dashboard widget configuration.
---

# Manage Davinci Collaborative Space

动作不明或已有对象待用户选择，先问并结束，不查上下文、定位或权限。目标明确后才按以下流程执行；权限以实时门禁为准，refs 和内部 ID 不能混用。

## 定位与导航

无 `space.list` 且有 `ui.open_space_page` 时进入空间域一次；到达后仍无工具就说明不支持，不循环导航。已有空间工具时，改名、批量成员查询、转让、删除、升级直接 `space.list` 定位后执行，不打开详情。只有当前空间成员编辑、邀请候选和目录操作需要 `space.open`；遇 `SPACE_CONTEXT_REQUIRED` 按 requiredAction 恢复。导航返回已完成 Handler ACK，同一 Session 直接继续。

## 工作流

- **改名：** 目标明确后 `space.list` 精确匹配唯一名称，再 `space.update_info {spaceRef,name}`。省略 description 保留描述；明确清空才传空串。重名先澄清。
- **查成员：** `space.member.list_by_spaces {queries:[{spaceRef,page:1}]}`，最多 10 个不同空间，每页 20 人。hasMore 为 true 继续分页；query 可按姓名搜索。forbidden/error 不等于空名单；仅目标 owner/admin 可读。memberRef 绑定空间，分页扩充候选，重读 space.list 后失效。
- **转让：** 从上述成员查询中找到用户指定的已加入成员，调用 `space.transfer_owner {spaceRef,memberRef}`；重名不取第一人，禁止跨空间 ref。原生确认展示新所有者、本人保留角色（默认 admin，可选 member/none）；只有当前 owner 可提交。
- **删除：** `space.delete {spaceRef}` 调用原生软删除或组织删除审批；默认通知成员，用户在确认框选择。组织空间 APPROVAL_FAILED 是无需审批的原生例外。data.state=deleted 才报告移入回收站，approval_pending 只报告已提交审批。
- **升级：** `space.upgrade {spaceRef,departmentName}`，仅小组空间 owner。部门全名由工具唯一解析；无匹配或重名就澄清。原生确认后提交，approval_pending 不是升级完成。
- **创建：** `space.create` 小组空间成功后自动打开并等待成员 Handler ACK，可继续邀请成员。组织空间需 departmentName，先展示原生确认、再提交审批；不询问或猜部门 ID。approval_pending 不能报告已可使用。
- **邀请：** 先 `space.get_context` 读待处理邀请，以 invitationRef 调用 `space.invitation.respond`。
- **当前空间成员编辑：** `space.get_context` 读角色和能力，`space.member.get_context` 读成员或邀请候选，再 `space.member.apply_changes`。这里的 ref 不与批量成员查询混用。
- **目录：** `space.menu.get_context` 读完整树，再 `space.menu.apply_changes`；读取截断时不重排。
- **空间看板：** 同时要求配置指标时，先按 CLAUDE.md 完成必要业务选择；用户明确先建空看板时按其顺序做。目标目录唯一后 `space.menu.get_context` 取 groupRef，再 `space.dashboard.create_and_open`；多个目录先问，不向 menu.apply_changes 传 create_page。成功回执已完成创建、刷新与导航，后续沿用，不重复创建。

## 写入与结果

面向用户直接说「已提交审批，等待结果」「所有权已转让」「已加入」等业务结果，不在括号或代码块附上 state、角色枚举或 ref。审批只代表申请提交，尚未删除或升级；取消只报告本次未执行。回执里的状态字段用于判断，不抄进最终回复。

每次只提交一个 operation；不同业务写入不合成假事务。refs 只来自当前 generation 的最近有效读取，不猜后端 ID。移除成员、删除目录/功能页、转让、删除、创建/升级组织空间使用原生确认；取消零写入，不能传 confirmed 绕过或自动重试。

PERSISTENCE_OUTCOME_UNKNOWN 按 requiredAction 先核对空间状态/所有者，不直接重试；一次读回仍不能确认就报告。PERSISTED_NAVIGATION_FAILED 或 partial 且已持久化，只补刷新/定位，不能重做写入。

订阅消息规则归 configure-subscription-rule；空间 owner/admin 也不保证能编辑他人规则，原生权限属于最后更新人，以 canManage 为准。个人规则不能因缺 page.space 判不可用。

永久删除、清空回收站、审批人的通过/拒绝、跨空间复制整张看板不支持，直接说明边界。组件配置用 configure-dashboard-widget。

参数以当前 schema 为准；[tools.md](references/tools.md) 遇疑问才查，不开局全读。
