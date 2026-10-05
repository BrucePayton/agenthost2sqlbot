# Davinci Workspace Session Phase 2 Multi-Host Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不重映射 Phase 1 内部 User/Workspace/Session 主键的前提下，把 Agent Host 演进为可信身份、无粘性多 API/Worker、副本间可恢复 continuation、对象化 Artifact/Memory，以及由 OpenSandbox 官方 Kubernetes provider 管理的按 Session 可回收 Sandbox。

**Architecture:** PostgreSQL 是身份、Workspace、Session、Turn、deferred envelope 和并发状态的权威；S3-compatible Object Store 是 Attachment/Output/Snapshot/Memory bundle 的权威；每个 Session 的可写 workdir/transcript 位于一个经验证的 RWOP PVC。应用不直接创建 Kubernetes Pod/PVC/CRD，而是通过现有 `SandboxPort` 调 OpenSandbox Server，由锁定并认证的官方 provider 管理计算与存储生命周期。二期按 2A 身份/多副本状态、2B 存储无状态化、2C Kubernetes runtime、2D 真实集群 Gate 分步切换，每步保持可回滚。

**Tech Stack:** FastAPI、SQLAlchemy/Alembic、PostgreSQL、S3-compatible Object Storage、OpenSandbox SDK/Server、Kubernetes CSI/NetworkPolicy、Claude Agent SDK、AG-UI Native V2、pytest/Jest/Node、Docker Compose integration tests。

---

## Global Constraints

- Repository: `/Users/a110356/work/code/claude_workspace_mvp`; execute from the Phase 1 completed branch in a clean worktree.
- Phase 2 begins only after every Phase 1 completion Gate is green and Phase 1 data is on external PostgreSQL.
- Preserve `issuer="davinci"`, `subject="obid:" + canonical_obid`, the existing identity mapping row, internal `user_id`, Personal `workspace_id`, and `session_id`. Trusted authentication strengthens how the same external OBID is proven; it must not create a second user.
- The browser never sends a raw identity header after 2A. It exchanges a trusted Davinci assertion for a short Agent bearer, keeps that bearer only in iframe memory, and uses `Authorization: Bearer` for every REST, AG-UI, continuation, and artifact call.
- Do not depend on third-party cookies in a cross-origin iframe. Never place assertions/bearers in URL, localStorage, postMessage, prompt, Workspace YAML, Session snapshot, log, PVC, or object content.
- Use PostgreSQL uniqueness/row leases for correctness; do not add Redis or a generic distributed-lock service.
- Do not serialize live Python `Future`, callback, SDK client, or frontend Bridge objects. Persist only immutable call/result envelopes and state transitions.
- OpenSandbox Server is single-replica in the first Kubernetes release. Application PostgreSQL remains the mapping authority; workers reconcile PostgreSQL, OpenSandbox, and Kubernetes facts after server restart.
- Start with a certified **BatchSandbox non-pooled** provider. Do not enable WarmPool, pause/resume, rootfs snapshots, Kueue, or an alternative `agent-sandbox` provider until the non-pooled real-cluster Gate proves a need.
- Do not invent Kubernetes CRDs/controllers, a custom execution gateway, a container per Workspace, RWX/NFS shared Workspace storage, a new memory engine, or a second runtime abstraction. Reuse existing `AgentRuntimePort` and `SandboxPort`.
- Every schema mutation is Alembic-managed. Every async state transition has an idempotency key/hash and a PostgreSQL uniqueness constraint.
- Use TDD and one focused commit per task. Run the exact RED/GREEN/Gate commands. If an external compatibility Gate cannot be run, stop that stage and record it as blocked; do not infer compatibility from upstream `main` documentation.
- This plan supersedes older documents that proposed an application-owned Kubernetes Controller/Execution Gateway topology.

## Delivery Sequence and Irreversible Boundaries

1. **2A:** deploy trusted bearer + PostgreSQL continuation state with `APP_RUNTIME_MODE=execution_disabled`; then enable API traffic on two replicas without sticky sessions.
2. **2B:** move Snapshot/Attachment/Output/Memory authority to Object Store and remove API host Paths. Continue using the existing execution backend during migration.
3. **2C:** certify an exact OpenSandbox SDK/Server/provider/CSI combination, then switch execution to Kubernetes under a write fence.
4. **2D:** prove multi-node recovery/security/performance in a real cluster. Only this Gate authorizes production-style multi-host use.

No stage combines an identity cutover, object migration, and runtime cutover in one release.

## Target File Map

### 2A: Trusted identity and durable protocol state

- Create: `app/auth/trusted_assertion.py`, `app/auth/agent_bearer.py`
- Create: `app/embed/repository.py`
- Create: `app/agui/deferred_repository.py`, `app/agui/snapshot_repository.py`
- Modify: `app/config.py`, `app/bootstrap.py`, `app/api/dependencies.py`, `app/embed/local.py`
- Modify: `app/agui/routes.py`, `app/agui/artifact_routes.py`, `app/agui/claude_tools.py`, `app/main.py`
- Modify: `app/runtime/claude.py`
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0009_trusted_agent_identity.py`
- Create: `app/db/alembic/versions/rev_0010_durable_agui_state.py`
- Create tests named in Tasks 1-4

### 2B: Object authority and API statelessness

- Create: `app/storage/contracts.py`, `app/storage/local.py`, `app/storage/s3.py`
- Create: `app/artifacts/models.py`, `app/artifacts/repository.py`, `app/artifacts/service.py`
- Create: `app/memory/bundles.py`, `app/memory/repository.py`
- Create: `app/migration/manifest.py`, `app/migration/service.py`, `app/migration/cli.py`
- Modify: `app/db/models.py`; create `rev_0011_artifact_authority.py`, `rev_0012_memory_bundle_authority.py`, and `rev_0013_migration_authority.py`
- Modify: Session/Attachment/Snapshot/Worker/Runner/API files named in Tasks 6-10
- Create: `compose.objectstore.integration.yaml`

### 2C/2D: OpenSandbox Kubernetes and cluster evidence

- Create: `deploy/opensandbox/kubernetes/README.md` and only certified manifests/values
- Create: contract/integration/security tests named in Tasks 11-13
- Modify: `app/config.py`, `app/sandbox/*`, `app/runner/protocol.py`, `.env.example`
- Create: `scripts/verify-davinci-phase-2a.sh`, `verify-davinci-phase-2b.sh`, `verify-davinci-phase-2c.sh`, `verify-davinci-phase-2d.sh`
- Create: `docs/operations/davinci-phase-2-migration.md`, `davinci-phase-2d-runbook.md`, `davinci-phase-2d-gate-ledger.md`

## Task 1: Define and Verify the Trusted Davinci Assertion

**Files:**
- Create: `app/auth/trusted_assertion.py`
- Modify: `app/config.py`, `.env.example`
- Create: `tests/test_trusted_assertion.py`
- Create: `docs/operations/davinci-trusted-identity-contract.md`

- [ ] **Step 1: Write the signed-assertion contract before code**

Document one exact contract:

```json
{
  "iss": "davinci-uat-gateway",
  "aud": "workspace-agent",
  "sub": "obid:00123",
  "obid": "00123",
  "iat": 1786500000,
  "exp": 1786500300,
  "jti": "opaque-random-id"
}
```

Only RS256 is accepted in the first contract because the current verified OIDC/JWKS path supports RS256. Maximum lifetime is five minutes; clock skew is at most 30 seconds. `sub` must equal `"obid:" + obid`. Davinci's authenticated gateway sends this assertion only to Agent Host's bootstrap endpoint using `Authorization: Bearer $DAVINCI_ASSERTION`. Client-supplied `X-Davinci-ObId` is stripped at the gateway and rejected by Agent Host in this mode. If the company gateway requires ES256, stop and extend the verifier/dependency tests in a separate compatibility change before deployment.

- [ ] **Step 2: Write RED verifier tests**

Cover valid verification plus wrong issuer/audience/algorithm/signature, expired/not-yet-valid, mismatched `sub`/`obid`, missing/invalid `jti`, lifetime over five minutes, and the mapping result retaining issuer `davinci` plus subject `obid:` + canonical OBID.

```python
@dataclass(frozen=True)
class VerifiedDavinciIdentity:
    ob_id: str
    assertion_issuer: str
    assertion_subject: str
    expires_at: datetime
    jti: str
```

- [ ] **Step 3: Run RED**

Run: `uv run pytest tests/test_trusted_assertion.py tests/test_config.py -q`

Expected: module/settings failures.

- [ ] **Step 4: Implement via existing OIDC/JWKS primitives**

Define:

```python
class TrustedAssertionVerifier:
    async def verify(self, token: str) -> VerifiedDavinciIdentity:
        raise NotImplementedError
```

Reuse the current RS256/JWKS validation path rather than writing crypto. Normalize OBID with Phase 1's `normalize_ob_id`. The verifier returns a proven external identity; the existing `IdentityRepository.resolve_or_create("davinci", f"obid:{ob_id}", {"name": f"Davinci {ob_id}"}, provider="obid")` returns the existing internal UUID.

Add settings for exact issuer, audience, JWKS URL, allowed algorithms, max TTL, and skew. Redact all key/token values.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/test_trusted_assertion.py tests/test_config.py tests/test_oidc.py -q`

Commit:

```bash
git add app/auth/trusted_assertion.py app/config.py .env.example \
  tests/test_trusted_assertion.py docs/operations/davinci-trusted-identity-contract.md
git commit -m "feat: verify trusted Davinci actor assertions"
```

## Task 2: Persist One-Time Bootstrap Exchanges and Short Agent Bearers

**Files:**
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0009_trusted_agent_identity.py`
- Create: `app/embed/repository.py`
- Create: `app/auth/agent_bearer.py`
- Modify: `app/embed/local.py`, `app/api/dependencies.py`, `app/bootstrap.py`
- Create: `tests/test_agent_bearer.py`, `tests/test_bootstrap_repository.py`
- Modify: `tests/test_local_davinci_bootstrap.py`, `tests/test_migrations.py`

- [ ] **Step 1: Write RED repository/token tests**

Prove code/bearer plaintext is returned once but only SHA-256 hashes are stored; bootstrap consume is atomic; wrong origin/contract/user/expiry and replay fail; bearer expiry/revocation fail; two repository instances sharing PostgreSQL can issue on A and consume/resolve on B.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/test_agent_bearer.py tests/test_bootstrap_repository.py \
  tests/test_local_davinci_bootstrap.py tests/test_migrations.py -q
```

- [ ] **Step 3: Add durable schema with unique replay boundaries**

Add records equivalent to:

```python
class BootstrapExchangeRecord(Base):
    id: Mapped[str]                          # UUID primary key
    code_hash: Mapped[str]                   # unique, non-null, 64 chars
    assertion_issuer: Mapped[str]            # non-null
    assertion_jti_hash: Mapped[str]          # unique with issuer; replay boundary
    user_id: Mapped[str]                     # FK users.id, RESTRICT, non-null
    parent_origin: Mapped[str]                # non-null
    protocol_version: Mapped[str]             # non-null
    contract_version: Mapped[str]             # non-null
    contract_digest: Mapped[str]              # non-null
    expires_at: Mapped[datetime]              # indexed, non-null
    consumed_at: Mapped[datetime | None]

class AgentBearerRecord(Base):
    id: Mapped[str]                          # UUID primary key
    token_hash: Mapped[str]                  # unique, non-null, 64 chars
    user_id: Mapped[str]                     # FK users.id, RESTRICT, non-null
    parent_origin: Mapped[str]                # non-null
    expires_at: Mapped[datetime]              # indexed, non-null
    revoked_at: Mapped[datetime | None]
```

Migration head becomes `0009`. Add named unique constraints
`uq_bootstrap_exchange_code_hash`, `uq_bootstrap_assertion_issuer_jti`, and
`uq_agent_bearer_token_hash`, plus named expiry indexes. The first insert of an
assertion issuer/JTI wins; replay cannot mint another exchange. Add downgrade
tests proving all constraints/indexes/tables are removed cleanly. Never store
raw codes, JTI values, or bearer tokens.

- [ ] **Step 4: Implement exchange and identity provider**

Contracts:

```python
class BootstrapExchangeRepository:
    async def issue(self, identity, binding) -> IssuedExchange:
        raise NotImplementedError
    async def consume(self, code, binding) -> ConsumedExchange:
        raise NotImplementedError

class AgentBearerIssuer:
    async def issue(self, user_id: str, parent_origin: str) -> IssuedBearer:
        raise NotImplementedError

class BearerIdentityProvider(IdentityProvider):
    async def resolve(self, request: Request) -> IdentityContext:
        raise NotImplementedError
```

The trusted bootstrap endpoint verifies Davinci assertion, resolves the existing user, and issues a 60-second exchange. `/embed` atomically consumes it and renders one short Agent bearer into inline bootstrap data; JavaScript holds it in memory only. All identity-dependent routes require `Authorization: Bearer`. Keep health/static endpoints public. Refresh requires a new Davinci bootstrap.

- [ ] **Step 5: Run GREEN, cross-instance PostgreSQL test, and commit**

Run:

```bash
uv run pytest tests/test_agent_bearer.py tests/test_bootstrap_repository.py \
  tests/test_local_davinci_bootstrap.py tests/test_migrations.py -q
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_postgres_authority.py -q
```

Commit:

```bash
git add app/db/models.py app/db/alembic/versions/rev_0009_trusted_agent_identity.py \
  app/embed/repository.py app/auth/agent_bearer.py app/embed/local.py \
  app/api/dependencies.py app/bootstrap.py tests/test_agent_bearer.py \
  tests/test_bootstrap_repository.py tests/test_local_davinci_bootstrap.py \
  tests/test_migrations.py
git commit -m "feat: persist trusted Agent bootstrap and bearer state"
```

## Task 3: Replace Process-Local Deferred and Snapshot State with PostgreSQL Envelopes

**Files:**
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0010_durable_agui_state.py`
- Create: `app/agui/deferred_repository.py`, `app/agui/snapshot_repository.py`
- Modify: `app/agui/routes.py`, `app/agui/artifact_routes.py`, `app/agui/claude_tools.py`, `app/main.py`
- Modify: `app/runtime/claude.py`
- Modify: `tests/test_agui_deferred_tools.py`, `tests/test_snapshot_artifacts.py`, `tests/test_claude_runtime.py`, `tests/test_migrations.py`
- Create: `tests/integration/test_multi_replica_agent_state.py`

- [ ] **Step 1: Write RED state-machine tests**

Prove initial Run on API A records a deferred ToolCall, result submission on API B atomically consumes it and starts a new Run, an identical repeated `(continuation_run_id, result_hash)` returns `replayed`, conflicting results reject deterministically, and snapshot upload on A is retrievable on B until expiry. Assert no repository field stores a Future/callback/client object.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/test_agui_deferred_tools.py tests/test_snapshot_artifacts.py \
  tests/integration/test_multi_replica_agent_state.py -q
```

- [ ] **Step 3: Add immutable envelope schema**

Add a `DeferredFrontendToolRecord` whose exact fields mirror the current public
types: UUID primary key; non-null `thread_id`, `origin_run_id`,
`tool_call_id`, `public_name`, canonical `arguments_json`, `argument_hash`,
nullable `continuation_run_id` and `result_hash`, plus timestamps. Use named
unique constraint `uq_deferred_tool_thread_call` on `(thread_id,
tool_call_id)` and an index on `tool_call_id` for SESSION_MISMATCH detection.
Add a CHECK requiring continuation/result to be either both null or both
non-null.

```python
DeferredFrontendToolRecord(
    id, thread_id, origin_run_id, tool_call_id, public_name,
    arguments_json, argument_hash, continuation_run_id,
    result_hash, created_at, updated_at,
)
```

Add `SnapshotArtifactRecord` with UUID primary key, unique non-null `ref_hash`,
non-null `owner_key`, `page_instance_id`, `resource_id`, `payload_json`,
`sha256`, `size_bytes`, `expires_at`, and `created_at`; index expiry. Payload
moves to Object Store in Task 7. Add upgrade/downgrade tests for all named
constraints/indexes.

- [ ] **Step 4: Implement repositories under existing store interfaces**

Preserve the current deferred public contract exactly so routes/runtime do not
need a parallel protocol:

```python
class DeferredEnvelopeStore:
    async def record(
        self, call: DeferredFrontendToolCall
    ) -> DeferredFrontendToolCall:
        raise NotImplementedError
    async def get(
        self, thread_id: str, tool_call_id: str
    ) -> DeferredFrontendToolCall | None:
        raise NotImplementedError
    async def consume(
        self, *, thread_id: str, continuation_run_id: str,
        tool_call_id: str, content: str, error: str | None,
    ) -> DeferredToolConsumption:
        raise NotImplementedError
```

Convert `SnapshotArtifactStore.create/read` and the PostgreSQL repository to an
explicit async contract because the application database exposes
`AsyncSession` only. Update and await every caller in
`app/agui/artifact_routes.py`, `app/runtime/claude.py`, and
`app/agui/claude_tools.py`; update their tests in this task. Do not use event
loop re-entry, a thread bridge, or a second synchronous database stack.
Replace only the process-local stores; do not persist
`FrontendToolBridgeRegistry` Future objects. Native V2 continuation remains a
request-level envelope/new Run.

- [ ] **Step 5: Run GREEN and commit**

Run:

```bash
uv run pytest tests/test_agui_deferred_tools.py tests/test_snapshot_artifacts.py tests/test_migrations.py -q
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_multi_replica_agent_state.py -q
```

Commit:

```bash
git add app/db/models.py app/db/alembic/versions/rev_0010_durable_agui_state.py \
  app/agui/deferred_repository.py app/agui/snapshot_repository.py \
  app/agui/routes.py app/agui/artifact_routes.py app/agui/claude_tools.py \
  app/runtime/claude.py app/main.py \
  tests/test_agui_deferred_tools.py tests/test_snapshot_artifacts.py \
  tests/test_claude_runtime.py tests/test_migrations.py \
  tests/integration/test_multi_replica_agent_state.py
git commit -m "feat: persist AG-UI continuation and snapshot state"
```

## Task 4: Prove 2A on Two API Replicas without Sticky Sessions

**Files:**
- Create: `tests/integration/test_multi_replica_auth.py`
- Modify/Create: `tests/integration/test_postgres_sse.py`
- Create: `scripts/verify-davinci-phase-2a.sh`
- Create: `docs/operations/davinci-phase-2a-runbook.md`

- [ ] **Step 1: Build a two-app integration fixture sharing only PostgreSQL**

Instantiate API A/B with distinct process-local stores and the same database. Use `APP_RUNTIME_MODE=execution_disabled` so this Gate tests control-plane correctness without coupling to a runtime cutover.

- [ ] **Step 2: Write RED cross-replica flows**

Issue/consume bootstrap across replicas; use bearer across replicas; create/list Session across replicas; record/submit deferred frontend tool across replicas; subscribe/reconnect SSE across replicas; revoke bearer on A and reject on B; reject old `X-Davinci-ObId` everywhere.

- [ ] **Step 3: Run RED and fix only hidden process-local authorities**

Run:

```bash
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_multi_replica_auth.py \
  tests/integration/test_multi_replica_agent_state.py \
  tests/integration/test_postgres_sse.py -q
```

Any required state that affects correctness must move to PostgreSQL. Caches may remain process-local only if cache loss never changes correctness.

- [ ] **Step 4: Add Gate script/runbook**

The script starts `compose.integration.yaml` PostgreSQL on an isolated Compose
project/port, exports its concrete asyncpg URL, installs an EXIT trap to remove
the disposable volume, runs migrations and the three integration files. The
runbook specifies two API replicas, no sticky-session requirement, bearer
TTL/rotation, PG backup, and rollback to Phase 1 only after disabling trusted
traffic and re-enabling the UAT OBID profile.

- [ ] **Step 5: Run GREEN and commit**

Commit:

```bash
git add tests/integration/test_multi_replica_auth.py \
  tests/integration/test_multi_replica_agent_state.py \
  tests/integration/test_postgres_sse.py scripts/verify-davinci-phase-2a.sh \
  docs/operations/davinci-phase-2a-runbook.md
git commit -m "test: prove trusted Agent state across API replicas"
```

## Task 5: Define and Certify a Standard ObjectStore Port

**Files:**
- Create: `app/storage/contracts.py`, `app/storage/local.py`, `app/storage/s3.py`
- Modify: `app/config.py`, `app/bootstrap.py`, `pyproject.toml`, `uv.lock`
- Create: `tests/test_object_store_contract.py`
- Create: `tests/integration/test_object_store.py`
- Create: `compose.objectstore.integration.yaml`
- Create: `docs/operations/object-store-certification.md`

- [ ] **Step 1: Write the implementation-neutral contract and RED suite**

```python
@dataclass(frozen=True)
class ObjectRef:
    key: str
    sha256: str
    size: int
    mime: str
    etag: str | None
    version_id: str | None

class ObjectStore(Protocol):
    async def put_immutable(
        self, namespace: str, digest: str, source: BinaryIO,
        *, size: int, mime: str,
    ) -> ObjectRef:
        raise NotImplementedError
    async def open(self, ref: ObjectRef) -> AsyncIterator[bytes]:
        raise NotImplementedError
    async def head(self, ref: ObjectRef) -> ObjectRef:
        raise NotImplementedError
    async def delete(self, ref: ObjectRef) -> None:
        raise NotImplementedError
```

Contract tests cover digest/size verification, immutable same-digest idempotency,
conditional-put conflict rejection, streaming read, head, version-specific
delete, transient error classification, and object keys containing only opaque
namespace/UUID/digest values. In a versioned bucket every ready `ObjectRef`
must contain `version_id`; in a non-versioned bucket the adapter must certify
immutable conditional put and bucket policy before allowing `version_id=None`.

- [ ] **Step 2: Run RED and implement LocalObjectStore**

Run: `uv run pytest tests/test_object_store_contract.py -q`

Implement a filesystem adapter for unit tests/single-host rollback. It must use atomic temp-file + rename and containment checks; it is not the Phase 2 distributed backend.

- [ ] **Step 3: Implement the S3-compatible adapter using the official SDK**

Add the official AWS Python SDK as a locked dependency. `S3ObjectStore` delegates blocking SDK calls through bounded `asyncio.to_thread`, uses multipart upload for configured large objects, validates metadata SHA-256/size after upload, and never exposes bucket credentials to Runner/model code. Configuration requires endpoint, region, bucket, TLS, and credential provider; no hard-coded access keys.

- [ ] **Step 4: Certify against a disposable S3-compatible service**

Use `compose.objectstore.integration.yaml` to start MinIO only for integration tests. Run the same contract suite against Local and S3 adapters:

```bash
docker compose -f compose.objectstore.integration.yaml up -d
TEST_OBJECT_STORE_S3=1 uv run pytest tests/integration/test_object_store.py -q
docker compose -f compose.objectstore.integration.yaml down -v
```

Record SDK/MinIO image digests, multipart threshold, retry/timeouts, TLS behavior, and test results. Before UAT/production, rerun the same test against the company S3-compatible endpoint; a failure blocks Task 7 and requires an adapter-specific follow-up plan rather than weakening the port.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/test_object_store_contract.py -q && git diff --check`

Commit:

```bash
git add app/storage app/config.py app/bootstrap.py pyproject.toml uv.lock \
  tests/test_object_store_contract.py tests/integration/test_object_store.py \
  compose.objectstore.integration.yaml docs/operations/object-store-certification.md
git commit -m "feat: add certified immutable object storage"
```

## Task 6: Add Artifact Metadata as the Single Authority for Attachments and Outputs

**Files:**
- Create: `app/artifacts/models.py`, `app/artifacts/repository.py`, `app/artifacts/service.py`
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0011_artifact_authority.py`
- Modify: `app/attachments/service.py`, `app/api/routes.py`, `app/api/dependencies.py`
- Modify: `tests/test_attachments.py`, `tests/test_api.py`, `tests/test_migrations.py`
- Create: `tests/test_artifacts.py`

- [ ] **Step 1: Write RED ownership/idempotency tests**

Cover upload stream -> immutable object -> metadata commit, content hash mismatch, DB commit failure after object upload, retry reconciliation, authenticated streaming download, A/B cross-user 404, output finalization, and tombstone behavior.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_artifacts.py tests/test_attachments.py tests/test_api.py tests/test_migrations.py -q`

- [ ] **Step 3: Add artifact records and explicit state transitions**

Add exact `ArtifactRecord` columns: UUID `id` primary key; non-null
`owner_user_id` FK `users.id` RESTRICT; non-null `workspace_id` FK
`workspaces.id` RESTRICT; non-null `session_id` FK `sessions.id` CASCADE;
nullable `turn_id` FK `turns.id` SET NULL; non-null `kind`, `client_request_id`,
`object_key`, `sha256`, `size_bytes`, `mime`, and `status`; nullable `etag`,
`version_id`, `finalized_at`, `expires_at`, `deleted_at`; non-null timestamps.
Use CHECKs for allowed kind/status/non-negative size and named unique constraint
`uq_artifacts_session_kind_request` on `(session_id, kind,
client_request_id)`. Add `attachments.artifact_id` as nullable FK during
backfill, make it non-null after migration verification, and test downgrade.
Legal statuses are `uploading -> ready|failed -> tombstoned -> deleted`.
PostgreSQL metadata is the authorization/index authority; Object Store is the
byte authority.

- [ ] **Step 4: Implement service/reconciliation without dual authority**

`ArtifactService` first commits an idempotent `uploading` intent with the final
object key/client request ID, then uploads and verifies the object, then marks
the same row `ready` in a second transaction. A crash or second-transaction
failure leaves a durable stale `uploading` row for reconciler cleanup/retry;
never rely on recording cleanup after the failing transaction. Once `ready`,
do not treat Session PVC/local copies as authoritative Attachment/Output bytes.
Downloads authorize through existing AccessService before opening the object.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/test_artifacts.py tests/test_attachments.py tests/test_api.py tests/test_migrations.py -q`

Commit:

```bash
git add app/artifacts app/db/models.py \
  app/db/alembic/versions/rev_0011_artifact_authority.py \
  app/attachments/service.py app/api/routes.py app/api/dependencies.py \
  tests/test_artifacts.py tests/test_attachments.py tests/test_api.py tests/test_migrations.py
git commit -m "feat: make artifacts object-store authoritative"
```

## Task 7: Move Dashboard Snapshots from PostgreSQL Payloads to ObjectStore

**Files:**
- Modify: `app/agui/snapshot_repository.py`, `app/agui/artifact_routes.py`
- Modify: `tests/test_snapshot_artifacts.py`, `tests/test_claude_runtime.py`
- Create: `tests/integration/test_snapshot_object_store.py`

- [ ] **Step 1: Write RED migration/read tests**

Prove new snapshots store only artifact/object reference metadata in PG, payload is read from ObjectStore on another API replica, TTL/owner/resource binding still applies, old 2A PG payload rows are migrated/read during the maintenance migration, and no snapshot is simultaneously authoritative in PG and ObjectStore after cutover. Keep regression coverage for the runtime/Claude tool callers against the async contract established in Task 3.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_snapshot_artifacts.py tests/integration/test_snapshot_object_store.py -q`

- [ ] **Step 3: Delegate bytes to ArtifactService**

SnapshotRepository creates a `kind="dashboard_snapshot"` Artifact and retains
ref hash, page/resource ownership, TTL, and artifact ID. Validate payload hash
before making metadata ready. Retain the Task 3 async repository contract;
resolve/read uses authorization metadata then streams/parses the object with
existing size/schema limits. Do not reintroduce a synchronous adapter.

- [ ] **Step 4: Add cleanup and failure recovery**

TTL reaper first tombstones metadata, then deletes object, then marks deleted. Upload-success/DB-failure enters orphan cleanup. Object-delete failure remains retryable and does not resurrect access.

- [ ] **Step 5: Run GREEN and commit**

Commit:

```bash
git add app/agui/snapshot_repository.py app/agui/artifact_routes.py \
  tests/test_snapshot_artifacts.py tests/test_claude_runtime.py \
  tests/integration/test_snapshot_object_store.py
git commit -m "feat: store dashboard snapshots as artifacts"
```

## Task 8: Remove API Host Paths and Materialize Session Input in Workers

**Files:**
- Modify: `app/sessions/service.py`, `app/sessions/snapshot.py`, `app/sessions/catalog.py`
- Create: `app/storage/session_volumes.py`
- Modify: `app/sandbox/contracts.py`, `app/sandbox/worker.py`, `app/sandbox/opensandbox_adapter.py`
- Modify: `app/runner/protocol.py`
- Modify: `app/api/routes.py`
- Create: `app/sessions/materializer.py`
- Create: `tests/test_session_materializer.py`
- Modify: `tests/test_sessions.py`, `tests/test_sandbox_worker.py`, `tests/test_runner_protocol.py`
- Create: `tests/integration/test_api_without_data_dir.py`

- [ ] **Step 1: Write RED no-local-path tests**

Run an API app with no mounted `APP_DATA_DIR` Session tree. Create Session/Attachment metadata on API A; claim Turn on a Worker with a different filesystem root; materialize PG Session snapshot + ObjectRefs; run; finalize Output Artifact; read result via API B. Assert no API/Worker protocol contains an absolute API-host Path.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/test_session_materializer.py tests/test_sessions.py \
  tests/test_sandbox_worker.py tests/test_runner_protocol.py \
  tests/integration/test_api_without_data_dir.py -q
```

- [ ] **Step 3: Make Session creation metadata-only**

`SessionService.create()` writes the immutable workspace/Skill/MCP snapshot to PostgreSQL but does not materialize a host directory. Skill listing for a Session reads its snapshot. Delete becomes `tombstone -> block new Turn -> async reaper`, never API `rename/rmtree`. Replace arbitrary workdir search with the authenticated Attachment/Artifact catalog for this release.

- [ ] **Step 4: Materialize in the Worker from portable references**

Define:

```python
@dataclass(frozen=True)
class SessionMaterializationManifest:
    session_id: str
    generation: int
    workspace_snapshot: dict[str, Any]
    attachments: tuple[RunnerAttachmentRef, ...]  # artifact/object refs, no host path

class SessionMaterializer:
    async def stage(self, manifest, destination: Path) -> None:
        raise NotImplementedError

class SessionVolumeStore(Protocol):
    async def ensure(self, session_id: str, generation: int) -> SessionVolumeRef:
        raise NotImplementedError
    async def seed_once(
        self, ref: SessionVolumeRef, staged_source: Path, manifest_hash: str
    ) -> None:
        raise NotImplementedError
    async def delete(self, ref: SessionVolumeRef) -> None:
        raise NotImplementedError
```

The Local implementation uses a Session directory; the OpenSandbox
implementation delegates volume identity/lifecycle to the certified provider
through `SandboxPort` and stores only an opaque reference. The Worker downloads
immutable objects into an ephemeral staging directory and
verifies hashes. `sync_workspace()` may seed only once per
`(session_id, generation)`, recorded by an idempotent materialization marker;
retries verify that marker and hashes and never replace the mutable workdir,
transcript, or Claude resume state. New Attachments are staged to immutable
relative attachment paths individually. Local Worker Paths are ephemeral
implementation details, not cross-service contracts or data authority. Runner
protocol includes relative mount paths plus artifact IDs/hashes only.

- [ ] **Step 5: Run GREEN and commit**

Run the Step 2 command and `uv run pytest tests/test_api.py tests/test_attachments.py -q`.

Commit:

```bash
git add app/sessions app/storage/session_volumes.py app/sandbox/contracts.py app/sandbox/worker.py \
  app/sandbox/opensandbox_adapter.py app/runner/protocol.py app/api/routes.py \
  tests/test_session_materializer.py tests/test_sessions.py \
  tests/test_sandbox_worker.py tests/test_runner_protocol.py \
  tests/integration/test_api_without_data_dir.py
git commit -m "feat: materialize sessions from portable worker inputs"
```

## Task 9: Replace Persistent Memory PVC/Local Directories with Versioned CAS Bundles

**Files:**
- Create: `app/memory/bundles.py`, `app/memory/repository.py`
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0012_memory_bundle_authority.py`
- Modify: `app/memory/scopes.py`, `app/turns/service.py`
- Modify: `app/sandbox/models.py`, `app/sandbox/opensandbox_adapter.py`, `app/sandbox/worker.py`
- Create: `tests/test_memory_bundles.py`
- Modify: `tests/test_memory_scopes.py`, `tests/test_sandbox_worker.py`
- Create: `tests/integration/test_memory_bundle_concurrency.py`

- [ ] **Step 1: Write RED CAS/lease tests**

Prove same `(user_id, workspace_id)` Sessions materialize the same version, different scopes cannot read it, only the valid existing PostgreSQL Memory lease can commit, expected-version mismatch rejects, retry with identical digest is idempotent, and Worker crash before commit leaves the prior version authoritative.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_memory_bundles.py tests/test_memory_scopes.py tests/integration/test_memory_bundle_concurrency.py -q`

- [ ] **Step 3: Implement immutable bundle metadata and CAS**

```python
@dataclass(frozen=True)
class AcquiredMemoryLease:
    scope_key: str
    owner_id: str
    fence_token: int
    expires_at: datetime

class MemoryBundleStore:
    async def materialize(
        self, scope: MemoryScope, version: int | None, dest: Path
    ) -> int | None:
        raise NotImplementedError
    async def commit(
        self, scope: MemoryScope, expected_version: int | None,
        source: Path, lease: AcquiredMemoryLease,
    ) -> int:
        raise NotImplementedError
```

Add `MemoryBundleRecord` with UUID primary key, unique `(scope_key, version)`,
unique object ref, digest/size, timestamps, and `MemoryBundleHeadRecord` keyed by
scope with nullable current version. `None` is the empty first version;
committing with expected `None` creates version 1. Tar deterministically,
reject symlinks/escape paths, hash before upload, store bundle as immutable
object, then atomically advance the PG pointer under the existing lease's
owner/fence token. Add migration upgrade/downgrade/CAS tests. Do not create a
new lock abstraction.

- [ ] **Step 4: Update Sandbox flow**

At Turn start, Worker acquires current Memory lease, downloads/materializes the
bundle into a temporary mounted directory, and points Claude Code
`autoMemoryDirectory` at it. On successful Turn finalization, commit CAS before
marking the Turn completed and before releasing the lease. Remove persistent
Memory PVC creation from OpenSandboxAdapter; Session PVC remains separate.

- [ ] **Step 5: Run GREEN and commit**

Commit:

```bash
git add app/memory app/db/models.py \
  app/db/alembic/versions/rev_0012_memory_bundle_authority.py \
  app/turns/service.py app/sandbox/models.py \
  app/sandbox/opensandbox_adapter.py app/sandbox/worker.py \
  tests/test_memory_bundles.py tests/test_memory_scopes.py \
  tests/test_sandbox_worker.py tests/integration/test_memory_bundle_concurrency.py
git commit -m "feat: persist workspace memory as versioned bundles"
```

## Task 10: Build the Frozen Migration Manifest and Admission Fence

**Files:**
- Create: `app/migration/manifest.py`, `app/migration/service.py`, `app/migration/cli.py`
- Modify: `app/config.py`, `app/main.py`, `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0013_migration_authority.py`
- Modify: `app/api/routes.py`, `app/sessions/service.py`, `app/attachments/service.py`, `app/artifacts/service.py`
- Modify: `app/turns/service.py`, `app/turns/repository.py`, `app/sandbox/repository.py`, `app/sandbox/worker.py`
- Modify: snapshot/artifact and cleanup entry points `app/agui/artifact_routes.py`, `app/agui/snapshot_repository.py`, `app/artifacts/repository.py`
- Create: `tests/test_migration_manifest.py`
- Create: `tests/integration/test_phase2_migration.py`
- Create: `docs/operations/davinci-phase-2-migration.md`
- Create: `scripts/verify-davinci-phase-2b.sh`

- [ ] **Step 1: Write RED manifest/resume/rollback tests**

Manifest must record migration epoch, every Session source path/file
size/SHA-256, every Attachment/Output/Snapshot object key/hash/size, and each
Memory scope bundle hash/version. Target PVC volume identity remains null until
the certified provider in Task 11/12 creates it. Test interruption/resume,
corrupted source, object upload success + DB commit failure, duplicate rerun,
and count/size/hash verification. PVC import/cutover tests move to Task 13.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_migration_manifest.py tests/integration/test_phase2_migration.py -q`

- [ ] **Step 3: Add a PostgreSQL write fence and migration CLI**

Add `MigrationEpochRecord` with UUID epoch primary key, status CHECK
`planned|admission_fenced|drained|hard_fenced|imported|verified|cutover|rolled_back`,
manifest SHA-256, timestamps and error; and `MigrationManifestItemRecord` with
UUID primary key, epoch FK CASCADE, unique `(epoch_id, item_type, source_id,
relative_path)`, source/target refs, size, digest and status. Add migration
upgrade/downgrade tests.

CLI commands:

```text
workspace-agent migrate-phase2 plan --epoch "$MIGRATION_EPOCH" --output manifest.json
workspace-agent migrate-phase2 admission-fence --epoch "$MIGRATION_EPOCH"
workspace-agent migrate-phase2 drain --epoch "$MIGRATION_EPOCH"
workspace-agent migrate-phase2 hard-fence --epoch "$MIGRATION_EPOCH"
workspace-agent migrate-phase2 export-objects --manifest manifest.json
workspace-agent migrate-phase2 verify-objects --manifest manifest.json
```

`admission-fence` blocks new Session/Turn claims/uploads/deletes but allows
already-running Turn finalization. `drain` waits for no running/finalizing Turn
and no valid execution/memory lease. Only then `hard-fence` blocks finalizers,
reapers, and maintenance writers. Export is resumable and immutable. There is
no long-term dual write.

- [ ] **Step 4: Encode ownership and rollback rules**

At this task's end Attachment/Output/Snapshot and Memory objects are verified,
but workdir/transcript routing has not cut over. A failure removes the fence and
routes to old workdirs. Do not merge new writes backward. Identity/product IDs
remain unchanged.

- [ ] **Step 5: Run GREEN, script Gate, and commit**

Run:

```bash
uv run pytest tests/test_migration_manifest.py tests/integration/test_phase2_migration.py -q
TEST_OBJECT_STORE_S3=1 bash scripts/verify-davinci-phase-2b.sh
```

Commit:

```bash
git add app/migration app/config.py app/main.py tests/test_migration_manifest.py \
  app/db/models.py app/db/alembic/versions/rev_0013_migration_authority.py \
  app/api/routes.py app/sessions/service.py app/attachments/service.py \
  app/artifacts/service.py app/artifacts/repository.py \
  app/agui/artifact_routes.py app/agui/snapshot_repository.py \
  app/turns/service.py app/turns/repository.py app/sandbox/repository.py \
  app/sandbox/worker.py \
  tests/integration/test_phase2_migration.py \
  docs/operations/davinci-phase-2-migration.md scripts/verify-davinci-phase-2b.sh
git commit -m "feat: freeze and verify portable Agent state"
```

## Task 11: Certify One Exact OpenSandbox Kubernetes Provider Stack

**Files:**
- Create: `deploy/opensandbox/kubernetes/README.md`
- Create: `tests/contract/test_opensandbox_kubernetes.py`
- Create: `tests/integration/test_opensandbox_kubernetes.py`
- Create: `tests/security/test_kubernetes_boundary.py`
- Create: `scripts/verify-davinci-phase-2c.sh`
- Modify only after observed compatibility: dependency/config/deploy lock files

- [ ] **Step 1: Record the currently pinned stack and candidate provider**

Start the certification ledger with current pins `Python SDK 0.1.15 / Server 0.2.2 / execd 1.0.21 / egress 1.1.4`, exact image digests, Kubernetes/CSI versions, and candidate `BatchSandbox non-pooled`. Do not claim support from upstream `main` docs.

- [ ] **Step 2: Write opt-in contract tests against real OpenSandbox/Kubernetes**

Gate create/inspect/exec/cancel/destroy, provider-opaque Sandbox IDs, explicit per-Session dynamic PVC request, `ReadWriteOncePod` preservation, `/session` remount after Pod recreation on another node, NetworkPolicy, credential injection boundary, server restart/reconcile, and deterministic lookup after a lost create response.

- [ ] **Step 3: Run the existing pins and stop on incompatibility**

Run:

```bash
RUN_OPENSANDBOX_K8S=1 uv run pytest \
  tests/contract/test_opensandbox_kubernetes.py \
  tests/integration/test_opensandbox_kubernetes.py \
  tests/security/test_kubernetes_boundary.py -q
```

If any required capability is absent, upgrade only the OpenSandbox SDK/Server/provider adapter to the smallest compatible exact version/digest, update the lock/ledger, and rerun. Do not change product API or build a custom controller.

- [ ] **Step 4: Prove non-duplication and storage fencing**

Simulate OpenSandbox Server restart and lost create response. Worker reconciles using PG `(session_id, generation, execution_id)` plus provider facts; it must adopt the existing Sandbox or mark a deterministic retry, never create a second writer. Prove the old Pod is fenced/detached before another Pod mounts RWOP. Certify the provider's opaque Session storage retention/deletion API; if it cannot delete retained storage by opaque reference, stop and add exactly one `SandboxPort.delete_session_storage(ref)` capability in Task 12. The application must not call Kubernetes directly.

- [ ] **Step 5: Commit certification evidence, not guesses**

Commit only after the Gate passes:

```bash
git add deploy/opensandbox/kubernetes tests/contract/test_opensandbox_kubernetes.py \
  tests/integration/test_opensandbox_kubernetes.py \
  tests/security/test_kubernetes_boundary.py scripts/verify-davinci-phase-2c.sh \
  pyproject.toml uv.lock
git commit -m "test: certify OpenSandbox Kubernetes provider contract"
```

If blocked, commit no "certified" marker; record the failed ledger and stop Phase 2C.

## Task 12: Switch `SandboxPort` to the Certified Kubernetes Provider

**Files:**
- Modify: `app/config.py`, `.env.example`, `app/main.py`
- Modify: `app/sandbox/models.py`, `app/sandbox/contracts.py`, `app/sandbox/opensandbox_adapter.py`, `app/sandbox/repository.py`, `app/sandbox/worker.py`
- Modify: `app/runner/protocol.py`
- Modify: `deploy/docker/Dockerfile.opensandbox-runner`
- Modify: `tests/test_config.py`, `tests/test_sandbox_worker.py`, `tests/test_opensandbox_adapter.py`, `tests/test_runner_protocol.py`

- [ ] **Step 1: Write RED provider-neutral configuration tests**

Add `APP_RUNTIME_MODE=opensandbox_worker`; keep `opensandbox_docker` as a deprecated compatibility alias to the same application orchestration. The actual Docker/Kubernetes provider is selected in OpenSandbox Server configuration, not in product logic.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_config.py tests/test_sandbox_worker.py tests/test_opensandbox_adapter.py -q`

- [ ] **Step 3: Extend only opaque Sandbox/volume state**

Keep current `SandboxPort` method family. Extend `SessionSandboxSpec/Handle` only with certified provider-opaque volume reference, generation, and execution identity. The application never imports Kubernetes clients or creates Pod/PVC/CRD.

OpenSandboxAdapter asks the certified provider for one non-pooled Sandbox mounting one persistent Session RWOP volume at `/session`; Memory is a temporary materialized directory from Task 9. It injects the existing short model credential at execution time and excludes it from snapshot/object/PVC state. User-level MCP credential delegation remains outside this phase until a concrete MCP audience/scope contract exists.

- [ ] **Step 4: Add reconcile/delete semantics**

On startup and before create, Worker reconciles PG mappings to OpenSandbox/provider facts. Delete flow is `DB tombstone -> block Turn -> destroy Sandbox -> confirm volume detach -> provider-certified opaque storage delete + Object cleanup -> mark complete`. If Task 11 required the extra storage-delete port method, add and test only that method here. Lost responses use stable client request/generation keys.

- [ ] **Step 5: Run certified Gate and commit**

Run:

```bash
uv run pytest tests/test_config.py tests/test_sandbox_worker.py tests/test_opensandbox_adapter.py -q
RUN_OPENSANDBOX_K8S=1 bash scripts/verify-davinci-phase-2c.sh
```

Commit:

```bash
git add app/config.py .env.example app/main.py app/sandbox/models.py \
  app/sandbox/contracts.py app/sandbox/opensandbox_adapter.py \
  app/sandbox/repository.py app/sandbox/worker.py app/runner/protocol.py \
  deploy/docker/Dockerfile.opensandbox-runner tests/test_config.py \
  tests/test_sandbox_worker.py tests/test_opensandbox_adapter.py \
  tests/test_runner_protocol.py
git commit -m "feat: run sessions on certified OpenSandbox Kubernetes"
```

## Task 13: Prove the Real Multi-Node Security and Recovery Gate

**Files:**
- Modify: `app/migration/service.py`, `app/migration/cli.py`
- Modify: `tests/integration/test_phase2_migration.py`
- Create: `tests/integration/test_multinode_recovery.py`
- Create: `tests/security/test_cluster_network.py`
- Create: `scripts/verify-davinci-phase-2d.sh`
- Create: `docs/operations/davinci-phase-2d-runbook.md`
- Create: `docs/operations/davinci-phase-2d-gate-ledger.md`
- Modify: the exact certified deployment manifest paths recorded by Task 11; add those paths to this task before execution if observed failures require changes

- [ ] **Step 1: Deploy the minimum production-like topology**

Two Agent API replicas, two Execution Workers, one PostgreSQL service, one S3-compatible Object Store, one OpenSandbox Server, certified provider, CSI, and at least two schedulable worker nodes. Browser exposes only HTTPS 443; Ingress -> API 8000; Worker -> PG 5432/Object 443/OpenSandbox 8080; OpenSandbox -> Kubernetes API 443. Do not expose execd, Docker dynamic ports, or Redis.

Before opening traffic, keep Task 10's hard fence and extend the migration CLI
with provider-backed `import-workdirs`, `verify-workdirs`, `cutover`, and
`rollback-routing`. Create each Session storage reference through the certified
OpenSandbox provider, import manifest files, verify count/size/SHA-256, and only
then atomically set epoch `cutover`. A pre-cutover failure destroys incomplete
target storage and removes the fence; no new writes are reverse-merged.

- [ ] **Step 2: Execute correctness/failure tests**

Prove no-sticky bootstrap/REST/AG-UI continuation/SSE; same Session single writer; same Memory scope single lease; API/Worker/OpenSandbox restart; Sandbox Pod kill; node drain/loss; RWOP detach/fence/cross-node resume with existing Claude session; create response loss; object upload/DB commit split failure; migration interruption; deletion retention; bearer revoke; old raw OBID rejection; A/B cross-user isolation.

- [ ] **Step 3: Execute security boundary tests**

Verify exact origin/CSP/CORS, NetworkPolicy deny-by-default, Sandbox egress allowlist, no hostPath/privileged container, read-only image/rootfs where certified, credential Vault/injection, and a canary secret that never appears in API frames/logs, PG, PVC, Object Store, ToolResult, or chat rendering.

- [ ] **Step 4: Measure before optimizing cold start**

Record cold and consecutive-Turn P50/P95/P99 for Sandbox readiness and first model token, image-pull vs volume-attach vs model/MCP latency, and resource use. Do not enable WarmPool/pause/snapshot in this task. If actual SLO fails, create a separate measurement-backed WarmPool design using the certified provider.

- [ ] **Step 5: Commit the runbook and truthful Gate ledger**

Run:

```bash
RUN_PHASE2D_CLUSTER=1 bash scripts/verify-davinci-phase-2d.sh
```

The ledger records cluster versions/digests, timestamps, commands, P50/P95/P99, failure injections, artifacts, and pass/fail. A failed Gate cannot be marked complete.

Commit:

```bash
git add app/migration/service.py app/migration/cli.py \
  tests/integration/test_phase2_migration.py \
  tests/integration/test_multinode_recovery.py \
  tests/security/test_cluster_network.py scripts/verify-davinci-phase-2d.sh \
  docs/operations/davinci-phase-2d-runbook.md \
  docs/operations/davinci-phase-2d-gate-ledger.md
git commit -m "docs: record Phase 2 multi-node recovery evidence"
```

If Task 11's certified manifest changed, stage only its exact path in a second
`git add` before commit; never stage the whole `deploy` directory.

## Phase 2 Completion Gate

Phase 2 is complete only when all are true:

- Trusted Davinci assertion resolves to the Phase 1 internal user; raw OBID headers are rejected.
- Browser keeps only a short Agent bearer in page memory; every identity-dependent call uses it and works across API replicas without sticky sessions.
- Bootstrap, bearer, deferred ToolCall/ToolResult, snapshot metadata, Turn events, and concurrency truth survive process restarts.
- API replicas do not require mounted Session directories; API/Worker protocols contain no API-host absolute Paths.
- Attachment/Output/Snapshot bytes have one ObjectStore authority; Memory has one versioned ObjectStore + PG CAS authority; Session workdir/transcript has one RWOP PVC authority.
- One active Session has one writer. Node failure cannot create a second Sandbox/PVC writer, and recovery resumes the original Session on another node.
- OpenSandbox/Kubernetes compatibility is proven for exact pinned versions/digests; the application owns no Kubernetes controller/CRD/scheduler.
- Migration manifest count/size/SHA-256 matches source/target, is resumable, and rollback never attempts uncontrolled reverse merge.
- Real two-node security/recovery Gate is green and documented; cold-start optimization remains evidence-driven and separate.
