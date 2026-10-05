# Docker Web Deployment Phase 2A.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the existing PostgreSQL + OpenSandbox runtime as a one-command Docker Web stack with loopback-only Compose management ports, truthful Worker availability, durable data, fake-mode smoke tests, and an opt-in real-Claude Gate.

**Architecture:** Build one immutable non-root application image and run it as separate API and Worker containers beside PostgreSQL and OpenSandbox Server. PostgreSQL remains the Turn authority and gains a short-lived Worker heartbeat; the API admits OpenSandbox Turns only while a compatible Worker heartbeat is current. A thin Bash launcher delegates safe environment rendering to `python-dotenv`, resolves immutable image IDs, and uses Docker Compose for lifecycle management.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, Claude Agent SDK, OpenSandbox SDK 0.1.15 / Server 0.2.2, Docker Compose, vanilla JavaScript, pytest, Playwright, Node test runner.

## Global Constraints

- This profile uses `APP_ENV=development`, `APP_IDENTITY_MODE=mock`, and one Mock user; it is not a production multi-tenant deployment.
- Publish every Compose-declared Web/management port on `127.0.0.1`; the official OpenSandbox Server `v0.2.2` dynamic Runner/egress ports are an accepted, explicitly reported `0.0.0.0` limitation confined to the configured `40000-60000` range.
- This profile is for a trusted developer workstation, not an untrusted/shared LAN host. Do not claim that OpenSandbox-created dynamic ports are loopback-only.
- The API container must not receive `OPENSANDBOX_API_KEY` or `ANTHROPIC_API_KEY`.
- Only the Worker receives both the OpenSandbox management key and, in Claude mode, the real model key.
- The Runner receives only `opensandbox-vault-placeholder`; the real model key continues through OpenSandbox Credential Vault.
- API and Worker use the exact same immutable application image ID. Runner execution uses an immutable `sha256:<64-hex>` image ID.
- Keep OpenSandbox versions fixed at SDK `0.1.15`, Server `0.2.2`, execd `1.0.21`, and egress `1.1.4`.
- `--fake` is the default and requires no model key. `--claude` is explicit and fails before startup when real-model configuration is incomplete.
- `opensandbox_docker` never falls back to `local_inline`.
- Ordinary `down`, `restart`, and mode changes preserve PostgreSQL, app-data, Session, and Memory volumes. Only `reset` deletes owned persistent data.
- Use a fixed default Compose project name and validate any override before passing it to Docker.
- Do not print secrets, generated secret-file contents, Authorization headers, Credential Vault values, model endpoints, or database passwords.
- Preserve the existing user-owned untracked files `agents.json`, `description.md`, `members.json`, and `squads.json`.
- Do not add Redis, a message queue, object storage, OIDC, public ingress, Kubernetes, a model gateway, or a second secret store in this phase.

---

## File Structure

### Application runtime and availability

- Modify `app/config.py`: make the model key optional at shared configuration time and add bounded heartbeat settings.
- Modify `app/runtime/claude.py`: require and retain the model key only when the inline Claude runtime is constructed.
- Modify `app/runtime/cohorts.py`: report runtime capabilities without constructing an unused Claude runtime.
- Modify `app/api/dependencies.py`: allow `AppServices.runtime` to be absent outside `local_inline` and expose optional Worker availability.
- Modify `app/bootstrap.py`: construct Claude only for `local_inline`; construct heartbeat-backed availability for `opensandbox_docker`.
- Modify `app/turns/service.py`: apply the availability admission check after idempotency lookup and before a new Turn is persisted.
- Modify `app/api/routes.py`: return `ready` or `degraded` health with a sanitized execution block.

### Heartbeat persistence and Worker process

- Modify `app/db/models.py`: add `WorkerHeartbeatRecord`.
- Create `app/db/alembic/versions/rev_0007_worker_heartbeats.py`: add/drop the heartbeat table and freshness index.
- Create `app/sandbox/heartbeat.py`: define compatibility, persistence, availability snapshot, and publisher contracts.
- Create `app/sandbox/health.py`: probe OpenSandbox `/health` without credentials.
- Modify `app/sandbox/main.py`: validate Worker-owned credentials, publish heartbeat state, and run heartbeat and execution loops together.

### Browser behavior

- Create `app/web/static/service-health.js`: pure service-state derivation helpers usable by browser and Node tests.
- Modify `app/web/static/app.js`: poll `/api/health`, keep transport and execution state separate, and disable execution only while degraded.
- Modify `app/web/templates/index.html`: load the service-health helper.
- Modify `app/web/static/app.css`: add degraded-state styling.

### Container and operator surface

- Create `.dockerignore`: exclude credentials, generated state, Git metadata, test output, local data, and worktrees.
- Modify `.gitignore`: ignore `.env.docker.local` and `.runtime/`.
- Create `.env.docker.example`: document non-secret defaults and Claude-mode inputs.
- Create `deploy/docker/Dockerfile.web`: build the shared non-root API/Worker image.
- Create `deploy/docker-web/compose.yaml`: define PostgreSQL, OpenSandbox, API, Worker, networks, and persistent volumes.
- Create `deploy/docker-web/compose.gate.yaml`: publish PostgreSQL/OpenSandbox on random loopback ports only for integration/live Gates.
- Create `deploy/docker-web/workspaces/example/workspace.yaml` and `CLAUDE.md`: provide a container-valid Workspace without host-only MCP or Skill paths.
- Create `app/db/cli.py`: expose the existing idempotent Alembic initialization as a one-shot container command.
- Create `scripts/docker_web_config.py`: safely load operator config, validate mode/project/ports, generate persistent local secrets, and write per-service env files with mode `0600`.
- Create `scripts/docker-web.sh`: implement `up`, `status`, `logs`, `restart`, `down`, and confirmed `reset`.
- Create `scripts/verify-phase-2a2.sh`: run an isolated Compose project and clean only that project.

### Tests and documentation

- Create `tests/test_external_runtime_bootstrap.py`.
- Create `tests/test_worker_heartbeat.py`.
- Create `tests/test_docker_web_config.py`.
- Create `tests/test_docker_web_launcher.py`.
- Create `tests/security/test_docker_web_boundary.py`.
- Create `tests/js/test_service_health.cjs`.
- Create `tests/integration/test_docker_web_stack.py`.
- Create `tests/live/test_docker_web_claude.py`.
- Modify `tests/test_migrations.py`, `tests/test_api.py`, `tests/test_sandbox_main.py`, `tests/test_runtime_cohorts.py`, and `tests/browser/test_workbench.py`.
- Modify `README.md`: add deployment, persistence, backup/restore, and troubleshooting instructions.
- Modify `docs/operations/runtime-v2-verification-ledger.md`: record the Phase 2A.2 Gate evidence.

---

### Task 1: Enforce the API/Worker model-credential boundary

**Files:**
- Create: `tests/test_external_runtime_bootstrap.py`
- Modify: `app/config.py:20-24,177-183,363-379`
- Modify: `app/runtime/claude.py:55-75,268-276,590-602`
- Modify: `app/runtime/cohorts.py:41-114`
- Modify: `app/api/dependencies.py:20-43`
- Modify: `app/bootstrap.py:42-126`
- Modify: `app/turns/service.py:29-65,250-275`
- Modify: `app/sandbox/main.py:17-58`
- Modify: `tests/test_sandbox_main.py`
- Modify: `tests/test_runtime_cohorts.py`

**Interfaces:**
- Consumes: existing `Settings`, `AgentRuntimePort`, `RuntimeCapabilities`, `ClaudeAgentRuntime`, and `PlatformModelCredentialProvider`.
- Produces: `Settings.anthropic_api_key: SecretStr | None`; `AppServices.runtime: AgentRuntimePort | None`; `probe_runtime_cohort(runtime: AgentRuntimePort | None, settings: Settings) -> RuntimeCohortReport`; `runtime_capabilities(protocol_version: str) -> RuntimeCapabilities`.

- [ ] **Step 1: Write failing tests for secret-free external bootstrap**

```python
def test_opensandbox_api_bootstrap_does_not_require_model_key(settings_factory):
    from app.bootstrap import build_app_services

    settings = settings_factory(
        anthropic_api_key=None,
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://opensandbox-server:8080",
        opensandbox_api_key=None,
        opensandbox_runner_runtime="fake",
        opensandbox_allowed_hosts=(),
    )
    services = build_app_services(settings)
    assert services.runtime is None


def test_local_inline_rejects_missing_model_key(settings_factory):
    from app.bootstrap import build_app_services

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        build_app_services(settings_factory(anthropic_api_key=None))
```

Add a Worker test showing fake mode succeeds without a model key and Claude mode raises `RuntimeError("Claude Worker requires ANTHROPIC_API_KEY")`.

- [ ] **Step 2: Run the focused tests and verify the current eager configuration fails**

Run: `uv run pytest tests/test_external_runtime_bootstrap.py tests/test_sandbox_main.py tests/test_runtime_cohorts.py -q`

Expected: FAIL because `Settings` rejects a missing model key and `build_app_services` always constructs `ClaudeAgentRuntime`.

- [ ] **Step 3: Make the shared model key optional but reject blank provided values**

```python
anthropic_api_key: SecretStr | None = Field(
    default=None,
    validation_alias="ANTHROPIC_API_KEY",
    repr=False,
)

@field_validator("anthropic_api_key")
@classmethod
def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
    if value is None:
        return None
    secret = value.get_secret_value().strip()
    if not secret:
        raise ValueError("ANTHROPIC_API_KEY cannot be empty")
    return SecretStr(secret)
```

Change `Settings.redacted_summary()` so it still emits only `"**********"` when a key exists and `None` otherwise.

- [ ] **Step 4: Move the inline credential requirement into `ClaudeAgentRuntime`**

```python
class ClaudeAgentRuntime:
    def __init__(self, settings: Settings, *, environ=None, client_factory=ClaudeSDKClient):
        if settings.anthropic_api_key is None:
            raise ValueError("local_inline requires ANTHROPIC_API_KEY")
        self.settings = settings
        self.api_key = settings.anthropic_api_key
        self.environ = environ if environ is not None else os.environ
        self.client_factory = client_factory
```

Use `self.api_key.get_secret_value()` in option construction and exception redaction so later code never dereferences an optional field.

- [ ] **Step 5: Stop constructing Claude in externally dispatched modes**

```python
resolved_runtime = runtime
if resolved_runtime is None and settings.app_runtime_mode == "local_inline":
    resolved_runtime = ClaudeAgentRuntime(settings)
```

Make `AppServices.runtime` and `TurnService.runtime` optional. Add a private `TurnService._require_inline_runtime()` that raises a programming error if `execute_turn()` is ever called without a runtime; call it immediately before `runtime.run(...)`.

- [ ] **Step 6: Report declared capabilities without an instantiated runtime**

```python
def runtime_capabilities(protocol_version: str) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        protocol_version=protocol_version,
        supports_resume=True,
        supports_interrupt=True,
        supports_auto_memory=True,
        supports_mcp=True,
        supports_skills=True,
    )


def probe_runtime_cohort(runtime: AgentRuntimePort | None, settings: Settings):
    capabilities = (
        runtime.capabilities
        if runtime is not None
        else runtime_capabilities(settings.app_runtime_protocol_version)
    )
    # preserve the existing dependency/version report
```

- [ ] **Step 7: Validate the credential only in the Claude Worker factory**

```python
if settings.opensandbox_runner_runtime == "claude":
    if settings.anthropic_api_key is None:
        raise RuntimeError("Claude Worker requires ANTHROPIC_API_KEY")
    credential_provider = PlatformModelCredentialProvider(
        str(settings.anthropic_base_url),
        settings.anthropic_api_key,
        settings.opensandbox_allowed_hosts,
    )
else:
    credential_provider = None
```

- [ ] **Step 8: Run the focused and regression tests**

Run: `uv run pytest tests/test_external_runtime_bootstrap.py tests/test_sandbox_main.py tests/test_opensandbox_dispatcher.py tests/test_runtime_cohorts.py tests/test_bootstrap.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the credential-boundary correction**

```bash
git add app/config.py app/runtime/claude.py app/runtime/cohorts.py app/api/dependencies.py app/bootstrap.py app/turns/service.py app/sandbox/main.py tests/test_external_runtime_bootstrap.py tests/test_sandbox_main.py tests/test_runtime_cohorts.py
git commit -m "refactor(runtime): isolate external execution credentials"
```

---

### Task 2: Persist compatible Worker heartbeats

**Files:**
- Create: `app/db/alembic/versions/rev_0007_worker_heartbeats.py`
- Create: `app/sandbox/heartbeat.py`
- Create: `tests/test_worker_heartbeat.py`
- Modify: `app/db/models.py:330-390`
- Modify: `tests/test_migrations.py:1-125`

**Interfaces:**
- Consumes: `Database`, SQLAlchemy async sessions, and database-generated timestamps.
- Produces: `WorkerCompatibility(runtime_cohort: str, protocol_version: str, runner_runtime: str, image_digest: str)`; `WorkerIdentity(instance_id: str, compatibility: WorkerCompatibility)`; `WorkerHeartbeatRepository.touch(identity: WorkerIdentity, status: Literal["ready", "unavailable"]) -> datetime`; `WorkerHeartbeatRepository.snapshot(compatibility: WorkerCompatibility, max_age_seconds: float) -> WorkerAvailabilitySnapshot`.

- [ ] **Step 1: Add the failing migration assertions**

```python
assert "worker_heartbeats" in tables
heartbeat_columns = {
    column["name"]
    for column in await connection.run_sync(
        lambda sync: inspect(sync).get_columns("worker_heartbeats")
    )
}
assert heartbeat_columns == {
    "instance_id",
    "runtime_cohort",
    "protocol_version",
    "runner_runtime",
    "image_digest",
    "status",
    "started_at",
    "last_seen_at",
}
assert migration_head == "0007"
```

- [ ] **Step 2: Run the migration test and verify revision `0006` is still current**

Run: `uv run pytest tests/test_migrations.py::test_migrations_create_current_schema_on_fresh_database -q`

Expected: FAIL because `worker_heartbeats` does not exist and the head is `0006`.

- [ ] **Step 3: Add the model and migration**

```python
class WorkerHeartbeatRecord(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready','unavailable')",
            name="ck_worker_heartbeats_status",
        ),
        Index(
            "ix_worker_heartbeats_compatibility_seen",
            "runtime_cohort",
            "protocol_version",
            "runner_runtime",
            "image_digest",
            "last_seen_at",
        ),
    )

    instance_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_cohort: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    runner_runtime: Mapped[str] = mapped_column(String(16), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Revision `0007` creates exactly this table and index and drops the table in `downgrade()`.

- [ ] **Step 4: Write failing repository tests for database time, compatibility, and staleness**

```python
@pytest.mark.asyncio
async def test_heartbeat_requires_exact_compatibility_and_expires(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    await database.initialize()
    repository = WorkerHeartbeatRepository(database)
    expected = WorkerCompatibility("docker-web", "1", "fake", "sha256:" + "a" * 64)
    identity = WorkerIdentity("worker-1", expected)

    recorded_at = await repository.touch(identity, "ready")
    assert (await repository.snapshot(expected, 15)).status == "ready"
    assert (await repository.snapshot(replace(expected, runner_runtime="claude"), 15)).status == "degraded"

    async with database.session() as db:
        await db.execute(
            update(WorkerHeartbeatRecord)
            .where(WorkerHeartbeatRecord.instance_id == "worker-1")
            .values(last_seen_at=recorded_at - timedelta(seconds=16))
        )
        await db.commit()
    assert (await repository.snapshot(expected, 15)).status == "degraded"
```

Import `replace`, `timedelta`, `update`, and `WorkerHeartbeatRecord` in the test. Keep
timestamp mutation in the test itself; do not add a production clock-mutation method.

- [ ] **Step 5: Run the heartbeat tests and verify the repository is absent**

Run: `uv run pytest tests/test_worker_heartbeat.py -q`

Expected: FAIL with an import error for `app.sandbox.heartbeat`.

- [ ] **Step 6: Implement the immutable contracts and repository**

```python
@dataclass(frozen=True, slots=True)
class WorkerCompatibility:
    runtime_cohort: str
    protocol_version: str
    runner_runtime: str
    image_digest: str


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    instance_id: str
    compatibility: WorkerCompatibility


@dataclass(frozen=True, slots=True)
class WorkerAvailabilitySnapshot:
    status: Literal["ready", "degraded"]
    worker: Literal["available", "unavailable"]
    last_seen_at: datetime | None

    def to_health_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "worker": self.worker,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }
```

`touch()` obtains `now` with `await db.scalar(select(func.now()))`, normalizes a naive SQLite timestamp to UTC, inserts or updates the row, and commits. `snapshot()` obtains a fresh database `now`, selects the newest exact-compatible row, and returns ready only when its status is `ready` and its age is no more than `max_age_seconds`.

- [ ] **Step 7: Run migration, heartbeat, and PostgreSQL integration tests**

Run: `uv run pytest tests/test_migrations.py tests/test_worker_heartbeat.py tests/integration/test_opensandbox_postgres.py -q`

Expected: local tests PASS; PostgreSQL tests remain skipped unless `TEST_POSTGRES_URL` is supplied.

- [ ] **Step 8: Commit heartbeat persistence**

```bash
git add app/db/models.py app/db/alembic/versions/rev_0007_worker_heartbeats.py app/sandbox/heartbeat.py tests/test_migrations.py tests/test_worker_heartbeat.py
git commit -m "feat(worker): persist compatible heartbeats"
```

---

### Task 3: Publish backend-aware Worker health

**Files:**
- Create: `app/sandbox/health.py`
- Modify: `app/sandbox/heartbeat.py`
- Modify: `app/sandbox/main.py:1-90`
- Modify: `app/config.py:90-125,280-345`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/test_worker_heartbeat.py`
- Modify: `tests/test_sandbox_main.py`

**Interfaces:**
- Consumes: Task 2 `WorkerHeartbeatRepository`, `WorkerIdentity`, and OpenSandbox's unauthenticated `/health` endpoint.
- Produces: `BackendHealthProbe.ready() -> Awaitable[bool]`; `OpenSandboxHealthProbe(api_url: str, timeout_seconds: float = 2.0)`; `WorkerHeartbeatPublisher.publish_once() -> WorkerAvailabilitySnapshot`; `WorkerHeartbeatPublisher.run() -> None`; `WorkerHeartbeatPublisher.mark_unavailable() -> None`.

- [ ] **Step 1: Promote `httpx` to an explicit runtime dependency**

Move `httpx>=0.28.1` from the dev group into `[project].dependencies`, then run:

Run: `uv lock`

Expected: `uv.lock` remains consistent and `uv lock --check` passes.

- [ ] **Step 2: Write failing OpenSandbox health-probe tests**

```python
@pytest.mark.asyncio
async def test_opensandbox_health_probe_accepts_only_http_200():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200 if request.url.path == "/health" else 404)
    )
    probe = OpenSandboxHealthProbe(
        "http://opensandbox-server:8080",
        client=httpx.AsyncClient(transport=transport),
    )
    assert await probe.ready() is True
```

Add cases for HTTP 503 and `httpx.ConnectError`, both returning `False` without logging the URL.

- [ ] **Step 3: Run the probe tests and verify the module is absent**

Run: `uv run pytest tests/test_worker_heartbeat.py -q`

Expected: FAIL importing `OpenSandboxHealthProbe`.

- [ ] **Step 4: Implement the credential-free health probe**

```python
class BackendHealthProbe(Protocol):
    async def ready(self) -> bool: ...


class OpenSandboxHealthProbe:
    def __init__(self, api_url: str, *, timeout_seconds: float = 2.0, client=None):
        self._health_url = api_url.rstrip("/") + "/health"
        self._client = client
        self._timeout = timeout_seconds

    async def ready(self) -> bool:
        try:
            if self._client is not None:
                response = await self._client.get(self._health_url, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(self._health_url)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
```

Do not attach an API key or log `_health_url`.

- [ ] **Step 5: Add bounded heartbeat settings**

```python
worker_heartbeat_interval_seconds: float = Field(
    default=5.0,
    ge=1.0,
    le=60.0,
    validation_alias="WORKER_HEARTBEAT_INTERVAL_SECONDS",
)
worker_heartbeat_stale_seconds: float = Field(
    default=15.0,
    ge=3.0,
    le=300.0,
    validation_alias="WORKER_HEARTBEAT_STALE_SECONDS",
)
```

In the settings model validator, require `stale_seconds >= interval_seconds * 2`.

- [ ] **Step 6: Write failing publisher tests**

```python
@pytest.mark.asyncio
async def test_publisher_records_backend_state(repository, identity):
    probe = SequenceProbe([False, True])
    publisher = WorkerHeartbeatPublisher(repository, identity, probe, interval_seconds=5)

    await publisher.publish_once()
    assert (await repository.snapshot(identity.compatibility, 15)).status == "degraded"
    await publisher.publish_once()
    assert (await repository.snapshot(identity.compatibility, 15)).status == "ready"
```

- [ ] **Step 7: Implement `WorkerHeartbeatPublisher`**

```python
class WorkerHeartbeatPublisher:
    async def publish_once(self) -> WorkerAvailabilitySnapshot:
        status = "ready" if await self.probe.ready() else "unavailable"
        await self.repository.touch(self.identity, status)
        return await self.repository.snapshot(
            self.identity.compatibility,
            self.interval_seconds * 3,
        )

    async def run(self) -> None:
        while True:
            await self.publish_once()
            await asyncio.sleep(self.interval_seconds)

    async def mark_unavailable(self) -> None:
        await self.repository.touch(self.identity, "unavailable")
```

Let database failures escape so the TaskGroup stops the Worker process and Compose can restart it.

- [ ] **Step 8: Integrate the publisher with the Worker process**

Build `WorkerIdentity` with a fresh UUID plus settings cohort, protocol, Runner runtime, and `APP_RUNTIME_IMAGE_DIGEST`. Before reconciliation, poll `publish_once()` until ready or `OPENSANDBOX_READY_TIMEOUT_SECONDS` elapses. Then run publisher and execution slots in one `asyncio.TaskGroup`; best-effort `mark_unavailable()` in `finally`.

- [ ] **Step 9: Run Worker lifecycle tests**

Run: `uv run pytest tests/test_worker_heartbeat.py tests/test_sandbox_main.py tests/test_sandbox_worker.py -q`

Expected: PASS, including cancellation of execution loops if the publisher fails.

- [ ] **Step 10: Commit backend-aware publication**

```bash
git add pyproject.toml uv.lock app/config.py app/sandbox/health.py app/sandbox/heartbeat.py app/sandbox/main.py tests/test_worker_heartbeat.py tests/test_sandbox_main.py
git commit -m "feat(worker): publish backend-aware readiness"
```

---

### Task 4: Gate new Turns and expose truthful health

**Files:**
- Modify: `app/sandbox/heartbeat.py`
- Modify: `app/api/dependencies.py:20-45`
- Modify: `app/bootstrap.py:90-145`
- Modify: `app/turns/service.py:30-150`
- Modify: `app/api/routes.py:40-65`
- Modify: `tests/test_worker_heartbeat.py`
- Modify: `tests/test_api.py:285-335`
- Modify: `tests/test_turns.py`
- Modify: `tests/live/test_opensandbox_claude.py`

**Interfaces:**
- Consumes: Task 2 heartbeat repository and Task 3 settings.
- Produces: `WorkerAvailabilityService.snapshot() -> WorkerAvailabilitySnapshot`; `WorkerAvailabilityService.require_available() -> None`; `AppServices.execution_availability: WorkerAvailabilityService | None`; API error code `execution_unavailable` with HTTP 503.

- [ ] **Step 1: Write failing availability and API-health tests**

```python
@pytest.mark.asyncio
async def test_opensandbox_health_is_degraded_without_worker(opensandbox_client):
    response = await opensandbox_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["execution"] == {
        "status": "degraded",
        "worker": "unavailable",
        "last_seen_at": None,
    }
```

After inserting one exact-compatible ready heartbeat, assert `status == "ready"`, `worker == "available"`, and no instance ID, endpoint, API key, or database URL appears.

- [ ] **Step 2: Write a failing Turn admission/idempotency test**

```python
@pytest.mark.asyncio
async def test_unavailable_worker_rejects_only_new_turns(turn_service, session):
    gate = ToggleAvailability(available=True)
    turn_service.execution_availability = gate
    created = await turn_service.start(session.id, "hello", [], "request-1")

    gate.available = False
    repeated = await turn_service.start(session.id, "hello", [], "request-1")
    assert repeated.id == created.id

    with pytest.raises(AppError) as error:
        await turn_service.start(session.id, "new", [], "request-2")
    assert error.value.code == "execution_unavailable"
    assert error.value.status_code == 503
```

- [ ] **Step 3: Run focused tests and verify health is still always `ok`**

Run: `uv run pytest tests/test_worker_heartbeat.py tests/test_api.py tests/test_turns.py -q`

Expected: FAIL because no availability service is wired into health or Turn creation.

- [ ] **Step 4: Implement `WorkerAvailabilityService`**

```python
class WorkerAvailabilityService:
    def __init__(self, repository, compatibility, *, stale_seconds: float):
        self.repository = repository
        self.compatibility = compatibility
        self.stale_seconds = stale_seconds

    async def snapshot(self) -> WorkerAvailabilitySnapshot:
        return await self.repository.snapshot(self.compatibility, self.stale_seconds)

    async def require_available(self) -> None:
        if (await self.snapshot()).status != "ready":
            raise AppError(
                "execution_unavailable",
                "Execution service is temporarily unavailable.",
                503,
            )
```

- [ ] **Step 5: Wire availability only for `opensandbox_docker`**

In `build_app_services`, construct exact `WorkerCompatibility` from runtime cohort, protocol, Runner runtime, and immutable application image digest. Pass the service to `TurnService` and store it on `AppServices`. Leave it `None` for `local_inline` and `execution_disabled` so their existing admission behavior does not change.

The existing direct Worker live test is not an API admission test. Set
`services.turns.execution_availability = None` in that test immediately after
`build_execution_worker(settings)` so its in-process helper can enqueue a Turn before
calling `worker.execute_one()`. The packaged Web Gate in Task 9 must not use this bypass.

- [ ] **Step 6: Check availability after idempotency lookup**

Inside the Session lock and immediately after returning an existing `(session_id, client_request_id)` Turn:

```python
if self.execution_availability is not None:
    await self.execution_availability.require_available()
```

Perform this before attachment binding, Session status mutation, or new Turn creation.

- [ ] **Step 7: Return sanitized `ready`/`degraded` health**

```python
execution = (
    await services.execution_availability.snapshot()
    if services.execution_availability is not None
    else None
)
return {
    "status": "degraded" if execution and execution.status == "degraded" else "ready",
    "database": "ok",
    "memory": "ok" if services.memory_scopes.ready else "unavailable",
    "workspace_count": len(entries),
    "valid_workspace_count": sum(entry.available for entry in entries),
    "runtime": probe_runtime_cohort(services.runtime, services.settings).to_health_dict(),
    **({"execution": execution.to_health_dict()} if execution else {}),
}
```

Update local-inline API expectations from top-level `"ok"` to `"ready"` without adding a fake Worker block.

- [ ] **Step 8: Run API, Turn, and privacy tests**

Run: `uv run pytest tests/test_worker_heartbeat.py tests/test_api.py tests/test_turns.py tests/test_opensandbox_dispatcher.py -q`

Expected: PASS, including secret scans and idempotent retry while degraded.

- [ ] **Step 9: Commit truthful admission and health**

```bash
git add app/sandbox/heartbeat.py app/api/dependencies.py app/bootstrap.py app/turns/service.py app/api/routes.py tests/test_worker_heartbeat.py tests/test_api.py tests/test_turns.py tests/live/test_opensandbox_claude.py
git commit -m "feat(api): gate turns on compatible workers"
```

---

### Task 5: Render degraded execution without losing history access

**Files:**
- Create: `app/web/static/service-health.js`
- Create: `tests/js/test_service_health.cjs`
- Modify: `app/web/static/app.js:1-320,440-465,560-610,879-920,1169-1220`
- Modify: `app/web/templates/index.html:7-20,240-245`
- Modify: `app/web/static/app.css:102-125`
- Modify: `tests/browser/test_workbench.py`

**Interfaces:**
- Consumes: Task 4 `/api/health` response with top-level `status` and optional `execution.status`.
- Produces: global/module `ServiceHealth.deriveState(transportState, executionState) -> "ready" | "degraded" | "reconnecting" | "disconnected"`; `ServiceHealth.canExecute(serviceState) -> boolean`; `refreshServiceHealth() -> Promise<void>`.

- [ ] **Step 1: Write failing pure JavaScript state tests**

```javascript
test("degraded execution survives healthy transport events", () => {
  assert.equal(ServiceHealth.deriveState("ready", "degraded"), "degraded");
  assert.equal(ServiceHealth.deriveState("reconnecting", "degraded"), "reconnecting");
  assert.equal(ServiceHealth.canExecute("degraded"), false);
  assert.equal(ServiceHealth.canExecute("ready"), true);
});
```

- [ ] **Step 2: Run the Node test and verify the module is absent**

Run: `node --test tests/js/test_service_health.cjs`

Expected: FAIL importing `service-health.js`.

- [ ] **Step 3: Implement a small UMD-style pure helper**

```javascript
(function serviceHealthModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ServiceHealth = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function factory() {
  function deriveState(transportState, executionState) {
    if (transportState !== "ready") return transportState;
    return executionState === "degraded" ? "degraded" : "ready";
  }
  function canExecute(serviceState) { return serviceState === "ready"; }
  return {deriveState, canExecute};
});
```

Load it before `app.js` in `index.html`.

- [ ] **Step 4: Separate transport and execution state in `app.js`**

Add `transportState: "ready"` and `executionState: "ready"` to state. Replace direct ready transitions with `setTransportState("ready")`; derive the displayed state through `ServiceHealth.deriveState(...)`. An SSE heartbeat may prove transport is connected but must not erase a degraded execution state.

- [ ] **Step 5: Poll health and keep read-only navigation usable**

```javascript
async function refreshServiceHealth() {
  try {
    const health = await api("/api/health");
    state.executionState = health.status === "degraded" ? "degraded" : "ready";
    setTransportState("ready");
  } catch (_) {
    setTransportState("disconnected");
  }
}
```

Call it during initialization before loading Workspaces and every five seconds. Keep Workspace/session/history/Skill navigation enabled when degraded. Require `ServiceHealth.canExecute(state.serviceState)` for message input, attachment actions, and send.

- [ ] **Step 6: Add stable labels and styling**

Use `执行服务不可用` in the top status and `执行服务暂不可用，请稍后重试` in `composerState`. Add an amber `.service-status.degraded .status-dot` rule. When API submission returns `execution_unavailable`, set `executionState = "degraded"` before rendering the toast.

- [ ] **Step 7: Write the browser regression test**

Intercept `/api/health` with:

```json
{
  "status": "degraded",
  "database": "ok",
  "memory": "ok",
  "workspace_count": 1,
  "valid_workspace_count": 1,
  "runtime": {},
  "execution": {"status": "degraded", "worker": "unavailable", "last_seen_at": null}
}
```

Assert the Session list and existing messages remain visible, the send button/input are disabled, and the top/composer warnings are rendered. Then switch the route to `ready`, call `refreshServiceHealth()`, and assert the composer becomes enabled without reloading the Session.

- [ ] **Step 8: Run JavaScript and targeted browser tests**

Run: `node --test tests/js/*.cjs`

Run: `uv run pytest tests/browser/test_workbench.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the degraded UI**

```bash
git add app/web/static/service-health.js app/web/static/app.js app/web/templates/index.html app/web/static/app.css tests/js/test_service_health.cjs tests/browser/test_workbench.py
git commit -m "feat(web): show degraded execution state"
```

---

### Task 6: Build the shared application image and Compose topology

**Files:**
- Create: `.dockerignore`
- Create: `.env.docker.example`
- Create: `deploy/docker/Dockerfile.web`
- Create: `deploy/docker-web/compose.yaml`
- Create: `deploy/docker-web/compose.gate.yaml`
- Create: `deploy/docker-web/workspaces/example/workspace.yaml`
- Create: `deploy/docker-web/workspaces/example/CLAUDE.md`
- Create: `app/db/cli.py`
- Create: `tests/test_db_cli.py`
- Create: `tests/security/test_docker_web_boundary.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Task 1 secret-free API bootstrap, Task 3 heartbeat variables, existing `deploy/opensandbox/server.toml`, and existing Runner Dockerfile.
- Produces: Compose services `postgres`, `opensandbox-server`, `api`, `worker`, and profile-gated one-shot `migrate`; named volumes `postgres-data`, `opensandbox-state`, and `app-data`; generated env-file inputs `DOCKER_WEB_API_ENV_FILE`, `DOCKER_WEB_WORKER_ENV_FILE`, and `DOCKER_WEB_OPENSANDBOX_ENV_FILE`.

- [ ] **Step 1: Write failing static boundary tests**

```python
def test_api_and_worker_share_image_but_not_secrets():
    compose = yaml.safe_load((ROOT / "deploy/docker-web/compose.yaml").read_text())
    api = compose["services"]["api"]
    worker = compose["services"]["worker"]
    assert api["image"] == worker["image"] == "${DOCKER_WEB_APP_IMAGE:?set DOCKER_WEB_APP_IMAGE}"
    assert "DOCKER_WEB_WORKER_ENV_FILE" not in str(api)
    assert "DOCKER_WEB_WORKER_ENV_FILE" in str(worker)
    assert api["ports"] == ["127.0.0.1:${DOCKER_WEB_PORT:-8765}:8000"]
```

Also assert only `opensandbox-server` mounts `/var/run/docker.sock`, no service is privileged, every Compose-declared port uses `127.0.0.1`, API/Worker run read-only where possible with writable `/tmp`, and Dockerfile uses `USER 10001:10001`, locked dependencies, and no credential literals. Do not assert that OpenSandbox-created dynamic ports are loopback-only.

- [ ] **Step 2: Run the static boundary test and verify files are absent**

Run: `uv run pytest tests/security/test_docker_web_boundary.py -q`

Expected: FAIL because the Docker Web deployment files do not exist.

- [ ] **Step 3: Create the non-root application Dockerfile**

Use the existing pinned Python base and `uv==0.7.6`. Copy `pyproject.toml`, `uv.lock`, `README.md`, `app/`, and `deploy/docker-web/workspaces/`; place the latter at `/app/workspaces`; run `uv sync --frozen --no-dev`; create `/var/lib/workspace-agent` and `/tmp` with UID/GID `10001`; make `/app/workspaces` non-writable; switch to `USER 10001:10001`.

The default command is:

```dockerfile
CMD ["python", "-m", "app.cli"]
```

Set container defaults `WORKSPACES_ROOT=/app/workspaces`, `APP_DATA_DIR=/var/lib/workspace-agent`, `APP_HOST=0.0.0.0`, and `APP_PORT=8000`. Container-wide `0.0.0.0` is allowed; the host publication remains loopback-only.

- [ ] **Step 4: Write the failing one-shot migration command test**

```python
@pytest.mark.asyncio
async def test_migrate_initializes_and_disposes_database(settings_factory, monkeypatch):
    calls = []
    database = FakeDatabase(calls)
    monkeypatch.setattr("app.db.cli.Database", lambda _url: database)

    await migrate(settings_factory())

    assert calls == ["initialize", "dispose"]
```

- [ ] **Step 5: Implement the migration container command**

```python
async def migrate(settings: Settings) -> None:
    database = Database(settings.resolved_database_url)
    try:
        await database.initialize()
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(migrate(Settings()))
```

Run: `uv run pytest tests/test_db_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Create `.dockerignore` and Git ignores**

Exclude at least `.git`, `.env`, `.env.*.local`, `.runtime`, `.venv`, `.worktrees`, `data`, `test-results`, caches, and user-owned root artifacts. Do not exclude `deploy/docker-web/workspaces/`, `app/`, `pyproject.toml`, `uv.lock`, or `README.md`.

Add:

```gitignore
.env.docker.local
.runtime/
```

- [ ] **Step 7: Define the base Compose stack**

The important service boundaries are:

```yaml
services:
  api:
    image: ${DOCKER_WEB_APP_IMAGE:?set DOCKER_WEB_APP_IMAGE}
    command: ["python", "-m", "app.cli"]
    env_file: [${DOCKER_WEB_API_ENV_FILE:?set DOCKER_WEB_API_ENV_FILE}]
    ports: ["127.0.0.1:${DOCKER_WEB_PORT:-8765}:8000"]
    volumes: ["app-data:/var/lib/workspace-agent"]
    depends_on:
      postgres: {condition: service_healthy}

  worker:
    image: ${DOCKER_WEB_APP_IMAGE:?set DOCKER_WEB_APP_IMAGE}
    command: ["python", "-m", "app.sandbox.main"]
    env_file:
      - ${DOCKER_WEB_API_ENV_FILE:?set DOCKER_WEB_API_ENV_FILE}
      - ${DOCKER_WEB_WORKER_ENV_FILE:?set DOCKER_WEB_WORKER_ENV_FILE}
    volumes: ["app-data:/var/lib/workspace-agent"]
    depends_on:
      postgres: {condition: service_healthy}
      opensandbox-server: {condition: service_started}

  migrate:
    image: ${DOCKER_WEB_APP_IMAGE:?set DOCKER_WEB_APP_IMAGE}
    command: ["python", "-m", "app.db.cli"]
    env_file: [${DOCKER_WEB_API_ENV_FILE:?set DOCKER_WEB_API_ENV_FILE}]
    profiles: ["tools"]
    depends_on:
      postgres: {condition: service_healthy}
```

Set `init: true` on API and Worker so Docker supplies signal-forwarding init without a
new image package. PostgreSQL uses `postgres:16-alpine` and a healthcheck. OpenSandbox
uses the existing pinned component configuration, cap drop, read-only root,
`no-new-privileges`, and the only Docker socket mount. Do not publish PostgreSQL or
OpenSandbox in the base file.

- [ ] **Step 8: Add the container-valid example Workspace**

Create a manifest with `id: example`, `skills: []`, no `skills_root_env`, no MCP
servers, and only the existing local file/tool allowlist (`Read`, `Write`, `Edit`,
`Glob`, `Grep`, `Bash`, `Skill`). Add a short `CLAUDE.md` that restricts work to the
Session workspace and prohibits credential disclosure. The clean image must report one
valid Workspace without any host path or executable.

- [ ] **Step 9: Add the Gate-only loopback override**

`compose.gate.yaml` adds only:

```yaml
services:
  postgres:
    ports: ["127.0.0.1:${DOCKER_WEB_POSTGRES_PORT:?}:5432"]
  opensandbox-server:
    ports: ["127.0.0.1:${DOCKER_WEB_OPENSANDBOX_PORT:?}:8080"]
```

- [ ] **Step 10: Add the operator example configuration**

Document `DOCKER_WEB_PORT=8765`, model name, non-secret base URL, exact host allowlist, TTL/concurrency, and heartbeat intervals. Leave `ANTHROPIC_API_KEY=` visibly empty with a comment that it is used only by `--claude`; do not include a usable secret.

- [ ] **Step 11: Validate Compose interpolation with synthetic env files**

Create temporary API/Worker/OpenSandbox env files containing test-only values, then run:

Run: `docker compose --env-file <temp-compose-env> -f deploy/docker-web/compose.yaml config --quiet`

Expected: exit 0. Inspect rendered config in the security test and assert the API environment has no model/OpenSandbox key while Worker has the test key names.

- [ ] **Step 12: Run static security tests**

Run: `uv run pytest tests/security/test_opensandbox_boundary.py tests/security/test_docker_web_boundary.py -q`

Expected: PASS.

- [ ] **Step 13: Commit the image and topology**

```bash
git add .dockerignore .gitignore .env.docker.example app/db/cli.py deploy/docker/Dockerfile.web deploy/docker-web/compose.yaml deploy/docker-web/compose.gate.yaml deploy/docker-web/workspaces/example/workspace.yaml deploy/docker-web/workspaces/example/CLAUDE.md tests/test_db_cli.py tests/security/test_docker_web_boundary.py
git commit -m "feat(deploy): define Docker Web stack"
```

---

### Task 7: Implement the safe lifecycle launcher

**Files:**
- Create: `scripts/docker_web_config.py`
- Create: `scripts/docker-web.sh`
- Create: `tests/test_docker_web_config.py`
- Create: `tests/test_docker_web_launcher.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: Task 6 Compose variables and image paths.
- Produces: `render_runtime_config(mode: Literal["fake", "claude"], source_path: Path | None, runtime_dir: Path, app_image: str, runner_image: str, project_name: str) -> RenderedConfig`; non-secret `deployment.json` ownership marker; shell commands `up [--fake|--claude]`, `status`, `logs [service]`, `restart`, `down`, and `reset [--yes]`; internal Gate switch `DOCKER_WEB_GATE=1` that adds only the repository-owned Gate override.

- [ ] **Step 1: Add `python-dotenv` as the env-file parser**

Add `python-dotenv>=1.1.0,<2` to project dependencies and run:

Run: `uv lock`

Expected: `uv lock --check` passes. Do not write a custom dotenv parser or `source` the operator file as shell code.

- [ ] **Step 2: Write failing config-renderer tests**

```python
def test_fake_config_omits_model_key_from_api_and_worker(tmp_path):
    rendered = render_runtime_config(
        mode="fake",
        source_path=None,
        runtime_dir=tmp_path,
        app_image="sha256:" + "a" * 64,
        runner_image="sha256:" + "b" * 64,
        project_name="workspace-agent-docker-web",
    )
    assert "ANTHROPIC_API_KEY" not in rendered.api_env.read_text()
    assert "ANTHROPIC_API_KEY" not in rendered.worker_env.read_text()
    assert oct(rendered.worker_env.stat().st_mode & 0o777) == "0o600"
```

Add tests proving Claude mode requires base URL/key/allowlist, the API file still omits the key, generated management/database secrets persist across repeated renders, project names reject whitespace/slashes, all image references require `sha256:<64-hex>`, and the marker contains only schema/project identity with no secret values.

- [ ] **Step 3: Run renderer tests and verify the module is absent**

Run: `uv run pytest tests/test_docker_web_config.py -q`

Expected: FAIL importing `scripts.docker_web_config`.

- [ ] **Step 4: Implement safe config rendering**

Use `dotenv_values()` for the optional local file, `secrets.token_hex(32)` for URL-safe database and OpenSandbox keys, atomic temporary-file replacement, runtime directory mode `0700`, and env-file mode `0600`.

Write five outputs:

```text
compose.env       # file paths, project, port, immutable image IDs
api.env           # database URL, Mock identity, runtime metadata, no management/model key
worker.env        # OpenSandbox URL/key, Runner image/runtime, optional Claude key
opensandbox.env   # OPENSANDBOX_SERVER_API_KEY only
deployment.json  # non-secret schema version and exact Compose project ownership
```

Never return secret values in `RenderedConfig.__repr__`.

- [ ] **Step 5: Write failing launcher argument tests**

Test `bash -n`, `--help`, rejection of unknown commands/services, `reset` refusal without a TTY or `--yes`, reset refusal for `/`, the user's home, the repository root, a missing marker, or a mismatched project marker, and the default `up` mode passed to the Python renderer as `fake`. Use a temporary `PATH` with a recording `docker` stub; do not invoke the real daemon in these unit tests.

- [ ] **Step 6: Run launcher tests and verify the script is absent**

Run: `uv run pytest tests/test_docker_web_launcher.py -q`

Expected: FAIL because `scripts/docker-web.sh` does not exist.

- [ ] **Step 7: Implement the launcher command surface**

At the top:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT=${DOCKER_WEB_PROJECT:-workspace-agent-docker-web}
RUNTIME_DIR=${DOCKER_WEB_RUNTIME_DIR:-$ROOT/.runtime/docker-web}
COMPOSE_FILE=$ROOT/deploy/docker-web/compose.yaml
```

After rendering, build a `COMPOSE_ARGS` array containing
`--project-name "$PROJECT" --env-file "$RUNTIME_DIR/compose.env" -f "$COMPOSE_FILE"`.
Only when
`DOCKER_WEB_GATE=1`, append `-f "$ROOT/deploy/docker-web/compose.gate.yaml"`; reject
every other value. This avoids accepting an arbitrary override path.

`up` checks `docker info` and `docker compose version`, validates every enabled
loopback port, builds `Dockerfile.web` and the existing Runner Dockerfile, obtains both
`docker image inspect --format '{{.Id}}'` values, and renders env files. Then it runs:

```bash
docker compose "${COMPOSE_ARGS[@]}" up -d postgres opensandbox-server
docker compose "${COMPOSE_ARGS[@]}" --profile tools run --rm migrate
docker compose "${COMPOSE_ARGS[@]}" up -d api worker
```

Finally poll `/api/health` until top-level `status` is `ready`. On timeout, print only
sanitized `compose ps` and recent logs.

After successful startup, print this fixed warning without interpolating any endpoint:

```text
Local development only: OpenSandbox v0.2.2 publishes dynamic Runner ports on
0.0.0.0 within 40000-60000. Do not use this profile on an untrusted/shared LAN.
```

- [ ] **Step 8: Implement non-destructive and destructive commands**

- `status`: run `compose ps`, fetch the API health JSON, print only status/worker/mode, and repeat the fixed OpenSandbox dynamic-port warning.
- `logs [api|worker|postgres|opensandbox-server]`: reject every other value and tail that known service.
- `restart`: `compose restart api worker opensandbox-server`, then wait for ready.
- `down`: `compose down` without `-v`.
- `reset`: require interactive `RESET` confirmation or `--yes`; canonicalize `$RUNTIME_DIR`; refuse `/`, the user's home, or `$ROOT`; and require `deployment.json` to contain the expected schema and exact validated `$PROJECT`. Remove `wa-session-*`/`wa-memory-*` volumes only after selecting their exact names from this deployment's PostgreSQL sandbox records and before the database volume is removed. If PostgreSQL is stopped, start only that project's `postgres` service, wait for its healthcheck, collect the names, then run `compose down -v --remove-orphans`. Delete the marked runtime directory last, after Compose and exact sandbox-volume cleanup succeed.

For reset volume selection, first use PostgreSQL `to_regclass` so an uninitialized database yields no sandbox volumes, then query `session_sandboxes.session_volume_name` and `memory_volume_name`; validate every result with `^wa-(session|memory)-[0-9a-f]{40}$` before passing it to `docker volume rm`. Never use a broad `docker volume prune` or prefix-only deletion.

- [ ] **Step 9: Run renderer and launcher tests**

Run: `uv run pytest tests/test_docker_web_config.py tests/test_docker_web_launcher.py -q`

Expected: PASS, with captured output containing no synthetic secret values.

- [ ] **Step 10: Commit the lifecycle surface**

```bash
git add pyproject.toml uv.lock scripts/docker_web_config.py scripts/docker-web.sh tests/test_docker_web_config.py tests/test_docker_web_launcher.py
git commit -m "feat(deploy): add Docker Web lifecycle commands"
```

---

### Task 8: Add the isolated fake-mode Compose and recovery Gate

**Files:**
- Create: `tests/integration/test_docker_web_stack.py`
- Create: `scripts/verify-phase-2a2.sh`
- Modify: `tests/security/test_docker_web_boundary.py`

**Interfaces:**
- Consumes: Task 7 launcher, Gate override, `/api/health`, Workspace/Session/Turn REST APIs, and Docker Compose.
- Produces: opt-in `RUN_DOCKER_WEB_STACK=1` integration test and one-command `bash scripts/verify-phase-2a2.sh` Gate.

- [ ] **Step 1: Write the skipped external-stack test skeleton with concrete assertions**

The test requires `RUN_DOCKER_WEB_STACK=1`, `DOCKER_WEB_URL`, `DOCKER_WEB_PROJECT`, `DOCKER_WEB_RUNTIME_DIR`, and `DOCKER_WEB_TEST_STATE`. It must:

```python
health = (await client.get("/api/health")).json()
assert health["status"] == "ready"
assert health["execution"]["worker"] == "available"

session = (await client.post("/api/workspaces/example/sessions")).json()
accepted = (await client.post(
    f"/api/sessions/{session['id']}/turns",
    json={
        "message": "fake gate",
        "attachment_ids": [],
        "file_references": [],
        "client_request_id": "phase2a2-fake-turn",
    },
)).json()
```

Poll the Turn until completed and assert assistant history contains `Fake response`.

- [ ] **Step 2: Add Worker failure/recovery assertions**

From the test, invoke the exact Compose project to stop `worker`, wait up to 30 seconds for health `degraded`, and assert a new client request returns HTTP 503 with error code `execution_unavailable`. Restart Worker, wait for `ready`, retry with the same rejected client request ID, and assert one completed Turn is created.

- [ ] **Step 3: Add persistence assertions**

Run launcher `down`, then `up --fake` with the same project/runtime directory. Fetch `/api/workspaces/example/sessions` and assert the original Session and message history still exist. Save IDs only in the Gate's temporary `DOCKER_WEB_TEST_STATE` path.

Query exact sandbox IDs from the isolated project's PostgreSQL records, inspect only
their `sandbox-*` / `sandbox-egress-*` containers, and assert every published host port
is within `40000-60000`. Record whether Docker reports `0.0.0.0` and require the
launcher output to contain the accepted-limitation warning. Do not describe these
ports as loopback-only.

- [ ] **Step 4: Write the isolated Gate wrapper**

`verify-phase-2a2.sh` must:

1. create a random safe project name and temporary runtime directory;
2. choose free loopback Web/PostgreSQL/OpenSandbox ports;
3. set `DOCKER_WEB_GATE=1` so the launcher adds the repository-owned Gate override;
4. run `up --fake`;
5. run `RUN_DOCKER_WEB_STACK=1 uv run pytest tests/integration/test_docker_web_stack.py -q`;
6. on failure, print sanitized `ps` and bounded logs;
7. in a trap, run `reset --yes` for only the random project.

- [ ] **Step 5: Run the Gate and capture the first concrete failure**

Run: `bash scripts/verify-phase-2a2.sh`

Expected before fixes: a specific container build/start/test failure, not a skipped test. Fix only the smallest topology/launcher defect revealed by that failure.

- [ ] **Step 6: Re-run the complete fake-mode Gate**

Run: `bash scripts/verify-phase-2a2.sh`

Expected: PASS with API, Worker, OpenSandbox, PostgreSQL, fake Turn, degraded admission, Worker recovery, and `down/up` persistence confirmed.

- [ ] **Step 7: Prove cleanup scope**

Create an unrelated named volume before the Gate, run the Gate, and assert the unrelated volume still exists afterward. Remove that single test volume explicitly.

- [ ] **Step 8: Commit the fake/recovery Gate**

```bash
git add scripts/verify-phase-2a2.sh tests/integration/test_docker_web_stack.py tests/security/test_docker_web_boundary.py
git commit -m "test(deploy): gate Docker Web recovery"
```

---

### Task 9: Run the real-Claude Gate through the packaged Web stack

**Files:**
- Create: `tests/live/test_docker_web_claude.py`
- Modify: `scripts/verify-phase-2a2.sh`
- Modify: `tests/security/test_docker_web_boundary.py`

**Interfaces:**
- Consumes: Task 8 isolated stack; real `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, model, allowlist; Gate-only PostgreSQL/OpenSandbox loopback endpoints.
- Produces: opt-in `RUN_LIVE_DOCKER_WEB_CLAUDE=1` packaged acceptance test preserving all Phase 2A.1 security assertions.

- [ ] **Step 1: Write the opt-in test preflight**

Skip unless `RUN_LIVE_DOCKER_WEB_CLAUDE=1`. Fail with only missing variable names when any of `DOCKER_WEB_URL`, `DOCKER_WEB_POSTGRES_URL`, `DOCKER_WEB_OPENSANDBOX_URL`, `OPENSANDBOX_API_KEY`, or the selected model configuration is absent. Never include values in failure text.

- [ ] **Step 2: Exercise managed Skill and same-Session resume through HTTP**

Create a unique managed Skill through `POST /api/workspaces/example/skills` with a complete `SKILL.md` body containing the marker, create a Session, send `/<skill-name>` through the Web Turn API, and assert the unique Skill marker plus non-zero input/output usage. Send a second Turn to the same Session, fetch the Session, and assert the persisted Claude Session ID did not change.

- [ ] **Step 3: Exercise cross-Session Auto Memory through HTTP**

In the first Session ask Claude to write and read an exact unique marker in `/memory/MEMORY.md`. Create a second Session in the same Workspace/user scope, ask it to read Auto Memory, and assert the marker is recalled.

- [ ] **Step 4: Inspect the real sandbox boundary**

Query `session_sandboxes` through the Gate PostgreSQL endpoint for both Session IDs. Connect through the Gate OpenSandbox endpoint and assert for every Runner:

```python
assert environment["ANTHROPIC_API_KEY"] == "opensandbox-vault-placeholder"
assert real_api_key not in request_json
assert real_api_key not in runner_logs
```

Inspect the API container environment and assert both `ANTHROPIC_API_KEY` and `OPENSANDBOX_API_KEY` are absent. Scan persisted Turn events and app-data files for the real key.

- [ ] **Step 5: Preserve the Credential Vault fail-closed assertion**

Delete the second Session sandbox's Credential Vault, rerun its already-written request directly through OpenSandbox without letting Worker refresh the Vault, and assert a non-zero exit, terminal `failed` frame, no plaintext fallback environment key, and no secret in frames.

- [ ] **Step 6: Extend the Gate wrapper**

When `RUN_LIVE_DOCKER_WEB_CLAUDE=1`, render a separate random project with `up --claude`, run only `tests/live/test_docker_web_claude.py`, then reset that project in the existing trap. The default Gate remains fake-only and uses no model quota.

- [ ] **Step 7: Run the opt-in Gate with configured deployment credentials**

Run: `RUN_LIVE_DOCKER_WEB_CLAUDE=1 bash scripts/verify-phase-2a2.sh`

Expected: PASS for real chat, managed Skill, resume, Auto Memory, usage, API secret absence, Runner placeholder-only environment, persistence/event scans, and Vault fail-closed execution.

- [ ] **Step 8: Commit the packaged live Gate**

```bash
git add tests/live/test_docker_web_claude.py scripts/verify-phase-2a2.sh tests/security/test_docker_web_boundary.py
git commit -m "test(deploy): gate packaged Claude execution"
```

---

### Task 10: Document operations and run the final verification matrix

**Files:**
- Modify: `README.md:310-410,430-470`
- Modify: `docs/operations/runtime-v2-verification-ledger.md`
- Modify: `docs/superpowers/specs/2026-07-31-docker-web-deployment-phase-2a2-design.md`

**Interfaces:**
- Consumes: all Task 1-9 commands and observed version/output evidence.
- Produces: copy-pasteable local deployment, status/log/restart/down/reset, backup/restore, and troubleshooting runbooks plus dated verification evidence.

- [ ] **Step 1: Add the Docker Web quick start**

Document:

```bash
# deterministic no-credential smoke deployment
bash scripts/docker-web.sh up --fake
open http://127.0.0.1:8765

# real Claude-compatible deployment
cp .env.docker.example .env.docker.local
# edit ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, CLAUDE_MODEL,
# and OPENSANDBOX_ALLOWED_HOSTS
bash scripts/docker-web.sh up --claude
```

State clearly that this is Mock identity, loopback-only, single-host Docker, and not production multi-tenancy.

- [ ] **Step 2: Document lifecycle and failure semantics**

Include exact commands for `status`, service-scoped logs, restart, non-destructive down, and confirmed reset. Explain that `degraded` preserves history but rejects new Turns, Worker failure after admission leaves a durable Turn for reconciliation, and there is no local-inline fallback.

- [ ] **Step 3: Document backup and restore using owned records**

Provide a PostgreSQL `pg_dump`/`pg_restore` sequence and app-data volume archive command. For Session/Memory volumes, query exact `session_volume_name` and `memory_volume_name` values from `session_sandboxes`, validate their `wa-session-<40 hex>` / `wa-memory-<40 hex>` shape, and archive each named volume. Restore into an empty stopped deployment before `up`; never recommend `docker volume prune`.

- [ ] **Step 4: Document troubleshooting without secrets**

Cover occupied port, Docker daemon unavailable, immutable image resolution failure, PostgreSQL unhealthy, Worker heartbeat stale, OpenSandbox `/health` unavailable, missing Claude-mode variables, Credential Vault failure, and the `v0.2.2` dynamic `0.0.0.0:40000-60000` limitation. State that a shared/untrusted LAN requires an independently managed host firewall or a later isolated runtime. Use variable names and service names only.

- [ ] **Step 5: Run static formatting and dependency checks**

Run: `uv lock --check`

Run: `uv run ruff check app tests scripts`

Run: `git diff --check`

Expected: PASS.

- [ ] **Step 6: Run all non-live automated tests**

Run: `node --test tests/js/*.cjs`

Run: `RUN_LIVE_CLAUDE_TESTS=0 RUN_LIVE_OPENSANDBOX_CLAUDE=0 RUN_LIVE_DOCKER_WEB_CLAUDE=0 uv run pytest -q`

Expected: every normal test passes; only explicitly opt-in live tests skip.

- [ ] **Step 7: Run browser and Docker Web Gates**

Run: `uv run pytest tests/browser/test_workbench.py -q`

Run: `bash scripts/verify-phase-2a2.sh`

Expected: PASS.

- [ ] **Step 8: Run the real Gate when credentials are available**

Run: `RUN_LIVE_DOCKER_WEB_CLAUDE=1 bash scripts/verify-phase-2a2.sh`

Expected: PASS. If credentials are intentionally unavailable, record the real Gate as not run rather than claiming success.

- [ ] **Step 9: Record exact verification evidence**

Append the date, main commit, application image ID, Runner image ID, dependency versions, test counts, fake Gate result, real Gate result, and confirmed security assertions to the ledger. Record no endpoint or credential values.

- [ ] **Step 10: Commit documentation and evidence**

```bash
git add README.md docs/operations/runtime-v2-verification-ledger.md docs/superpowers/specs/2026-07-31-docker-web-deployment-phase-2a2-design.md
git commit -m "docs: record Docker Web deployment gate"
```

---

## Final Acceptance Checklist

- [ ] `bash scripts/docker-web.sh up --fake` works from a clean local deployment state.
- [ ] `http://127.0.0.1:8765` serves the Web UI and completes a fake Turn through OpenSandbox.
- [ ] API and Worker use the same immutable application image ID.
- [ ] API container environment contains neither OpenSandbox nor model keys.
- [ ] Fake Worker needs no model key; Claude Worker fails closed without one.
- [ ] Current compatible Worker heartbeat produces `ready`; stale, mismatched, or backend-unavailable heartbeat produces `degraded`.
- [ ] Degraded UI preserves history navigation and disables new execution.
- [ ] Idempotent retry returns an existing Turn even while degraded; a genuinely new Turn receives `execution_unavailable`/503.
- [ ] Worker restart does not duplicate execution.
- [ ] Ordinary `down/up` preserves Session, history, app data, Session volumes, and Memory volumes.
- [ ] Confirmed `reset` requires the exact deployment marker and deletes only exact deployment-owned resources.
- [ ] The fake Compose/recovery Gate passes without model credentials.
- [ ] The opt-in packaged Claude Gate passes, or is explicitly recorded as not run.
- [ ] Every Compose-declared port is loopback-only; every exact project sandbox dynamic port is in `40000-60000`, and the accepted `0.0.0.0` limitation is visible in launcher/status/docs.
- [ ] Real model key is absent from API, Runner env/request/command/volumes/logs/events and appears only in Worker/Vault ownership boundaries.
- [ ] No unrelated untracked file, Docker project, container, image, network, or volume is modified.
