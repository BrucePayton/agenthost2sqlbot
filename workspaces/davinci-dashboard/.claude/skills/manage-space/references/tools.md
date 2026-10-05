# 空间工具约定

参数一律以当前注入的工具 schema 为准，本文件不复述参数；任何示例与 schema 冲突时以 schema 为准。

- 空间角色能力只是上限；实际 action catalog/execute 仍按当前可信 Page State 复核实时门禁。
- 实时门禁至少包括 `busy`、`publishStatus`、`embed/snapshot`、Widget presence/eligibility 与 `revision`；owner/admin 不保证必然可发布，member 打开数据推送/预警也要通过各自门禁，alert 仅限 eligible Widget。
- 角色上限内，member 只可读数据、使用运行时控制并打开数据推送/预警；delegated 只可读数据与只读配置，不能使用消息、持久化或发布工具。
- `ui.open_space_page` 只导航，不创建、删除、移交空间，也不做成员增删或角色管理。不要把它当成 `space.open`。
- `ui.open_space_page` 固定导航到 `/share/collaborative-space`，不接受 `spaceId`、path 或 URL。
- `ui.open_personal_workspace` 固定导航到 `/share/workbench-new`，用于离开协同空间且不接受 path、URL 或资源参数。
- 所有 ref 都是本次页面 generation 内的临时能力，不是后端 ID；切页或重新读取后必须使用最新返回的 ref。
- **候选人 ref 例外**：`space.member.get_context`（`mode=invite_candidates`）是「扩充候选集合」而非「推翻上次读取」，同一空间页面内先搜到的 `candidateRef` 会一直有效。邀请多人的正确做法是按姓名逐个搜完，再用一次 `space.member.apply_changes` 提交全部 `candidateRefs`（上限 30）；不需要搜一个邀一个，也不要为绕开重复调用限制而换关键词重搜同一个人。仍返回 `INVALID_ARGUMENT` 说明该 ref 确实不属于当前空间页面（中途切过空间或页面被重开），重搜这个姓名再提交，不要盲试其他参数。
- `space.menu.apply_changes` 的 `create_group` 与 `update` 成功回执带 `targetRef`，它就是新建/被改目录的 `groupRef`/`itemRef`，可直接用于 `space.dashboard.create_and_open`，不必再读 `space.menu.get_context`。
- 明确的业务拒绝按拒因办，不要当未知结果处理：`EXECUTION_FAILED` 且 `details.failureCode` 形如 `EXECUTION_FAILED:DUPLICATE_SPACE_NAME` 时，message 已写明原因（空间名称已存在），直接转告用户并请其换名——不必再 `space.list` 核实，更不要用同一个名称重试。`PERSISTENCE_OUTCOME_UNKNOWN` 才是结果未知：按 `requiredAction` 读回一次，仍不能证实就停下向用户报告，不要反复读回或重试写入。
- `space.dashboard.create_and_open` 只接受最新菜单上下文中的 `groupRef`；成功返回后新仪表盘的 Dashboard 工具已经就绪。
- `space.menu.apply_changes` 不再接受 `operation=create_page`；收到 `ACTION_REPLACED` 时必须改用错误指定的 `requiredAction`。


## 按目标空间管理（无需打开详情）

`space.member.list_by_spaces` 接收 queries，每项 spaceRef、page、query；最多 10 个不同空间、每页 20 人。只读目标自己的 owner/admin 权限。memberRef 绑定空间，分页扩充候选，space.list 重读会失效。

`space.transfer_owner {spaceRef,memberRef,retainedRole?}` 默认 admin，可选 member/none；`space.delete {spaceRef,notifyMembers?}` 默认通知；`space.upgrade {spaceRef,departmentName}` 使用部门全名。均由原生确认最终决定，模型不能传 confirmed 绕过。仅目标 owner；待审批不等于完成。创建组织空间同样先确认。改名省略 description 会保留已有描述。
