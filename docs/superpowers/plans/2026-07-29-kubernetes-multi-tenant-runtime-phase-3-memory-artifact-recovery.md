# Phase 3 Memory, Artifact, and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make attachments, immutable Config/Skill bundles, Artifacts, Personal Auto Memory, and recovery metadata durable across Runner/Node replacement without sharing writable storage between Sessions.

**Architecture:** Store large immutable payloads in S3-compatible object storage and keep authoritative owner/version/checksum pointers in PostgreSQL. Supervisor materializes one Personal Memory bundle per Turn, commits it through lease plus compare-and-swap after Agent cleanup, publishes output Artifacts, records tool effects, and only then completes finalization. Session workdir/transcript stays on the RWOP PVC.

**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL 16, boto3 via bounded worker threads, S3/MinIO, CSI RWOP and VolumeSnapshot, ZIP bundle codec, pytest, Hypothesis, Playwright.

## Global Constraints

- PostgreSQL owns metadata, leases, versions, checksums, and current pointers; S3 owns immutable object bytes.
- S3 object keys are server-generated. Browser, Agent, Prompt, Skill, and MCP cannot provide trusted bucket/key/owner fields.
- Personal Memory scope is exactly a server-derived opaque hash of `(user_id, workspace_id)`.
- Only one memory-active Turn per scope. Waiting is represented by `waiting_for_memory`, not a generic queued state.
- Claude Code continues to read/write Markdown through `autoMemoryDirectory=/memory`; the platform does not parse facts or perform vector retrieval/merge.
- Interrupted Agent/Pod work never advances the Memory pointer.
- Memory upload followed by unknown PG CAS result is resolved by reading the pointer/hash, not blindly retrying.
- Config/Knowledge/Skill bundles are immutable, content-addressed, checksum-verified, and mounted/read as read-only.
- Session workdir and transcript remain on the Session PVC. Do not introduce a custom SDK SessionStore.
- Non-idempotent write tools remain disabled. Ledger support does not authorize them.
- A Turn cannot leave `finalizing` until required transcript, process cleanup, Artifact, and Memory decisions are durable.
- Session deletion tombstones access before Runner fencing or storage deletion; Personal Memory is not deleted with one Session.

---

## File Structure

### New files

- `app/db/alembic/versions/rev_0007_memory_artifacts_recovery.py` — object, Artifact, Memory, tool-operation, restore, and tombstone records.
- `app/storage/objects.py` — object-store port, immutable put/head/get/delete/version operations.
- `app/storage/s3.py` — S3 adapter with checksum, timeout, and bounded concurrency.
- `app/storage/local.py` — local development adapter with identical ownership semantics.
- `app/bundles/codec.py` — deterministic safe ZIP pack/unpack and limits.
- `app/bundles/config.py` — content-addressed ConfigBundle builder/manifest.
- `app/bundles/service.py` — publish, resolve, validate, and materialize bundles.
- `app/artifacts/service.py` — creator-private Artifact metadata and upload/download flows.
- `app/artifacts/routes.py` — Session/Turn-scoped Artifact API.
- `app/memory/repository.py` — lease, version, CAS, and warning persistence.
- `app/memory/bundles.py` — Memory bundle validation/materialization.
- `app/turns/tool_ledger.py` — effect policy and operation records.
- `app/turns/finalizer.py` — ordered, resumable finalization protocol.
- `app/recovery/service.py` — restore manifest, deletion ledger, and recovery states.
- `app/recovery/routes.py` — creator-visible recovery/status API.
- `tests/test_object_store.py`, `tests/test_bundle_codec.py`, `tests/test_config_bundles.py`, `tests/test_artifacts.py`, `tests/test_memory_repository.py`, `tests/test_memory_bundles.py`, `tests/test_tool_ledger.py`, `tests/test_finalizer.py`, `tests/test_recovery.py`.
- `tests/integration/test_s3_durability.py`, `tests/integration/test_cross_node_resume.py`.
- `scripts/verify-phase-3.sh`.

### Modified files

- `pyproject.toml`, `uv.lock` — S3 dependency.
- `app/config.py` — object store, bucket, size, retention, snapshot, and finalization settings.
- `app/db/models.py` — new durable records and ownership constraints.
- `app/attachments/service.py` — object-store backend with local compatibility mode.
- `app/sessions/snapshot.py`, `app/workspaces/materializer.py` — immutable ConfigBundle pointer/materialization.
- `app/gateway/routes.py`, `app/gateway/service.py` — object and finalization operations derived from Runner lease.
- `app/runner/supervisor.py`, `app/runner/protocol.py` — Memory/Artifact/finalization lifecycle.
- `app/auth/access.py`, `app/api/routes.py`, `app/api/dependencies.py`, `app/bootstrap.py` — creator-private object APIs.
- `app/web/static/app.js`, `app/web/static/styles.css` — waiting, warning, unknown outcome, recovery, and Artifact UI.
- `tests/test_attachments.py`, `tests/test_sessions.py`, `tests/test_gateway.py`, `tests/test_api.py`, `tests/browser/test_workbench.py`, `tests/test_migrations.py`.
- `compose.integration.yaml`, `.env.example`, `README.md`, `docs/operations/runtime-v2-verification-ledger.md`.

---

### Task 1: Add immutable object metadata and storage adapters

**Files:**
- Create: `app/db/alembic/versions/rev_0007_memory_artifacts_recovery.py`
- Modify: `app/db/models.py`
- Create: `app/storage/objects.py`
- Create: `app/storage/s3.py`
- Create: `app/storage/local.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/test_object_store.py`
- Create: `tests/integration/test_s3_durability.py`
- Modify: `compose.integration.yaml`
- Modify: `tests/test_migrations.py`

**Interfaces:**
- `ObjectStore.put_immutable(namespace, digest, stream, size, content_type) -> ObjectVersion`.
- `ObjectStore.head(version)`, `open(version)`, and `delete_version(version)`.
- `ObjectVersion` includes bucket, server-generated key, version ID/etag, SHA-256, size, and encryption metadata.
- No overwrite API exists.

- [ ] **Step 1: Add object contract tests**

Run the same contract suite against local and MinIO adapters: immutable same-digest dedupe, different-content conflict, checksum mismatch, bounded read, timeout, missing object, version metadata, and cleanup.

- [ ] **Step 2: Add schema assertions**

Create object metadata/reference records with owner, Workspace, Session/Turn scope, retention, created time, checksum, size, content type, and tombstone state. Do not store presigned URLs.

- [ ] **Step 3: Implement S3 adapter safely**

Use `boto3` inside `anyio.to_thread.run_sync` with explicit connect/read timeouts and a bounded limiter. Enable server-side encryption configuration. Stream to a bounded temporary file while hashing; do not buffer arbitrary uploads in memory.

- [ ] **Step 4: Verify local and MinIO**

```bash
uv run pytest tests/test_object_store.py tests/test_migrations.py -q
docker compose -f compose.integration.yaml up -d postgres minio
TEST_S3_ENDPOINT='http://127.0.0.1:59000' uv run pytest tests/integration/test_s3_durability.py -q
docker compose -f compose.integration.yaml down
```

- [ ] **Step 5: Commit**

```bash
git add app/db/alembic/versions/rev_0007_memory_artifacts_recovery.py app/db/models.py app/storage pyproject.toml uv.lock tests/test_object_store.py tests/integration/test_s3_durability.py compose.integration.yaml tests/test_migrations.py
git commit -m "feat(storage): add immutable object authority"
```

---

### Task 2: Build a safe deterministic bundle codec and immutable ConfigBundle

**Files:**
- Create: `app/bundles/codec.py`
- Create: `app/bundles/config.py`
- Create: `app/bundles/service.py`
- Modify: `app/sessions/snapshot.py`
- Modify: `app/workspaces/materializer.py`
- Create: `tests/test_bundle_codec.py`
- Create: `tests/test_config_bundles.py`

**Interfaces:**
- `BundleCodec.pack(files: Mapping[PurePosixPath, bytes]) -> EncodedBundle`.
- `BundleCodec.unpack(stream, destination, limits) -> BundleManifest`.
- `ConfigBundleService.publish(workspace_id, source, actor) -> ConfigSnapshot`.
- Manifest contains schema, Workspace, paths, per-file digest/size/mode, total digest, and creator.

- [ ] **Step 1: Add adversarial bundle tests**

Reject absolute paths, `..`, duplicate normalized names, NUL, backslash ambiguity, symlink, hardlink, device/socket, encrypted ZIP, decompression ratio/size overflow, file count overflow, and case-fold collisions. Accept only regular files.

- [ ] **Step 2: Add deterministic encoding tests**

Identical logical files in different input orders produce identical bytes/hash. Normalize timestamps, mode, path separators, and manifest ordering.

- [ ] **Step 3: Implement ConfigBundle contents and policy**

Allow only:

```text
CLAUDE.md
.claude/rules/**
.claude/skills/**
sdk-settings.json
mcp-manifest.json
model-policy.json
tool-policy.json
manifest.json
```

Materialize outside Agent-writable Session directories and bind/mount as read-only. `setting_sources=["project"]` must refer only to platform-generated sources. Reject runtime hash mismatch before Runner becomes Ready.

- [ ] **Step 4: Verify**

```bash
uv run pytest tests/test_bundle_codec.py tests/test_config_bundles.py tests/test_sessions.py -q
```

- [ ] **Step 5: Commit**

```bash
git add app/bundles app/sessions/snapshot.py app/workspaces/materializer.py tests/test_bundle_codec.py tests/test_config_bundles.py
git commit -m "feat(bundles): publish immutable workspace config"
```

---

### Task 3: Move attachments and output Artifacts to creator-private object storage

**Files:**
- Modify: `app/attachments/service.py`
- Create: `app/artifacts/service.py`
- Create: `app/artifacts/routes.py`
- Modify: `app/auth/access.py`
- Modify: `app/api/routes.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/bootstrap.py`
- Modify: `tests/test_attachments.py`
- Create: `tests/test_artifacts.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- `AttachmentService.create_upload(owner, session, stream, metadata) -> AttachmentRecord`.
- `AttachmentService.open_for_principal(principal, workspace_id, session_id, attachment_id) -> ObjectStream`.
- `AttachmentService.open_for_runner_lease(runner_lease, attachment_id) -> ObjectStream`.
- `ArtifactService.publish_from_runner(runner_lease, relative_output_path, metadata) -> ArtifactRecord`.
- `ArtifactService.open_for_owner(identity, artifact_id) -> ObjectStream`.
- Runner publication accepts only a path beneath `/session/outputs` and derives Turn/Session/owner from lease.
- Business/API paths must not expose or call a raw `get(id)` that returns attachment metadata or bytes before principal/lease authorization.

- [ ] **Step 1: Add creator-private API tests**

Cover same-Workspace non-owner, admin, guessed ID, cross-Workspace, deleted membership, stale membership, range download, checksum, content-disposition sanitization, upload abort, object/database failure ordering, direct service use without principal, and Runner lease bound to a different Session/Turn.

- [ ] **Step 2: Implement upload commit protocol**

Write immutable object, verify head/checksum, then commit metadata. If DB commit fails, enqueue the unreferenced object for garbage collection. Never return an object key or privileged S3 URL to Agent.

- [ ] **Step 3: Implement Artifact publication**

Supervisor enumerates allowed regular output files after Agent stop. Reject symlinks, path escapes, devices, sockets, hardlinks, excessive size/count, and changed-during-read files. Record immutable checksums and owner ACL metadata.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_attachments.py tests/test_artifacts.py tests/test_api.py -q
git add app/attachments/service.py app/artifacts app/auth/access.py app/api/routes.py app/api/dependencies.py app/bootstrap.py tests/test_attachments.py tests/test_artifacts.py tests/test_api.py
git commit -m "feat(artifacts): persist private attachments and outputs"
```

---

### Task 4: Implement Personal Memory lease, materialization, and CAS

**Files:**
- Create: `app/memory/repository.py`
- Create: `app/memory/bundles.py`
- Modify: `app/memory/scopes.py`
- Modify: `app/memory/locks.py`
- Modify: `app/gateway/service.py`
- Modify: `app/runner/supervisor.py`
- Create: `tests/test_memory_repository.py`
- Create: `tests/test_memory_bundles.py`
- Modify: `tests/test_memory_locks.py`
- Modify: `tests/test_gateway.py`

**Interfaces:**
- `MemoryRepository.acquire(scope_id, turn_id, ttl) -> MemoryLease | MemoryBusy`.
- `MemoryRepository.renew(lease) -> MemoryLease`.
- `MemoryRepository.commit(lease, expected_version, object_version) -> MemoryVersion`.
- `MemoryRepository.release(lease, outcome) -> None`.
- `MemoryBundleManager.materialize(lease, directory)` and `build_candidate(directory)`.

- [ ] **Step 1: Add database race and lease tests**

Use two Sessions under the same user+Workspace and concurrent PostgreSQL connections. Exactly one acquires; the other Turn becomes `waiting_for_memory`. Cover renewal, expiry, stolen generation, stale release, CAS conflict, upload success plus lost DB response, and owner/Workspace isolation.

- [ ] **Step 2: Add Memory bundle safety tests**

Allow regular Markdown/text files only, with explicit per-file/total/count limits. Reject symlinks, hardlinks, binary files, path escapes, sockets/devices, invalid UTF-8 where required, and mutations during packaging.

- [ ] **Step 3: Implement Supervisor ordering**

1. Acquire lease before claim becomes running.
2. Ask Execution Gateway to stream the current immutable bundle from S3 into `emptyDir:/memory`; Runner never receives S3 credentials or direct S3 network access.
3. Provide platform settings with `autoMemoryDirectory=/memory`.
4. After SDK exit, stop Agent/Bash/Skill/Subagent processes.
5. Validate/package the candidate and stream it to Execution Gateway for immutable S3 upload.
6. CAS expected version plus lease generation.
7. Release lease and persist warning/outcome.

An interrupted Agent skips steps 5-6 and preserves the previous pointer.

- [ ] **Step 4: Remove in-process correctness dependency**

`MemoryScopeLockRegistry` may remain as a SQLite development optimization but production correctness must rely solely on PostgreSQL lease/CAS.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_memory_repository.py tests/test_memory_bundles.py tests/test_memory_locks.py tests/test_gateway.py -q
git add app/memory app/gateway/service.py app/runner/supervisor.py tests/test_memory_repository.py tests/test_memory_bundles.py tests/test_memory_locks.py tests/test_gateway.py
git commit -m "feat(memory): persist versioned auto memory"
```

---

### Task 5: Add Tool Operation Ledger and unknown-outcome policy

**Files:**
- Create: `app/turns/tool_ledger.py`
- Modify: `app/gateway/routes.py`
- Modify: `app/gateway/service.py`
- Create: `tests/test_tool_ledger.py`
- Modify: `tests/test_gateway.py`

**Interfaces:**
- `ToolEffectClass = read_only | idempotent_write | non_idempotent_write`.
- `ToolOperationService.begin(lease, call, policy) -> ToolOperation`.
- `complete`, `fail_known`, and `mark_unknown` update by operation ID and current lease.
- Tool policy declares retry, approval, reconciliation, and idempotency-key contract.
- Status transitions are `prepared -> dispatched -> succeeded | failed_known | outcome_unknown`, with `outcome_unknown -> reconciled` only after a supported reconciliation query.
- Records include `turn_attempt_id`, lease generation, authorization policy version, `dispatched_at`, downstream operation ID, and reconciliation status.

- [ ] **Step 1: Add effect policy tests**

Read-only transport retries are bounded. Idempotent writes retry only with a validated downstream idempotency contract. Non-idempotent writes are rejected in Phase 3. A sent request with lost response becomes `outcome_unknown` and is never resent. A reconciliation query may close an unknown operation but must never resend the original write as a probe.

- [ ] **Step 2: Implement append/update ledger records**

Persist `prepared` before network dispatch, then persist dispatch/terminal/reconciliation state with request/response digests and downstream operation IDs, not secrets or arbitrary bodies. Tool call IDs are unique within a Turn. Old generations cannot dispatch, close, or reconcile operations. Authorization overlay is evaluated again immediately before dispatch and its policy version is stored.

- [ ] **Step 3: Connect Turn state**

If a write result is unknown, transition the Turn to `outcome_unknown`; do not continue normal finalization or make Runner Idle until the Agent is stopped and ledger state is durable.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_tool_ledger.py tests/test_gateway.py -q
git add app/turns/tool_ledger.py app/gateway/routes.py app/gateway/service.py tests/test_tool_ledger.py tests/test_gateway.py
git commit -m "feat(tools): record operation outcomes"
```

---

### Task 6: Make finalization ordered, resumable, and explicit

**Files:**
- Create: `app/turns/finalizer.py`
- Modify: `app/runner/supervisor.py`
- Modify: `app/gateway/service.py`
- Create: `tests/test_finalizer.py`

**Interfaces:**
- Finalization stages: `agent_stopped`, `transcript_confirmed`, `artifacts_published`, `memory_decided`, `ledger_closed`, `events_flushed`, `complete`.
- `TurnFinalizer.resume(turn_id, runner_lease) -> FinalizationResult`.
- Every stage is idempotent by immutable object/checksum or database CAS.

- [ ] **Step 1: Add crash-at-every-stage tests**

After each durable stage, simulate Supervisor/Gateway restart and resume. No duplicate Artifact, Memory version, terminal event, or ledger closure may appear.

- [ ] **Step 2: Implement result policy**

- User answer complete + Memory commit failure: `completed` with `memory_commit_failed` warning.
- Transcript/PVC cannot be confirmed: `recovery_required`.
- External write result unknown: `outcome_unknown`.
- Required Artifact publish failure: `recovery_required`; optional Artifact failure may be a warning according to declared output policy.

- [ ] **Step 3: Anchor idle TTL after finalization**

Set `idle_since` in the same transaction that completes finalization. Do not calculate idle from last token, browser connection, preliminary result, or Agent exit.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_finalizer.py tests/test_gateway.py tests/test_runner_protocol.py -q
git add app/turns/finalizer.py app/runner/supervisor.py app/gateway/service.py tests/test_finalizer.py
git commit -m "feat(turns): make finalization resumable"
```

---

### Task 7: Implement restore manifests, cross-node resume, and deletion tombstones

**Files:**
- Create: `app/recovery/service.py`
- Create: `app/recovery/routes.py`
- Modify: `app/auth/access.py`
- Modify: `app/control/controller.py`
- Create: `tests/test_recovery.py`
- Create: `tests/integration/test_cross_node_resume.py`

**Interfaces:**
- `RestoreManifest(pg_lsn, object_versions, pvc_snapshot_id, encryption_key_version, created_at)`.
- `RecoveryService.tombstone_session(identity, session_id) -> DeletionOperation`.
- `RecoveryService.validate_restore(manifest) -> RestoreValidation`.
- Deletion ledger is reapplied after restore before data becomes readable.

- [ ] **Step 1: Add deletion and restore tests**

Tombstone immediately blocks API/Gateway, waits for hard fence, applies retention, deletes PVC/object references, preserves Personal Memory, and survives backup restore without exposing deleted Session content.

- [ ] **Step 2: Add cross-node resume test**

Run a Session on node A, complete finalization, terminate/fence/detach, schedule a new generation on node B, mount the same PVC at identical absolute paths, resume `claude_session_id`, and read the prior transcript/workdir. The test must fail if detach evidence is skipped.

- [ ] **Step 3: Implement restore validation**

Before marking a restored Session available, verify all referenced Config/Memory/Attachment/Artifact object versions and PVC snapshot. Missing data yields `recovery_required` with a non-secret diagnostic.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_recovery.py -q
RUN_CLUSTER_RECOVERY_TESTS=1 uv run pytest tests/integration/test_cross_node_resume.py -q
git add app/recovery app/auth/access.py app/control/controller.py tests/test_recovery.py tests/integration/test_cross_node_resume.py
git commit -m "feat(recovery): restore and delete session state"
```

---

### Task 8: Expose honest waiting, warning, unknown, recovery, and Artifact UI

**Files:**
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/styles.css`
- Modify: `tests/browser/test_workbench.py`
- Modify: `tests/test_web_page.py`

- [ ] **Step 1: Add browser tests**

Verify visible states for `waiting_for_memory`, `finalizing`, completed with Memory warning, `outcome_unknown`, `recovery_required`, downloadable Artifact, and creator-private denial. Unknown outcome must display known operations and disable blind one-click retry.

- [ ] **Step 2: Implement state-specific copy and actions**

Do not collapse the states into generic error/interrupted. Reconciliation is shown only when the tool declares it. Recovery details expose trace/reference IDs, not credentials or object keys.

- [ ] **Step 3: Verify real geometry and interaction**

```bash
uv run pytest tests/test_web_page.py tests/browser/test_workbench.py -q
```

Use a real browser viewport to confirm messages, Artifact controls, composer, and timeline remain reachable without page-width/height reflow.

- [ ] **Step 4: Commit**

```bash
git add app/web/static/app.js app/web/static/styles.css tests/browser/test_workbench.py tests/test_web_page.py
git commit -m "feat(ui): expose durable turn outcomes"
```

---

### Task 9: Run durability and recovery Gate

**Files:**
- Create: `scripts/verify-phase-3.sh`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/operations/runtime-v2-verification-ledger.md`

- [ ] **Step 1: Automate the failure matrix**

Cover S3 unavailable, upload succeeds/DB response lost, PG unavailable, Memory CAS conflict, Agent crash, Supervisor crash at every finalization stage, old generation event, PVC attach timeout, node move, object missing, deletion restore, and `outcome_unknown` UI.

- [ ] **Step 2: Run full checks**

```bash
uv run ruff check app tests
uv run pytest -q
bash scripts/verify-phase-3.sh
```

- [ ] **Step 3: Record evidence**

Record PG revision/LSN, S3/MinIO version, bucket versioning/encryption settings, CSI snapshot/restore IDs, cross-node results, exact fault outcomes, browser evidence, and reviewer.

- [ ] **Step 4: Commit**

```bash
git add scripts/verify-phase-3.sh README.md .env.example docs/operations/runtime-v2-verification-ledger.md
git commit -m "test(durability): record phase three gate"
```

## Phase Gate

- Attachments, Artifacts, Config/Skill, and Memory objects are immutable, checksummed, owner-scoped, and version-addressed.
- Same memory scope serializes through PostgreSQL lease/CAS; interruption preserves the previous version.
- Config/Skill/Knowledge material is read-only and hash-verified.
- Finalization resumes without duplicate objects/events and sets idle time only after completion.
- Unknown external writes are visible and never blindly replayed.
- Cross-node PVC resume succeeds only after hard fence/detach.
- Backup restore validates object/PVC references and reapplies deletion tombstones.
- UI distinguishes waiting, warnings, unknown outcomes, and recovery states.

Do not proceed to real credentials or users until Phase 4 security and operational Gates pass.
