# Agent Host 全局与个人 Skill 管理设计

## 文档状态

- 日期：2026-08-19
- 分支：`codex/global-personal-skill-management`
- 基线：`codex/identity-workspace-session@f03dd1e`
- 状态：产品与架构方向已确认，等待书面规格最终审阅
- 当前目标环境：本地与单机 UAT
- 未来目标环境：多副本 Agent Host
- 2026-08-21 内测权限调整：全局 Skill 管理改为所有已认证注册用户开放，详见
  `2026-08-21-open-global-skill-collaboration-design.md`；本文原 `skill_admin`
  权限段仅保留为收口方案和历史设计记录。

## 1. 结论

Agent Host 只提供两类 Skill：

1. **全局 Skill**：平台统一维护，所有用户可见；每个个人 Workspace
   可以单独开启或关闭，默认开启。
2. **个人 Skill**：归属一个个人 Workspace，由该 Workspace 的用户上传、
   更新、开启、关闭或归档；首次上传默认开启。

所有 Skill 配置变更只影响之后创建的新 Session。Session 创建时固定精确的
Skill 版本和内容 Hash，已有 Session 不因 Skill 更新、开关或归档而变化。

持久化采用可替换的控制面与 Artifact 存储：

| 环境 | 元数据 | Skill Bundle |
|---|---|---|
| 本地 | SQLite | 本地持久化文件系统 |
| 单机 UAT | SQLite 或现有 PostgreSQL | 单机持久化文件系统 |
| 未来多机 | PostgreSQL | OSS、S3 或 MinIO |

本期实现 SQLite/PostgreSQL 共用的数据模型和文件系统
`SkillArtifactStore`。对象存储适配器不在本期实现，但 Artifact Key、不可变版本、
Session 快照和服务边界从本期开始保持稳定，使后续迁移不改变产品 API。

## 2. 范围

### 2.1 本期包含

- iframe 顶部 Skill 入口和 iframe 内完整管理视图；
- 全局 Skill 与个人 Skill 两个分组；
- Skill 文件夹、`.skill` 和 `.zip` 上传；
- 全局与个人 Skill 的查看和开启/关闭；
- 个人 Skill 的上传、替换、重命名导入和归档；
- 平台 Skill 管理员对全局 Skill 的上传、替换、重命名导入和归档；
- 不可变 Skill 版本、Bundle Hash 和 Artifact 存储；
- 新 Session 的有效 Skill 解析、版本固定和本地物化；
- 当前数据库 BLOB Skill 的无损迁移；
- SQLite 与 PostgreSQL 的单元、集成和迁移验证。

### 2.2 本期不包含

- 团队 Workspace Skill；
- Skill 市场、分享、订阅和评分；
- Git 仓库同步；
- 浏览器内创建或编辑 `SKILL.md`；
- 跨 Workspace 复制；
- 用户可见的版本历史、回滚和版本选择；
- 对象存储适配器及多副本部署；
- 管理平台 Skill 管理员的 UI；
- Session 其他工作文件的跨机器恢复。

现有团队 Workspace Skill API 仅保留兼容读取，不在新管理页暴露，也不扩展新能力。

## 3. 当前代码基线

当前实现已经具备以下能力：

- `skills.content` 保存根 `SKILL.md` 文本；
- `skill_files.content_blob` 按路径保存支持文件；
- 文件夹与 ZIP 上传共用 Bundle 解析、路径安全检查和确定性 Bundle Hash；
- `SkillService` 支持同名冲突、覆盖、重命名、启停和归档；
- Session 创建时读取当前 Workspace 的已启用 Skill；
- `workspace_snapshot_json` 保存 Skill Manifest；
- Session 目录保存 `.claude/skills/<name>/SKILL.md` 及支持文件；
- 已有 Session 不重新读取当前 Skill 数据。

本设计保留 Claude Agent SDK 的文件系统执行契约，仅替换管理范围、持久化来源、
有效 Skill 解析和管理界面。

## 4. 产品行为

### 4.1 管理入口

iframe 顶部现有 `Skills` 按钮继续作为唯一入口。点击后：

- 整个会话应用壳（含顶部栏、侧栏、聊天时间线和输入区）隐藏；
- 页面切换为完整的 Skill 管理视图；
- 管理视图提供明确的“返回会话”按钮；
- 返回时恢复此前选中的 Workspace、Session 和聊天滚动状态。

Skill 管理不再使用 `dialog`，切换管理页时不创建或切换 Session。

### 4.2 管理页

页面固定显示两个分组：

```text
Skill 管理                                      返回会话

全局 Skill
  平台提供，默认开启；开关仅影响之后的新会话
  [名称] [描述] [版本] [已开启/已关闭] [开关]

个人 Skill                                      上传 Skill
  仅属于当前个人 Workspace
  [名称] [描述] [版本] [已开启/已关闭] [开关] [更多]
```

“上传 Skill”只保留两个选择：

- 选择包含根 `SKILL.md` 的文件夹；
- 选择 `.skill` 或 `.zip` 包。

点击一行显示只读详情，包括名称、描述、来源、版本号、Bundle Hash、支持文件
Manifest 和创建时间。更新通过重新上传完成，不在页面内直接编辑文件内容。

全局分组中，普通用户只有个人开关。拥有平台 Skill 管理权限的用户额外看到上传、
替换和归档操作。

所有开关和上传成功反馈必须显示“仅对新会话生效”。

### 4.3 默认与更新规则

- 新发布的全局 Skill 对所有个人 Workspace 默认开启；
- 用户关闭全局 Skill 后，更新该全局 Skill 不改变其关闭状态；
- 新建个人 Skill 默认开启；
- 替换个人 Skill 创建新版本并保留原启停状态；
- 重命名导入创建新的个人 Skill并默认开启；
- 同名且 Bundle Hash 相同的上传返回幂等成功，不创建新版本；
- 归档只影响新 Session，已有 Session 保持不变；
- 删除语义统一为软归档，不直接删除 Artifact。

## 5. 权限模型

### 5.1 个人 Skill

个人 Skill 必须归属 `kind='personal'` 的 Workspace。该 Workspace 的 Owner 可以：

- 查看个人 Skill；
- 上传、替换、重命名导入和归档；
- 开启或关闭；
- 在新 Session 中使用。

未授权访问继续使用当前 fail-closed 404 行为，不暴露其他 Workspace 或 Skill 是否存在。

### 5.2 全局 Skill

所有有个人 Workspace 的用户可以查看全局 Skill，并写入自己个人 Workspace 的开关。

全局上传、更新和归档需要独立的平台能力 `skill_admin`。该能力不能复用
Workspace Owner/Admin，因为每个用户都是个人 Workspace Owner。

新增 `platform_role_bindings`：

| 列 | 含义 |
|---|---|
| `user_id` | 内部用户 UUID |
| `role` | 本期唯一值 `skill_admin` |
| `granted_by` | 授权操作者；系统引导可为空 |
| `created_at` | 授权时间 |

主键为 `(user_id, role)`。本期不提供管理 UI；通过部署引导配置或运维 CLI 为首个
用户写入绑定。API 和前端 Bootstrap 只消费 `can_manage_global_skills` 能力，不直接
判断用户标识。

## 6. 有效 Skill 解析

个人 Workspace 新 Session 的有效 Skill 集合为：

```text
所有 active 全局 Skill
  - workspace_global_skill_settings 中 enabled=false 的 Skill
+ 当前个人 Workspace 中 active 且 enabled=true 的个人 Skill
```

全局设置不存在时按 `enabled=true` 处理。解析结果按 Skill 名大小写无关排序，保证
快照和运行参数稳定。

### 6.1 名称冲突

同一有效集合不允许存在大小写无关的同名 Skill：

- 同一 Workspace 的个人 Skill 名保持唯一；
- 个人上传若与 active 全局 Skill 同名，返回 `409 skill_name_conflict`；
- 全局发布若与任一 active 个人 Skill 同名，返回 `409 skill_name_conflict`，详情只向
  平台 Skill 管理员返回冲突 Workspace 数量，不返回其他用户信息；
- 全局更新不允许把名称改为存在冲突的名称；
- 归档后名称可以重新使用。

全局和个人同名检查必须在同一数据库事务内按规范化名称串行化。SQLite 依赖其单写
事务；PostgreSQL 使用数据库事务锁，不能依赖单进程内存锁。

## 7. 数据模型

### 7.1 `skills`

`skills` 表表示逻辑 Skill，版本内容移出该表。

| 列 | 含义 |
|---|---|
| `id` | 稳定 UUID |
| `scope` | `global` 或 `workspace` |
| `workspace_id` | 全局为空；个人 Skill 指向个人 Workspace |
| `name` | `SKILL.md` 中的规范名称 |
| `normalized_name` | 大小写无关冲突键 |
| `description` | 当前版本描述，用于列表和自动补全 |
| `current_version_id` | 当前发布的不可变版本 |
| `enabled` | 仅个人 Skill使用；全局 Skill 必须为真 |
| `config_json` | 来源和迁移信息，不保存文件内容 |
| `created_by` | 创建者；系统导入可为空 |
| `archived_at` | 软归档时间 |
| 时间字段 | 创建和更新时间 |

约束：

- `scope='global'` 时 `workspace_id IS NULL`；
- `scope='workspace'` 时 `workspace_id IS NOT NULL`；
- 全局 active `normalized_name` 唯一；
- 同一 Workspace 内个人 active `normalized_name` 唯一；
- `current_version_id` 必须属于当前 `skill_id`；该跨表条件由事务服务验证。

现有 `content` 和 `bundle_hash` 列在迁移期保留为兼容列，新写入链路不再以其为权威；
完成至少一个版本的兼容验证后另行删除。

### 7.2 `skill_versions`

| 列 | 含义 |
|---|---|
| `id` | 版本 UUID |
| `skill_id` | 所属逻辑 Skill |
| `version_no` | Skill 内从 1 递增的版本号 |
| `bundle_hash` | 解包后规范内容 Hash |
| `artifact_key` | 与后端无关的内容寻址 Key |
| `artifact_sha256` | 标准化压缩包本身的 SHA-256 |
| `manifest_json` | 文件路径、大小和内容 Hash |
| `size_bytes` | 标准化压缩包大小 |
| `status` | `validating`、`ready` 或 `failed` |
| `created_by` | 上传者；系统迁移可为空 |
| `created_at` | 创建时间 |

唯一约束为 `(skill_id, version_no)`；同一 Skill 的相同 `bundle_hash` 不重复创建版本。
版本记录不可修改，只有 `status` 可从 `validating` 单向变为 `ready` 或 `failed`。

### 7.3 `workspace_global_skill_settings`

| 列 | 含义 |
|---|---|
| `workspace_id` | 个人 Workspace |
| `skill_id` | 全局 Skill |
| `enabled` | 当前 Workspace 是否启用 |
| `updated_by` | 操作者 |
| `updated_at` | 更新时间 |

主键为 `(workspace_id, skill_id)`。设置行只允许引用个人 Workspace 和 active/global
Skill。全局 Skill 归档后可以保留设置行，重新发布同一逻辑 Skill 时沿用用户选择。

### 7.4 Session 快照

`workspace_snapshot_json.schema_version` 从 2 升到 3，每个 Skill 固定：

```json
{
  "id": "skill-uuid",
  "scope": "global",
  "version_id": "version-uuid",
  "version_no": 3,
  "name": "brainstorming",
  "description": "...",
  "bundle_hash": "sha256:...",
  "artifact_key": "skills/sha256/ab/...zip",
  "files": [
    {"path": "references/checklist.md", "sha256": "sha256:...", "size_bytes": 1234}
  ]
}
```

旧的 schema v2 Session 继续按已物化文件运行，不迁移、不重新下载 Skill。

## 8. Artifact 存储

### 8.1 接口

新增与业务模型无关的 `SkillArtifactStore`：

```python
class SkillArtifactStore(Protocol):
    def put(self, artifact: SkillArtifact) -> StoredArtifact: ...
    def read(self, artifact_key: str) -> BinaryIO: ...
    def exists(self, artifact_key: str) -> bool: ...
    def delete(self, artifact_key: str) -> None: ...
```

业务服务不能拼接本机绝对路径，只使用 `artifact_key`。本期实现
`FilesystemSkillArtifactStore`；未来实现 `ObjectSkillArtifactStore` 时保持接口和 Key
不变。

### 8.2 文件系统后端

默认配置：

```text
SKILL_ARTIFACT_BACKEND=filesystem
SKILL_ARTIFACT_ROOT=<APP_DATA_DIR>/skill-artifacts
```

标准 Key：

```text
skills/sha256/<hash前2位>/<完整bundle_hash>.zip
```

Bundle 在写入前被标准化：根 `SKILL.md` 与所有支持文件按规范路径排序，ZIP 元数据
使用固定时间和权限。写入流程为同目录临时文件、`fsync`、Hash 校验、原子
`os.replace`。同 Key 已存在时校验 `artifact_sha256` 后幂等返回。

单机 UAT 必须把 `APP_DATA_DIR` 挂载到持久化磁盘，不能使用容器临时层。SQLite
`app.db`、Skill Artifact、Session 和 Memory 均位于该持久化边界内，但使用独立子目录。

### 8.3 未来对象存储后端

多副本部署时切换为 PostgreSQL 和 OSS/S3/MinIO。对象存储后端继续使用同一
`artifact_key`，执行 `put-if-absent`、读取和完整性校验。运行节点本地只保留按
Bundle Hash 命名的可删除缓存，不作为权威来源。

本期不增加对象存储 SDK 依赖。

## 9. 上传与发布流程

文件夹和 ZIP 共用以下服务流程：

1. 在读取正文前完成 Workspace 或平台权限校验；
2. 使用现有有界 multipart 收集器读取上传内容；
3. 验证根 `SKILL.md`、YAML Frontmatter、文件路径和 Bundle 限制；
4. 拒绝绝对路径、`..`、反斜杠、NUL、重复路径、路径前缀冲突、软链接、硬链接、
   设备文件和 ZIP 路径穿越；
5. 继续使用 10 MiB 单文件、50 MiB 解压后总大小和 200 个支持文件的默认限制；
6. 对 ZIP 同时限制条目数、声明解压大小和实际解压字节，防止压缩炸弹；
7. 生成现有语义一致的 `SkillBundle` 和 `bundle_hash`；
8. 生成标准化 ZIP、`artifact_sha256` 和 Manifest；
9. 原子写入 Artifact Store；
10. 在数据库事务内创建 Skill/Version 或切换 `current_version_id`；
11. 仅在 Version 为 `ready` 后返回成功。

Artifact 先写、数据库后提交。数据库提交失败时 Artifact 是不可见的孤儿对象；启动
维护任务根据数据库引用清理超过保留期的孤儿。更新失败时旧
`current_version_id` 保持不变。

冲突响应继续使用现有行为：

- 相同名称、相同 Hash：`already_imported`；
- 相同名称、不同 Hash且未选择策略：`409 skill_import_conflict`；
- `overwrite`：校验 `expected_hash`，创建新版本并原子切换当前版本；
- `rename`：只改上传副本 Frontmatter 名称，创建新 Skill；
- 并发更新失败：`409 skill_changed`。

## 10. Session 创建与运行

新 Session 创建：

1. 校验用户对个人 Workspace 的访问；
2. 在数据库事务中解析全局设置和已启用个人 Skill；
3. 只选择 `current_version.status='ready'` 的版本；
4. 构建 schema v3 Session 快照并写入 Session 记录；
5. 从 Artifact Store 读取每个固定版本；
6. 校验压缩包 SHA-256、解压后的 Bundle Hash 和 Manifest；
7. 在 Session 临时目录写入 `.claude/skills/<name>/`；
8. 原子完成 Session Workspace 物化；
9. 全部成功后 Session 进入 `idle`。

任一 Artifact 缺失、损坏或暂时不可读时，Session 创建返回明确的基础设施错误，
不能静默省略 Skill。失败的临时 Session 目录被清理，失败记录保留稳定错误码用于排障。

恢复已有 Session 时继续使用其物化目录，不查询当前 Skill。未来多机恢复可以根据
schema v3 快照重新物化同一版本；Session 内其他可变文件的跨机恢复不属于本期。

## 11. API

### 11.1 用户 API

```text
GET    /api/workspaces/{workspace_id}/skills
GET    /api/workspaces/{workspace_id}/skills/{skill_id}
POST   /api/workspaces/{workspace_id}/skills/import
POST   /api/workspaces/{workspace_id}/skills/import-directory
PATCH  /api/workspaces/{workspace_id}/skills/{skill_id}/enabled
DELETE /api/workspaces/{workspace_id}/skills/{skill_id}

PUT    /api/workspaces/{workspace_id}/global-skills/{skill_id}/setting
```

Workspace Skill 列表响应分组返回：

```json
{
  "global": [],
  "personal": [],
  "effective_count": 0,
  "changes_apply_to": "new_sessions"
}
```

全局条目的 `enabled` 是当前 Workspace 的有效开关；个人条目的 `enabled` 来自 Skill。

### 11.2 平台管理员 API

```text
POST   /api/admin/global-skills/import
POST   /api/admin/global-skills/import-directory
GET    /api/admin/global-skills/{skill_id}
DELETE /api/admin/global-skills/{skill_id}
```

替换和重命名继续通过上传字段 `on_conflict`、`expected_hash` 和 `target_name` 表达。
普通用户调用管理员 API 统一返回 fail-closed 404。

### 11.3 Session API

`GET /api/sessions/{session_id}/skills` 继续读取 Session 快照和已物化目录，不读取当前
全局或个人配置，保证自动补全与实际运行内容一致。

## 12. 迁移

迁移分两层，禁止在 Alembic 数据库事务中直接写外部文件。

### 12.1 Schema 迁移

- 扩展 `skills` 的 scope、规范名称和当前版本字段；
- 允许全局 Skill 的 `workspace_id` 为空；
- 新建 `skill_versions`、`workspace_global_skill_settings` 和
  `platform_role_bindings`；
- 保留 `skills.content`、`skills.bundle_hash` 和 `skill_files` 作为兼容来源；
- 为 SQLite 和 PostgreSQL 分别建立等价约束和索引。

### 12.2 幂等 Artifact 回填

应用启动 Gate 在接受请求前执行幂等回填：

1. 查询没有 `current_version_id` 的 legacy Skill；
2. 从 `skills.content + skill_files` 重建并校验 Bundle；
3. 生成标准化 Artifact 并原子写入 Store；
4. 创建版本 1；
5. 将 legacy Skill 标记为 `scope='workspace'` 并切换当前版本；
6. 所有记录完成后写入 `app_metadata` 迁移标记。

任一步失败时启动失败且不修改旧权威数据；下次启动可以按 Hash 幂等重试。至少经过
一个发布周期并验证新链路后，才单独设计删除旧 BLOB 列的迁移。

模板内置 Skill 通过一次性发布命令导入全局目录，不在每次启动时扫描或覆盖管理员
已经发布的版本。

## 13. 配置与部署

新增配置：

```text
SKILL_ARTIFACT_BACKEND=filesystem
SKILL_ARTIFACT_ROOT=<optional; default APP_DATA_DIR/skill-artifacts>
```

本期只接受 `filesystem`。配置验证规则：

- Artifact Root 不能是文件、软链接或 Workspace/Session 子目录；
- 启动时创建并验证可写性；
- 路径必须通过已有 `APP_DATA_DIR` 安全边界解析；
- 单机部署可以使用 SQLite；Skill 功能不新增 PostgreSQL 启动 Gate；
- 若未来配置 Agent Host 副本数大于 1，必须在部署 Gate 切换 PostgreSQL 和对象存储。

UAT Docker/进程必须备份同一持久化边界下的 `app.db` 与 `skill-artifacts`。SQLite
备份使用在线 Backup API 或停写快照，不能在活跃写入时直接复制数据库文件。

## 14. 错误处理

保留现有错误包络和 request ID，并增加稳定错误码：

- `skill_artifact_unavailable`：Artifact 后端暂时不可用，HTTP 503；
- `skill_artifact_corrupt`：压缩包或 Bundle 完整性失败，HTTP 500；
- `skill_name_conflict`：全局/个人有效名称冲突，HTTP 409；
- `skill_scope_invalid`：非个人 Workspace 尝试创建个人 Skill，HTTP 422；
- `skill_version_not_ready`：当前版本未完成发布，HTTP 409。

上传、开关和 Session 创建都不得把基础设施错误错误映射为业务冲突。

## 15. 测试与验收

### 15.1 数据库与迁移

- SQLite 与 PostgreSQL schema、约束和索引一致；
- legacy BLOB 回填可中断、可重试且 Hash 不变；
- 回填后旧 Session schema v2 不变化；
- 并发同名上传只有一个有效结果；
- 全局/个人同名并发不会同时发布；
- 全局设置默认开启且 upsert 幂等。

### 15.2 Artifact

- 标准化 ZIP 对相同 Bundle 产生相同 SHA-256；
- 临时写入和原子替换不暴露半包；
- Artifact 缺失、截断、被篡改均被识别；
- 路径穿越、软硬链接、设备文件、超限文件和 ZIP 炸弹被拒绝；
- 孤儿 Artifact 清理不删除任何被版本引用的对象。

### 15.3 权限与 API

- 普通用户能查看和切换全局 Skill，但不能发布全局 Skill；
- 个人 Workspace Owner 能管理自己的个人 Skill；
- 跨 Workspace 访问 fail-closed；
- 平台 Skill 管理员能发布和归档全局 Skill；
- 非个人 Workspace 不能使用新的个人 Skill 写接口；
- 文件夹和 ZIP 上传保持相同冲突语义。

### 15.4 Session 不变性

- 全局 Skill 默认出现在新 Session；
- 关闭全局 Skill 后只从之后的新 Session 消失；
- 个人上传成功后默认出现在新 Session；
- 替换 Skill 后旧 Session 使用旧版本，新 Session 使用新版本；
- 归档后旧 Session 仍可使用已物化 Skill；
- Session 自动补全只列出其快照中的 Skill；
- Artifact 故障导致 Session 创建明确失败，不创建缺 Skill 的 Session。

### 15.5 UI

- 顶部入口在聊天页始终可见；
- 点击后进入非弹窗管理视图并能返回原会话；
- 两个分组、权限操作和“仅对新会话生效”提示正确；
- Workspace/页面切换使迟到请求失效；
- 上传冲突期间不丢失已选择文件；
- 文件夹和 ZIP 都能完成真实浏览器端到端测试。

### 15.6 单机与未来迁移边界

- 本地和单机 UAT 在未配置 PostgreSQL 时使用 SQLite 正常工作；
- UAT 重启后数据库、Artifact 和新旧 Session 均可恢复；
- Artifact Store 契约测试可被未来对象存储实现复用；
- 模拟“节点 A 上传、节点 B 物化”时只依赖数据库记录和 Artifact Store，不读取上传
  节点的临时目录。

## 16. 实施边界

本期实现完成后，Agent Host 仍是单机部署，但 Skill 管理的产品 ID、版本、快照和
Artifact Key 已满足多机迁移要求。未来多机阶段只新增：

1. PostgreSQL 部署与 SQLite 数据迁移；
2. OSS/S3/MinIO `SkillArtifactStore`；
3. Artifact 本地缓存；
4. Session 其他工作文件的持久化与跨节点恢复。

不得在本期为了模拟多机而引入 Redis、对象存储 SDK、分布式锁或新的调度系统。
