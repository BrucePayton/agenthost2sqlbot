# Kubernetes 多租户 Runtime V2 外部 Review 处置记录

**日期：** 2026-07-29
**Review 来源：** 网页版 ChatGPT 对代码快照、V2 总体设计与 Phase 0-4 计划的整体 Review
**处置结论：** `APPROVE WITH REQUIRED CLARIFICATIONS`
**权威基线：** `docs/superpowers/specs/2026-07-29-kubernetes-multi-tenant-runtime-v2-design.md`

## 1. 总结

Review 指出的主要迁移风险是真实的，但其中数项把“当前单机 MVP 的实现现状”误写成“V2 设计缺失”，另有四项与已提交的 V2/Phase 计划直接矛盾。处置原则如下：

- 接受：Phase 2 提前落最小 Pod/Network 安全基线；把 Supervisor/Agent 的同 Pod 信任边界写得更精确；强化 Tool Operation 状态机；Attachment 增加服务层纵深授权。
- 部分接受：当前进程内任务/锁是迁移约束，但 Phase 0、Phase 1、Phase 3 已按正确顺序替换，不能把尚未实施的未来能力当作当前生产缺陷。
- 驳回：V2 缺少 Tool Operation Ledger、快照/动态授权拆分、Session tombstone、SDK N/N-1 兼容矩阵的结论。四项均已在现有 V2 或 Phase 计划中明确设计。
- 修正一个不现实的安全表述：同 Pod 内 Agent 能访问的本地 endpoint，其 Bash/Skill 子进程也可能访问。安全性不能依赖“Bash 看不见 endpoint”，而必须依赖无凭据代理、当前 Turn 绑定、schema/allowlist、动态授权、额度和 Ledger。

## 2. 逐项处置

| Review 项 | 处置 | 核对结果 | 写回动作 |
|---|---|---|---|
| P0-1 Turn 依赖进程内 `asyncio.Task` | 接受，已规划 | 当前 `TurnService` 确实持有 `_tasks`/`_cancel_events` 并 `create_task`；这是当前单机执行方式。Phase 0 已要求抽出 `AgentRuntimePort` 和显式 dispatcher，Phase 1 多副本 rehearsal 使用 `execution_disabled`，不会继续依赖进程内执行保证生产正确性。 | 保持 Phase 0/1 顺序；不在本次文档 Review 中改运行代码。 |
| P0-2 Session/Memory lock 为进程内锁 | 部分接受 | 当前两个 lock registry 确为进程内。Session active Turn 的 PG partial unique constraint 已在 Phase 1；Memory lease/CAS 已在 Phase 3。Phase 1 多副本不执行 Turn，因此把 Memory lease 提前到 Phase 1 没有正确性收益，反而打乱对象存储/Memory Bundle 依赖。 | 保持 Session 约束在 Phase 1、Memory lease/CAS 在 Phase 3；进程内锁只保留为 SQLite/local 优化。 |
| P0-3 Supervisor/Agent 同 Pod 隔离不足 | 部分接受 | Review 正确指出同 Pod 共享网络命名空间，NetworkPolicy 不能按容器隔离。V2 已明确凭据只进 Supervisor、Agent 直连 Gateway 会因认证/lease 失败。本次进一步明确 shared process namespace、UID/GID、mount、两类本地通道和 Bash 威胁模型。 | 更新 V2 与 Phase 2；Phase 2 增加静态与 live deny 验收。 |
| P0-4 快照与动态权限没有拆开 | 驳回“缺失”，接受措辞补强 | V2 6.3 已分别列出固定 snapshot 与动态 membership/OBO/revocation/quota/approval。Review 结论与文档不符。 | 增加明确公式：`effective = snapshot candidate ∩ current authorization overlay`；overlay 只能收窄，不能扩权，并记录 policy version。 |
| P0-5 缺少 attempt/operation/outcome 模型 | 驳回“设计缺失”，接受状态机补强 | V2 已有 `turn_attempts`、`tool_operations`、effect class、idempotency、reconciliation、`outcome_unknown`；Phase 3 有专门 Task 5。当前表尚未实现是分期事实。 | 补充 operation 状态转移、dispatch 时间、attempt、policy version 与 reconciliation 状态。 |
| P1-1 `AttachmentService.get(id)` 可绕过授权 | 部分接受，纵深防御 | 当前公开 attachment content/delete 路由在调用 service 前均执行 creator owner 校验，未发现现有 API 越权路径。但 raw ID service 方法容易被未来内部调用误用。 | Phase 3 改为 `open_for_principal` 与 `open_for_runner_lease` 两条显式接口，禁止业务路径直接 raw `get(id)`。 |
| P1-2 SDK import 未隔离 | 接受，已规划 | SDK 当前集中在 `app/runtime/claude.py`，但正式 Port/Adapter 与 import guard 尚未实施；Phase 0 Tasks 1-2 已覆盖。 | 不重复新增方案；以 Phase 0 Gate 为准。 |
| P1-3 Auto Memory 仍是本地路径 | 接受，已规划 | 当前单机 MVP 使用本地路径符合现状；V2/Phase 3 已设计 Memory Bundle + PG lease/CAS，且保持 Claude Code 原生 Markdown 写入。 | 不提前造自定义 memory engine；按 Phase 3 实施。 |
| P1-4 最小安全能力放到 Phase 4 太晚 | 接受 | Phase 2 已有非 root、无 SA token、无 host、凭据隔离，但 baseline NetworkPolicy 和 live network denial 原放在 Phase 4。纵向执行链路在 Phase 2 就应具备最小安全基线。 | 把 restricted Pod profile、default-deny/allowlist NetworkPolicy、credential-isolation 和 live denial Gate 前移 Phase 2；Phase 4 保留 RuntimeClass 选型、目标集群生产 OBO/撤权、审计、配额和 Pilot。 |
| P2-1 EventBroker 仅进程内 | 接受，已规划 | 当前 `EventBroker` 是进程内；Phase 1 已明确 PG event history + `LISTEN/NOTIFY`/短轮询，Redis 仅在吞吐数据证明需要时引入。 | 不新增 Redis，不改变一期权威模型。 |
| P2-2 Session 直接删除 | 接受，已规划 | 当前 local MVP 删除前校验 owner 并拒绝运行中 Session。生产 tombstone、hard fence、retention、restore deletion ledger 已在 V2 15.2 和 Phase 3 Task 7。 | 不把当前本地删除路径误当生产删除协议；按 Phase 3 迁移。 |

## 3. 对“未来缺陷摘要”的纠正

以下四项不成立，不能据此重写架构：

1. **“Tool Operation Ledger 缺失”不成立。** V2 第 14 节和 Phase 3 Task 5 已给出 effect class、retry、approval、reconciliation、idempotency、unknown outcome 和持久化字段。
2. **“Capability Snapshot 与 Dynamic Authorization 未拆分”不成立。** V2 第 6.3 节已经拆分；本次只把交集公式和 monotonic deny 语义写得更机械可验。
3. **“Session tombstone 不充分”不成立。** V2 第 15.2 节与 Phase 3 Task 7 已要求 tombstone、HardFence、retention、删除 ledger 与 restore 后重放删除。
4. **“SDK N/N-1 矩阵缺失”不成立。** V2 SDK 兼容章节已要求双向 N/N-1 transcript/resume 测试与 cohort pinning。

此外，Review 的证据链接多次指向被 V2 取代的 2026-07-28 附件，而判断对象却声称包含 V2/Phase 计划。正式决策必须以仓库当前文件为准，不能以旧附件摘要覆盖新版本正文。

## 4. 本次实际修改边界

本次只更新设计和开发计划，不修改运行时代码：

- V2：明确 capability 交集公式、Supervisor/Agent 本地协议与同 Pod 威胁边界、Tool Operation 状态机、Phase 2 最小安全基线。
- Roadmap：Phase 2 纳入 baseline Pod/Network security；Phase 4 改为生产 hardening 与 Pilot。
- Phase 2：加入 `shareProcessNamespace=false`、分 UID/GID、Supervisor-only secret mount、baseline NetworkPolicy、live deny 与本地代理滥用测试。
- Phase 3：Attachment service 改为 principal/runner lease 显式授权接口；Tool Ledger 增加完整状态与 reconciliation 字段。
- Phase 4：明确继承 Phase 2 基线，只负责目标集群强化、动态生产授权与 Pilot Gate。

## 5. 最终决策

总体架构无需推翻。完成上述文档补强后，V2 保持可实施，下一步仍从 Phase 0 开始，并逐阶段通过 Gate；不得因为外部 Review 的误报跳过既定数据权威、fencing、恢复或 SDK 兼容验证。
