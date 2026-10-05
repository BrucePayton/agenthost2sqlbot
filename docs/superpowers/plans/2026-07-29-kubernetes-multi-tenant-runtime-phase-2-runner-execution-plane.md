# Phase 2 Minimal Safe Runner Execution Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute one queued test Turn in an isolated Kubernetes Runner with a per-Session RWOP PVC, a unique generation, an execution barrier, deterministic finalization, warm reuse, and hard fencing.

**Architecture:** Add a PostgreSQL-driven Sandbox Controller and an internal Execution Gateway. The Controller creates ordinary `restartPolicy: Never` Pods with trusted Supervisor and untrusted Agent containers. The Supervisor owns Gateway credentials and lifecycle; Agent runs the existing runtime Adapter through narrow lifecycle and current-Turn capability interfaces. Kubernetes and PostgreSQL facts are reconciled before any new writer is admitted. Phase 2 also establishes the minimum restricted Pod and default-deny/allowlist network baseline; Phase 4 strengthens this baseline for production rather than introducing it for the first time.

**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL 16, kubernetes-asyncio, Kubernetes 1.29+, CSI RWOP, Docker/BuildKit, Kustomize, pytest, Hypothesis, Kind for protocol tests, company development cluster for CSI tests.

## Global Constraints

- This phase uses test Workspaces, test model credentials, read-only MCP, and non-sensitive data only.
- One active Session maps to one ordinary Pod named `runner-<session-short-id>-g<generation>`.
- Pod uses `restartPolicy: Never`, `activeDeadlineSeconds: 14400`, and `automountServiceAccountToken: false`.
- Pod uses the restricted security profile, `shareProcessNamespace=false`, no host namespaces/paths/ports, fixed non-root UID/GID, read-only root filesystems, dropped capabilities, and seccomp `RuntimeDefault`.
- Supervisor and Agent use different UID/GID. Supervisor secrets/credentials use Supervisor-only mounts and never a shared env, `emptyDir`, Session PVC, or Agent-readable projected volume.
- Session PVC uses `ReadWriteOncePod`; one PVC contains `/session/workspace`, `/session/claude-config`, and `/session/outputs`.
- A missing heartbeat, deleted Pod object, or rejected old token is not a hard fence.
- A new generation is allowed only after old Pod terminal status plus CSI detach/storage fencing evidence.
- Controller is the only component with Kubernetes API permissions. Runner has no Kubernetes, PostgreSQL, or S3 access.
- Supervisor control credential never enters Agent environment, filesystem, argv, logs, Prompt, Skill, Bash, or Subagent.
- Standard NetworkPolicy is treated as Pod-scoped, not container-scoped. Namespace/Runner policies default deny and allow only documented Gateway, DNS, and telemetry paths; an Agent-originated direct Gateway request must still fail authentication/lease checks.
- Assume Bash, Skill, and Subagent can reach any Agent-accessible Pod-local endpoint. The local capability proxy carries no bearer token, accepts no trusted IDs or arbitrary URL, and is constrained by current Turn, schema, immutable snapshot/current authorization intersection, quota, effect policy, approval, and Ledger.
- Gateway assigns Session, Turn, user, Workspace, attempt, and generation from the authenticated lease; Agent cannot select them.
- SDK starts only after PostgreSQL atomically records `assigned -> running` and a new execution nonce.
- Once `running`, the whole Turn is never automatically replayed.
- Keep `local_inline` only for local development and deterministic unit tests.

---

## File Structure

### New files

- `app/db/alembic/versions/rev_0006_runner_control_plane.py` — runtime state and append-only Runner instances.
- `app/control/models.py` — desired state, generation, fencing, and Runner state enums.
- `app/control/repository.py` — PG queue scan, generation lease, registration, heartbeat, and fencing CAS.
- `app/control/kubernetes.py` — narrow Kubernetes client port and official client adapter.
- `app/control/pod_spec.py` — deterministic PVC and two-container Pod builders.
- `app/control/controller.py` — reconcile loop.
- `app/control/main.py` — Controller process entrypoint.
- `app/gateway/auth.py` — short-lived, audience-scoped Runner token verification.
- `app/gateway/routes.py` — register, heartbeat, claim, event, finalize, and cancel endpoints.
- `app/gateway/service.py` — execution barrier and lease-derived authorization.
- `app/gateway/main.py` — internal Gateway process entrypoint.
- `app/runner/protocol.py` — narrow Supervisor/Agent local protocol.
- `app/runner/supervisor.py` — trusted lifecycle process.
- `app/runner/agent.py` — untrusted Adapter process.
- `app/runner/main.py` — `supervisor` and `agent` entrypoints.
- `deploy/docker/Dockerfile.control-plane` — API/Controller/Gateway image.
- `deploy/docker/Dockerfile.runner` — immutable Runner image.
- `deploy/kubernetes/base/` — Kustomize Namespace, deployments, services, RBAC, and Runner templates.
- `deploy/kubernetes/base/network-policy.yaml` — baseline default-deny and explicit Gateway/DNS/telemetry allowlist.
- `tests/test_runner_state_machine.py`, `tests/test_controller.py`, `tests/test_pod_spec.py`, `tests/test_gateway.py`, `tests/test_runner_protocol.py` — unit/contract tests.
- `tests/security/test_phase2_baseline.py` — static credential, process, filesystem, and network-policy assertions.
- `tests/integration/test_phase2_network_denial.py` — opt-in live denial and direct-Gateway authentication checks.
- `tests/integration/test_runner_vertical_slice.py` — opt-in cluster vertical slice.
- `scripts/verify-phase-2.sh` — reproducible Gate checks.

### Modified files

- `pyproject.toml`, `uv.lock` — Kubernetes and cryptographic dependencies.
- `app/config.py` — controller/gateway/runner modes, URLs, cohort, timeouts, and signing-key locations.
- `app/db/models.py` — `SessionRuntimeStateRecord`, `RunnerInstanceRecord`, and Turn lease fields.
- `app/turns/dispatcher.py` — `KubernetesExecutionDispatcher` queues only; it does not create Pods.
- `app/bootstrap.py`, `app/api/dependencies.py`, `app/main.py` — select dispatcher without granting API Kubernetes permissions.
- `tests/test_dispatcher.py`, `tests/test_migrations.py`, `tests/test_api.py` — expanded contracts.
- `.env.example`, `README.md`, `docs/operations/runtime-v2-verification-ledger.md` — deployment and Gate evidence.

---

### Task 1: Persist Runner generations and fencing states

**Files:**
- Create: `app/db/alembic/versions/rev_0006_runner_control_plane.py`
- Create: `app/control/models.py`
- Create: `app/control/repository.py`
- Modify: `app/db/models.py`
- Modify: `tests/test_migrations.py`
- Create: `tests/test_runner_state_machine.py`
- Modify: `tests/integration/test_postgres_authority.py`

**Interfaces:**
- `session_runtime_state(session_id, current_generation, desired_state, current_runner_id, idle_since, recovery_reason)`.
- `runner_instances(id, session_id, generation, pod_name, pod_uid, node_name, image_digest, sdk_version, protocol_version, status, heartbeat_at, registered_at, ended_at, exit_reason)`.
- `RunnerRepository.reserve_generation(session_id, cohort) -> RunnerReservation`.
- `RunnerRepository.record_fence_evidence(runner_id, pod_terminal, storage_detached, evidence) -> FenceDecision`.

- [ ] **Step 1: Add migration and state-machine tests**

Require unique `(session_id, generation)`, append-only instance identity, one current pointer, monotonic generation, and these transitions:

```text
Absent -> Provisioning -> Registered -> Ready -> Busy -> Finalizing -> Idle
Idle -> Busy | Draining
Busy|Finalizing -> Suspect -> FenceRequested -> HardFenced|RecoveryRequired
HardFenced -> Terminated -> Absent
RecoveryRequired -> HardFenced
```

- [ ] **Step 2: Add concurrency tests**

Use two PostgreSQL transactions calling `reserve_generation()` for the same Session. Exactly one wins and no generation number is skipped by a retry of the same reservation token.

- [ ] **Step 3: Implement CAS repository methods**

Every registration, heartbeat, claim, event, finalize, and fence update compares Session ID, Runner instance ID, generation, and expected state. Old generations receive a stable `stale_runner_generation` error.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_migrations.py tests/test_runner_state_machine.py -q
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_postgres_authority.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/db/alembic/versions/rev_0006_runner_control_plane.py app/control/models.py app/control/repository.py app/db/models.py tests/test_migrations.py tests/test_runner_state_machine.py tests/integration/test_postgres_authority.py
git commit -m "feat(control): persist runner generations"
```

---

### Task 2: Build deterministic RWOP PVC and Runner Pod specifications

**Files:**
- Create: `app/control/kubernetes.py`
- Create: `app/control/pod_spec.py`
- Create: `tests/test_pod_spec.py`
- Modify: `app/config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- `KubernetesPort.create_pvc`, `get_pvc`, `create_pod`, `get_pod`, `delete_pod`, `list_volume_attachments`.
- `build_session_pvc(session_id, storage_class, size) -> V1PersistentVolumeClaim`.
- `build_runner_pod(reservation, config) -> V1Pod`.

- [ ] **Step 1: Add exact manifest tests**

Assert deterministic DNS-safe names, labels for Session/generation/cohort, RWOP access mode, fixed but distinct non-root UID/GID, `shareProcessNamespace=false`, no service account token, no host namespaces/paths/ports, read-only roots, dropped capabilities, seccomp, deadline, resource limits, separate Agent/Supervisor environment, and fixed narrow Pod-local protocol endpoints.

- [ ] **Step 2: Assert credential isolation**

The Pod spec test must recursively inspect Agent env, envFrom, volumes, mounts, commands, args, projected secrets, shared `emptyDir`, and Session PVC paths and prove no control/model/MCP signing key, Gateway bearer, database URL, Kubernetes token, or S3 secret is reachable by Agent. Add a canary secret to the Supervisor mount and prove it is absent from Agent-visible paths.

- [ ] **Step 3: Implement the narrow Kubernetes adapter**

Only the Controller deployment constructs the official Kubernetes client. Other modules depend on `KubernetesPort`. Translate 409 create conflicts into read-after-create reconciliation, not a new name.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_pod_spec.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/control/kubernetes.py app/control/pod_spec.py app/config.py pyproject.toml uv.lock tests/test_pod_spec.py
git commit -m "feat(control): build isolated runner pods"
```

---

### Task 3: Implement the idempotent Sandbox Controller

**Files:**
- Create: `app/control/controller.py`
- Create: `app/control/main.py`
- Create: `tests/test_controller.py`

**Interfaces:**
- `SandboxController.reconcile_once(limit: int) -> ReconcileReport`.
- `SandboxController.run(stop_event) -> None`.
- PG scan uses `FOR UPDATE SKIP LOCKED` to find Sessions with queued Turns and no Ready/Busy/Finalizing Runner.

- [ ] **Step 1: Add fake-Kubernetes reconcile tests**

Cover new PVC/Pod, create response lost but resource exists, Controller restart, duplicate Controller replicas, Pod Pending, registration timeout, Pod terminal, missed heartbeat, old Pod still Running, PVC still attached, and successful hard fence.

- [ ] **Step 2: Implement desired-state reconciliation**

The Controller performs one bounded action per resource per reconcile iteration and persists intent before external create/delete. A Pod create conflict reads the deterministic name and validates labels/UID; it never allocates a second generation.

- [ ] **Step 3: Implement hard-fence evidence rules**

Require:

```python
hard_fenced = (
    pod_phase in {"Succeeded", "Failed"}
    and not matching_volume_attachment_exists
)
```

If the cluster/CSI cannot expose sufficient detach evidence, transition to `RecoveryRequired`; do not infer safety from timeout.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_controller.py tests/test_runner_state_machine.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/control/controller.py app/control/main.py tests/test_controller.py
git commit -m "feat(control): reconcile session runners"
```

---

### Task 4: Implement authenticated Gateway registration, heartbeat, and execution barrier

**Files:**
- Create: `app/gateway/auth.py`
- Create: `app/gateway/service.py`
- Create: `app/gateway/routes.py`
- Create: `app/gateway/main.py`
- Create: `tests/test_gateway.py`

**Interfaces:**
- `POST /internal/runners/register`
- `POST /internal/runners/heartbeat`
- `POST /internal/runners/claim`
- `POST /internal/runners/events`
- `POST /internal/runners/finalize`
- `GET /internal/runners/cancel-state`
- Runner token claims contain runner instance, generation, audience, expiry, and nonce; user/Workspace/Turn scope is derived from PG.

- [ ] **Step 1: Add authentication and stale-generation tests**

Cover wrong audience, expiry, replayed registration nonce, mismatched Pod UID, mismatched cohort/protocol, old generation heartbeat/event/finalize, arbitrary Turn ID, and a token presented to a model/MCP endpoint.

- [ ] **Step 2: Add execution-barrier tests**

Two claim requests may race; only one creates `turn_attempts` and atomically writes `assigned -> running`, `execution_nonce`, Runner generation, and attempt. The response that authorizes SDK start is returned only after commit.

- [ ] **Step 3: Implement lease-derived authorization**

Do not accept trusted Session, user, Workspace, or generation parameters from Agent payload. Validate each event against the authenticated Runner's current busy Turn. Assign event sequence in PostgreSQL.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_gateway.py tests/test_turn_repository.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/gateway tests/test_gateway.py
git commit -m "feat(gateway): enforce runner execution barrier"
```

---

### Task 5: Split trusted Supervisor from untrusted Agent

**Files:**
- Create: `app/runner/protocol.py`
- Create: `app/runner/supervisor.py`
- Create: `app/runner/agent.py`
- Create: `app/runner/main.py`
- Create: `tests/test_runner_protocol.py`
- Modify: `app/runtime/contracts.py`

**Interfaces:**
- Lifecycle channel operations: `prepare`, `start_turn`, `event`, `result`, `interrupt`, `shutdown`.
- Current-Turn capability proxy operations: schema-bound model/MCP requests only; no credential retrieval, arbitrary URL forwarding, trusted-ID override, file read, Runner lifecycle, or Supervisor administration.
- Supervisor supplies one current `RuntimeRequest` over the local channel.
- Agent sends normalized `RuntimeEvent`; it cannot call Gateway directly.

- [ ] **Step 1: Add protocol validation tests**

Reject unknown operations, oversized frames, invalid JSON, path fields outside fixed mount roots, events after terminal result, second simultaneous Turn, Agent attempts to set trusted IDs, arbitrary proxy URLs, undeclared model/MCP/schema, snapshot expansion, stale authorization policy, quota bypass, and non-read-only effects.

- [ ] **Step 2: Implement Unix-domain socket or loopback protocol**

Bind on fixed Pod-local endpoints inaccessible outside the Pod. Do not claim they are inaccessible to Agent child processes. Apply message-size and request-rate limits; bind each capability request to the current Turn/lease and have Gateway recompute the snapshot/current-authorization intersection. Derive fixed paths in Supervisor:

```text
cwd=/session/workspace
CLAUDE_CONFIG_DIR=/session/claude-config
outputs=/session/outputs
```

- [ ] **Step 3: Implement lifecycle ordering**

Supervisor registers, verifies mount/config, claims, receives committed execution barrier, sends `start_turn`, forwards events, interrupts on cancel, waits for Agent/subprocess cleanup, finalizes, then marks Runner Idle. No next Turn is claimed while finalization is incomplete.

- [ ] **Step 4: Verify process isolation locally**

Run Supervisor and Agent as separate subprocesses with a fake runtime and fake Gateway. Kill each process at every protocol boundary and assert the resulting durable state.

```bash
uv run pytest tests/test_runner_protocol.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/runner app/runtime/contracts.py tests/test_runner_protocol.py
git commit -m "feat(runner): separate supervisor and agent"
```

---

### Task 6: Add Kubernetes dispatch while preserving local development

**Files:**
- Modify: `app/turns/dispatcher.py`
- Modify: `app/bootstrap.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/main.py`
- Modify: `app/config.py`
- Modify: `tests/test_dispatcher.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- `KubernetesExecutionDispatcher.submit(turn_id)` leaves the durable Turn queued and emits a PG wake-up; it never calls Kubernetes.
- `cancel(turn_id)` records `cancel_requested_at`; Gateway/Supervisor enforce it.
- Production accepts only `APP_RUNTIME_MODE=kubernetes_runner` after this phase Gate; local/test can use `local_inline`.

- [ ] **Step 1: Add mode-selection tests**

Verify production rejects inline, API has no Kubernetes dependency/token, queued state survives API restart, and cancellation is durable.

- [ ] **Step 2: Implement mode selection**

Construct Controller and Gateway only in their own entrypoints. `create_app()` must not instantiate the Kubernetes client.

- [ ] **Step 3: Verify**

```bash
uv run pytest tests/test_dispatcher.py tests/test_api.py tests/test_bootstrap.py -q
```

- [ ] **Step 4: Commit**

```bash
git add app/turns/dispatcher.py app/bootstrap.py app/api/dependencies.py app/main.py app/config.py tests/test_dispatcher.py tests/test_api.py
git commit -m "feat(runtime): dispatch queued turns to runners"
```

---

### Task 7: Build immutable images and development-cluster manifests

**Files:**
- Create: `deploy/docker/Dockerfile.control-plane`
- Create: `deploy/docker/Dockerfile.runner`
- Create: `deploy/kubernetes/base/kustomization.yaml`
- Create: `deploy/kubernetes/base/api.yaml`
- Create: `deploy/kubernetes/base/controller.yaml`
- Create: `deploy/kubernetes/base/gateway.yaml`
- Create: `deploy/kubernetes/base/services.yaml`
- Create: `deploy/kubernetes/base/rbac.yaml`
- Create: `deploy/kubernetes/base/namespace.yaml`
- Create: `deploy/kubernetes/base/network-policy.yaml`
- Create: `tests/test_deploy_manifests.py`
- Create: `tests/security/test_phase2_baseline.py`

- [ ] **Step 1: Add manifest policy tests**

Parse built YAML and assert pinned image digests in non-local overlays, Controller-only Kubernetes RBAC, no wildcard verbs/resources, Runner token automount disabled, restricted security contexts, `shareProcessNamespace=false`, resource limits, probes, Supervisor-only secret mounts, Namespace default deny, and only documented egress/ingress allowlists. Explicitly record that NetworkPolicy is Pod-scoped.

- [ ] **Step 2: Implement multi-stage images**

Run as fixed non-root UID/GID, install from lockfile, omit compilers/package managers from final image where possible, and provide separate entry commands for API, Controller, Gateway, Supervisor, and Agent.

- [ ] **Step 3: Render and verify manifests**

```bash
kubectl kustomize deploy/kubernetes/base > /tmp/workspace-agent.yaml
uv run pytest tests/test_deploy_manifests.py -q
uv run pytest tests/security/test_phase2_baseline.py -q
docker build -f deploy/docker/Dockerfile.control-plane -t workspace-control:phase2 .
docker build -f deploy/docker/Dockerfile.runner -t workspace-runner:phase2 .
```

- [ ] **Step 4: Commit**

```bash
git add deploy tests/test_deploy_manifests.py tests/security/test_phase2_baseline.py
git commit -m "build(k8s): package control and runner planes"
```

---

### Task 8: Run the vertical slice and hard-fence Gate

**Files:**
- Create: `tests/integration/test_runner_vertical_slice.py`
- Create: `tests/integration/test_phase2_network_denial.py`
- Create: `scripts/verify-phase-2.sh`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/operations/runtime-v2-verification-ledger.md`

- [ ] **Step 1: Test Kind-compatible protocol behavior**

Use Kind for API/Controller/Gateway/Runner protocol, deterministic Pod, execution barrier, fake model, read-only fake MCP, finalization, warm reuse, idle exit, and static policy rendering. Do not claim Kind validates company CSI hard fencing or live NetworkPolicy enforcement unless the test cluster CNI explicitly supports it.

- [ ] **Step 2: Test real development CSI behavior**

On the company development cluster verify Kubernetes/CSI versions, RWOP enforcement, attach/detach times, terminal Pod observation, VolumeAttachment evidence, node drain, and node network partition procedure. Run cross-node mount only after confirmed detach.

- [ ] **Step 3: Execute failure matrix**

Kill Controller, Gateway, Supervisor, and Agent at registration, pre-barrier, post-barrier, runtime, and finalizing boundaries. Delete API connections after Pod create. Reject old-generation heartbeats/events. Confirm no test causes two writers.

- [ ] **Step 4: Execute minimum live security matrix**

On a development cluster with the production-class CNI, prove Agent denial of Kubernetes API, PostgreSQL, S3, metadata IP, public internet, direct company Model/MCP endpoints, other Runner Pods, and non-allowlisted DNS. Prove direct Agent/Bash access to the network-reachable Gateway fails without Supervisor credential, and Pod-local proxy misuse cannot select another Turn/Principal, expand snapshot capabilities, bypass current revocation/quota, retrieve secrets, or invoke an undeclared effect.

- [ ] **Step 5: Run checks**

```bash
uv run ruff check app tests
uv run pytest -q
bash scripts/verify-phase-2.sh
```

- [ ] **Step 6: Record exact evidence and commit**

Record cluster/server version, CSI/sidecar versions, storage class, Runner image digest, SDK/CLI/protocol versions, cold/warm P50/P95/P99, failure results, and reviewer.

```bash
git add tests/integration/test_runner_vertical_slice.py tests/integration/test_phase2_network_denial.py scripts/verify-phase-2.sh README.md .env.example docs/operations/runtime-v2-verification-ledger.md
git commit -m "test(runner): verify phase two vertical slice"
```

## Phase Gate

- Exactly one Runner generation can own a Session PVC and claim its Turn.
- Old Pod terminal plus storage detach/fence evidence is required before the next generation.
- SDK starts only after the committed execution barrier.
- A post-barrier crash never queues an automatic replay.
- Supervisor and Agent are separate processes/containers; Agent cannot read control credentials.
- Restricted Pod baseline, separate UID/GID, process-namespace isolation, Supervisor-only secret mounts, and baseline NetworkPolicy pass static and live checks.
- Agent/Bash can reach only explicitly exposed local capabilities, and cannot use them to select trusted IDs, expand snapshot permissions, bypass current authorization/quota/effect policy, or retrieve credentials.
- Cold start, one Turn, finalization, warm reuse, five-minute idle exit, and cancellation work.
- API has no Kubernetes permissions; Runner has no PostgreSQL/S3/Kubernetes permissions.
- Tests use only test Workspace, fake/test model, read-only MCP, and non-sensitive content.

Do not connect production model/MCP credentials or users in this phase.
