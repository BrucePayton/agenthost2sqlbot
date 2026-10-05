# Phase 4 Production Security and Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the multi-tenant execution plane, connect dynamic company identity/model/MCP authorization, enforce quotas and auditability, prove SDK cohort rollback, and admit a bounded first production Pilot only after objective Gates pass.

**Architecture:** Inherit the Phase 2 restricted Pod, credential-isolation, current-Turn proxy, and default-deny/allowlist network baseline, then harden it for the target production cluster at Ingress, API, Gateway, node, storage, network, and credential boundaries. Route all Agent model/MCP traffic through a Supervisor local proxy and Execution Gateway using distinct short-lived audiences. Add dynamic membership/revocation checks, emergency deny, quota admission, redacted audit/telemetry, backup exercises, and data-driven runtime isolation selection.

**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL 16, Kubernetes 1.29+, Kustomize, Pod Security Standards, NetworkPolicy, company OIDC/Space/Vault/Model/MCP/OBO gateways, OpenTelemetry, Prometheus-compatible metrics, Grafana-compatible dashboards, pytest, Playwright, k6 or Locust.

## Global Constraints

- Pilot limit defaults: 20 concurrent Runners, 2 per user, 10 per Workspace, one active Turn per Session, one memory-active Turn per user+Workspace.
- Warm dispatch P95 target is <=3 seconds; cold Session Ready P95 target is <=60 seconds; Turn timeout is 30 minutes; idle TTL is 5 minutes; Runner maximum age is 4 hours and drains only between Turns.
- Control-plane availability target is 99.5%; PostgreSQL/S3 RPO <=15 minutes and regional RTO <=60 minutes.
- Production credentials remain disabled until credential-isolation and network-escape tests pass in the target cluster.
- Phase 4 must not compensate for a missing Phase 2 baseline. If restricted Pod policy, separate Supervisor/Agent identity/mounts, no service-account token, baseline NetworkPolicy, or development-cluster denial tests did not pass, return to the Phase 2 Gate.
- Runner Pod cannot access Kubernetes API, PostgreSQL, S3, metadata service, public internet, or company Model/MCP directly. Because Pod containers share a network namespace, Agent may be able to send packets to Execution Gateway, but it has no Supervisor credential and every direct request must fail authentication/lease checks.
- Supervisor control, model, and MCP/OBO credentials have distinct audience/scope/lifetime and cannot substitute for one another.
- Config snapshot does not freeze authorization. Membership, OBO, emergency deny, revocation, quota, and approval are checked at sensitive-operation time.
- Prompt, file content, raw tool bodies, tokens, and secrets are excluded from default logs, metrics labels, and audit payloads.
- Candidate SDK rollback stops new assignments. Sessions touched by a candidate remain cohort-pinned unless N/N-1 compatibility proves downgrade safe.
- No Pilot expansion based only on average latency. P95/P99, failure modes, quota, storage fencing, and recovery evidence are required.
- Do not add Redis, Kueue, SessionStore, Job replacement, or a warm pool unless Pilot measurements cross a documented threshold and a separate design is approved.

---

## File Structure

### New files

- `app/security/credentials.py` — audience-scoped credential client and validation.
- `app/security/redaction.py` — structured secret/content redaction.
- `app/security/revocation.py` — current membership, emergency deny, and drain decisions.
- `app/gateway/local_proxy.py` — current-Turn model/MCP proxy policy.
- `app/gateway/obo.py` — company MCP/OBO adapter.
- `app/quotas/models.py` — quota dimensions and decisions.
- `app/quotas/service.py` — PG-backed admission/counters.
- `app/audit/models.py`, `app/audit/service.py` — privacy-preserving audit records.
- `app/observability/metrics.py`, `app/observability/tracing.py` — stable metrics/traces.
- `app/runtime/cohort_repository.py`, `app/runtime/cohort_scheduler.py` — candidate allocation and Session pinning.
- `app/operations/backup.py`, `app/operations/restore.py` — restore manifest and tombstone-aware exercises.
- `deploy/kubernetes/overlays/pilot/` — Pilot limits, NetworkPolicy, Pod Security, RuntimeClass, node affinity, disruption, and telemetry configuration.
- `deploy/observability/dashboards/runtime-v2.json` — versioned dashboard definition.
- `deploy/observability/alerts/runtime-v2.yaml` — actionable alerts and runbook links.
- `tests/security/` — credential, network, identity, filesystem, bundle, and tenant-isolation tests.
- `tests/load/locustfile.py` — bounded cold/warm/mixed workload.
- `tests/integration/test_sdk_cohorts.py`, `tests/integration/test_revocation.py`, `tests/integration/test_backup_restore.py`.
- `docs/operations/runtime-v2-runbook.md`, `docs/operations/runtime-v2-incident-matrix.md`, `docs/operations/runtime-v2-pilot-checklist.md`.
- `scripts/verify-phase-4.sh`.

### Modified files

- `app/config.py`, `app/bootstrap.py` — production-only required integrations.
- `app/gateway/service.py`, `app/gateway/routes.py`, `app/runner/supervisor.py` — dynamic credentials, revocation, quota, redaction, and proxy.
- `app/control/controller.py`, `app/control/pod_spec.py`, `app/control/repository.py` — cohort, max-age drain, quota, and secure Pod placement.
- `app/auth/access.py`, `app/api/routes.py` — current membership and emergency deny enforcement.
- `app/db/models.py` and new Alembic migration — quota/audit/cohort/credential metadata without secrets.
- `deploy/kubernetes/base/` — security contexts and policy labels.
- `pyproject.toml`, `uv.lock` — telemetry/load dependencies if not supplied by company base image.
- `tests/test_gateway.py`, `tests/test_controller.py`, `tests/test_pod_spec.py`, `tests/test_api.py`.
- `.env.example`, `README.md`, `docs/operations/runtime-v2-verification-ledger.md`.

---

### Task 1: Select and enforce the production sandbox profile

**Files:**
- Modify: `app/control/pod_spec.py`
- Modify: `deploy/kubernetes/base/namespace.yaml`
- Modify: `deploy/kubernetes/base/rbac.yaml`
- Create: `deploy/kubernetes/overlays/pilot/kustomization.yaml`
- Create: `deploy/kubernetes/overlays/pilot/security.yaml`
- Create: `deploy/kubernetes/overlays/pilot/network-policy.yaml`
- Create: `deploy/kubernetes/overlays/pilot/node-placement.yaml`
- Create: `tests/security/test_pod_security.py`
- Create: `tests/security/test_network_policy.py`

**Interfaces:**
- Pilot overlay names an approved sandbox `RuntimeClass` after benchmark.
- Namespace preserves the Phase 2 Pod Security `restricted` baseline and adds target-cluster policy/exception controls.
- Runner node pool uses explicit labels/taints/tolerations.
- NetworkPolicy defaults deny ingress/egress and admits only Runner Pod to Execution Gateway plus required DNS/telemetry paths; it does not claim per-container egress separation.

- [ ] **Step 1: Benchmark candidate RuntimeClasses**

Run representative Git, Python, npm, archive, stdio MCP, CSI attach, transcript, and Subagent workloads under restricted `runc`, gVisor, and Kata if available. Record compatibility plus cold/warm P50/P95/P99 CPU/RSS/IO/PID results. Select the strongest compatible target; do not assume gVisor is available or faster.

- [ ] **Step 2: Add static manifest policy tests**

Re-run the Phase 2 assertions for distinct non-root UID/GID, `shareProcessNamespace=false`, Supervisor-only secret mounts, read-only roots, `allowPrivilegeEscalation=false`, capabilities drop ALL, seccomp RuntimeDefault, no host namespaces/path/port, no service account token, CPU/RAM/ephemeral/PID limits, and only documented writable mounts. Then add RuntimeClass, node-pool, admission-policy, exception-expiry, and target-cluster-specific assertions.

- [ ] **Step 3: Add live network-deny tests**

From Agent prove denial of API server, PG, S3, metadata IP, public internet, company model/MCP direct endpoints, DNS names outside allowlist, and other Runner Pods. Also prove an Agent-originated direct request to the network-reachable Execution Gateway is rejected because it lacks the Supervisor-only credential and valid current lease. Record that standard NetworkPolicy operates at Pod, not container, granularity.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/security/test_pod_security.py tests/security/test_network_policy.py tests/test_pod_spec.py -q
kubectl kustomize deploy/kubernetes/overlays/pilot >/tmp/workspace-agent-pilot.yaml
git add app/control/pod_spec.py deploy/kubernetes tests/security tests/test_pod_spec.py
git commit -m "security(k8s): enforce pilot sandbox policy"
```

---

### Task 2: Separate credentials and proxy all model/MCP access

**Files:**
- Create: `app/security/credentials.py`
- Create: `app/gateway/local_proxy.py`
- Create: `app/gateway/obo.py`
- Modify: `app/gateway/service.py`
- Modify: `app/runner/supervisor.py`
- Modify: `app/control/pod_spec.py`
- Create: `tests/security/test_credential_isolation.py`
- Create: `tests/security/test_gateway_proxy.py`
- Modify: `tests/test_gateway.py`

**Interfaces:**
- Credential audiences: `runner-control`, `model-execution`, `mcp-execution`.
- `CredentialBroker.issue(runner_lease, audience, scopes, ttl) -> EphemeralCredential`.
- `LocalExecutionProxy` binds only in the Pod and derives current Turn context from Supervisor state.
- `OboGatewayClient.call(principal_context, capability, arguments, idempotency) -> ToolResponse`.

- [ ] **Step 1: Add substitution and leakage tests**

Prove each audience is rejected by the other endpoints. Search Agent `/proc/self/environ`, mounted files, argv, shared directories, child processes, error/event payloads, and logs for canary secrets. Run malicious Prompt/Skill/Bash attempts to exfiltrate them. A raw Agent request to Execution Gateway may reach the Service at the network layer but must receive an authentication denial and produce a redacted audit record.

- [ ] **Step 2: Implement Supervisor-only credential acquisition**

Never place control/model/MCP tokens in the Pod spec's Agent container. Prefer workload identity or a Supervisor-only projected credential. Rotate credentials before expiry and bind them to Runner instance/generation and current Turn where supported.

- [ ] **Step 3: Route SDK endpoints through local proxy**

Point SDK model base URL and MCP endpoints at Supervisor localhost. Supervisor calls Execution Gateway; Gateway derives trusted user/Workspace from Turn ownership and fetches short OBO downstream credentials outside the Runner.

- [ ] **Step 4: Enforce read-only Pilot policy**

Publish only read-only MCP capabilities. Tool Ledger must record each call. Any undeclared effect class, direct endpoint, uploaded stdio server, or write capability is denied before network dispatch.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/security/test_credential_isolation.py tests/security/test_gateway_proxy.py tests/test_gateway.py -q
git add app/security/credentials.py app/gateway/local_proxy.py app/gateway/obo.py app/gateway/service.py app/runner/supervisor.py app/control/pod_spec.py tests/security tests/test_gateway.py
git commit -m "security(gateway): isolate execution credentials"
```

---

### Task 3: Enforce dynamic revocation and emergency deny

**Files:**
- Create: `app/security/revocation.py`
- Modify: `app/auth/access.py`
- Modify: `app/api/routes.py`
- Modify: `app/gateway/service.py`
- Modify: `app/control/controller.py`
- Create: `tests/integration/test_revocation.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- `AuthorizationSnapshot` is evaluated for every model/MCP/Artifact operation.
- `RevocationService.authorize(operation_context) -> AuthorizationDecision`.
- Decision can `allow`, `deny`, or `deny_and_drain` with stable reason code.

- [ ] **Step 1: Add running-Session revocation tests**

Remove a member while a Turn is running. Subsequent model, MCP, Artifact publication/download, new Turn, and resume operations must fail within the documented SLA. Controller drains Runner; already persisted creator-private history remains inaccessible while membership is absent.

- [ ] **Step 2: Add emergency-deny tests**

Revoke model, MCP, Skill, or Workspace capability independently of Session Config snapshot. Old snapshot hash remains immutable but runtime authorization denies the action and emits a redacted audit decision.

- [ ] **Step 3: Implement fail-closed outage policy**

For a sensitive operation, stale membership plus unavailable authority denies. Emergency deny cache has a short bounded TTL plus push invalidation if company infrastructure supports it; PostgreSQL records current policy version.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/integration/test_revocation.py tests/test_auth.py tests/test_gateway.py -q
git add app/security/revocation.py app/auth/access.py app/api/routes.py app/gateway/service.py app/control/controller.py tests/integration/test_revocation.py tests/test_auth.py
git commit -m "security(auth): enforce dynamic revocation"
```

---

### Task 4: Add atomic quota admission and max-age draining

**Files:**
- Create: `app/db/alembic/versions/rev_0008_pilot_governance.py`
- Modify: `app/db/models.py`
- Create: `app/quotas/models.py`
- Create: `app/quotas/service.py`
- Modify: `app/control/repository.py`
- Modify: `app/control/controller.py`
- Modify: `app/gateway/service.py`
- Create: `tests/test_quotas.py`
- Modify: `tests/test_controller.py`

**Interfaces:**
- `QuotaService.reserve_runner(user_id, workspace_id, session_id) -> QuotaReservation`.
- `QuotaService.release_runner(reservation, terminal_reason) -> None`.
- Limits default to global 20, user 2, Workspace 10; configured server-side.
- Runner at four hours sets `desired_state=draining` after current finalization and cannot claim another Turn.

- [ ] **Step 1: Add concurrent quota tests**

Race multiple Controllers/API replicas at each limit. Counters/reservations must not leak after failed Pod creation, terminal Pod, Controller restart, or hard-fence wait. Queued Turns remain visible with a stable quota reason.

- [ ] **Step 2: Implement PostgreSQL admission**

Use one transaction and row locks/advisory locking with documented key derivation. Do not rely on metrics, Kubernetes Pod counts, or in-memory semaphores for correctness.

- [ ] **Step 3: Implement max age and idle policy**

Idle TTL starts at durable finalization. Maximum age does not kill an active Turn; it prevents the next claim and drains between Turns. Turn wall clock still stops at 30 minutes by Supervisor policy.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_quotas.py tests/test_controller.py tests/test_gateway.py tests/test_migrations.py -q
git add app/db/alembic/versions/rev_0008_pilot_governance.py app/db/models.py app/quotas app/control/repository.py app/control/controller.py app/gateway/service.py tests/test_quotas.py tests/test_controller.py
git commit -m "feat(governance): enforce pilot quotas"
```

---

### Task 5: Add redacted audit, metrics, traces, alerts, and runbooks

**Files:**
- Create: `app/security/redaction.py`
- Create: `app/audit/models.py`
- Create: `app/audit/service.py`
- Create: `app/observability/metrics.py`
- Create: `app/observability/tracing.py`
- Modify: `app/bootstrap.py`
- Create: `deploy/observability/dashboards/runtime-v2.json`
- Create: `deploy/observability/alerts/runtime-v2.yaml`
- Create: `docs/operations/runtime-v2-runbook.md`
- Create: `docs/operations/runtime-v2-incident-matrix.md`
- Create: `tests/test_redaction.py`
- Create: `tests/test_observability.py`

**Interfaces:**
- Stable correlation: `trace_id`, internal Workspace/Session/Turn/attempt/Runner generation IDs.
- Audit records actor/decision/resource/reason/policy version/timestamp; Prompt and content are omitted by default.
- Metrics labels are bounded and never include user text, object keys, tokens, error bodies, or arbitrary tool names.

- [ ] **Step 1: Add canary-secret redaction tests**

Inject secrets into headers, nested payloads, exceptions, SDK events, tool arguments, filenames, and subprocess stderr. Assert logs, audit, SSE errors, traces, and metrics exports contain no secret.

- [ ] **Step 2: Instrument decision-useful metrics**

Include queue/status dwell, cold/warm start, idle hit, Suspect/fence/recovery, PVC attach/detach, memory CAS/failure, unknown outcome, stuck finalizing, SDK resume, MCP readiness, token/cost, and Runner resource utilization.

- [ ] **Step 3: Add actionable alerts and runbooks**

Every alert names threshold, duration, owner, severity, dashboard, safe diagnosis, and recovery. Include duplicate registration, old generation traffic, hard-fence wait, finalizing timeout, object reference mismatch, and revocation SLA breach.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_redaction.py tests/test_observability.py -q
git add app/security/redaction.py app/audit app/observability app/bootstrap.py deploy/observability docs/operations/runtime-v2-runbook.md docs/operations/runtime-v2-incident-matrix.md tests/test_redaction.py tests/test_observability.py
git commit -m "feat(ops): add redacted runtime observability"
```

---

### Task 6: Implement SDK cohort canary, pinning, and rollback

**Files:**
- Create: `app/runtime/cohort_repository.py`
- Create: `app/runtime/cohort_scheduler.py`
- Modify: `app/control/repository.py`
- Modify: `app/control/controller.py`
- Create: `tests/integration/test_sdk_cohorts.py`
- Modify: `tests/test_runtime_cohorts.py`

**Interfaces:**
- Cohort states: `disabled`, `canary`, `stable`, `draining`.
- `CohortScheduler.select(workspace, session, request) -> RuntimeCohort`.
- Once candidate writes a Session transcript, that Session stores/preserves its cohort until a compatibility decision permits reassignment.

- [ ] **Step 1: Add N/N-1 matrix tests**

For pinned Runner images execute new Session, normal resume, cross-Pod resume, N reads N-1 transcript, N-1 reads N-written transcript or records downgrade prohibition, compaction, Subagent resume, Skill, MCP, Memory, interrupt, and timeout. When the Claude Agent SDK or MCP SDK major/revision changes, separately cover in-process MCP, HTTP MCP, stdio MCP, company MCP/OBO Gateway negotiation, tool discovery/call/cancel/error mapping, and v2-only feature gating.

- [ ] **Step 2: Implement allocation controls**

Candidate targets only explicit test/canary Workspaces and Sessions. No browser parameter can select image/cohort. Each Runner instance records image digest, Claude Agent SDK, Claude CLI, MCP SDK, internal Runner protocol, and verified MCP protocol revision/feature set.

- [ ] **Step 3: Implement honest rollback**

Rollback changes candidate to `draining/disabled` for new generations. It does not move a candidate-touched Session to stable unless the compatibility matrix says downgrade-safe.

- [ ] **Step 4: Verify and commit**

```bash
RUN_CLUSTER_SDK_TESTS=1 uv run pytest tests/integration/test_sdk_cohorts.py tests/test_runtime_cohorts.py -q
git add app/runtime/cohort_repository.py app/runtime/cohort_scheduler.py app/control/repository.py app/control/controller.py tests/integration/test_sdk_cohorts.py tests/test_runtime_cohorts.py
git commit -m "feat(runtime): canary sdk cohorts safely"
```

---

### Task 7: Automate backup, restore, and deletion-resurrection exercises

**Files:**
- Create: `app/operations/backup.py`
- Create: `app/operations/restore.py`
- Create: `tests/integration/test_backup_restore.py`
- Modify: `docs/operations/runtime-v2-runbook.md`

**Interfaces:**
- Backup manifest correlates PostgreSQL LSN, S3 object versions, PVC snapshot IDs, and encryption key version.
- Restore validates references and reapplies deletion ledger before reopening traffic.
- Supports single Session, Workspace, and full-service exercise modes.

- [ ] **Step 1: Add deterministic restore tests**

Cover consistent restore, missing object, missing snapshot, wrong encryption key version, point-in-time before/after Memory CAS, partial Session restore, and deleted Session/user/Workspace not resurrected.

- [ ] **Step 2: Implement dry-run and execute modes**

Dry-run performs validation without mutation. Execute requires an operator confirmation token from a separate administrative workflow and writes an audit event. Never take arbitrary bucket/key/PVC names from public API input.

- [ ] **Step 3: Measure RPO/RTO**

Run an isolated Pilot restore and record achieved RPO/RTO against 15/60-minute targets, including identity/membership resynchronization and deletion-ledger replay.

- [ ] **Step 4: Verify and commit**

```bash
RUN_BACKUP_RESTORE_TESTS=1 uv run pytest tests/integration/test_backup_restore.py -q
git add app/operations tests/integration/test_backup_restore.py docs/operations/runtime-v2-runbook.md
git commit -m "feat(ops): automate restore verification"
```

---

### Task 8: Run bounded load, failure, and tenant-isolation tests

**Files:**
- Create: `tests/load/locustfile.py`
- Create: `tests/security/test_tenant_isolation.py`
- Create: `scripts/verify-phase-4.sh`
- Create: `docs/operations/runtime-v2-pilot-checklist.md`
- Modify: `docs/operations/runtime-v2-verification-ledger.md`

- [ ] **Step 1: Define representative load mix**

Use short text Turns, file-heavy Turns, MCP reads, long Turns, Session resume, Memory waits, cancellation, Subagent fanout, and cold/warm mixes. Ramp only to the Pilot maximum of 20 Runners.

- [ ] **Step 2: Execute tenant isolation matrix**

For two users in one Team Workspace and users in different Workspaces prove isolation of Session list/detail, SSE, messages, files, attachments, Artifacts, workdir/PVC, transcript, Memory, Config authorization, Runner lease, and Gateway operations. Include admin/owner roles and forged IDs.

- [ ] **Step 3: Execute controlled failures**

Restart API/Controller/Gateway, disrupt PG/S3/model/MCP, remove membership, revoke capability, drain a node, delay CSI detach, kill Runner containers, and stop candidate cohort assignment. Confirm alerts/runbooks and no duplicate writers/replays.

- [ ] **Step 4: Run full verification**

```bash
uv run ruff check app tests
uv run pytest -q
bash scripts/verify-phase-4.sh
```

- [ ] **Step 5: Record exact evidence**

Record P50/P95/P99, error rate, queue dwell, warm hit, cold start, quota decisions, hard-fence waits, recovery results, security evidence, image/cohort digests, and open exceptions. Never paste credentials or Prompt content.

- [ ] **Step 6: Commit**

```bash
git add tests/load tests/security/test_tenant_isolation.py scripts/verify-phase-4.sh docs/operations/runtime-v2-pilot-checklist.md docs/operations/runtime-v2-verification-ledger.md
git commit -m "test(pilot): verify production runtime gate"
```

---

### Task 9: Admit the first Pilot through a manual Gate

**Files:**
- Modify: `docs/operations/runtime-v2-pilot-checklist.md`
- Modify: `README.md`
- Modify: `.env.example`

- [ ] **Step 1: Review every V2 production criterion**

The checklist must show pass evidence for Product Workspace boundaries, creator-private APIs/UI, hard fencing, duplicate Runner prevention, execution barrier, no replay, Memory CAS, read-only Config bundle, Agent credential isolation, Gateway/OBO enforcement, cross-node resume, backup/deletion, SDK cohort rollback, metrics/quota/audit, and target performance.

- [ ] **Step 2: Record company dependencies**

Record exact Kubernetes/CSI/OIDC/Space/Model/MCP/OBO/S3/Vault owners, versions/endpoints by non-secret identifier, SLAs, escalation contacts, and tested failure behavior.

- [ ] **Step 3: Enable a bounded allowlist**

Use a server-side allowlist for the initial Workspace/user cohort. Keep non-idempotent writes disabled. Do not silently expand concurrency or roles.

- [ ] **Step 4: Define rollback triggers**

Include duplicate-writer evidence, credential leakage, creator-private breach, unresolved unknown outcomes, fencing SLA breach, RPO/RTO failure, SDK incompatibility, or sustained SLO failure. Rollback stops admissions/candidate assignment and drains safely; it does not replay Turns.

- [ ] **Step 5: Commit the approved checklist**

```bash
git add docs/operations/runtime-v2-pilot-checklist.md README.md .env.example
git commit -m "docs(pilot): approve bounded production cohort"
```

## Production Pilot Gate

Pilot admission is blocked until all conditions are true:

1. Creator-private authorization and tenant isolation pass at API, SSE, object, PVC, Memory, and Gateway layers.
2. Agent cannot read credentials or bypass local Supervisor proxy/network controls.
3. Dynamic membership removal and emergency deny meet the recorded revocation SLA.
4. Quotas hold under concurrent admission and recover after failure.
5. Old Runner cannot write or claim after generation/fence changes.
6. `running`, `finalizing`, `outcome_unknown`, and `recovery_required` semantics survive restarts.
7. Cross-node resume and tombstone-aware backup restore pass.
8. SDK candidate cohort can be stopped without unsafe downgrade.
9. Pilot performance/SLO targets are measured at P50/P95/P99 and accepted.
10. Alerts, dashboards, runbooks, owners, on-call path, and rollback triggers are approved.

Only after this Gate may the bounded allowlist receive real users and production-scoped credentials.
