# OpenSandbox Docker Runtime Phase 2A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the existing Web chat end to end in an OpenSandbox-managed Docker sandbox, with durable PostgreSQL authority, warm Session reuse, retained Session and personal-memory volumes, durable cancellation, and recovery-safe execution semantics.

**Architecture:** The API only validates and persists queued Turns. A separate execution Worker claims Turns from PostgreSQL, obtains a user-plus-Workspace memory lease, reconciles one OpenSandbox sandbox per Session, commits the existing execution barrier, and then invokes a thin ClaudeRunner inside the sandbox. The application wraps the official OpenSandbox Python SDK behind one adapter; OpenSandbox owns container, command, volume, egress, and Credential Vault mechanics, while PostgreSQL remains authoritative for Session, Turn, attempt, event, sandbox generation, and recovery state.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, PostgreSQL 16, Alembic, `opensandbox==0.1.15`, OpenSandbox Server `v0.2.2`, Docker Desktop/Engine 29+, Claude Agent SDK, pytest, Hypothesis, Playwright, Docker Compose.

## Global Constraints

- Phase 2A is a local development and test gate. `opensandbox_docker` must be rejected when `APP_ENV=production`.
- The browser and API process never receive an OpenSandbox API key and never access the Docker socket.
- Only `app/sandbox/opensandbox_adapter.py` imports the `opensandbox` package.
- PostgreSQL is authoritative for queued work, execution barriers, attempts, events, cancellation, sandbox generation, leases, and terminal outcomes.
- OpenSandbox logs and command status are reconciliation evidence, not the product event history.
- One Session has at most one active sandbox and one active command; one user-plus-Workspace memory scope has at most one writing Turn.
- A Turn may retry before the execution barrier. After `running` is committed, the entire Turn is never replayed automatically.
- Runner input uses fixed container paths and cannot override trusted user, Workspace, Session, Turn, attempt, generation, volume, credential, or network-policy identifiers.
- Real model credentials must use OpenSandbox Credential Vault and fail closed. There is no plaintext environment fallback in `opensandbox_docker` mode.
- Session and memory volumes outlive sandbox deletion. Session deletion and memory deletion are separate future retention operations.
- Docker process termination is sufficient only for this local gate and is not Kubernetes hard-fence evidence.
- Exact image tags are pinned in source. The verification Gate must resolve and record the actual image digests; no unverified digest is written into configuration.
- Existing `local_inline` remains available for deterministic local development, and existing `execution_disabled` remains the only production mode until Phase 2B passes.

---

## File Structure

### New files

- `app/sandbox/__init__.py` — sandbox package boundary.
- `app/sandbox/contracts.py` — application-owned sandbox port and error contracts.
- `app/sandbox/models.py` — lifecycle, handle, command, volume, lease, and Runner frame value objects.
- `app/sandbox/repository.py` — PostgreSQL sandbox generation, queue claim, command binding, memory lease, cancel, and reconciliation CAS operations.
- `app/sandbox/opensandbox_adapter.py` — sole official SDK adapter.
- `app/sandbox/worker.py` — execution and idle-reaper orchestration.
- `app/sandbox/main.py` — Worker process entrypoint.
- `app/runner/__init__.py` — Runner package boundary.
- `app/runner/protocol.py` — immutable request and JSONL frame schema.
- `app/runner/claude_runner.py` — maps fixed sandbox paths to the existing Claude runtime.
- `app/runner/main.py` — one-Turn CLI entrypoint.
- `app/db/alembic/versions/rev_0006_opensandbox_runtime.py` — Phase 2A authority tables and attempt columns.
- `deploy/docker/Dockerfile.opensandbox-runner` — immutable non-root Runner image.
- `deploy/opensandbox/compose.yaml` — local OpenSandbox Server deployment.
- `deploy/opensandbox/server.toml` — pinned Docker backend, execd, egress, security, and Credential Vault settings.
- `scripts/verify-phase-2a.sh` — reproducible automated and live Docker Gate.
- `tests/test_sandbox_models.py`, `tests/test_sandbox_repository.py`, `tests/test_opensandbox_adapter.py`, `tests/test_runner_protocol.py`, `tests/test_runner_cli.py`, `tests/test_sandbox_worker.py`, `tests/test_opensandbox_dispatcher.py`, `tests/test_opensandbox_config.py` — unit and contract tests.
- `tests/integration/test_opensandbox_postgres.py` — concurrency and lease authority tests.
- `tests/integration/test_opensandbox_docker.py` — opt-in fake-model real Docker vertical slice.
- `tests/integration/test_opensandbox_live_model.py` — opt-in live model smoke.
- `tests/security/test_opensandbox_boundary.py` — secrets, mounts, argv, and network-denial checks.

### Modified files

- `pyproject.toml`, `uv.lock` — pin OpenSandbox SDK and Runner dependencies.
- `app/config.py` — Docker-mode settings and production rejection.
- `app/db/models.py` — sandbox, memory lease, and attempt mappings.
- `app/turns/repository.py` — attach sandbox generation and command identity to the existing attempt.
- `app/turns/service.py` — extract reusable request building and finalization without duplicating product rules.
- `app/turns/dispatcher.py` — queue-only OpenSandbox dispatcher.
- `app/bootstrap.py`, `app/api/dependencies.py`, `app/main.py` — mode selection and dependency health without SDK/Docker access in API.
- `tests/test_migrations.py`, `tests/test_turn_repository.py`, `tests/test_dispatcher.py`, `tests/test_runtime_cohorts.py`, `tests/test_api.py`, `tests/browser/test_workbench.py` — expanded contracts.
- `.env.example`, `README.md`, `docs/operations/runtime-v2-verification-ledger.md` — operator configuration, limits, and Gate evidence.

---

### Task 1: Define the application-owned sandbox boundary and configuration

**Files:**
- Create: `app/sandbox/__init__.py`
- Create: `app/sandbox/contracts.py`
- Create: `app/sandbox/models.py`
- Create: `tests/test_sandbox_models.py`
- Create: `tests/test_opensandbox_config.py`
- Modify: `app/config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.env.example`

**Interfaces:**

```python
class SandboxPort(Protocol):
    async def create_session_sandbox(self, spec: SessionSandboxSpec) -> SandboxHandle: ...
    async def inspect_sandbox(self, sandbox_id: str) -> SandboxObservation: ...
    async def write_request(self, handle: SandboxHandle, request: RunnerRequest) -> None: ...
    async def run_turn(self, handle: SandboxHandle, request_path: str) -> CommandHandle: ...
    async def read_frames(self, command: CommandHandle, cursor: str | None) -> FrameBatch: ...
    async def inspect_command(self, command: CommandHandle) -> CommandObservation: ...
    async def cancel_command(self, command: CommandHandle) -> CancelResult: ...
    async def renew_sandbox(self, sandbox_id: str, timeout_seconds: int) -> None: ...
    async def destroy_sandbox(self, sandbox_id: str) -> None: ...
```

- `SessionSandboxSpec` contains opaque Session and memory-scope keys, generation, exact Runner image, fixed mounts, resource limits, network allowlist, credential-proxy requirement, and timeout.
- `SandboxHandle` contains only sandbox ID, generation, created timestamp, image reference, Session volume name, and memory volume name.
- Settings add `opensandbox_docker` to `APP_RUNTIME_MODE`, server URL/protocol/API-key secret reference, Runner image, worker polling/concurrency, five-minute idle TTL, sandbox timeout, command timeout, image component tags, resource limits, and network allowlist.

- [ ] **Step 1: Write failing model and configuration tests**

Test deterministic opaque volume names, invalid raw user/Workspace names, generation validation, fixed mount constants, unique sandbox states, redacted representations, required API authentication, exact dependency defaults, and validation of allowlist hosts. Assert `APP_ENV=production` rejects both `opensandbox_docker` and `local_inline`, while development accepts all three modes.

- [ ] **Step 2: Add the pinned SDK dependency**

Add `opensandbox==0.1.15` to the Worker dependency set. Regenerate `uv.lock` and assert the lock contains exactly the selected direct version. Do not add the Docker Python SDK.

- [ ] **Step 3: Implement contracts, value objects, and settings**

Keep all OpenSandbox SDK types out of contracts. Use frozen dataclasses or immutable Pydantic models, normalize host allowlists once, and mark API keys with `SecretStr` so settings and health output cannot reveal them.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_sandbox_models.py tests/test_opensandbox_config.py tests/test_runtime_cohorts.py -q
uv lock --check
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock app/config.py app/sandbox .env.example tests/test_sandbox_models.py tests/test_opensandbox_config.py tests/test_runtime_cohorts.py
git commit -m "feat(sandbox): define opensandbox runtime boundary"
```

---

### Task 2: Persist sandbox generations, command identity, and memory leases

**Files:**
- Create: `app/db/alembic/versions/rev_0006_opensandbox_runtime.py`
- Create: `app/sandbox/repository.py`
- Create: `tests/test_sandbox_repository.py`
- Create: `tests/integration/test_opensandbox_postgres.py`
- Modify: `app/db/models.py`
- Modify: `app/turns/repository.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_turn_repository.py`

**Schema:**

- `session_sandboxes(session_id PK/FK, generation, sandbox_id, session_volume_name, memory_volume_name, status, runtime_cohort, runner_image, image_digest, active_turn_id, active_attempt_id, active_command_session_id, created_at, ready_at, heartbeat_at, idle_since, ended_at, last_error, recovery_reason, version)`.
- `memory_scope_leases(scope_key PK, owner_session_id, owner_turn_id, owner_attempt_id, lease_token UNIQUE, acquired_at, heartbeat_at, expires_at, version)`.
- Add nullable `sandbox_generation`, `sandbox_id`, `command_session_id`, and `command_execution_id` to `turn_attempts`. They become non-null together after command binding, while the execution nonce remains the barrier identity.

- [ ] **Step 1: Write failing migration and lifecycle tests**

Assert one sandbox authority row per Session, monotonic positive generation, unique live sandbox IDs, deterministic retained volume names, legal status transitions, command identity coherence, and clean upgrade/downgrade from revision `0005` to `0006`.

- [ ] **Step 2: Write PostgreSQL concurrency tests**

Run two transactions against one Session and prove exactly one queued Turn claim succeeds. Run two Sessions owned by the same user in one Workspace and prove exactly one memory-scope lease succeeds. Prove a stale lease token cannot heartbeat or release a newer lease, and expired pre-barrier leases are reclaimable.

- [ ] **Step 3: Implement repository CAS operations**

Implement `claim_next_turn`, `get_or_reserve_generation`, `record_sandbox_ready`, `bind_attempt_command`, `heartbeat`, `mark_idle`, `mark_reaped`, `mark_recovery_required`, `acquire_memory_lease`, `renew_memory_lease`, and `release_memory_lease`. Every write compares expected version, Session ID, generation, active Turn/attempt, and lease token. Queue claiming uses `FOR UPDATE SKIP LOCKED` on PostgreSQL and a deterministic single-worker fallback only in tests using SQLite.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_migrations.py tests/test_turn_repository.py tests/test_sandbox_repository.py -q
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_opensandbox_postgres.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/db/alembic/versions/rev_0006_opensandbox_runtime.py app/db/models.py app/sandbox/repository.py app/turns/repository.py tests/test_migrations.py tests/test_turn_repository.py tests/test_sandbox_repository.py tests/integration/test_opensandbox_postgres.py
git commit -m "feat(sandbox): persist docker sandbox authority"
```

---

### Task 3: Wrap the official OpenSandbox SDK

**Files:**
- Create: `app/sandbox/opensandbox_adapter.py`
- Create: `tests/test_opensandbox_adapter.py`
- Modify: `app/sandbox/contracts.py`
- Modify: `app/sandbox/models.py`

**Official SDK mapping:**

- Construct `ConnectionConfig(api_key=..., domain=..., protocol=..., request_timeout=..., use_server_proxy=...)` only inside the adapter.
- Create with `Sandbox.create(image, timeout=..., ready_timeout=..., env=..., metadata=..., resource=..., network_policy=..., credential_proxy=..., entrypoint=..., volumes=..., connection_config=...)`.
- Use one `Volume(name=<session-volume>, pvc=PVC(claimName=<session-volume>, createIfNotExists=True, deleteOnSandboxTermination=False), mountPath="/session", readOnly=False)` and an equivalent retained PVC-backed named volume for `/memory` in Docker mode.
- Use `NetworkPolicy(defaultAction="deny", egress=[...])` and `CredentialProxyConfig(enabled=True)` for real-model execution.
- Use `commands.run(..., opts=RunCommandOpts(background=True, working_directory="/session/workspace", timeout=...))`, `commands.get_command_status(...)`, `commands.get_background_command_logs(...)`, and `commands.interrupt(...)` for resumable command control. `run_in_session` is not used because it streams until completion before returning the execution identity. The compatibility command-session field mirrors the background execution ID in Phase 2A.
- Use `Sandbox.connect`, `renew`, and `kill` for reconciliation, lease renewal, and idle reaping.

- [ ] **Step 1: Write a fake-SDK boundary test**

Monkeypatch only the private SDK factory used by the adapter. Assert exact arguments for both fake-model and real-model specs, two retained volumes, deny-by-default network policy, credential proxy requirement, metadata without PII, fixed entrypoint, command working directory, cursor propagation, and SDK exception translation.

- [ ] **Step 2: Add failure and redaction tests**

Cover create timeout, sandbox not found, command not found, server unavailable, cancel races, malformed logs, and Credential Vault unavailable. Assert application exceptions contain stable codes but no API key, model credential, request body, or raw SDK response.

- [ ] **Step 3: Implement the adapter**

Keep SDK calls small and separately testable. Convert SDK responses immediately to application observations. Reject a real-model spec when credential proxy is disabled or the allowlist is empty. Never fall back to plaintext credentials.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_opensandbox_adapter.py -q
rg -n "^(from|import) opensandbox" app | tee /tmp/opensandbox-imports.txt
test "$(wc -l < /tmp/opensandbox-imports.txt | tr -d ' ')" = "1"
```

- [ ] **Step 5: Commit**

```bash
git add app/sandbox/contracts.py app/sandbox/models.py app/sandbox/opensandbox_adapter.py tests/test_opensandbox_adapter.py
git commit -m "feat(sandbox): adapt official opensandbox sdk"
```

---

### Task 4: Define and implement the one-Turn ClaudeRunner protocol

**Files:**
- Create: `app/runner/__init__.py`
- Create: `app/runner/protocol.py`
- Create: `app/runner/claude_runner.py`
- Create: `app/runner/main.py`
- Create: `tests/test_runner_protocol.py`
- Create: `tests/test_runner_cli.py`
- Modify: `app/turns/service.py`

**Wire protocol:**

- Worker writes `/session/control/request.json` atomically with mode `0600` and invokes `python -m app.runner.main --request /session/control/request.json`.
- `RunnerRequest` contains protocol version, untrusted prompt and attachments manifest, immutable Skill/MCP snapshot, model profile key, fixed resume metadata, and opaque trusted IDs already signed into the file by the Worker. Paths are logical references, not caller-provided host paths.
- Runner maps all data to `/session/workspace`, `/session/claude-config`, `/session/outputs`, and `/memory`; absolute paths outside those roots and path traversal are rejected.
- Stdout is JSONL. Each `RunnerFrame` contains protocol version, monotonic sequence, frame kind, timestamp, and bounded payload. Allowed kinds are `phase`, `assistant_delta`, `tool_event`, `artifact`, `heartbeat`, `usage`, `terminal`, and `error`.
- Maximum request size is 2 MiB, frame size 1 MiB, and diagnostic text 16 KiB. Unknown protocol versions and duplicate/decreasing sequence numbers fail before product events are appended.

- [ ] **Step 1: Write protocol validation tests**

Cover round trips, stable canonical JSON, fixed paths, traversal/symlink escape rejection, size limits, invalid frame kinds, sequence monotonicity, exactly one terminal frame, and redaction of credential-like values from error frames.

- [ ] **Step 2: Write CLI tests with a fake runtime**

Inject an `AgentRuntimePort` fake into `run_request`. Prove phases/deltas/tool events/artifacts/usage/terminal mapping, nonzero exit on invalid input, signal-to-cancel behavior, transcript completion before terminal output, and no stdout outside JSONL.

- [ ] **Step 3: Extract reusable Turn request construction**

Move the current `TurnService` request-building rules into one application service callable by both local inline and Worker execution. Keep ownership, attachment, Skill, MCP, and memory-scope validation in the application layer. Do not duplicate or import API routes in Runner code.

- [ ] **Step 4: Implement ClaudeRunner**

Map `RunnerRequest` to the existing `RuntimeRequest` and call `app/runtime/claude.py` through `AgentRuntimePort`. Fake-model mode uses a deterministic runtime selected at image build/test configuration; live mode uses the existing Claude adapter and only a fake placeholder token that the Credential Vault replaces on approved outbound requests.

- [ ] **Step 5: Verify**

```bash
uv run pytest tests/test_runner_protocol.py tests/test_runner_cli.py tests/test_turn_service.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/runner app/turns/service.py tests/test_runner_protocol.py tests/test_runner_cli.py tests/test_turn_service.py
git commit -m "feat(runner): add sandbox claude runner protocol"
```

---

### Task 5: Execute queued Turns with barrier-safe Worker orchestration

**Files:**
- Create: `app/sandbox/worker.py`
- Create: `app/sandbox/main.py`
- Create: `tests/test_sandbox_worker.py`
- Modify: `app/sandbox/repository.py`
- Modify: `app/turns/repository.py`
- Modify: `app/turns/service.py`

**Worker sequence:**

```text
claim queued Turn
  -> acquire memory-scope lease
  -> reconcile/create Session sandbox
  -> materialize immutable request
  -> commit assigned -> running + attempt + nonce + sandbox generation
  -> create background command and persist execution identity
  -> stream validated frames into turn_events
  -> finalize Turn and sandbox idle state
  -> release memory lease
```

- [ ] **Step 1: Write failure-injection tests before every barrier**

Inject failure at queue claim, memory lease, sandbox inspect/create, readiness, request write, and barrier commit. Prove no runtime command starts, bounded retry metadata is persisted, and a second Worker can safely resume.

- [ ] **Step 2: Write post-barrier tests**

Inject failure after barrier commit, after command creation but before binding, during frame streaming, after terminal frame, and before finalization. Prove no automatic second command is started. A stored command is reattached; missing/ambiguous command state marks `recovery_required` with an interrupted/unknown Turn outcome.

- [ ] **Step 3: Implement Worker orchestration**

Use explicit methods for pre-barrier preparation, barrier commit, command binding, streaming, and finalization. Persist public phase events (`provisioning`, `sandbox_ready`, `warm_reuse`, `recovering`, `idle`) without exposing sandbox IDs. Renew sandbox and memory leases during long commands. Limit concurrency globally and by configured user/Workspace keys.

- [ ] **Step 4: Validate event and finalization ordering**

Reject non-monotonic Runner frames. Append terminal runtime frames before committing terminal Turn status. Release the memory lease only after transcript and memory writes have completed. Mark sandbox idle only after attempt finalization succeeds.

- [ ] **Step 5: Verify**

```bash
uv run pytest tests/test_sandbox_worker.py tests/test_turn_repository.py tests/test_sse.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/sandbox/worker.py app/sandbox/main.py app/sandbox/repository.py app/turns/repository.py app/turns/service.py tests/test_sandbox_worker.py tests/test_turn_repository.py tests/test_sse.py
git commit -m "feat(worker): execute turns through opensandbox"
```

---

### Task 6: Add durable cancellation, restart reconciliation, and idle reaping

**Files:**
- Modify: `app/sandbox/worker.py`
- Modify: `app/sandbox/repository.py`
- Modify: `app/turns/repository.py`
- Modify: `app/turns/service.py`
- Modify: `tests/test_sandbox_worker.py`
- Create: `tests/test_sandbox_recovery.py`

- [ ] **Step 1: Write cancellation race tests**

Cover cancellation while queued, preparing, command starting, running, terminal-but-unfinalized, and already terminal. Persist the cancel request first; issue `commands.interrupt` only when command identity is authoritative; wait for terminal command observation; preserve both volumes; make repeated cancel idempotent.

- [ ] **Step 2: Write Worker restart reconciliation tests**

For every nonterminal sandbox/attempt state, restart with an empty in-memory Worker. Prove it reconnects using stored sandbox and command IDs, continues from the stored log cursor, finalizes a known terminal command, leaves a running command alone, and marks missing/ambiguous post-barrier commands `recovery_required` without replay.

- [ ] **Step 3: Write idle-reaper tests with a fake clock**

Prove warm reuse before 300 seconds, no reap while command/lease/finalization is active, exactly one `kill` after the threshold, retained deterministic volume names, generation increment on later recreation, and stale-generation updates rejected.

- [ ] **Step 4: Implement cancellation, reconciliation, and reaper loops**

Use independent bounded loops so an OpenSandbox outage cannot block queue durability. Treat unavailable management API as unknown, not terminated. Record last observation, retry time, and stable recovery reason in PostgreSQL.

- [ ] **Step 5: Verify**

```bash
uv run pytest tests/test_sandbox_worker.py tests/test_sandbox_recovery.py -q
```

- [ ] **Step 6: Commit**

```bash
git add app/sandbox/worker.py app/sandbox/repository.py app/turns/repository.py app/turns/service.py tests/test_sandbox_worker.py tests/test_sandbox_recovery.py
git commit -m "feat(worker): reconcile cancel and idle sandboxes"
```

---

### Task 7: Connect queue-only dispatch, health, SSE, and the existing Web flow

**Files:**
- Create: `tests/test_opensandbox_dispatcher.py`
- Modify: `app/turns/dispatcher.py`
- Modify: `app/bootstrap.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/main.py`
- Modify: `tests/test_dispatcher.py`
- Modify: `tests/test_api.py`
- Modify: `tests/browser/test_workbench.py`

- [ ] **Step 1: Write dispatcher and composition tests**

Assert `OpenSandboxQueuedDispatcher` returns after durable enqueue/notification and never imports/constructs the adapter. Assert only `app.sandbox.main` constructs `OpenSandboxAdapter`. Production rejects Docker mode; development Docker mode requires PostgreSQL and a configured authenticated OpenSandbox endpoint.

- [ ] **Step 2: Write API, SSE, and browser tests**

Submit through existing `POST /turns`, stream provisioning/warm-reuse/recovery events, cancel through the existing endpoint, and prove creator-only Session visibility remains enforced. Browser labels show human-readable phases and never show sandbox ID, volume name, command ID, API key, or raw recovery diagnostics.

- [ ] **Step 3: Implement queue-only dispatch and health**

Use PostgreSQL notification as a latency optimization, with polling as correctness fallback. Health exposes selected runtime mode, Worker reachability timestamp, OpenSandbox server/component versions, and capability flags; it redacts URLs containing credentials and never probes Docker from the API process.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_opensandbox_dispatcher.py tests/test_dispatcher.py tests/test_api.py tests/test_sse.py -q
uv run pytest tests/browser/test_workbench.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/turns/dispatcher.py app/bootstrap.py app/api/dependencies.py app/main.py tests/test_opensandbox_dispatcher.py tests/test_dispatcher.py tests/test_api.py tests/test_sse.py tests/browser/test_workbench.py
git commit -m "feat(api): dispatch turns to opensandbox worker"
```

---

### Task 8: Package a pinned non-root Runner and local OpenSandbox stack

**Files:**
- Create: `deploy/docker/Dockerfile.opensandbox-runner`
- Create: `deploy/opensandbox/compose.yaml`
- Create: `deploy/opensandbox/server.toml`
- Create: `tests/security/test_opensandbox_boundary.py`
- Modify: `.env.example`
- Modify: `README.md`

**Pinned compatibility baseline:**

- Python SDK: `opensandbox==0.1.15`.
- Server image: `opensandbox/server:v0.2.2`.
- Execd image: `opensandbox/execd:v1.0.21`.
- Egress image: `opensandbox/egress:v1.1.4`, matching Server `v0.2.2` generated configuration. Upgrade to `v1.1.5` only after the same Gate proves compatibility and the ledger records the change.
- Egress interception mode: `dns+nft`, required for Credential Vault.
- Runner image: project-owned immutable tag plus digest recorded after build.

- [ ] **Step 1: Write static security tests**

Parse Dockerfile, Compose, and TOML. Assert non-root Runner, fixed entrypoint, no shell-form secret args, read-only base where supported, dropped capabilities, `no-new-privileges`, PID/resource limits, no host network/privileged mode, authenticated Server, no API key in image/config, persistent Server metadata, exact component tags, `dns+nft`, and Docker socket mounted only into OpenSandbox Server.

- [ ] **Step 2: Build the Runner image**

Install only locked application/runtime dependencies, copy the required application modules, create fixed `/session` and `/memory` mount points, run as a numeric non-root UID/GID, and make `python -m app.runner.main` the command. Build for the current `linux/arm64` Docker engine and keep the Dockerfile multi-architecture compatible.

- [ ] **Step 3: Configure OpenSandbox Server**

Bind management API to loopback for local development, require an API key, persist Server metadata, select bridge networking, enforce resource/capability/seccomp limits, configure `dns+nft` egress, enable Credential Vault, and define explicit model/MCP allowlists. Do not expose the Server or Docker socket to API, browser, or Runner networks.

- [ ] **Step 4: Resolve and record real image digests**

After pulling/building, use `docker image inspect` and, where registry metadata is available, provenance/signature verification. Write observed digests to the verification ledger, not fabricated values into TOML. If a digest cannot be resolved or verified, record the exact limitation and keep the Gate non-production.

- [ ] **Step 5: Verify**

```bash
uv run pytest tests/security/test_opensandbox_boundary.py -q
docker compose -f deploy/opensandbox/compose.yaml config --quiet
docker build -f deploy/docker/Dockerfile.opensandbox-runner -t workspace-agent-runner:phase2a .
docker image inspect workspace-agent-runner:phase2a --format '{{json .RepoDigests}} {{.Id}}'
```

- [ ] **Step 6: Commit**

```bash
git add deploy/docker/Dockerfile.opensandbox-runner deploy/opensandbox tests/security/test_opensandbox_boundary.py .env.example README.md
git commit -m "build(sandbox): package local opensandbox stack"
```

---

### Task 9: Pass the fake-model Docker Gate and opt-in live smoke

**Files:**
- Create: `tests/integration/test_opensandbox_docker.py`
- Create: `tests/integration/test_opensandbox_live_model.py`
- Create: `scripts/verify-phase-2a.sh`
- Modify: `docs/operations/runtime-v2-verification-ledger.md`
- Modify: `README.md`

- [x] **Step 1: Implement an opt-in real-Docker fixture**

Start PostgreSQL, OpenSandbox Server, API, Worker, and the existing Web assets with unique test prefixes. Wait on health, collect component versions, and clean sandboxes while intentionally retaining then explicitly deleting only test-prefixed volumes. Refuse to run cleanup without the unique prefix.

- [x] **Step 2: Prove the deterministic fake-model vertical slice**

Through the public API/Web path, prove cold execution, one committed attempt, durable SSE history, same-Session warm reuse, cancellation, 300-second idle reap via an injectable test clock, generation increment, retained Session transcript, separate personal memory volume, new-Session memory read, serialization for shared memory scope, and independent concurrent Sessions.

- [x] **Step 3: Prove security denials**

From Runner context, verify denial of PostgreSQL, Docker socket/API, OpenSandbox management API, metadata endpoints, arbitrary private networks, and a non-allowlisted public host. Inspect environment, request files, argv, mounts, and captured logs to prove absence of database credentials, OpenSandbox API key, Docker credentials, and real model/MCP secrets.

- [ ] **Step 4: Run the opt-in live model smoke**

Using current proxy configuration through Credential Vault, complete one Web Turn, one same-Session resume, one managed Skill invocation, one explicit Auto Memory write, and one new Session read in the same user-plus-Workspace scope. Skip with a clear reason when credentials are absent; never substitute plaintext injection.

- [x] **Step 5: Create the reproducible Gate script**

`scripts/verify-phase-2a.sh` runs lock checks, migrations, unit/property/security suites, PostgreSQL integration, Runner build, fake-model Docker vertical slice, and optionally the live smoke. It prints versions, test counts, cold/warm latency, image IDs/digests, and cleanup results, and exits nonzero on any required check.

- [x] **Step 6: Run the complete regression and Gate**

```bash
uv run pytest -q
node --test tests/js/*.test.mjs
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_opensandbox_postgres.py -q
RUN_OPENSANDBOX_DOCKER=1 ./scripts/verify-phase-2a.sh
RUN_OPENSANDBOX_DOCKER=1 RUN_LIVE_MODEL=1 ./scripts/verify-phase-2a.sh
```

- [x] **Step 7: Record Gate evidence and explicit exclusions**

Record execution date, host architecture, Docker/OpenSandbox/SDK/Claude versions, exact commands, test counts, cold/warm latency, image IDs/digests, reviewer, failures/retries, and known exceptions. State explicitly that Kubernetes scheduling, CSI, RWOP, multi-machine recovery, CNI isolation, hard fencing, and production tenancy remain unverified until Phase 2B.

- [ ] **Step 8: Commit**

```bash
git add tests/integration/test_opensandbox_docker.py tests/integration/test_opensandbox_live_model.py scripts/verify-phase-2a.sh docs/operations/runtime-v2-verification-ledger.md README.md
git commit -m "test(sandbox): pass opensandbox docker phase 2a gate"
```

---

## Final Review Gate

- [ ] Every new behavior has a failing test observed before implementation and a passing targeted test afterward.
- [ ] A repository search finds no unresolved implementation placeholder introduced by this phase.
- [x] `rg -n "^(from|import) opensandbox" app` returns only `app/sandbox/opensandbox_adapter.py`.
- [x] API and browser processes have no Docker socket and no OpenSandbox API key.
- [x] Runner has no PostgreSQL, Docker, OpenSandbox management, or plaintext model credential.
- [x] PostgreSQL records exactly one execution barrier and one attempt for every executed Turn.
- [x] Same-Session warm reuse, five-minute reap, generation increment, retained Session state, and memory-scope serialization have real Docker evidence.
- [x] Post-barrier missing or ambiguous commands become `recovery_required`; no full-Turn replay occurs.
- [x] Production configuration still accepts only `execution_disabled`.
- [x] Full Python, JavaScript, PostgreSQL, browser, security, and Docker Gate suites pass.
- [x] `git diff --check` is clean and unrelated worktree changes remain untouched.

## Phase 2B Handoff

After Phase 2A passes, retain the application-owned `SandboxPort`, Worker, Runner protocol, PostgreSQL barrier, queue, event model, and Web/API contracts. Phase 2B changes the OpenSandbox Server backend and evidence requirements to Kubernetes: per-Session RWOP PVC, Pod terminal plus CSI detach/VolumeAttachment hard-fence evidence, production CNI/RuntimeClass isolation, cluster quota/scheduling, and multi-machine recovery. Phase 2A must never be relabeled as that production gate.
