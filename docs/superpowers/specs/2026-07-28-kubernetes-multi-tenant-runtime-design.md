# Kubernetes 多租户 Workspace Agent 总体设计

**日期：** 2026-07-28
**状态：** 已由 V2 取代
**适用项目：** claude_workspace_mvp

> 本文档保留为第一版历史记录。更新后的可实施方案见
> `docs/superpowers/specs/2026-07-29-kubernetes-multi-tenant-runtime-v2-design.md`。

## 1. 目标

把当前面向本机或受信任单实例部署的 Workspace Agent，演进为可以部署在数据中心、由多个用户通过 Web 使用的多租户服务。

目标部署具备以下能力：

- 个人空间、团队空间分别映射为独立 Workspace。
- Workspace 中的 Session 只对创建者可见。
- 多台 Kubernetes 工作节点共同承载并发 Session。
- 每个活跃 Session 使用一个隔离的短租约 Runner 容器。
- 连续 Turn 复用同一个 Runner；空闲 5 分钟后回收。
- Pod 被回收或迁移到其他节点后，Session 可以恢复。
- Personal Auto Memory 按 user + workspace 隔离并自动读写。
- Workspace、Skill、MCP 和模型配置以 Session 创建时的不可变快照执行。
- 优先复用 Kubernetes、PostgreSQL、Redis、S3、CSI 和 Claude Agent SDK，不自研通用基础设施。
- Claude Agent SDK 升级被限制在 Runtime Adapter 和 Runner 镜像内，可以灰度、验证和快速回滚。

## 2. 已确认的产品决策

- 数据中心的个人空间和团队空间都映射为应用 Workspace。
- Session 属于一个 Workspace 和一个创建者。
- 即使位于团队 Workspace，Session、消息、附件、文件和执行过程也只对创建者可见。
- 第一期不提供 Session 发布或共享。
- Personal Auto Memory 由 Claude Code 自动识别和写入，不建设自研记忆抽取、向量检索或冲突解决系统。
- Personal Auto Memory 的范围是 user + workspace，不跨 Workspace。
- 第一期没有可自动写入的团队共享记忆；团队稳定知识继续使用 CLAUDE.md、托管 Skill 和业务 MCP。
- 每个活跃 Session 使用一个 Runner，而不是每个 Workspace 一个常驻容器，也不是每个 Turn 都创建冷容器。
- 一个 Runner 在连续 Turn 之间保持温热；最后一个 Turn 完成后空闲 5 分钟退出。
- 同一 Session 同一时刻只允许一个执行中的 Turn。
- 不自研 Kubernetes 调度器、分布式文件系统、对象存储、消息队列或 Claude transcript 格式。

## 3. 非目标

本设计暂不包含：

- 多 Kubernetes 集群的跨集群调度；第一阶段只支持一个多节点集群。
- 每个 Workspace 一个 Kubernetes Namespace。
- 每个 Workspace 一个永久运行的容器。
- 一个 Turn 一个全新容器的强制冷启动模式。
- 自研容器运行时、Kubernetes Scheduler 或 Sandbox CRD。
- 动态团队记忆、跨 Workspace 个人记忆或独立记忆管理页面。
- 任意用户上传并运行自定义容器镜像或任意 stdio MCP 可执行程序。
- 对已经开始执行外部副作用的 Turn 做透明自动重放。
- 第一阶段的跨地域双活。

## 4. 当前状态与生产差距

| 当前实现 | 单机有效性 | 多实例问题 | 目标替换 |
|---|---:|---|---|
| SQLite | 有效 | 多实例写入、HA 和备份能力不足 | PostgreSQL |
| APP_DATA_DIR/sessions | 有效 | 其他节点不可见 | CSI RWX + S3 |
| 本地 Claude transcript | 有效 | Pod 回收或迁移后不可恢复 | SDK SessionStore + RWX 恢复副本 |
| 进程内 SessionLockRegistry | 有效 | 不跨 API 实例 | PostgreSQL 状态约束 + 租约 |
| 进程内 MemoryScopeLockRegistry | 有效 | 不跨 Runner | PostgreSQL Memory lease + Pod 终止屏障 |
| 进程内 EventBroker | 有效 | SSE 订阅者只能收到本实例事件 | PostgreSQL 历史 + Redis 实时通知 |
| asyncio.create_task 执行 Turn | 有效 | API 重启即中断 | Redis Stream + Session Runner |
| Mock IdentityProvider | 仅可信本机 | 不能用于多租户 | 数据中心 OIDC/OBO IdentityProvider |
| 服务进程环境中的模型/MCP密钥 | 单机可用 | Runner 可见长期密钥 | Model/MCP Gateway 注入凭据 |

现有的 AgentRuntime、Workspace 快照、Session 创建者隔离、Skill 托管和 Memory Scope 是正确的领域边界，应保留并迁移其实现，而不是推倒重写。

## 5. 方案比较

### 5.1 选择方案：Session 级 Hybrid Runner

一个活跃 Session 对应一个 Kubernetes Job/Pod。Runner 可处理多个串行 Turn，空闲 5 分钟后正常退出；下次输入时创建新一代 Runner，并从共享存储恢复。

优点：

- Session 文件、Claude 子进程和 Auto Memory 的边界清楚。
- 连续对话不用每个 Turn 冷启动。
- 空闲 Session 不持续占用计算资源。
- 与 Claude Agent SDK 官方 Hybrid Session 模式一致。
- 节点迁移和 SDK 镜像灰度容易控制。

代价：

- 需要 Runner 租约、心跳和 fencing token。
- 冷 Session 的第一个 Turn 仍有 Pod 启动延迟。

### 5.2 不选：Workspace 级常驻容器

团队 Workspace 会把多个用户的文件、设置、Auto Memory 和进程放进同一安全边界；单个容器故障影响整个 Workspace，大 Workspace 也难以水平扩容。

### 5.3 不选：Turn 级一次性容器

隔离最强但连续聊天反复支付 Pod、CLI、MCP 和 Session 恢复成本。保留为未来高风险一次性任务的可选策略，不作为默认方式。

## 6. 总体架构

~~~mermaid
flowchart LR
    Browser["Web Browser"] --> Ingress["Ingress / API Gateway"]
    Ingress --> API["Workspace Agent API<br/>stateless replicas"]
    API --> Identity["OIDC / Space Membership"]
    API --> PG[("PostgreSQL")]
    API --> Redis[("Redis")]
    API --> S3[("S3-compatible Object Store")]

    Relay["Outbox Relay"] --> PG
    Relay --> Redis
    Controller["Sandbox Controller"] --> PG
    Controller --> K8s["Kubernetes API"]

    K8s --> Job["Session Runner Job / Pod"]
    Job --> RWX[("CSI RWX Storage")]
    Job --> Gateway["Sandbox Gateway"]

    Gateway --> PG
    Gateway --> Redis
    Gateway --> S3
    Gateway --> Model["Model / MCP Gateway"]
    Model --> Claude["Claude-compatible Model Endpoint"]
    Model --> MCP["Business MCP Services"]

    API -. "SSE history" .-> PG
    API -. "SSE live notification" .-> Redis
~~~

架构分为两个平面：

- **控制面：** Ingress、API、PostgreSQL、Redis、Outbox Relay、Sandbox Controller、身份和配置管理。
- **执行面：** Session Runner、CSI 工作目录、Sandbox Gateway、Model/MCP Gateway。

Runner 不直接访问 PostgreSQL、Redis、S3、Kubernetes API 或长期密钥。所有跨安全边界操作都通过短期授权的 Sandbox Gateway 或 Model/MCP Gateway 完成。

## 7. 逻辑组件与职责

| 组件 | 单一职责 | 主要依赖 |
|---|---|---|
| Workspace Agent API | 产品 API、权限、Session/Turn 管理、SSE | PostgreSQL、Redis、S3 |
| Access Service | 将可信 Principal、membership 和 Session owner 转成授权决策 | PostgreSQL、身份系统 |
| Turn Service | 创建幂等 Turn、维护状态机、写 Outbox | PostgreSQL |
| Outbox Relay | 把已提交的 Turn/控制事件可靠发布到 Redis | PostgreSQL、Redis |
| Sandbox Controller | 把 Runner 租约状态协调为 Kubernetes Job | PostgreSQL、Kubernetes API |
| Sandbox Gateway | Runner 心跳、claim、事件、SessionStore、Artifact 和取消通道 | PostgreSQL、Redis、S3 |
| Session Runner | 在隔离 Pod 内串行执行当前 Session 的 Turn | SDK Adapter、共享卷、Gateway |
| ClaudeSdkAdapter | 唯一允许依赖 claude_agent_sdk 的模块 | Claude Agent SDK |
| GatewaySessionStore | 实现 SDK 官方 SessionStore Protocol | Sandbox Gateway |
| Model/MCP Gateway | 凭据注入、OBO、allowlist、限流和审计 | 密钥平台、模型代理、MCP |
| Artifact Service | 附件、产物、Skill Bundle 的对象存储生命周期 | S3、PostgreSQL |

这些是逻辑边界，不要求第一阶段全部拆成独立微服务：

- API + Outbox Relay 可同仓库、不同进程入口。
- Sandbox Controller 使用独立 Deployment。
- Sandbox Gateway + Artifact API 可先同一 Deployment。
- Model/MCP Gateway 优先复用公司现有网关。

## 8. 身份、Workspace 与权限

### 8.1 身份来源

- Ingress 验证数据中心签发的 OIDC/JWT。
- IdentityProvider 从已验证请求上下文读取 issuer + subject，映射为内部 user_id。
- 数据中心空间 ID 映射为内部 workspace_id。
- Workspace membership 和角色从数据中心权威空间服务同步或按请求校验。
- 模型、Prompt、query 参数和自定义 Header 都不能提供可信 user_id、workspace_id 或 obId。

### 8.2 权限规则

- 用户必须是 Workspace 成员才能创建 Session。
- Session 读取、重命名、删除、附件、文件、消息、Turn 和 SSE 都必须同时满足 Workspace membership 与 created_by == principal.user_id。
- Workspace owner/admin 管理 Workspace Skill 和 MCP manifest；普通 member 只能使用已启用配置。
- Session 快照创建后不随 Workspace 配置修改而变化。
- 成员被移出 Workspace 后立即失去新请求权限；正在执行的 Runner token 被撤销或在很短 TTL 后过期。

### 8.3 OBO

业务 MCP 使用可信 Principal 生成的短期 OBO 上下文。Runner 只传递由 Gateway 签发的 Session token，不能自行选择业务用户身份。

## 9. 数据与存储设计

| 数据 | 权威存储 | 运行时形态 |
|---|---|---|
| 用户、Workspace、membership | PostgreSQL | API 缓存可选 |
| Session、Turn、消息、配置快照 | PostgreSQL | Runner 只读快照 |
| Runner 租约与 generation | PostgreSQL | Redis 仅做唤醒 |
| Claude transcript mirror | PostgreSQL JSONB | SDK SessionStore |
| Session workspace | CSI RWX | /workspace |
| Claude 本地运行状态 | CSI RWX | /claude-config |
| Personal Auto Memory | CSI RWX | /memory |
| 附件、发布产物、Skill Bundle、备份 | S3 | 按需物化 |
| Turn 队列、实时通知、限流计数 | Redis | 可由 PostgreSQL 重建 |
| 模型/MCP长期密钥 | Vault/公司密钥平台 | Gateway 运行时注入 |

### 9.1 Runner 挂载边界

Runner 只看到当前 Session 和当前用户 Workspace Memory 的精确目录：

~~~text
/workspace
/claude-config
/memory
/skills
/tmp
~~~

真实目录使用 UUID 或稳定不可逆 key，不使用用户输入拼接：

~~~text
/runtime/sessions/<session_uuid>/workspace
/runtime/sessions/<session_uuid>/claude-config
/runtime/memories/<user_key>/<workspace_key>
~~~

Runner 不挂载租户根目录，不使用 hostPath，也不能遍历其他 Session 子目录。

### 9.2 附件、产物和 Skill

- 上传附件先写 S3，PostgreSQL 保存 owner、Workspace、checksum、大小和 MIME。
- Artifact Service 把当前 Turn 允许的附件物化到 /workspace/attachments。
- Agent 先把结果写入 /workspace/outputs；Turn 完成后发布为 S3 Artifact。
- Skill metadata 位于 PostgreSQL，Bundle 位于 S3 并按 SHA-256 寻址。
- 创建 Session 时生成不可变 manifest，Runner 只物化该 manifest 指向的版本。
- Skill 脚本在 Sandbox 内运行，仍受工具 allowlist、资源限制和网络策略约束。

### 9.3 Auto Memory

- Memory scope 固定为 user_id + workspace_id。
- Runner 只挂载该 scope，并通过 SDK settings 指向 /memory。
- 同一 memory scope 的执行中 Turn 使用 PostgreSQL memory lease 串行化；Redis 只负责唤醒等待者。
- Runner 不直接操作租约，由 Sandbox Gateway 在事务内申请、续租和释放。
- 因为 Auto Memory 直接写共享文件，Gateway fencing 不能撤销一个旧进程已经获得的文件句柄。发生 heartbeat 丢失时，Controller 必须先终止旧 Pod，并确认 Kubernetes/节点 fencing 完成，才能把同一 Memory scope 授予新执行。
- 每个 Turn 结束时 Runner 断开 SDK client，回收整个子进程组，确认没有 Bash、Subagent 或 MCP 子进程遗留后才释放 Memory lease。
- Memory 目录加密存储、限制总大小和单文件大小，并定期快照到 S3。
- 第一期不解析 MEMORY.md，不建立 memory facts 表，也不做向量索引。

### 9.4 Claude SessionStore

最新 Agent SDK 提供 SessionStore，用于把 transcript 镜像到 S3、Redis 或数据库，并支持不同主机恢复。项目采用 GatewaySessionStore 实现官方 Python Protocol：

~~~text
Claude SDK -> GatewaySessionStore -> Sandbox Gateway -> PostgreSQL
~~~

规则：

- append、load 和 list_subkeys 遵循官方契约。
- transcript entry 作为 opaque JSONB 原样存取，不解释 SDK 私有字段。
- 主键包含 project_key + claude_session_id + subpath + sequence。
- 有 UUID 的 entry 以 UUID 幂等；同一 Session 的 append 在事务内排序。
- 使用 SDK 自带 run_session_store_conformance 验证。
- 保留 /claude-config RWX 副本，因为 SDK 的外部 mirror 是 best-effort。
- 收到 mirror_error 时把 Session 标为 transcript_degraded，告警并保留本地配置目录；后续必须从同一 RWX 副本恢复，不能只依赖 mirror。
- Retention 由平台负责，SDK 不会主动清理外部 store。
- PostgreSQL 是第一阶段最少组件的选择。如果 transcript 容量或 retention 明显挤压业务数据库，只替换 Gateway 后端为官方 S3 part-file 模型，Runner 和业务 API 不变。

SessionStore 只覆盖 transcript，不覆盖 Memory 或 working directory，因此三类状态分别治理：transcript 用 SessionStore，workspace 和 memory 用共享卷或对象存储。

## 10. Runner 与 Kubernetes Job 生命周期

### 10.1 Job 配置

Runner 是有限生命周期进程：启动、处理一个或多个 Turn、空闲退出。第一阶段不定义 CRD，由 Sandbox Controller 根据 PostgreSQL Runner lease 创建普通 Job：

~~~yaml
completions: 1
parallelism: 1
backoffLimit: 0
ttlSecondsAfterFinished: 600
restartPolicy: Never
~~~

backoffLimit 为 0 是有意选择：Pod 异常退出后不让 Kubernetes 在不知道外部工具副作用的情况下自动重放。恢复决策由应用状态机完成。

### 10.2 Runner lease

每一代 Runner lease 至少包含：

~~~text
session_id
generation
runner_id
job_name
status
fencing_token_hash
runner_image_digest
sdk_version
protocol_version
heartbeat_at
idle_deadline
max_deadline
~~~

Runner 获得短期签名 token，包含 session_id、generation、runner_id、权限 scope 和过期时间。Gateway 每次请求同时验证 token 与 PostgreSQL 当前 generation，旧 Runner 的迟到事件会被拒绝。

### 10.3 状态机

~~~mermaid
stateDiagram-v2
    [*] --> Absent
    Absent --> Provisioning: queued Turn
    Provisioning --> Ready: Runner heartbeat
    Provisioning --> Failed: startup timeout
    Ready --> Busy: claim Turn
    Busy --> Idle: Turn terminal
    Idle --> Busy: new Turn within 5 min
    Idle --> Draining: idle deadline or max age
    Draining --> Terminated: Runner exits
    Failed --> Absent: new generation allowed
    Terminated --> Absent
~~~

初始默认值：

- Heartbeat 间隔 10 秒。
- 连续 30 秒没有 heartbeat 判定 lease 丢失。
- 最后一个 Turn 终态后 Idle TTL 为 5 分钟。
- Runner 最大年龄 4 小时，只在 Turn 之间 drain。
- Job 启动超时根据集群实测配置。

### 10.4 冷启动和温复用

- 有同 Session 的健康 Idle/Ready Runner：直接投递，属于 warm hit。
- 没有 Runner 或 Runner 正在退出：创建 generation + 1 的 Job。
- 旧 generation 即使稍后恢复，也不能 claim 新 Turn 或提交事件。
- 新 generation 挂载同一 Session workspace 前，必须确认旧 Pod 已终止；fencing token 只能保护 Gateway，不能替代共享文件系统的写入隔离。

降低冷启动延迟优先使用成熟能力：

- Runner 镜像固定 digest。
- Sandbox 节点池通过 DaemonSet 预拉镜像。
- Cluster Autoscaler 保留可配置的最小节点容量。
- 第一期不建设自研 warm-pod pool。

### 10.5 Controller 多副本

- Job 名称由 session_id + generation 确定，重复创建天然幂等。
- 多个 Controller 通过 PostgreSQL 行锁或 FOR UPDATE SKIP LOCKED 领取 reconcile 工作。
- Kubernetes Job 状态只是执行事实，PostgreSQL lease 是应用权威状态；Controller 持续比对并修复差异。
- 不需要永久单 leader，也不需要自定义 Kubernetes Scheduler。

## 11. Turn 数据流与状态机

### 11.1 创建与分发

1. API 验证 Principal、Workspace membership 和 Session owner。
2. PostgreSQL 事务内以 session_id + client_request_id 幂等创建 queued Turn。
3. 同一事务写入 Outbox row。
4. Outbox Relay 发布到 Redis Stream。
5. Sandbox Controller 确保该 Session 有可用 Runner lease。
6. Runner 通过 Gateway long-poll 获取任务，不直接持有队列或数据库权限。
7. Gateway 原子校验 generation，把 Turn 从 queued 变为 running。
8. Runner 执行 SDK Turn，按 sequence 上传标准化事件。
9. Gateway 先持久化 PostgreSQL，再通过 Redis 通知 SSE 实例。
10. Turn 进入终态后 Runner 开始 5 分钟 idle 计时。

第一阶段“温复用”指复用 Runner Pod、已挂载卷和已拉取镜像。ClaudeSDKClient 仍按 Turn 创建并在 Turn 终态后断开，与当前实现一致。只有压测证明长连接能带来明确收益，并且后台 Subagent、取消和 Memory lease 语义验证完成后，才考虑跨 Turn 保持同一个 SDK 子进程。

### 11.2 Turn 状态

~~~text
queued -> dispatching -> running
running -> completed | failed | cancelled | interrupted
queued/dispatching -> cancelled
running -> cancel_requested -> cancelled | interrupted
~~~

PostgreSQL 必须保证一个 Session 最多一个非终态 Turn。Redis 锁不能单独承担该正确性约束。

### 11.3 SSE

- 历史事件以 PostgreSQL 为准。
- Redis 只通知“有新 sequence”，不作为唯一事件存储。
- 浏览器使用 Last-Event-ID 重连；API 先从 PostgreSQL 补齐，再订阅 Redis。
- Redis 暂时不可用时，客户端仍能轮询 PostgreSQL，不会丢历史。

## 12. 取消、超时与删除

### 12.1 取消 Turn

1. API 标记 cancel_requested 并写 Outbox。
2. Runner 收到信号后调用 SDK interrupt。
3. grace period 内等待 Claude 子进程和工具停止。
4. 超时后发送 SIGTERM，再超时才 SIGKILL。
5. 健康 Runner 可进入 Idle；状态不确定的 Runner 直接 drain。

### 12.2 超时

- Turn wall-clock timeout 独立于 SDK maxTurns。
- maxTurns 限制 Agent 循环次数，平台 timeout 限制总执行时间。
- 超时 Turn 不自动重放。

### 12.3 删除 Session

- 删除请求先标记 deleting，撤销 Runner token，并终止 Job。
- 后台幂等清理 workspace、claude-config、transcript、附件引用和对象存储产物。
- Personal Memory 不随单个 Session 删除，因为它属于 user + workspace。
- 不能先删除数据库记录再留下永久失去归属的文件目录。

## 13. 故障转移语义

| 故障 | 系统行为 | 自动重试 Turn |
|---|---|---:|
| API 实例重启 | PostgreSQL 中的 Turn 仍在，其他实例继续服务 | 未 dispatch 的可以 |
| Redis 短暂不可用 | Outbox 保留，恢复后重新发布 | queued 可以 |
| Controller 重启 | 按 PG lease 与 K8s Job reconcile | 不涉及执行重放 |
| Runner 启动失败 | generation 失败，新 generation 可重建 | 尚未 claim 的可以 |
| Runner 在 Turn 中崩溃 | Turn 标记 interrupted | 不自动 |
| 节点失联 | fencing 旧 generation，Turn interrupted | 不自动 |
| SessionStore mirror 失败 | 标记 degraded，保留 RWX transcript | 不重放 |
| RWX 不可用 | Runner 不 Ready，阻止执行 | 恢复后 queued 可继续 |
| S3 不可用 | 需要附件/Skill 的 Turn 阻止执行 | 恢复后 queued 可继续 |
| Model Gateway 不可用 | 明确失败或首次请求前返回 retriable | 不透明重放 |
| MCP readiness 失败 | 模型执行前失败并显示具体 MCP | 用户重试 |
| PostgreSQL 不可用 | 控制面 fail closed | 否 |

原则：

- 未开始的 Turn 可以安全重新投递。
- 运行中的 Turn 不能假设可重放，Claude 可能已经调用有副作用的 MCP。
- 业务 MCP 支持幂等时，Gateway 传递 turn_id + tool_call_id 作为 idempotency key。
- 所有旧 Runner 事件都必须经过 fencing 校验。

## 14. Sandbox 与网络安全

### 14.1 Kubernetes 隔离

控制面和执行面使用不同 Node Pool：

- control：API、Gateway、Controller、OTel Collector。
- sandbox：仅运行 Session Runner，使用 taint/toleration 和专用 RuntimeClass。

Runner Pod 使用：

- gVisor RuntimeClass；基础设施不支持时再评估 Kata/Firecracker。
- Pod Security restricted。
- runAsNonRoot。
- allowPrivilegeEscalation 为 false。
- capabilities drop ALL。
- seccomp RuntimeDefault。
- 只读 root filesystem，只有指定 volume 与 /tmp 可写。
- automountServiceAccountToken 为 false。
- 禁止 privileged、hostNetwork、hostPID、hostIPC、hostPath 和 hostPort。
- CPU、内存、ephemeral storage、进程数和文件大小限制。

### 14.2 网络

agent-sandbox Namespace 默认拒绝 ingress 和 egress，只允许：

- DNS。
- Sandbox Gateway。
- 必需的 OTel Collector；也可以经 Gateway 转发。

Runner 不直接访问公网、数据库、Redis、S3、模型供应商或业务系统。

### 14.3 密钥

- 模型 API Key 留在 Model Gateway，通过 ANTHROPIC_BASE_URL 路由。
- MCP/OBO 凭据由 MCP Gateway 或外部 MCP 服务注入。
- Runner 只持有短期、Session-scoped token。
- Skill、Prompt 和模型输出不能读取长期凭据。
- 日志、事件和错误消息统一做 Secret redaction。

### 14.4 工具策略

- Workspace 快照定义候选 allowlist。
- SDK PreToolUse hook 做 Runner 内第一层检查。
- Gateway 对 MCP、Artifact 和外部动作做第二层权威检查。
- dontAsk 只表示不在无人值守容器里弹交互问题，不等于绕过平台权限。
- 删除、发布、写业务系统等高风险工具在没有 Web approval 时默认禁用。
- 用户导入 Skill 不能改变 NetworkPolicy、挂载目录、Runner token scope 或 MCP 凭据。

### 14.5 SDK 文件设置隔离

- cwd 固定为当前 Session 的 /workspace。
- CLAUDE_CONFIG_DIR 固定为当前 Session 的 /claude-config。
- setting_sources 只加载已经物化并审计过的 project 配置，不加载宿主机 user/global 配置。
- Auto Memory 是有意启用的，但目录只能是当前用户当前 Workspace 的 /memory。
- /skills 是只读 Bundle 来源，启动时物化到 /workspace/.claude/skills；不让 SDK 扫描宿主机 Skill 目录。
- 一个 Runner Pod 只服务一个 Session，不在同一容器内混跑多个租户的 SDK 进程。

## 15. PostgreSQL 关键模型变化

保留现有领域模型，增加或调整：

### 15.1 sessions

~~~text
runtime_image_digest
sdk_version
runtime_protocol_version
transcript_store_status
deleted_at
~~~

### 15.2 turns

~~~text
runner_generation
dispatch_attempt
cancel_requested_at
interrupted_reason
last_event_sequence
~~~

增加“每个 Session 只有一个 active Turn”的 PostgreSQL partial unique index。

### 15.3 runner_leases

~~~text
session_id PK/FK
generation
runner_id
job_name
status
fencing_token_hash
image_digest
sdk_version
protocol_version
heartbeat_at
idle_deadline
max_deadline
created_at
terminated_at
~~~

### 15.4 outbox_events

保存 aggregate、event type、payload、created/delivered time 和 attempts。

### 15.5 memory_scope_leases

保存 user/workspace scope key、generation、holder runner/turn、heartbeat、expires 和 release time。租约过期本身不代表可立即重分配；旧 Pod/进程组终止确认也是授权条件。

### 15.6 claude_session_entries

保存 project_key、claude_session_id、subpath、sequence、可空 entry_uuid、opaque entry_jsonb 和 created_at。

### 15.7 artifacts 与 audit_events

- artifacts 保存 S3 key、checksum、owner、Workspace、Session/Turn 归属和 retention。
- audit_events 记录身份、配置、Skill、MCP、Runner、权限拒绝和高风险工具决策，不默认保存 Prompt 全文。

## 16. Claude Agent SDK 兼容层

### 16.1 代码边界

~~~text
Business services
    -> AgentRuntimePort
        -> ClaudeSdkAdapter
            -> claude_agent_sdk
        -> GatewaySessionStore
            -> SDK SessionStore Protocol
~~~

约束：

- 只有 Runtime Adapter 包可以 import claude_agent_sdk。
- API、Turn、Session、Workspace 和 UI 只使用内部 RuntimeRequest 与 RuntimeEvent。
- 不调用 SDK 私有模块，不解析 SDK 内部 JSONL，不把 SDK dataclass 直接写入业务 API。
- transcript JSONB 使用开放结构，SDK 增加字段不触发数据库迁移。
- SDK feature 通过 capability probe 和 adapter tests 判断，不在业务代码散布版本比较。

### 16.2 版本策略

- Runner 镜像固定 SDK lockfile 和 image digest。
- Session lease 记录 SDK 版本和 Runner image digest。
- 正在运行的 warm Session 不热切换 SDK。
- Runner 达最大年龄后在 Turn 间隙 drain，下一代使用目标镜像。
- Patch 版本持续跟进，但仍需自动化测试和 canary。
- Minor 版本先阅读官方 changelog、运行完整兼容矩阵，再灰度。
- 至少保留当前正式镜像和上一稳定镜像。

### 16.3 CI 兼容矩阵

候选 SDK 必须通过：

- Runtime Adapter 单元测试。
- Runtime event golden tests。
- 官方 SessionStore conformance suite。
- 新 Session、resume、跨 Pod resume、Subagent resume。
- Skill、MCP readiness、Auto Memory 和附件测试。
- interrupt、timeout、mirror_error 和 malformed event 测试。
- Kind/K3s Runner Job smoke test。
- 指定测试 Workspace 的真实模型 canary。

### 16.4 灰度

~~~text
candidate image
  -> test Workspace only
  -> 1% new Runner generations
  -> 10%
  -> 50%
  -> 100%
~~~

灰度依据是新创建的 Runner generation，不改变既有 Session 快照。出现协议错误、mirror_error、启动失败或核心指标退化时，停止新调度并回退 image digest。

## 17. 可观测性与审计

Claude Agent SDK/CLI 原生支持 OpenTelemetry。平台 trace context 传入 Runner，使浏览器请求、API、排队、Pod 启动、模型请求和工具执行处于同一条 trace。

关键指标：

- API latency、error rate、SSE connections。
- Outbox backlog、Redis publish latency、queued Turn 数与排队时间。
- Controller reconcile error。
- cold start、warm hit rate、Runner ready time。
- active/idle/draining Runner 数、heartbeat loss、fencing rejection。
- time-to-first-token、Turn 总耗时和成功率。
- SDK/CLI 启动失败、mirror_error。
- MCP readiness latency、Auto Memory lock wait。
- token、cost、tool call、Subagent fanout。
- PostgreSQL、Redis、RWX、S3 的延迟和错误率。

日志与隐私：

- 结构化日志包含 trace_id、workspace_id、session_id、turn_id、runner_generation。
- Prompt、tool input/result 和文件内容默认不进入平台日志。
- 内容审计必须单独开启、授权并配置 retention。
- 审计日志与普通应用日志分开存储。

## 18. 容量、扩缩容与成本

- API、Gateway、Relay、Controller 使用 Deployment 多副本。
- API/Gateway 按 CPU、请求量和连接数 HPA。
- Runner 数由活跃 Session 自然决定，不建设常驻 worker pool。
- Cluster Autoscaler 根据 Sandbox Pod requests 增减节点。
- Sandbox Controller 通过应用级 quota 限制并发，Redis Stream 第一阶段保持 FIFO；不实现复杂公平调度，也不自研 K8s scheduler。

配额配置化：

- 每用户最大活跃 Runner。
- 每 Workspace 最大并发 Turn。
- 每 Session 一个 active Turn。
- 每 Turn 最大 wall time、maxTurns、token 和 tool call。
- Runner CPU、RAM、ephemeral storage。
- Workspace/Session/Memory/S3 总存储。
- Subagent 并发上限。

资源值不在设计阶段拍死。以官方建议的每 Agent 约 1 CPU、1 GiB RAM、5 GiB disk 作为压测起点，用真实数据分析、文件操作、MCP 和长 Session 测 peak RSS/P95 后定义 small、standard Profile。

成本优化顺序：

1. 5 分钟 idle TTL。
2. 合理分离 request 与 limit。
3. 预拉镜像，不自研 warm pool。
4. 限制无界 Subagent fanout 和 maxTurns。
5. 按用户/Workspace 记录 token 与基础设施用量。
6. 观察 warm hit 与冷启动后再调整 idle TTL。

## 19. 高可用、备份与恢复

- PostgreSQL 使用公司标准 HA 并启用 PITR。
- Redis 使用标准高可用，但不作为业务唯一数据源。
- S3 启用 server-side encryption、versioning 和 lifecycle。
- RWX 使用 CSI snapshot 或公司存储备份能力。
- Memory 与 claude-config 的备份要和 PostgreSQL metadata 一起做恢复演练。
- Runner Job 和 Redis 实时事件不备份，可由 PostgreSQL 重建。
- 第一阶段为单集群、多节点、单区域 HA；跨区域灾备另行设计。

初始建议目标：

- PostgreSQL/S3 RPO 不高于 15 分钟。
- 单区域服务 RTO 不高于 60 分钟。
- 最终值由接入后的公司基础设施 SLA 覆盖。

## 20. 测试策略

### 20.1 单元与契约

- Principal/Workspace/Session owner 授权矩阵。
- Turn 状态机和幂等 client request。
- Runner generation 与 fencing。
- Runtime Adapter 标准事件。
- SessionStore 官方 conformance。
- Artifact path、checksum 和 scope。
- Auto Memory scope 与租约。

### 20.2 集成

- PostgreSQL + Redis + S3-compatible service + RWX。
- Kind/K3s 创建、复用、idle 退出和 TTL 清理 Job。
- API 多副本 SSE 重连。
- 从节点 A 创建 Session、在节点 B 恢复。
- Runner image canary 和 rollback。

### 20.3 故障注入

- Turn 执行前或执行中杀死 Runner。
- 网络分区后旧 Runner 迟到提交。
- Redis 停机与 outbox 重放。
- SessionStore append 超时和 mirror_error。
- RWX、S3、MCP、Model Gateway 不可用。
- Controller 重启和重复 reconcile。

### 20.4 安全

- 尝试读取其他 Session/Memory。
- Symlink、Zip Slip、Skill 路径穿越。
- Pod Security 和 NetworkPolicy 验证。
- Runner 直接访问公网、K8s API、metadata service 的阻断测试。
- Prompt 注入尝试取得其他用户身份、密钥或业务 MCP 权限。

### 20.5 压测

- 多用户同时创建冷 Session。
- 高 warm-hit 连续聊天。
- 长 Session transcript 和 compaction。
- 大文件、Skill Bundle、MCP 慢响应。
- 平台开销与模型耗时分开统计 P50/P95/P99。

## 21. 生产验收标准

上线前必须满足：

1. 不同用户无法枚举或访问对方 Session、文件、transcript 或 Memory。
2. 同一用户不同 Workspace 的 Auto Memory 不互通。
3. 同一 Session 并发 Turn 只有一个能进入 running。
4. 连续 Turn 在 5 分钟内复用 Runner，超过 5 分钟可新建 generation 并恢复。
5. 节点故障后旧 Runner 的迟到事件被 fencing 拒绝。
6. 运行中断不自动重放有潜在副作用的 MCP。
7. API/Gateway 任一副本重启不丢已提交 Turn 或历史事件。
8. Redis 丢失后可由 PostgreSQL/outbox 恢复分发。
9. Runner 无长期模型、MCP、数据库、S3 或 Kubernetes 凭据。
10. NetworkPolicy 阻止 Runner 直接访问未授权网络。
11. SDK 候选版本能按 Workspace 灰度并按 image digest 回滚。
12. SessionStore conformance、跨 Pod resume、Memory 和 MCP 回归通过。
13. PostgreSQL、S3、RWX 完成备份恢复演练。
14. 关键告警、Dashboard 和审计检索可用。

## 22. 分阶段交付

总体方案拆成独立工作包，不做一次性“大爆炸”上线。

### 阶段 0：运行时解耦

- 固化 AgentRuntimePort 和内部 RuntimeEvent。
- 把 SDK import 收口到 ClaudeSdkAdapter。
- 建立 SDK 兼容矩阵、SessionStore conformance 和 Runner 镜像。
- 不改变当前单实例用户体验。

### 阶段 1：可信身份和 PostgreSQL

- 实现数据中心 IdentityProvider/OIDC。
- SQLite 迁移 PostgreSQL。
- 把进程内 Session 正确性约束迁移到数据库。
- 保持 Session creator-private。

### 阶段 2：共享存储

- 接入 S3、CSI RWX 和 Artifact Service。
- 迁移 workspace、claude-config、Memory 和 Skill Bundle。
- 实现 GatewaySessionStore。
- 完成备份恢复。

### 阶段 3：Sandbox 执行面

- 实现 Sandbox Gateway、Runner 协议和 Sandbox Controller。
- 用 Kubernetes Job 完成 cold start、warm reuse、idle exit、generation 和 fencing。
- 从 API 进程内执行切换到 Redis/outbox 分发。

### 阶段 4：生产安全

- gVisor/等价 RuntimeClass、Sandbox Node Pool。
- Pod Security、NetworkPolicy、secret proxy、MCP OBO。
- 配额、审计和安全测试。

### 阶段 5：HA、可观测与灰度

- API/Gateway/Controller 多副本。
- OTel、告警、容量看板。
- SDK canary、自动停止灰度和 image rollback。
- 故障注入和负载验收。

每个阶段都应有独立 spec、实现计划、迁移脚本、回滚路径和验收报告。本总体设计只定义共同边界。

## 23. 复用优先级

| 能力 | 首选复用 | 不做 |
|---|---|---|
| 容器调度 | Kubernetes Job、Scheduler、Cluster Autoscaler | 自研 Scheduler |
| Job 清理 | TTL-after-finished | 自研定时扫 Pod |
| 隔离 | gVisor、Pod Security、NetworkPolicy | 自研容器 Sandbox |
| 关系数据 | PostgreSQL | 自研分布式数据库 |
| 临时队列/通知 | Redis Stream/PubSub | 自研消息总线 |
| 大对象 | 公司 S3/MinIO/Ceph RGW | 数据库存大文件 |
| POSIX 文件 | 公司 CSI RWX | 自研对象存储同步协议 |
| Transcript | SDK SessionStore + 官方 conformance | 自研 transcript 语义 |
| Memory | Claude Code Auto Memory | 自研提取、Embedding、向量库 |
| Telemetry | SDK OTel + 公司 Collector | 自研 Agent trace 系统 |
| 凭据 | Vault/公司密钥平台 + Gateway 注入 | 长期密钥发给 Runner |

## 24. 关键参考

- [Claude Agent SDK Hosting](https://code.claude.com/docs/en/agent-sdk/hosting)：subprocess、本地状态、Hybrid Session、资源、扩缩容和多租户边界。
- [Claude SessionStore](https://code.claude.com/docs/en/agent-sdk/session-storage)：外部 transcript、参考适配器、conformance、mirror_error 和 retention。
- [Claude Secure Deployment](https://code.claude.com/docs/en/agent-sdk/secure-deployment)：gVisor、代理注入凭据、网络和文件系统边界。
- [Claude OpenTelemetry](https://code.claude.com/docs/en/agent-sdk/observability)：模型、工具、token、cost 和 trace context。
- [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/) 与 [TTL After Finished](https://kubernetes.io/docs/concepts/workloads/controllers/ttlafterfinished/)。
- [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/) 与 [NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)。
