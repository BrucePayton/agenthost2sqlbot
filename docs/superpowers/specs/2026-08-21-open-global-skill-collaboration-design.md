# Agent Host 全局 Skill 开放协作设计

## 目标

Agent Host 处于内测阶段。所有已认证且已写入 Agent Host 用户表的用户都可以上传、
覆盖、重命名导入、查看详情和归档全局 Skill，以便多个用户通过全局 Skill 共享存在
相互依赖的能力。

## 行为边界

- 不开放匿名访问；所有接口继续依赖现有 `Identity` 认证。
- 不限制全局 Skill 的创建者；任一已认证用户都可更新或归档任一全局 Skill。
- 同名冲突、乐观 Hash 校验、Bundle 安全校验和软归档语义保持不变。
- 新发布或更新的全局 Skill 默认进入所有个人 Workspace 的有效集合。
- 用户此前关闭某个全局 Skill 的 Workspace 级设置保持不变。
- 已有 Session 固定原 Skill 版本；只有之后创建的新 Session 使用最新有效版本。
- 个人 Skill 权限与行为不变。

## 技术方案

保留现有 `/api/admin/global-skills/*` 路径，避免破坏当前 Web 客户端和 UAT 网关契约；
路径名称仅作为兼容标识，不再代表调用者必须具备 `skill_admin`。

新增显式的 `global_contributor` 写授权：

1. 路由在读取上传正文前确认调用者是已注册用户。
2. `SkillService` 在解析 Bundle 和执行变更前再次确认调用者。
3. `SkillRepository` 在创建、覆盖或归档的写事务内锁定并确认 `users.id` 仍存在：
   PostgreSQL 使用 `FOR UPDATE` 行锁；SQLite 因忽略该语义且延迟开启事务，须在读取
   用户前执行 `BEGIN IMMEDIATE` 取得写保留锁，防止预检查后身份被删除的竞态。
4. `can_manage_global_skills` 对已注册用户返回 `true`，现有前端因此展示全局导入和
   归档入口；组件仍保留能力字段判断，以便未来重新收口。
5. 全局分组说明改为“团队共享，所有用户可上传”，避免继续暗示仅由平台管理员维护。

`skill_admin` 角色表、启动引导配置和运维 CLI 保留，但不再作为内测期全局 Skill
变更的门槛。本次不增加数据库迁移，不新增依赖，也不修改 Artifact 与 Session 快照
结构。

## 错误与并发

- 身份不存在时继续以 `404 skill_not_found` 失败，且上传路由不得读取请求正文。
- 同名上传继续返回 `409 skill_import_conflict`，用户可选择覆盖或重命名。
- 覆盖和归档继续要求匹配当前 Bundle Hash；并发更新返回既有 `skill_changed`。
- 全局与个人 Skill 名称冲突继续在数据库事务和名称锁中处理。
- 用户删除与全局 Skill 写入并发时只能按事务先后顺序完成：删除先完成则写入拒绝，
  写入先完成则删除只能在该写入提交后继续，禁止“删除已提交但写入仍成功”的顺序。

## 验证

- 服务测试证明普通注册用户可创建、覆盖、重命名和归档全局 Skill。
- 事务测试证明用户在预检查后被删除时写入失败且数据不变，并通过真实 SQLite/WAL
  交错证明授权读取与归档提交之间不存在删除穿透窗口。
- API 测试证明普通用户可通过文件夹和 ZIP 路径管理全局 Skill，未知身份在读取上传
  正文前失败。
- Workspace API 测试证明普通用户获得 `can_manage_global_skills=true`。
- 浏览器测试证明未授予 `skill_admin` 的用户能看到入口、完成上传，并且新 Session
  获得 Skill；已存在 Session 仍固定旧版本。
