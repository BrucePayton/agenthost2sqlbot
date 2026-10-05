# Phase 1 PostgreSQL, OIDC, and Creator-Private Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PostgreSQL and trusted data-center identity the authoritative multi-replica control plane while continuing to execute Turns through the Phase 0 local dispatcher.

**Architecture:** Add a PostgreSQL production profile, OIDC subject-to-user mapping, expiring Workspace membership projections, creator-private repository methods, durable Turn state/event tables, and PostgreSQL notification-backed SSE. Keep SQLite plus local execution as a single-instance development compatibility path. Multi-replica rehearsal runs control-plane-only with execution disabled until the Phase 2 Runner exists.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, Alembic, PostgreSQL 16, asyncpg, PyJWT cryptography, httpx, pytest, Hypothesis, Docker Compose for integration tests.

## Global Constraints

- PostgreSQL is the only production authority for Workspace, Session, Turn, queue, event, identity mapping, membership projection, and idempotency state.
- OIDC issuer, audience, signature, expiry, not-before, and subject are verified server-side. Browser-supplied user IDs, Workspace IDs, `obId`, and trusted-looking headers are not identities.
- Space membership authority stays in the data-center Space service; PostgreSQL stores only a projection with `source_version` and `expires_at`.
- Expired projection plus unavailable authority fails closed for writes and sensitive reads.
- Workspace owner/admin does not imply access to another creator's Session, message, file, attachment, Artifact, transcript metadata, or SSE events.
- API replicas do not use in-memory locks or queues for correctness.
- `local_inline` execution is allowed only for one development/test process. A multi-replica or production Phase 1 deployment uses `execution_disabled`; Turns remain durably queued for Phase 2 instead of risking concurrent local transcript or Auto Memory writers.
- `turns.status='queued'` is the only queue. `LISTEN/NOTIFY` is a wake-up optimization, not an event or queue authority.
- No Redis, Outbox Relay, Kubernetes Controller, Runner Pod, S3 migration, vector memory, or custom SDK SessionStore in this phase.
- `APP_IDENTITY_MODE=mock` and SQLite remain explicit development-only modes.

---

## File Structure

### New files

- `app/db/alembic/versions/rev_0005_control_plane_authority.py` — identity, projection, snapshot, Turn attempt/event, and database constraints.
- `app/auth/oidc.py` — OIDC verification and trusted Principal construction.
- `app/auth/identity_repository.py` — issuer/subject to internal user mapping.
- `app/auth/membership.py` — Space authority port, projection refresh, and fail-closed policy.
- `app/auth/space_client.py` — HTTP adapter for the data-center Space service.
- `app/turns/repository.py` — transactional Turn, attempt, and event authority.
- `app/turns/state_machine.py` — explicit legal transitions and active-state definitions.
- `app/turns/notifications.py` — in-process development and PostgreSQL notification adapters.
- `app/turns/sse_store.py` — event history replay plus notification/short-poll tailing.
- `tests/test_oidc.py` — token validation and key rotation.
- `tests/test_membership_projection.py` — refresh, expiry, version, and fail-closed behavior.
- `tests/test_turn_state_machine.py` — legal transitions and property tests.
- `tests/test_turn_repository.py` — idempotency, active-Turn, attempt, and event ordering.
- `tests/integration/test_postgres_authority.py` — multi-connection PostgreSQL concurrency tests.
- `tests/integration/test_postgres_sse.py` — cross-process-compatible event notification tests.
- `compose.integration.yaml` — isolated PostgreSQL integration dependency.

### Modified files

- `pyproject.toml` and `uv.lock` — asyncpg, JWT crypto, and test dependencies.
- `app/config.py` — database/identity/Space authority settings and validation.
- `app/db/base.py` — production PostgreSQL requirements and dialect capabilities.
- `app/db/models.py` — new authority records and constraints.
- `app/auth/models.py` — issuer/audience/subject-aware identity context.
- `app/auth/provider.py` — mode-selected provider and preserved mock adapter.
- `app/auth/access.py` — projection freshness plus creator-private repository filters.
- `app/sessions/snapshot.py` — immutable `config_snapshot_id` and schema version metadata.
- `app/sessions/service.py` — repository-backed owner-scoped queries.
- `app/turns/service.py` — durable transitions/events through `TurnRepository`.
- `app/turns/sse.py` — durable replay and PG notification tail.
- `app/api/routes.py` — trusted Principal and consistent authorization errors.
- `app/api/dependencies.py`, `app/bootstrap.py`, `app/main.py` — wire new services.
- `tests/test_auth.py`, `tests/test_sessions.py`, `tests/test_turns.py`, `tests/test_sse.py`, `tests/test_migrations.py`, `tests/test_api.py` — updated contracts.
- `.env.example`, `README.md` — production and local profiles.

---

### Task 1: Add a validated PostgreSQL production profile

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `app/config.py`
- Modify: `app/db/base.py`
- Create: `compose.integration.yaml`
- Modify: `tests/test_database.py`
- Create: `tests/integration/test_postgres_authority.py`

**Interfaces:**
- `APP_ENV=production` requires `postgresql+asyncpg://...`.
- SQLite is accepted only when `APP_ENV` is `development` or `test`.
- Integration tests receive `TEST_POSTGRES_URL`; repository code never embeds credentials.

- [ ] **Step 1: Add failing settings tests**

Test production rejection of blank/SQLite URLs, redacted string representation, and successful asyncpg configuration.

```python
def test_production_rejects_sqlite_database() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(app_env="production", database_url="sqlite+aiosqlite:///tmp/test.db")
```

- [ ] **Step 2: Add dependencies and configuration**

Add `asyncpg` as a runtime dependency. Add PostgreSQL 16 to `compose.integration.yaml` with a health check, a named test-only volume, and no production defaults.

- [ ] **Step 3: Implement dialect-aware startup**

Keep SQLite pragmas only on SQLite. Add PostgreSQL pool pre-ping, statement timeout, and application name through SQLAlchemy connect args. Do not call dialect-specific SQL from service modules.

- [ ] **Step 4: Run local and PostgreSQL smoke tests**

```bash
uv run pytest tests/test_database.py -q
docker compose -f compose.integration.yaml up -d postgres
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_postgres_authority.py -q
docker compose -f compose.integration.yaml down
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock app/config.py app/db/base.py compose.integration.yaml tests/test_database.py tests/integration/test_postgres_authority.py
git commit -m "feat(db): add postgres production profile"
```

---

### Task 2: Migrate the durable control-plane schema

**Files:**
- Create: `app/db/alembic/versions/rev_0005_control_plane_authority.py`
- Modify: `app/db/models.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/integration/test_postgres_authority.py`

**Interfaces:**
- Adds `identity_mappings`, `workspace_membership_projections`, `config_snapshots`, `turn_attempts`, and `turn_events`.
- Extends `sessions` with `config_snapshot_id` and soft-delete metadata.
- Extends `turns` with the V2 states, `effect_state`, `finalization_status`, `warning_code`, cancellation timestamp, and execution barrier metadata.
- Adds `(session_id, client_request_id)` unique constraint and PostgreSQL partial unique index for one active Turn per Session.

- [ ] **Step 1: Write fresh-schema and legacy-upgrade assertions**

Assert exact table names, FKs, unique constraints, indexes, non-null defaults, and preservation of existing Sessions, Turns, messages, skills, attachments, and memory paths.

- [ ] **Step 2: Define active states once**

In migration and Python state machine use:

```text
queued
waiting_for_memory
assigned
running
finalizing
```

Cancellation intent is stored in `cancel_requested_at`, not as a separate active status. Terminal pre-execution outcomes include `failed_before_execution` and `cancelled_before_execution`. The database partial index predicate must exactly match the Python active-state constant. Add a test that parses/compares both representations.

- [ ] **Step 3: Implement additive migration**

Backfill existing message rows into `turn_events` in stable `(created_at, id)` order. Preserve the old `messages` table for one compatibility release; new writes target `turn_events`. Backfill existing runtime state to `completed`, `failed`, `cancelled`, or `interrupted` without inventing execution attempts.

- [ ] **Step 4: Validate on both dialects**

```bash
uv run pytest tests/test_migrations.py -q
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_postgres_authority.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/db/alembic/versions/rev_0005_control_plane_authority.py app/db/models.py tests/test_migrations.py tests/integration/test_postgres_authority.py
git commit -m "feat(db): add durable control plane schema"
```

---

### Task 3: Implement trusted OIDC identity mapping

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `app/auth/models.py`
- Create: `app/auth/oidc.py`
- Create: `app/auth/identity_repository.py`
- Modify: `app/auth/provider.py`
- Modify: `app/config.py`
- Create: `tests/test_oidc.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- `OidcIdentityProvider.resolve(request) -> IdentityContext` verifies a Bearer JWT.
- `IdentityContext` contains internal `user_id`, `issuer`, `external_subject`, and display metadata; only internal IDs enter authorization queries.
- `IdentityRepository.resolve_or_create(issuer, subject, profile) -> UserRecord` is transactionally unique on `(issuer, subject)`.

- [ ] **Step 1: Add token validation tests**

Cover valid token, wrong issuer, wrong audience, expired/not-yet-valid token, missing subject, algorithm confusion, unknown key ID, JWKS refresh, clock skew, and forged identity headers.

- [ ] **Step 2: Run and observe missing provider**

```bash
uv run pytest tests/test_oidc.py tests/test_auth.py -q
```

- [ ] **Step 3: Implement explicit OIDC settings**

Require:

```text
APP_IDENTITY_MODE=oidc
APP_OIDC_ISSUER=https://identity.example
APP_OIDC_AUDIENCE=workspace-agent
APP_OIDC_JWKS_URI=https://identity.example/.well-known/jwks.json
```

Do not infer issuer or audience from the token. Cache keys with a bounded TTL and refresh once on an unknown `kid`.

- [ ] **Step 4: Implement mapping and provider selection**

Keep `MockIdentityProvider` only when mode is `mock` and environment is non-production. Production startup with mock mode must fail.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_oidc.py tests/test_auth.py tests/test_api.py -q
git add pyproject.toml uv.lock app/auth app/config.py tests/test_oidc.py tests/test_auth.py tests/test_api.py
git commit -m "feat(auth): add trusted oidc identity"
```

---

### Task 4: Add expiring Space membership projections

**Files:**
- Create: `app/auth/membership.py`
- Create: `app/auth/space_client.py`
- Modify: `app/auth/access.py`
- Modify: `app/bootstrap.py`
- Create: `tests/test_membership_projection.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- `SpaceMembershipAuthority.fetch_membership(subject, workspace_external_id) -> AuthorityMembership | None`.
- `MembershipProjectionService.require_current_membership(identity, workspace_id, sensitivity) -> WorkspaceMembership`.
- Projection stores `source_version`, `expires_at`, `refreshed_at`, and role.

- [ ] **Step 1: Write freshness and revocation tests**

Cover fresh hit without remote request, expiry refresh, newer source version, removed member, authority outage on stale projection, authority outage on fresh projection, role downgrade, and concurrent refresh collapse.

- [ ] **Step 2: Implement authority adapter and projection policy**

Use bounded connect/read timeouts and structured errors. A fresh read projection may be used until expiry. A write or sensitive content read with an expired projection must refresh or fail closed.

- [ ] **Step 3: Make all resource checks creator-private**

Every Session-derived query must include `workspace_id`, `created_by`, and current membership in one repository operation where possible. Add explicit checks for Session detail/delete, Turn create/cancel, messages, attachments, files, Skills attached to a Session, and SSE.

- [ ] **Step 4: Run the authorization matrix**

```bash
uv run pytest tests/test_membership_projection.py tests/test_auth.py tests/test_sessions.py tests/test_attachments.py tests/test_api.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/auth/membership.py app/auth/space_client.py app/auth/access.py app/bootstrap.py tests/test_membership_projection.py tests/test_auth.py
git commit -m "feat(auth): project workspace memberships"
```

---

### Task 5: Move Turn transitions, idempotency, and event order into PostgreSQL

**Files:**
- Create: `app/turns/state_machine.py`
- Create: `app/turns/repository.py`
- Modify: `app/turns/service.py`
- Create: `tests/test_turn_state_machine.py`
- Create: `tests/test_turn_repository.py`
- Modify: `tests/test_turns.py`
- Modify: `tests/integration/test_postgres_authority.py`

**Interfaces:**
- `TurnRepository.create_queued(session_id, client_request_id, request_payload) -> CreatedTurn`.
- `TurnRepository.transition(turn_id, expected_statuses, next_status, metadata) -> TurnRecord` uses compare-and-set SQL.
- `TurnRepository.append_event(turn_id, event_type, role, payload) -> TurnEventRecord` assigns a server sequence.
- `TurnStateMachine.require_transition(current, next)` is the single application transition policy.

- [ ] **Step 1: Add property and concurrency tests**

Use Hypothesis to assert terminal states cannot transition back to active states, `running` requires an execution nonce/attempt, and only `finalizing` can become `completed`. In PostgreSQL, run two connections creating the same request ID and two different active Turns for one Session.

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_turn_state_machine.py tests/test_turn_repository.py -q
```

- [ ] **Step 3: Implement transaction boundaries**

The create transaction must insert the Turn and first user event atomically. Duplicate `(session_id, client_request_id)` returns the original Turn without appending another user event. Server sequence is monotonic per Turn and never accepted from runtime payloads.

- [ ] **Step 4: Adapt local execution**

The Phase 0 dispatcher remains local, but it calls repository transitions instead of mutating ORM rows ad hoc. Map current success/failure/cancel paths into the expanded state machine without auto-retrying a Turn that reached `running`.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_turn_state_machine.py tests/test_turn_repository.py tests/test_turns.py -q
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_postgres_authority.py -q
git add app/turns/state_machine.py app/turns/repository.py app/turns/service.py tests/test_turn_state_machine.py tests/test_turn_repository.py tests/test_turns.py tests/integration/test_postgres_authority.py
git commit -m "feat(turns): persist queue and state transitions"
```

---

### Task 6: Replace in-process SSE correctness with durable history and PG wake-ups

**Files:**
- Create: `app/turns/notifications.py`
- Create: `app/turns/sse_store.py`
- Modify: `app/turns/sse.py`
- Modify: `app/turns/broker.py`
- Modify: `app/bootstrap.py`
- Create: `tests/integration/test_postgres_sse.py`
- Modify: `tests/test_sse.py`

**Interfaces:**
- `TurnNotifier.notify(turn_id, sequence) -> None`.
- `TurnEventStream.iter_events(turn_id, after_sequence, heartbeat_seconds) -> AsyncIterator[TurnEventRecord]`.
- PG payload contains only a stable Turn ID and sequence, never Prompt or event body.

- [ ] **Step 1: Add reconnect and cross-instance tests**

Use two database engines to represent two API replicas. Create the SSE iterator on one and append/notify on the other. Cover missed notifications, duplicate notifications, reconnect with `Last-Event-ID`, heartbeat, terminal close, and authorization revocation during reconnect.

- [ ] **Step 2: Implement history-first streaming**

Algorithm:

1. Query durable events after the client sequence.
2. Yield in server sequence order.
3. Listen for notification or wait for bounded short-poll timeout.
4. Query durable history again; never trust notification payload as content.
5. Close only after a persisted terminal event has been yielded.

- [ ] **Step 3: Keep in-process notifier for SQLite tests only**

Rename/document `EventBroker` as a development notifier. Production startup with SQLite or in-process notification must fail.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_sse.py -q
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' \
  uv run pytest tests/integration/test_postgres_sse.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/turns/notifications.py app/turns/sse_store.py app/turns/sse.py app/turns/broker.py app/bootstrap.py tests/test_sse.py tests/integration/test_postgres_sse.py
git commit -m "feat(sse): stream durable postgres events"
```

---

### Task 7: Prove multi-replica behavior and record the Gate

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/operations/runtime-v2-verification-ledger.md`
- Create: `scripts/verify-phase-1.sh`

- [ ] **Step 1: Add one reproducible verification script**

The script starts PostgreSQL, migrates a fresh database, starts two API processes on different ports with `APP_RUNTIME_MODE=execution_disabled` using one database, and runs an API scenario with one OIDC test issuer. It must clean up only resources it created.

- [ ] **Step 2: Verify concurrency and privacy**

The scenario must prove:

- duplicate request IDs across replicas create one Turn;
- two new Turns for one Session cannot both be active;
- different Sessions can queue independently without an API process executing either Turn;
- a same-Workspace non-owner receives 404/403 according to the documented non-enumeration policy for every private resource;
- an expired removed membership loses access;
- an SSE client on replica A receives an event written through replica B by the controlled test event writer.

- [ ] **Step 3: Run all checks**

```bash
uv run ruff check app tests
uv run pytest -q
bash scripts/verify-phase-1.sh
```

- [ ] **Step 4: Append exact Gate evidence**

Record database revision, PostgreSQL version, commands, exact test results, replica ports, and known exceptions.

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md docs/operations/runtime-v2-verification-ledger.md scripts/verify-phase-1.sh
git commit -m "docs(control-plane): record phase one gate"
```

## Phase Gate

- Production profile cannot start with SQLite or mock identity.
- OIDC signature/issuer/audience/subject and JWKS rotation tests pass.
- Expired membership projections fail closed.
- All private resource queries require both current membership and creator ownership.
- PostgreSQL enforces request idempotency and one active Turn per Session across replicas.
- SSE content is replayed from durable PostgreSQL history; notifications are optional wake-ups.
- Single-instance development execution still works through the explicit dispatcher; multi-replica rehearsal does not execute local SDK/Memory work.
- No Kubernetes Runner, S3 migration, Redis, or production credential path has been enabled.

Do not start Phase 2 until a development Kubernetes cluster version, CSI driver version, RWOP support, VolumeAttachment visibility, and hard-fence procedure are documented in the verification ledger.
