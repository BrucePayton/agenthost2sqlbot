# Kubernetes 多租户 Workspace Agent 总体设计 V2

**日期：** 2026-07-29
**状态：** 已由用户确认，已完成外部 Review 处置与分阶段计划拆分；Phase 0 待实施
**适用项目：** `claude_workspace_mvp`
**取代文档：** `2026-07-28-kubernetes-multi-tenant-runtime-design.md`
**Review 处置：** `docs/superpowers/reviews/2026-07-29-kubernetes-multi-tenant-runtime-v2-review-disposition.md`

## 0. 结论与本次修订

目标架构仍采用“一个活跃 Session 一个短租约 Kubernetes Runner”，连续 Turn 复用同一个 Runner，完成全部收尾后空闲 5 分钟回收。

V2 不推翻总体方向，但收敛一期实现：

- Session 工作目录与 Claude 本地 transcript 使用每 Session 一个 CSI `ReadWriteOncePod` PVC，避免 RWX 多写者。
- PostgreSQL 是产品数据、执行状态、队列、租约和版本指针的唯一权威；一期不引入 Redis Stream 和 Outbox Relay。
- Personal Auto Memory 仍由 Claude Code 自动读写，但通过“本地物化 + S3 版本化 Bundle + PostgreSQL CAS”持久化，不长期共享挂载 RWX。
- 一期不实现自定义 SDK `SessionStore`。Claude transcript 以 Session PVC 上的本地文件为权威；后续只把 SessionStore 作为跨存储恢复镜像，不宣称替代本地 transcript。
- Runner 内分成可信 Supervisor 和不可信 Agent 两个容器。控制凭据只进入 Supervisor；Claude、Bash、Skill 和 Subagent 永远拿不到控制凭据。
- Turn 状态显式区分“执行前失败”和“外部副作用结果未知”，不承诺 exactly-once，不透明重放运行中的 Turn。
- Workspace 配置快照是只读能力快照，不是永久授权。membership、OBO、emergency deny、额度和撤权每次动态校验。
- SDK 功能快照与 Runtime 版本分离。停止分配候选镜像可以快速回滚；被候选版本写过的 Session 是否能降级，必须由 N/N-1 transcript 兼容测试决定。

## 1. 目标与一期成功标准

把当前单机、单实例 Workspace Agent 演进为可部署在公司数据中心 Kubernetes 集群上的多用户 Web 服务。

一期必须具备：

- 个人空间、团队空间分别映射为 Product Workspace。
- Team Workspace 内的 Session、消息、附件、工作文件和 Artifact 仍只对创建者可见。
- 团队成员共享只读 Knowledge/Config/Skill/MCP Bundle，不共享其他成员的 Session Workdir。
- 一个活跃 Session 对应一个隔离 Runner Pod；连续 Turn 温复用，Idle 5 分钟回收。
- Pod 回收后可以在同节点或其他节点重新挂载 Session PVC 并恢复 Claude Session。
- Personal Auto Memory 按 `user_id + workspace_id` 自动读写和跨 Session 复用。
- API 可多副本，Runner 可分布到多台工作节点。
- 外部身份、MCP、模型、对象存储和 Kubernetes 权限符合最小权限原则。
- SDK 被限制在 Adapter 和 Runner 镜像中，可以独立升级、灰度和停止分配。

一期 Pilot 默认指标：

| 指标 | 默认门槛 |
|---|---:|
| Pilot 最大并发 Runner | 20 |
| 单用户最大活跃 Runner | 2 |
| 单 Workspace 最大活跃 Runner | 10 |
| 同一 Session 执行中 Turn | 1 |
| 同一 `user + workspace` memory-active Turn | 1 |
| Warm Turn 分配 P95 | 3 秒以内 |
| Cold Session 到 Runner Ready P95 | 60 秒以内 |
| Runner Idle TTL | 5 分钟 |
| 单 Turn wall-clock timeout | 30 分钟 |
| Runner 最大年龄 | 4 小时，只在 Turn 间 drain |
| 控制面月可用性目标 | 99.5% |
| PostgreSQL/S3 RPO | 15 分钟以内 |
| 单区域 RTO | 60 分钟以内 |

这些是首个生产 Pilot 的保护性默认值。扩大规模前必须用真实文件操作、MCP、长 Session 和 Subagent 负载重新校准。

### 1.1 如何理解这些阶段

Phase 0–4 是累计建设，不是五套互相独立的产品版本。后一阶段继承前一阶段已经通过 Gate 的能力；Gate 未通过时，不能用“功能代码已经存在”代替可用性结论。

- **当前基线：** 本机或受信任网络中的单实例 MVP，已经能聊天、恢复 Session、上传附件、管理 Skill、调用已配置 MCP，并按 `user + workspace` 使用 Auto Memory。
- **Phase 0–1：** 主要改造边界、数据和身份。Phase 1 可以演示多用户控制面，但还没有多机器安全执行面。
- **Phase 2–3：** 只面向开发/测试 Workspace，验证 Kubernetes 隔离执行、持久化与恢复；仍不接真实生产用户和生产凭据。
- **Phase 4：** 第一个可以让受控真实用户使用的多租户生产 Pilot。
- **Phase 5：** 根据 Pilot 数据选择性演进，不预先承诺全部实现。

### 1.2 每一期能实现的功能

| 阶段 | 可用对象 | 用户能够使用的功能 | 管理/平台能力 | 本期仍不能做 |
|---|---|---|---|---|
| 当前基线 | 本机开发者、受信任单实例 | Workspace 聊天；Session 创建、恢复、停止、重命名和删除；图片/文件上传；Skill 导入、启停和 `/` 调用；已配置 MCP；个人 Auto Memory 跨 Session 复用 | SQLite 与本地文件持久化；Mock 身份；单实例进程锁 | 不能公网多租户部署；不能多 API 副本；没有 Kubernetes 隔离和真实 OIDC |
| Phase 0：运行时边界 | 与当前基线相同 | 用户体验保持不变，现有聊天、Skill、MCP、附件、resume 和 Memory 均继续可用 | SDK 被收口到 Adapter；执行位置由 Dispatcher 决定；健康检查报告 SDK/CLI/MCP 兼容能力和 cohort | 不增加真实登录、PostgreSQL、多机执行或新产品功能 |
| Phase 1：多租户控制面 | 开发/集成环境中的测试用户 | 使用真实 OIDC 登录；看到自己有权进入的个人/团队 Workspace；只访问自己创建的 Session、消息、附件和 SSE；重复提交不会产生两个 Turn | PostgreSQL 成为权威；Space membership 投影；API 可多副本；持久队列、状态机、事件历史与跨副本 SSE | 多副本模式还不能执行 Agent Turn；没有 Runner Pod；不能向真实用户开放 |
| Phase 2：Kubernetes 执行面 | 开发集群中的测试 Workspace | 在隔离 Runner 中运行聊天 Turn；同一 Session 连续 Turn 温复用；空闲 5 分钟回收；取消；Pod 回收后重新挂载 Session PVC 并 resume；使用只读测试 Skill/MCP | Controller、Execution Gateway、每 Session RWOP PVC、generation、execution barrier、hard fencing、Supervisor/Agent 双容器、基础网络隔离 | 只使用测试模型和无敏感数据；附件/Artifact/Memory 尚未完成 S3 级耐久；不能接生产凭据和真实用户 |
| Phase 3：耐久化与恢复 | 预生产测试用户 | 跨节点继续 Session；附件和结果 Artifact 可持久下载；个人 Auto Memory 跨 Session/Runner 使用；看到等待 Memory、恢复中、写操作结果未知等真实状态 | S3 版本化 Bundle；Memory lease + CAS；Artifact Service；Tool Operation Ledger；可恢复 finalization；备份、恢复与删除 tombstone | 仍未获得生产准入；non-idempotent 写 MCP 仍禁用；没有团队动态记忆和 Session 分享 |
| Phase 4：生产 Pilot | 服务端 allowlist 中的首批真实用户 | 在个人/团队 Workspace 中稳定聊天、用私有 Session、附件、Skill、只读 MCP、经审计且可幂等/对账的白名单写工具、个人 Memory、Artifact 和跨节点 resume；成员被移除后新操作及时失效 | 生产 sandbox、短期 OBO、动态撤权、emergency deny、配额、审计、指标告警、备份演练、SDK cohort 灰度和回滚 | 不面向全量用户；默认不开放非幂等写 MCP；不提供团队动态记忆、Session 发布或任意自定义镜像/stdio MCP |
| Phase 5：按数据演进 | Pilot 证明有需求后逐项开放 | 可能增加审批后的写 MCP、团队记忆、Web deferred approval、更快冷启动或跨区域恢复 | 可选 SessionStore recovery mirror、Redis 通知、Kueue、公平排队、Warm Pool、多区域 | 不是固定一期范围；每项必须有独立 Spec、风险评估、测试和上线 Gate |

因此，从产品视角看，**Phase 4 才是第一期可交付生产版本**；Phase 0–3 是它必须依次通过的工程里程碑。若只需要内部单机试用，当前基线即可继续使用，不需要等待 Kubernetes 全部完成。

## 2. 产品概念与已采用决策

为避免“Workspace”同时指产品空间和容器目录，统一使用三个概念：

| 概念 | 定义 | 可见性 |
|---|---|---|
| Product Workspace | 个人/团队空间的权限和能力配置域 | Workspace 成员可见 |
| Session Workdir | 单个 Session 的执行目录和 Claude 本地状态 | 仅创建者可见 |
| Team Knowledge Bundle | Workspace 管理员发布的 CLAUDE.md、Skill、MCP 与策略快照 | Workspace 成员只读使用 |

一期采用以下产品规则：

- Session 归属于一个 Product Workspace 和一个创建者。
- Workspace owner/admin 默认不能读取成员私有 Session 内容；平台合规特权访问另走受审计流程，不复用普通管理权限。
- 第一期不提供 Session 发布、转让或共享。
- 团队只共享管理过的配置与知识，不共享用户工作文件。
- Personal Auto Memory 范围是 `user_id + workspace_id`，不跨 Workspace。
- 同一用户在同一 Workspace 的多个 Session 可以同时打开，但使用 Auto Memory 的 Turn 必须串行。
- 第一期无团队动态记忆；稳定团队知识通过 Team Knowledge Bundle 发布。
- 第一期只开放 read-only MCP，以及已经证明支持 idempotency/reconciliation 的白名单写工具。
- 非幂等写 MCP 默认禁用；以后开放时必须接入 Tool Operation Ledger 和审批策略。
- 节点网络分区时优先一致性：旧 Runner 未硬隔离前，不启动新的写者。
- 运行中的用户被移出 Workspace 时，Gateway 立即撤销其后续模型、MCP 和 Artifact 权限，并 drain 当前 Runner。
- `outcome_unknown` 的 Turn 不允许直接一键重试；用户必须先看到可能已经执行的工具操作。

## 3. 方案比较

### 3.1 方案 A：RWOP Session Volume + S3 Memory Bundle（采用）

- 每 Session 一个 RWOP PVC，保存 Workdir 和 Claude 本地 transcript。
- Memory 只在 Turn 期间物化到 Pod 本地目录，完成后上传版本化 Bundle。
- PostgreSQL 同时承担业务状态、队列、租约与 CAS pointer。
- 无 Redis、Outbox、自定义 SessionStore。

优点：单写语义清晰、应用组件少、能复用 SDK 原生本地 transcript 和 Kubernetes CSI。

代价：Session 数量会产生较多 PVC；必须验证公司 CSI 的 attach/detach、快照、配额和回收能力。

### 3.2 方案 B：一期启用 SDK SessionStore（延后）

- 本地 transcript 仍先写盘，再通过 SDK SessionStore 镜像到 S3/PostgreSQL。
- 任意 Runner 可以从共享 Store 恢复。

优点：符合 SDK 官方跨主机恢复接口，降低对 Session PVC 的依赖。

代价：Python 项目仍需维护持久化 Adapter；mirror 是 best-effort，失败批次可能被丢弃；必须新增 degraded 状态、conformance 和双向版本兼容测试。

### 3.3 方案 C：共享 RWX + generation fencing（拒绝）

应用 generation 只能拒绝旧 Runner 的 Gateway 请求，不能撤销旧节点已经持有的文件句柄。节点分区时可能出现两代 Runner 同时修改 Workdir、transcript 或 Memory，不能作为生产基线。

## 4. 总体架构

~~~mermaid
flowchart LR
    Browser["Web Browser"] --> Ingress["Ingress / API Gateway"]
    Ingress --> API["Workspace Agent API\nFastAPI modular monolith"]
    API --> Identity["Data Center OIDC / Space Service"]
    API --> PG[("PostgreSQL")]
    API --> S3[("S3-compatible Object Store")]

    Controller["Sandbox Controller"] --> PG
    Controller --> K8s["Kubernetes API"]
    K8s --> Runner["Session Runner Pod\nSupervisor + Agent"]
    Runner --> PVC[("Per-Session RWOP PVC")]
    Runner --> Gateway["Execution Gateway"]

    Gateway --> PG
    Gateway --> S3
    Gateway --> Model["Company Model Gateway"]
    Gateway --> MCP["Company MCP / OBO Gateway"]

    S3 --> Bundles["Attachments / Artifacts\nConfig / Skill / Memory Bundles"]
    API -. "SSE history + PG notification" .-> PG
~~~

架构分为：

- **产品控制面：** Ingress、API、PostgreSQL、S3 和外部身份系统。
- **运行控制面：** Sandbox Controller 与 Execution Gateway。
- **不可信执行面：** Session Runner 中的 Agent 容器及其 Claude/Bash/Skill/Subagent 进程。

一期逻辑组件不等于微服务：

- Access、Workspace、Session、Turn、Artifact metadata 都是 FastAPI 模块化单体内部模块。
- Execution Gateway 与 API 可以同仓库，但使用独立 Deployment、内部 Service 和 ServiceAccount。
- Sandbox Controller 是独立 Deployment，只有它能访问 Kubernetes API。
- Model/MCP Gateway、OIDC、Vault、S3 和 CSI 复用公司能力。

## 5. 组件职责

| 组件 | 单一职责 | 不负责 |
|---|---|---|
| Workspace Agent API | Web API、OIDC Principal、Workspace/Session 权限、Turn 创建、SSE、管理配置 | 启动 Pod、执行 Claude |
| PostgreSQL | 产品权威数据、状态机、队列、租约、事件、版本指针 | 保存大文件、运行脚本 |
| Sandbox Controller | 把 PG desired state 协调成唯一 Runner Pod | 业务授权、模型调用 |
| Execution Gateway | Runner 注册/claim/heartbeat/event/finalize、动态授权、Artifact/Memory 提交 | Kubernetes 调度、保存长期凭据到 Runner |
| Supervisor 容器 | 持有短期 control credential，管理 Turn 生命周期、Memory 物化/提交和事件 | 运行模型生成的 Bash/Skill |
| Agent 容器 | 运行 Claude Agent SDK、Bash、Skill、Subagent | 持有 control credential、直接访问基础设施 |
| ClaudeSdkAdapter | 唯一依赖 `claude_agent_sdk` 的代码边界 | 业务权限和数据模型 |
| S3 | 附件、Artifact、Config/Skill/Memory Bundle 和备份对象 | Turn 队列、租约 |
| RWOP PVC | 当前 Session Workdir 和本地 Claude transcript | 跨 Session Memory、团队共享文件 |

## 6. 身份、授权与动态撤权

### 6.1 权威来源

- 数据中心 OIDC 是用户 identity authority。
- 数据中心空间服务是 Workspace membership authority。
- PostgreSQL 只保存内部 ID 映射、带 `source_version` 和 `expires_at` 的 membership projection，以及审计历史。
- projection 过期且外部服务不可用时，写操作和敏感读取 fail closed。
- 客户端、Prompt、模型、MCP 参数和自定义 Header 都不能提供可信 `user_id`、`workspace_id` 或 `obId`。

### 6.2 Session 私有规则

Session、消息、附件、Artifact、SSE、文件 API 和 transcript metadata 的每个查询必须同时验证：

~~~text
principal is current workspace member
AND resource.workspace_id == requested workspace_id
AND resource.created_by == principal.user_id
~~~

仅检查 Workspace membership 不足以保护 Team Workspace 中的私有 Session。

### 6.3 快照与实时授权

Session 快照固定：

- Team Knowledge Bundle 版本。
- Skill Bundle hash。
- MCP 候选 manifest。
- model policy 和 tool allowlist。
- SDK 功能 schema version。

以下信息不允许被快照永久固定，必须在每次敏感操作时动态覆盖：

- 当前 membership。
- 当前用户 OBO。
- emergency denylist。
- 已撤销 Skill/MCP。
- 用户和 Workspace quota。
- 高风险工具审批状态。

有效能力按以下公式计算：

~~~text
effective_capability
  = immutable_snapshot_candidate
  INTERSECT current_authorization_overlay
~~~

`current_authorization_overlay` 是单调收窄的 deny/constraint 层，不能加入快照中不存在的模型、Skill、MCP 或工具能力。Gateway 在每次模型请求、MCP 调用、Artifact/Memory 提交和其他敏感操作前重新求交集，并记录 snapshot hash、authorization policy version、decision 和 reason。撤权、membership 失效、emergency deny 或 quota 用尽立即让后续操作失败，即使 Session 快照仍包含该候选能力。

## 7. 数据权威性

| 数据 | 唯一权威 | 运行时副本/缓存 |
|---|---|---|
| Identity、membership | 数据中心身份/空间服务 | PG projection |
| Workspace、Session、Turn、消息 | PostgreSQL | 无 |
| Turn queue、Runner desired state | PostgreSQL | `LISTEN/NOTIFY` 仅唤醒 |
| Turn event history | PostgreSQL | SSE 连接内缓存 |
| Runner 当前状态与 generation | PostgreSQL | Kubernetes Pod 是执行事实 |
| Runner 实例历史 | PostgreSQL `runner_instances` | Kubernetes Event 辅助诊断 |
| Claude transcript | Session RWOP PVC 上的本地 JSONL | 后续 SessionStore 只做恢复镜像 |
| Session Workdir | Session RWOP PVC | S3/CSI Snapshot 备份 |
| Personal Auto Memory | PG current pointer + S3 immutable bundle | Turn 内 Pod 本地 `/memory` |
| Config/Skill Bundle | PG metadata + S3 content-addressed object | Runner 只读物化 |
| Attachment/Artifact | S3 object + PG owner/ACL/checksum metadata | Runner 按需物化 |
| 长期模型/MCP密钥 | 公司 Vault/Gateway | Runner 无副本 |

Redis 不在一期正确性路径中。需要更高通知吞吐时可以加入 Redis Pub/Sub，但它永远不是第二队列或第二事件权威。

## 8. Session Volume 与 transcript

### 8.1 每 Session 一个 RWOP PVC

PVC 只挂给当前 generation 的一个 Runner Pod：

~~~text
/session/workspace       # 用户可写 Workdir
/session/claude-config   # Claude 本地 transcript/config state
/session/outputs         # 待发布 Artifact
~~~

要求：

- Kubernetes 版本至少 1.29，存储必须是支持 RWOP 的 CSI。
- 新 generation 只有在旧 Pod terminal 且 CSI 确认旧 attachment 已解除后才允许挂载。
- 节点失联时不自动 force-detach 后立即创建新写者；由 CSI/节点 fencing 证明旧写者已失去访问后再恢复。
- `cwd` 和 `CLAUDE_CONFIG_DIR` 在所有 generation 中保持完全相同的容器绝对路径。
- `claude_session_id` 保存于 PG，但 PG 不复制或解释 SDK JSONL。
- PVC 删除前先 tombstone Session，完成 Runner hard fence、retention 和备份引用清理。

### 8.2 一期不启用自定义 SessionStore

理由：

- SDK SessionStore 是本地 transcript 的 best-effort mirror，不是替代品。
- mirror 失败不会停止 Turn，最终可能产生不完整 Store。
- Python 持久化后端仍需项目维护 Adapter。
- RWOP PVC 已满足同集群跨节点重新挂载与恢复的 Pilot 目标。

引入 SessionStore 的准入条件：

- RWOP PVC 数量或 attach 延迟成为已测量瓶颈。
- 已完成官方 conformance。
- 定义 `transcript_durable_through_turn_id` 和 `transcript_degraded`。
- `mirror_error` 后不会销毁最后一份完整本地 transcript。
- N 读取 N-1、N-1 读取被 N 写过的 transcript、compaction 和 Subagent resume 均通过。

## 9. 只读 Config、Knowledge、Skill 与 MCP

Session 创建时生成 content-addressed `ConfigBundle`：

~~~text
bundle_id
schema_version
workspace_id
CLAUDE.md
.claude/rules/**
.claude/skills/**
sdk-settings.json
mcp-manifest.json
model-policy.json
tool-policy.json
sha256
created_by
created_at
~~~

Runner 物化规则：

- Bundle 从 S3 下载到独立 volume，并以只读方式挂载。
- `/session/workspace/.claude` 和 `/session/workspace/CLAUDE.md` 使用只读 mount，不复制后再允许 Agent 修改。
- `setting_sources=["project"]` 只加载平台生成的只读 project source，不加载 Agent 可写的 user/local settings。
- `autoMemoryDirectory=/memory` 通过平台生成的只读 `--settings` 文件配置；不接受 Project settings 重定向。
- `skills` 只允许 Session manifest 中列出的名称和 hash。
- 生产环境默认不扫描宿主机或插件缓存目录。
- Workspace owner/admin 的新配置只影响新 Session；emergency deny 可以立即覆盖旧快照。

MCP 规则：

- Agent 看到的是能力名和参数契约，不看到长期凭据。
- 业务 MCP 请求通过公司 MCP/OBO Gateway，用户身份由可信 Principal 动态派生。
- 第一期只开放 read-only MCP；允许写操作必须声明 effect class、idempotency 和 reconciliation 能力。
- 任意用户上传的 stdio MCP 默认禁止；平台内置且经审计的 stdio MCP 可以随 Runner 镜像发布。
- 区分 **MCP Python SDK major version** 与 **MCP protocol revision**。包能够安装不等于 Runner、Claude CLI、Gateway 和服务端已经端到端支持该 revision 的新能力。
- 当前基线 `claude-agent-sdk==0.2.128` 要求 `mcp>=1.23.0,<2.0.0`，锁定解析结果为 `mcp==1.29.0`；不能把 MCP Python SDK v2 与 Claude Agent SDK 安装在同一 Runner 环境。
- 外部 MCP v2 Server 即使兼容旧 revision，也只能先按实际协商结果视为旧能力可用；在 canary 证明前不得宣称支持 v2 专属特性。
- 若需要提前试验 MCP v2，使用独立进程/镜像和明确的 Gateway Adapter，不污染稳定 Runner 的 Python 依赖环境。

## 10. Personal Auto Memory

### 10.1 范围与并发

Memory scope 固定为：

~~~text
memory_scope_id = hash(user_id, workspace_id)
~~~

同一 scope 同时只能有一个 `memory-active` Turn。其他 Session 的 Turn 显示 `waiting_for_memory_lease`，而不是笼统显示 queued。

### 10.2 版本化协议

PostgreSQL 保存：

~~~text
memory_scope_id
current_version
current_bundle_sha256
lease_generation
lease_holder_turn_id
lease_expires_at
updated_at
~~~

每个 Turn：

1. Supervisor 在 PG 事务中获得 memory lease。
2. Supervisor 从 S3 下载 `current_version` 的 immutable Bundle 到 Pod 本地 `emptyDir:/memory`。
3. Agent 通过平台 settings 使用 `/memory`，Claude Code 自行决定读取和写入 Markdown。
4. Turn 进入 finalizing 后停止 Agent 容器内的 Claude/Bash/Skill/Subagent 进程。
5. Supervisor 校验文件类型、路径、总大小和单文件大小，生成新 content-addressed Bundle。
6. 上传 S3 后以 `expected_current_version` 做 PostgreSQL CAS。
7. CAS 成功后更新 current pointer 并释放 lease；失败则丢弃候选 Bundle并重新排队或人工处理。

中断规则：

- Agent/Pod 中断时不提交该次 Memory 修改，上一稳定版本保持有效。
- Bundle 上传成功但 PG CAS 结果未知时，通过 bundle hash 和数据库读回确认，不盲目重试 pointer 更新。
- Memory 提交失败不抹掉已经生成的用户回答；Turn 可以 `completed`，但增加 `memory_commit_failed` warning。
- 一期不解析 `MEMORY.md`，不建立 facts 表，不做向量检索或自动 merge。

## 11. Runner 工作负载与硬 fencing

### 11.1 一期使用 Controller 管理普通 Pod

一期不使用会自动创建替代 Pod 的 Deployment/ReplicaSet，也不依赖 Job 自动重试。Controller 创建确定性名称的普通 Pod：

~~~text
runner-<session-short-id>-g<generation>
~~~

Pod 使用：

~~~yaml
restartPolicy: Never
activeDeadlineSeconds: 14400
automountServiceAccountToken: false
~~~

选择普通 Pod 的原因：

- 失败后不会由 Kubernetes 在应用未知副作用时自动补一个执行实例。
- `session_id + generation` 的确定性名称让 create 请求丢失后的 reconcile 幂等。
- Controller 已经负责 desired state，无需再让 Job Controller产生第二套替换决策。

后续若公司集群达到 Kubernetes 1.34+ 并完成重复实例故障测试，可以评估 Job + `podReplacementPolicy: Failed`；这不是一期前置条件。

### 11.2 Runner Pod 内部

Runner Pod 至少两个容器：

- **Supervisor：** 可信；持有彼此分离的短期 control/model/MCP execution credential；负责注册、claim、heartbeat、event、Memory、受限本地代理和 finalization。
- **Agent：** 不可信；运行 Claude Agent SDK 及其子进程；不持有任何 Gateway credential，也不持有数据库、S3、Kubernetes 或长期模型/MCP凭据。

Pod 必须设置 `shareProcessNamespace=false`，Supervisor 与 Agent 使用不同固定 UID/GID。控制/model/MCP credential、证书和私钥只挂载到 Supervisor，不能出现在共享 env、argv、`emptyDir`、Session PVC 或 Agent 可读的 projected volume。共享可写目录只承载有界协议帧或非秘密数据。

二者使用两个逻辑上分离的 Pod 内窄接口：

- **Lifecycle channel：** `prepare/start/interrupt/shutdown/result`，不携带 bearer token，Agent 不能提交可信 ID。
- **Current-Turn capability proxy：** 只暴露当前 lease 对应的 model/MCP 能力；由 Supervisor 注入 trusted context，并由 Gateway执行 schema、snapshot/overlay、quota、approval 和 Tool Ledger 校验。

Agent 只能：

- 获取当前 Turn 输入和只读 capability manifest。
- 上送当前 Turn 的标准化事件。
- 通过 Supervisor 暴露的当前 Turn 本地代理请求允许的 model/MCP 操作。
- 报告结果和退出状态。

它不能提交任意 `session_id`、`turn_id`、`user_id` 或 generation；这些由 Supervisor credential 和当前 PG lease 推导。

安全模型明确假设 Agent 启动的 Bash、Skill、Subagent 可能访问任何 Agent 可访问的 Pod-local endpoint，因此不把“子进程不知道端口”当作边界。即使子进程调用 capability proxy，也只能使用当前 Turn 已允许的无凭据接口，不能选择其他 Principal/Session/Turn、扩展 snapshot、读取 Supervisor credential 或执行未通过动态授权与 Ledger 的操作。

### 11.3 Hard fencing 协议

Heartbeat 丢失只进入 `Suspect`，不能直接授权新写者：

~~~text
Suspect
  -> FenceRequested
  -> Pod terminal confirmed
  -> PVC detached / storage fence confirmed
  -> HardFenced
  -> NewGenerationAllowed
~~~

禁止把以下事件当作 HardFenced：

- 只删除了 Kubernetes Pod 对象。
- API Server 中看不到 Pod。
- 30 秒没有 heartbeat。
- Gateway 已拒绝旧 generation。

如果节点失联且 CSI 不能证明 detach，Session 状态为 `recovery_waiting_for_storage_fence`，不牺牲一致性抢占恢复。

## 12. 状态机

### 12.1 Runner

~~~mermaid
stateDiagram-v2
    [*] --> Absent
    Absent --> Provisioning: queued Turn
    Provisioning --> Registered: Supervisor registers
    Registered --> Ready: volume and policy verified
    Ready --> Busy: Turn claimed
    Busy --> Finalizing: Agent process stops
    Finalizing --> Idle: transcript/artifact/memory finalized
    Idle --> Busy: new Turn before idle deadline
    Idle --> Draining: idle deadline or max age
    Draining --> Terminated: Pod exits
    Busy --> Suspect: heartbeat lost
    Finalizing --> Suspect: heartbeat lost
    Suspect --> FenceRequested
    FenceRequested --> HardFenced: Pod terminal and storage detached
    FenceRequested --> RecoveryRequired: hard fence cannot be proven
    HardFenced --> Terminated
    RecoveryRequired --> HardFenced: operator or storage confirms
    Terminated --> Absent
~~~

Runner 实例历史不能被新 generation 覆盖。每一代记录 Pod UID、node、image digest、SDK 版本、注册/开始/结束时间和退出原因。

### 12.2 Turn

~~~mermaid
stateDiagram-v2
    [*] --> Queued
    Queued --> WaitingForMemory: memory scope busy
    WaitingForMemory --> Assigned: lease acquired
    Queued --> Assigned: no memory wait
    Assigned --> Running: execution barrier committed
    Assigned --> FailedBeforeExecution
    Assigned --> CancelledBeforeExecution
    Running --> Finalizing: model loop ended
    Finalizing --> Completed
    Running --> Failed
    Running --> Cancelled
    Running --> Interrupted
    Running --> OutcomeUnknown: external write result unknown
    Finalizing --> RecoveryRequired: durable finalization incomplete
~~~

关键约束：

- PG partial unique index 保证同一 Session 最多一个 active Turn。
- Gateway 在 PG 原子提交 `running + execution_nonce` 后，Supervisor 才允许启动 SDK。
- 一旦进入 `running`，平台永不自动重放整个 Turn。
- Runner 只有在 transcript 本地落盘、Artifact 处理、Memory CAS 和进程清理完成后才能从 `finalizing` 进入 Idle。
- 5 分钟 idle 从 `idle_since` 开始计算，不从模型最后一个 token、浏览器断开或 Turn 初步终态开始计算。

## 13. Turn 端到端数据流

1. Browser 发送 `client_request_id`。
2. API 验证 OIDC、最新 membership 和 Session owner。
3. PG 事务幂等创建 `queued` Turn 与首条 event。
4. Controller 扫描有 queued Turn 且无可用 Runner 的 Session。
5. Controller 事务内生成下一 generation 和 `runner_instance`，创建确定性 Pod。
6. Supervisor 注册 Pod UID、node、image/sdk/protocol，并验证 PVC、Config Bundle hash 和当前 generation。
7. Supervisor 获取 Memory lease并物化 Memory Bundle。
8. Runner 通过 Execution Gateway claim Turn。
9. Gateway 原子写入 `assigned -> running`、execution nonce 与 attempt。
10. Agent 容器启动 SDK：`cwd=/session/workspace`，`CLAUDE_CONFIG_DIR=/session/claude-config`。
11. Agent 的模型/MCP 流量只通过批准的 Gateway；Supervisor 按序上送事件。
12. SDK 结束后 Turn 进入 `finalizing`，禁止 claim 下一 Turn。
13. Supervisor 清理 Agent 进程、发布 Artifact、提交 Memory Bundle、确认本地 transcript。
14. Finalization 完成后 Turn 才进入 completed，Runner 进入 Idle 并设置 5 分钟 deadline。
15. 新 Turn 在 deadline 前到达则 warm reuse；否则 Supervisor退出，Controller 清理 terminal Pod。

一期队列实现：

- `turns.status=queued` 是唯一队列。
- Controller 使用 `FOR UPDATE SKIP LOCKED` 扫描需要 Runner 的 Session。
- Runner long-poll 由 Gateway 查询 PG。
- PG `LISTEN/NOTIFY` 可减少轮询延迟，但丢失通知不影响正确性。
- SSE 先读 PG event history，再通过 PG notification 或短轮询获取新 sequence。

## 14. 外部副作用与 Tool Operation Ledger

平台不提供 exactly-once 幻觉。每个外部工具定义：

~~~text
effect_class = read_only | idempotent_write | non_idempotent_write
retry_policy
approval_policy
supports_reconciliation
idempotency_key_contract
~~~

`tool_operations` 至少记录：

~~~text
operation_id
turn_id
turn_attempt_id
tool_call_id
tool_name
effect_class
request_digest
idempotency_key
status
downstream_operation_id
authorization_policy_version
reconciliation_status
started_at
dispatched_at
completed_at
response_digest
unknown_reason
~~~

状态转换固定为：

~~~text
prepared
  -> dispatched
  -> succeeded | failed_known | outcome_unknown
outcome_unknown
  -> reconciled
~~~

只有在持久化 `prepared` 后才能向下游发送请求；发送前后都记录 attempt、lease generation 和 policy version。`outcome_unknown` 只能通过受支持的 reconciliation 转为 `reconciled`，不能靠重复发送原操作“验证”。

规则：

- read-only 工具允许受控 transport retry。
- idempotent write 只有下游明确实现幂等键时才允许自动重试。
- non-idempotent write 超时或响应丢失立即进入 `outcome_unknown`，Gateway 不重发。
- 用户重试创建新 Turn，不复用旧 execution nonce。
- UI 在允许新 Turn 前展示上一 Turn 已知工具操作、未知结果和 reconciliation 入口。
- 第一期默认禁用 non-idempotent write，因此可以先完成 Ledger 基础模型和 read-only 审计，再开放写工具。

## 15. 取消、删除与恢复

### 15.1 取消

- API 写 `cancel_requested_at`，Gateway 立即阻止新的外部工具调用。
- Supervisor 请求 SDK interrupt，并等待 Agent 清理。
- 已发送的 MCP 请求可能仍完成；没有确认结果的写操作记为 `outcome_unknown`。
- 超过 grace period 后 drain 整个 Pod，不把状态不确定的 Runner放回 Idle。

### 15.2 Session 删除

- 先写 tombstone，撤销新请求和 Runner 权限。
- 等当前 Runner HardFenced。
- 按 retention 删除 PVC、transcript、附件引用和 Artifact。
- Personal Auto Memory 属于 `user + workspace`，不随单 Session 删除。
- 删除操作记录对象版本和审计；备份恢复不能让已删除数据重新暴露。

### 15.3 故障分类

| 故障点 | Turn 结果 | 自动重放 |
|---|---|---:|
| PG commit 前、SDK 未启动 | `failed_before_execution` | 可创建新 attempt |
| SDK 已启动、无外部写证据 | `interrupted` | 不自动 |
| 外部写已发送、响应未知 | `outcome_unknown` | 禁止 |
| 模型完成、Memory 提交失败 | `completed` + warning | 不重放 |
| transcript/PVC 状态无法确认 | `recovery_required` | 禁止 |
| Runner 启动失败、未 claim Turn | 保持 queued 或 `failed_before_execution` | 可重新分配 |

Session Workdir 在崩溃后默认保留原状，并将 Session 标为 `recovery_required`。一期不自动回滚文件；用户继续前必须看到中断提示。未来如启用 SDK file checkpointing，需要单独评估其与 SessionStore 的兼容约束。

## 16. Sandbox 与网络安全

### 16.1 Pod 安全

Runner 使用独立 sandbox Node Pool 和 Namespace，默认：

- Pod Security restricted。
- `runAsNonRoot`、固定 UID/GID。
- Supervisor 与 Agent 使用不同 UID/GID，`shareProcessNamespace=false`。
- `allowPrivilegeEscalation=false`。
- capabilities drop `ALL`。
- seccomp `RuntimeDefault`，并评估 AppArmor/SELinux profile。
- readonly root filesystem，仅 `/session/workspace`、`/session/claude-config`、`/memory`、`/tmp` 和受控 outputs 可写。
- 禁止 privileged、hostNetwork、hostPID、hostIPC、hostPath 和 hostPort。
- `automountServiceAccountToken=false`。
- CPU、RAM、ephemeral storage、PID、文件总量和单文件限制。

gVisor 作为候选 RuntimeClass，不直接作为未经验证的既定前提。上线前比较 gVisor、Kata 和 restricted container 对 Git、Python、npm、压缩解压、stdio MCP 与公司 CSI 的兼容性和 P95/P99 性能。

### 16.2 网络

Namespace 默认 deny ingress/egress。Agent 容器只允许：

- Pod 内 Supervisor 暴露的当前 Turn 本地代理。
- 受限 telemetry sidecar；也可以由 Supervisor 转发 telemetry。

Agent 不直接访问公司 Model/MCP Gateway。SDK 的 `ANTHROPIC_BASE_URL` 和 MCP endpoint 指向 Supervisor 的本地代理；Supervisor 再通过短期、分 audience 的 credential 调用 Execution Gateway。Runner Pod 只允许访问 Execution Gateway，不能直连 PostgreSQL、S3、Redis、Kubernetes API、metadata service、模型供应商或业务系统。

Supervisor 与 Agent 位于同一个 Pod 时共享网络命名空间，标准 Kubernetes NetworkPolicy 不能按容器区分 egress。因此一期不声称在网络层阻止 Agent 向 Execution Gateway 发包，而是通过以下组合保证其不能成功调用：Gateway 只接受 Supervisor 独占的 mTLS/短期 credential；该凭据不挂载到 Agent；所有请求绑定当前 Runner generation 和 Turn lease；无凭据或错误 audience 的直接请求被拒绝并审计。若后续合规要求达到“Agent 连 Gateway TCP 都不可达”，需把 Supervisor 拆为独立网络身份，或采用经过公司验证的 per-process/cgroup eBPF 策略，另行设计和验收。

同理，Pod-local capability proxy 对 Agent 可达就视为对 Bash/Skill/Subagent 可达。它不是通用 Supervisor 管理 API：不得暴露 credential、任意 URL 转发、任意 trusted ID、文件读取或 Runner 控制能力；每次调用都被当前 Turn、有效 snapshot/authorization 交集、schema、effect policy、quota 和 Ledger 约束。

### 16.3 凭据

- Supervisor control credential、model credential 和 MCP/OBO credential 使用不同 audience 和 scope。
- Control credential 只进入 Supervisor 容器，不通过共享环境变量、共享文件或 Claude Prompt 下发。
- Agent 不获得任何 Gateway bearer token；它只能访问不含长期凭据的本地代理，代理从当前 lease 推导 Session、Turn 和用户上下文。
- 长期凭据由公司 Gateway/Vault 在 Runner 安全边界外注入。
- 所有日志、错误、event 和 Artifact metadata 做 secret redaction。

## 17. PostgreSQL 核心模型

一期新增或调整：

| 表 | 关键字段/职责 |
|---|---|
| `identity_mappings` | issuer、subject、internal_user_id |
| `workspace_membership_projections` | workspace、user、role、source_version、expires_at |
| `config_snapshots` | bundle_id、schema_version、sha256、created_by |
| `sessions` | workspace、created_by、snapshot_id、claude_session_id、deleted_at |
| `turns` | client_request_id、status、effect_state、finalization_status、warning_code |
| `turn_attempts` | attempt、runner_generation、execution_nonce、started/ended |
| `turn_events` | server-assigned sequence、event type、payload、created_at |
| `session_runtime_state` | current_generation、desired_state、current_runner_id |
| `runner_instances` | generation、pod_uid、node、image/sdk/protocol、status、exit reason |
| `tool_operations` | effect class、idempotency、downstream ID、outcome |
| `memory_scope_state` | current version、bundle hash、current lease generation |
| `memory_versions` | immutable version、S3 key、sha256、size、creator turn |
| `artifacts` | S3 key、checksum、owner、workspace/session/turn、retention |
| `audit_events` | actor、decision、resource、reason、trace，不默认存 Prompt 内容 |

数据库约束：

- `(session_id, client_request_id)` 唯一。
- 每个 Session 最多一个 active Turn 的 partial unique index。
- 每个 Session 只有一个 current generation。
- Memory pointer 更新必须比较 expected version 和 lease generation。
- Turn event sequence 由数据库/Gateway 分配，不信任 Agent 自报 sequence。
- Runner 实例历史 append-only；`session_runtime_state` 只保存 current pointer。

## 18. Claude Agent SDK 兼容策略

### 18.1 Adapter 边界

只有 `ClaudeSdkAdapter` 包允许 import `claude_agent_sdk`。业务代码只看到内部类型：

~~~text
RuntimeRequest
RuntimeEvent
RuntimeResult
RuntimeCapabilities
~~~

禁止：

- 业务层依赖 SDK dataclass。
- 解析或修改 SDK 私有 JSONL。
- 在业务代码散布版本比较。
- 把 beta OTel span 字段当稳定业务契约。

### 18.2 版本与 cohort

- 2026-07-29 基线为 `claude-agent-sdk==0.2.128`、`mcp==1.29.0`；项目声明 `claude-agent-sdk>=0.2.128,<0.3`，Runner image 按 lockfile/digest 固定。
- `ConfigSnapshot` 记录功能 schema，不记录不可变 runtime 镜像。
- 每个 `runner_instance` 记录实际 image digest、Claude Agent SDK、Claude CLI、MCP SDK、内部 Runner protocol 和已验证 MCP protocol revision/feature set。
- 候选 SDK 先用于测试 Workspace 和专用 canary Session。
- 被候选版本写过的真实 Session 固定在 candidate cohort，直到证明可降级。
- 回滚的准确含义是“停止给新 generation 分配候选镜像”；既有 Session 是否能回旧镜像由兼容测试决定。

候选版本必须通过：

- Adapter unit/golden tests。
- 新 Session、正常 resume、跨 Pod resume。
- N 读取 N-1 transcript。
- N-1 读取被 N 写过的 transcript，或明确禁止降级。
- compaction、Subagent resume、Skill、MCP、Memory、interrupt 和 timeout。
- in-process MCP Server 与 HTTP/stdio 外部 MCP 的连接、能力发现、工具调用、取消和错误映射；MCP major/revision 变化时单独形成兼容矩阵。
- Runner image Kind/K3s smoke。
- 指定 canary Workspace 的真实模型测试。

MCP v2 的解除阻塞条件：

1. Claude Agent SDK 官方依赖不再限制 `mcp<2.0.0`，或稳定 Runner 明确改为隔离进程 Adapter。
2. Adapter 不向业务层泄漏 MCP SDK v1/v2 类型。
3. in-process、HTTP、stdio 与公司 MCP/OBO Gateway 的端到端矩阵通过。
4. 明确记录实际协商 protocol revision；不能用“服务端是 v2”替代协商证据。
5. v2 专属功能逐项 feature-gate；旧 cohort 不能收到其无法解释的请求或事件。

当引入 SessionStore 时再增加官方 conformance、mirror_error、degraded resume 和 store retention 测试。

## 19. 可观测性、容量与成本

关键指标：

- API latency/error、SSE 连接和 PG queue depth。
- queued、waiting_for_memory、assigned、running、finalizing 的停留时间。
- cold start、warm dispatch、Runner ready、idle hit rate。
- Suspect、hard-fence wait、orphan Runner、duplicate registration。
- PVC attach/detach latency、snapshot error、storage fence wait。
- `outcome_unknown`、stuck finalizing、memory CAS conflict、memory commit failure。
- SDK/CLI 启动失败、resume 失败、config hash mismatch。
- MCP readiness、model latency、token/cost、tool call、Subagent fanout。
- Agent/Supervisor CPU、RSS、disk IO、PID 数量。

日志包含 `trace_id/workspace_id/session_id/turn_id/attempt/runner_generation`，但默认不保存 Prompt、tool body、文件内容或长期凭据。

成本优化顺序：

1. 5 分钟 Idle TTL。
2. 镜像预拉，不建设自研 Warm Pool。
3. 限制 maxTurns、wall time、Subagent fanout 和存储额度。
4. 调整 Pod request/limit 和 Sandbox 节点池最小容量。
5. 用 warm hit 与冷启动数据调整 Idle TTL。
6. 规模证明 PG notification 不够后再引入 Redis。
7. 公平调度成为真实问题后评估 Kueue，不自研 Scheduler。

## 20. 备份、恢复与删除

- PostgreSQL 使用公司 HA 和 PITR。
- S3 开启 server-side encryption、versioning 和 lifecycle。
- Session PVC 使用 CSI VolumeSnapshot 或公司存储备份能力。
- 每个恢复点生成 restore manifest，记录 PG LSN、S3 object version、PVC snapshot ID 和 encryption key version。
- 支持单 Session、单 Workspace 和全服务恢复演练。
- 恢复过程验证 PG 引用的 Memory/Artifact/Config object 和 PVC snapshot 均存在。
- 删除 user/Workspace 后生成 deletion ledger；备份恢复时重新应用 tombstone，防止已删除内容复活。
- Runner Pod 和 PG notification 不备份，可由权威状态重建。

## 21. 测试与生产验收

### 21.1 正确性与竞态

- 状态机 property-based test。
- API 多副本并发创建相同 `client_request_id`。
- 同 Session 两个 Turn 同时提交，只有一个进入 active。
- 同 memory scope 两个 Session 同时执行，只有一个获得 lease。
- Controller create Pod 响应丢失后 reconcile 不产生第二 generation。
- 重复 Supervisor 注册、旧 Pod 迟到 heartbeat/event 被拒绝。
- Turn 在 execution barrier 前后分别崩溃。
- Memory S3 上传成功但 PG CAS 响应丢失。

### 21.2 Kubernetes 与存储故障

- 节点网络分区后旧进程仍可能写盘的故障注入。
- 旧 Pod 未 terminal/PVC 未 detach 时新 Pod无法挂载。
- CSI attach/detach 超时进入 recovery wait，不强行双写。
- 从节点 A 停止 Runner，在节点 B 挂载同一 PVC并 resume。
- Controller 重启、API 重启、Gateway 重启。
- S3、PG、Model、MCP 不可用的明确降级状态。

### 21.3 安全

- Agent 不能读取 Supervisor control credential。
- Agent 不能访问 PG、S3、Kubernetes API、metadata service 或公网。
- 其他 Session、Workspace、用户 Memory 均不可读。
- OIDC issuer/audience/subject、时钟偏差和 JWKS 轮换。
- 伪造 Ingress Header、user/workspace/obId 无效。
- 成员移除后运行中 Runner 的新外部访问在撤权 SLA 内失败。
- Zip Slip、symlink、hardlink、device、socket、路径穿越均被拒绝。

### 21.4 上线 Gate

生产 Pilot 前必须全部满足：

1. Product Workspace、Session Workdir、Team Knowledge Bundle 边界在 API、UI 和文档一致。
2. Session/附件/Artifact/SSE 的 creator-private 权限矩阵通过。
3. 旧 Runner 未 HardFenced 时无法产生新的写者。
4. 重复 Pod/注册场景最多一个实例能 claim Turn 和写 Session volume。
5. Turn 具备 execution barrier、finalizing 和 `outcome_unknown`。
6. 运行中 Turn 永不透明自动重放。
7. Personal Memory 版本化、CAS 和中断不提交通过。
8. Config/Skill/Knowledge Bundle 只读且 SHA 校验通过。
9. Control credential 不可被 Agent/Bash/Skill/Subagent 读取。
10. Runner 不能绕过 Model/MCP Gateway 或 OBO。
11. 跨节点 resume、备份恢复和删除不复活通过。
12. SDK candidate 停止分配和 cohort pinning 通过。
13. P50/P95/P99、quota、告警和审计可见。
14. 安全 Gate 在真实多租户和生产凭据接入前完成。

## 22. 分阶段交付

每个阶段单独写实施计划、迁移和验收报告。以下能力是累计关系；面向用户的摘要见第 1.2 节。

### 阶段 0：运行时与领域边界固化

- **使用对象：** 当前本机/受信任单实例用户。
- **用户功能：** 不新增功能，聊天、Session、Skill、MCP、附件、resume 和 Auto Memory 行为保持不变。
- **平台功能：**
  - 固化 `AgentRuntimePort` 和内部 RuntimeEvent。
  - SDK import 收口到 ClaudeSdkAdapter。
  - 明确 Product Workspace、Session Workdir 与 Team Knowledge Bundle。
  - 建立 SDK/CLI/MCP version、image、protocol 与 capability probe。
- **不包含：** OIDC、PostgreSQL、多副本和 Kubernetes Runner。

**Gate：** 不改变现有单机体验，现有测试全部通过。

### 阶段 1：PostgreSQL、OIDC 与 creator-private

- **使用对象：** 开发/集成环境测试用户和 API 多副本演练。
- **用户功能：** 真实登录、个人/团队 Workspace 列表、creator-private Session、跨 API 副本一致的历史和 SSE。
- **平台功能：** PostgreSQL 权威数据、OIDC Principal、Space membership projection、Session owner、active Turn、幂等请求、持久 event/state machine 和 PG queue。
- **不包含：** 多副本环境中的真实 Agent 执行、Runner Pod、S3 和生产用户。

**Gate：** API 多副本下权限与并发一致；不接 Runner Pod。

### 阶段 2：最小安全执行纵向链路

- **使用对象：** 公司开发 Kubernetes 集群中的测试 Workspace。
- **用户功能：** Kubernetes 隔离 Turn、连续 Turn 温复用、取消、5 分钟 Idle 回收、Session PVC resume、只读测试 Skill/MCP。
- **平台功能：** Sandbox Controller、Execution Gateway、普通 Runner Pod、每 Session RWOP PVC、generation、execution barrier、Hard fencing、Supervisor/Agent 双容器、只读 Config Bundle、基础 restricted Pod 与 NetworkPolicy。
- **不包含：** 生产模型/MCP 凭据、真实用户、完整 S3 Artifact/Memory 与生产准入。

**Gate：** 只使用测试 Workspace、read-only MCP 和无生产凭据。

### 阶段 3：Memory、Artifact 与恢复

- **使用对象：** 预生产测试用户。
- **用户功能：** 持久附件/Artifact、个人 Auto Memory、跨节点 resume、恢复状态、`outcome_unknown` 提示和可下载结果。
- **平台功能：** S3 Attachment/Artifact/Config/Skill/Memory Bundle、Memory lease/物化/CAS、跨节点 PVC resume、restore manifest、删除 tombstone、Tool Operation Ledger 和可恢复 finalization。
- **不包含：** 生产凭据、全量用户、团队动态记忆和默认写 MCP。

**Gate：** 故障注入和恢复演练通过。

### 阶段 4：生产强化与 Pilot

- **使用对象：** 服务端 allowlist 中的首批真实用户。
- **用户功能：** 个人/团队 Workspace 的生产级私有 Session、附件、Skill、read-only MCP、经审计且可幂等/对账的白名单写工具、个人 Memory、Artifact、跨节点 resume 与及时撤权。
- **平台功能：** 目标 RuntimeClass/Node Pool、安全策略、Gateway 短期凭据、OBO、动态撤权、emergency deny、配额、审计、告警、Dashboard、真实负载压测、SDK/MCP cohort 灰度和回滚。
- **不包含：** 全量开放、团队动态记忆、Session 发布、任意镜像/stdio MCP 和默认 non-idempotent write MCP。

**Gate：** 第 21.4 节全部通过后才能接入首批真实用户。

### 阶段 5：按数据演进

**使用对象：** 已运行的 Pilot 或后续正式服务。每项独立决策，不作为 Phase 4 延期理由。

只在指标证明必要且独立 Gate 通过时引入：

- SDK SessionStore recovery mirror。
- Redis Pub/Sub 通知。
- Kueue 公平排队。
- Job + `podReplacementPolicy: Failed`。
- Warm Pod Pool。
- Team 动态记忆。
- Web approval/deferred interaction。
- MCP v2 专属 feature、审批后的 non-idempotent write MCP。
- 多区域或跨集群。

## 23. 明确不做

- 不为每个 Workspace 创建 Namespace。
- 不使用每 Workspace 常驻容器。
- 不默认每 Turn 冷启动新容器。
- 不自研 Kubernetes Scheduler、分布式文件系统或通用消息总线。
- 不解析 SDK 私有 transcript JSONL。
- 不把 PostgreSQL、SessionStore 和本地 transcript 同时宣称为权威。
- 不承诺外部工具 exactly-once。
- 不透明重放运行中或 `outcome_unknown` 的 Turn。
- 不在一期自研向量记忆、记忆事实抽取或自动 merge。
- 不允许用户任意上传容器镜像或可执行 stdio MCP。
- 不在安全 Gate 前接入真实多租户用户和生产凭据。

## 24. 决策记录

本 V2 已采用以下默认决定，用户复核时可单独提出修改：

1. 团队 Workspace 只共享只读 Knowledge/Config/Skill/MCP Bundle。
2. 同一 `user + workspace` 同时只允许一个 memory-active Turn。
3. 一期 transcript 由 Session RWOP PVC 承担，不实现自定义 SessionStore。
4. 一期用 Controller 管理普通 Pod，不让 Job Controller 自动补实例。
5. 节点网络分区优先一致性，允许 Session 等待 Hard fence。
6. 中断后的 Workdir 保留并进入 recovery 状态，不自动回滚。
7. 第一期默认禁用 non-idempotent write MCP。
8. 成员被移除后立即撤销 Gateway 权限并 drain Runner。
9. Workspace owner/admin 默认不能读取成员私有 Session。
10. SDK canary 先使用测试 Workspace 和专用 Session，不直接切换任意真实旧 Session。

## 25. 参考与复核日期

以下能力已于 2026-07-29 按官方文档复核；实施时仍需按锁定版本重新验证：

- [Claude Agent SDK Hosting](https://code.claude.com/docs/en/agent-sdk/hosting)：subprocess、本地状态、Hybrid 模式、资源、网络、SessionStore 与多租户隔离。
- [Claude Agent SDK Sessions](https://code.claude.com/docs/en/agent-sdk/sessions)：`resume`、固定 cwd、跨主机 session file 恢复。
- [Claude Agent SDK Session Storage](https://code.claude.com/docs/en/agent-sdk/session-storage)：本地先写、外部 mirror、`mirror_error`、conformance、Subagent subkeys 和 retention。
- [Claude Secure Deployment](https://code.claude.com/docs/en/agent-sdk/secure-deployment)：凭据代理、文件系统、网络和隔离技术。
- [Claude Auto Memory](https://code.claude.com/docs/en/memory)：`autoMemoryDirectory` 只接受 policy/user/`--settings`，不接受 project/local settings。
- [Claude Agent SDK 0.2.128 metadata](https://pypi.org/pypi/claude-agent-sdk/0.2.128/json)：当前依赖约束为 `mcp>=1.23.0,<2.0.0`。
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)：v2 stable、protocol revision 与 migration 说明；当前项目不得据此绕过 Claude Agent SDK 的依赖上限。
- [Kubernetes RWOP](https://kubernetes.io/docs/tasks/administer-cluster/change-pv-access-mode-readwriteoncepod/)：1.29 起稳定，仅支持 CSI。
- [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/)：1.34 起 `podReplacementPolicy: Failed` 稳定；V2 一期不依赖该能力。

公司侧实施前必须取得并记录：

- Kubernetes server 版本。
- CSI driver 和 sidecar 版本、RWOP/VolumeSnapshot/force-detach 语义。
- 节点 fencing 与存储 detach SLA。
- OIDC issuer/audience、membership API 和撤权 SLA。
- Model/MCP Gateway 的 OBO、幂等、审计和 credential injection 能力。
- S3 versioning、encryption、retention 和 object lock 能力。
