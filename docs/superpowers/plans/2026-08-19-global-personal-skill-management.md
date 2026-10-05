# Global and Personal Skill Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a full-screen Agent Host Skill manager with platform-global Skills, personal-Workspace Skills, immutable versions, filesystem Artifact persistence for local/single-node UAT, and immutable Skill selection for new Sessions.

**Architecture:** Keep PostgreSQL/SQLite as the control plane and introduce a `SkillArtifactStore` boundary for immutable normalized Skill bundles. The current release implements a content-addressed filesystem store under `APP_DATA_DIR`; global/personal resolution pins `skill_version_id` and hashes into Session snapshot schema v3 before materializing ordinary files for the Claude Agent SDK.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, SQLite/PostgreSQL, Pydantic Settings, vanilla JavaScript/CSS, Node test runner, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-19-global-personal-skill-management-design.md`

## Global Constraints

- Product scope is exactly `global` and personal-Workspace Skills; no team Skill UI, marketplace, sharing, Git sync, cross-Workspace copy, or browser editor.
- Global Skills default enabled per personal Workspace; missing setting rows mean `enabled=true`.
- New personal Skills default enabled; replacement preserves the existing enabled state.
- All mutations affect new Sessions only; existing Session snapshot and materialized files are immutable.
- Local and single-node UAT must work with SQLite plus persistent filesystem storage; this feature must not introduce a PostgreSQL startup gate.
- The current release implements only `SKILL_ARTIFACT_BACKEND=filesystem` and adds no object-store SDK dependency.
- Artifact limits remain 10 MiB per supporting file, 50 MiB uncompressed total, and 200 supporting files.
- Artifact writes use a temporary file, fsync, hash verification, and atomic replace; business code uses opaque Artifact Keys, never absolute paths.
- Authorization is checked before upload bodies are read; unauthorized resources remain fail-closed.
- Existing schema-v2 Sessions and their materialized Skills are not migrated or rematerialized.
- Preserve unrelated worktree state and follow the existing two-space/Python and Standard/Prettier JavaScript styles.

---

## File and Responsibility Map

### New files

- `app/skills/artifacts.py` — deterministic Bundle ZIP codec, Artifact DTOs, store protocol, and filesystem backend.
- `app/skills/access.py` — platform `skill_admin` authorization and bootstrap binding.
- `app/skills/migration.py` — idempotent legacy BLOB-to-Artifact/version migration and
  orphan Artifact garbage collection.
- `app/skills/admin_cli.py` — grant/revoke/list platform Skill administrators by existing user subject.
- `app/db/alembic/versions/rev_0009_global_personal_skills.py` — schema for scopes, versions, global settings, role bindings, and normalized-name serialization.
- `tests/test_skill_artifacts.py` — deterministic codec and filesystem safety tests.
- `tests/test_skill_repository.py` — versioned repository, catalog, setting, and conflict tests.
- `tests/test_skill_artifact_migration.py` — legacy migration and restart/idempotency tests.
- `tests/test_skill_admin_cli.py` — operator role command tests.

### Existing backend files

- `app/config.py` and `.env.example` — Artifact backend/root and bootstrap admin subjects.
- `app/db/models.py` — Skill scope/version/settings/platform role/name-lock ORM models.
- `app/api/dependencies.py` and `app/bootstrap.py` — construct and initialize store, access service, migrator, Skill service, and Session service.
- `app/auth/access.py` — personal-Workspace owner boundary.
- `app/skills/models.py` — managed Skill, immutable version, catalog, and resolved-bundle value objects.
- `app/skills/repository.py` — transactional scoped Skill/version/settings reads and writes.
- `app/skills/service.py` — personal/global workflows, Artifact publishing, effective resolution, and one-time bootstrap.
- `app/skills/schemas.py` and `app/skills/routes.py` — grouped catalog and user/admin HTTP contracts.
- `app/sessions/snapshot.py`, `app/sessions/service.py`, and `app/workspaces/materializer.py` — schema-v3 pinning and exact Artifact materialization.
- `app/api/schemas.py` and `app/api/routes.py` — expose `can_manage_global_skills` and effective Skill counts.
- `README.md` — single-node persistence, admin bootstrap, upload behavior, and future backend migration.

### Existing frontend files

- `app/web/templates/index.html` — replace the Skill dialog/editor with a full-screen management view.
- `app/web/static/skill-manager.js` — grouped catalog controller, scope-specific import, toggles, detail, conflicts, and stale-response guards.
- `app/web/static/app.js` — chat/Skill view navigation and capability wiring.
- `app/web/static/app.css` — full-screen management layout and responsive behavior.
- `tests/js/test_skill_manager.cjs`, `tests/test_web_page.py`, and `tests/browser/test_workbench.py` — controller, markup, and end-to-end acceptance.

---

### Task 1: Deterministic Skill Artifact Codec and Filesystem Store

**Files:**
- Create: `app/skills/artifacts.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Create: `tests/test_skill_artifacts.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `SkillBundle`, `SkillBundleLimits`, `build_bundle()`, and `load_bundle_from_archive()`.
- Produces:
  - `SkillArtifact(artifact_key, archive_bytes, artifact_sha256, bundle_hash, manifest_json, size_bytes)`
  - `build_skill_artifact(bundle: SkillBundle) -> SkillArtifact`
  - `load_skill_artifact(raw: bytes, *, expected_artifact_sha256: str, expected_bundle_hash: str, limits: SkillBundleLimits) -> SkillBundle`
  - `SkillArtifactStore.put(artifact: SkillArtifact) -> None`
  - `SkillArtifactStore.read(artifact_key: str, *, maximum_size: int) -> bytes`
  - `SkillArtifactStore.exists(artifact_key: str) -> bool`
  - `SkillArtifactStore.delete(artifact_key: str) -> None`
  - `SkillArtifactStore.iter_objects() -> tuple[SkillArtifactObject, ...]`
  - `FilesystemSkillArtifactStore(root: Path)`

- [ ] **Step 1: Add RED codec and filesystem tests**

Create `tests/test_skill_artifacts.py` with concrete round-trip, deterministic, corruption, traversal, and atomicity cases:

~~~python
from pathlib import Path

import pytest


def test_artifact_round_trip_is_deterministic(tmp_path: Path) -> None:
    from app.skills.artifacts import (
        FilesystemSkillArtifactStore,
        build_skill_artifact,
        load_skill_artifact,
    )
    from app.skills.bundle import SkillBundleLimits, build_bundle

    bundle = build_bundle(
        b"---\nname: review\ndescription: Review safely\n---\nRun review.\n",
        [("references/policy.bin", b"\x00\xffpolicy")],
    )
    first = build_skill_artifact(bundle)
    second = build_skill_artifact(bundle)
    assert first.archive_bytes == second.archive_bytes
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.artifact_key == (
        f"skills/sha256/{bundle.bundle_hash[7:9]}/{bundle.bundle_hash[7:]}.zip"
    )

    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    store.put(first)
    raw = store.read(first.artifact_key, maximum_size=50 * 1024 * 1024)
    assert load_skill_artifact(
        raw,
        expected_artifact_sha256=first.artifact_sha256,
        expected_bundle_hash=bundle.bundle_hash,
        limits=SkillBundleLimits(),
    ) == bundle
~~~

Add exact tests named:

- `test_artifact_rejects_wrong_archive_sha256`;
- `test_artifact_rejects_wrong_bundle_hash`;
- `test_filesystem_store_rejects_absolute_parent_and_backslash_keys`;
- `test_filesystem_store_detects_existing_corrupt_object`;
- `test_filesystem_store_leaves_no_visible_partial_file_when_replace_fails`;
- `test_filesystem_store_refuses_symlink_root_and_symlink_parent`;
- `test_filesystem_store_bounds_reads_before_allocating_excess`.

Use monkeypatch on `os.replace` in the partial-file test and assert the final Key is absent and the temporary file is removed.

- [ ] **Step 2: Run Artifact tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_skill_artifacts.py -q
~~~

Expected: collection fails because `app.skills.artifacts` does not exist.

- [ ] **Step 3: Implement the deterministic codec**

In `app/skills/artifacts.py` define fixed ZIP metadata and sorted entries:

~~~python
@dataclass(frozen=True)
class SkillArtifact:
    artifact_key: str
    archive_bytes: bytes
    artifact_sha256: str
    bundle_hash: str
    manifest_json: str
    size_bytes: int

@dataclass(frozen=True)
class SkillArtifactObject:
    artifact_key: str
    size_bytes: int
    modified_at: datetime


def build_skill_artifact(bundle: SkillBundle) -> SkillArtifact:
    entries = [("SKILL.md", bundle.content.encode("utf-8"))]
    entries.extend((item.path, item.content) for item in bundle.files)
    raw = _canonical_zip(tuple(sorted(entries)))
    digest = hashlib.sha256(raw).hexdigest()
    content_digest = bundle.bundle_hash.removeprefix("sha256:")
    return SkillArtifact(
        artifact_key=f"skills/sha256/{content_digest[:2]}/{content_digest}.zip",
        archive_bytes=raw,
        artifact_sha256=f"sha256:{digest}",
        bundle_hash=bundle.bundle_hash,
        manifest_json=_manifest_json(bundle),
        size_bytes=len(raw),
    )
~~~

Use `zipfile.ZIP_DEFLATED`, `compresslevel=9`, sorted POSIX paths, timestamp
`(1980, 1, 1, 0, 0, 0)`, regular-file mode `0o100644 << 16`, empty extra/comment,
and UTF-8 flag. `load_skill_artifact()` first verifies archive bytes, delegates archive
validation to `load_bundle_from_archive()`, then verifies the reconstructed Bundle Hash.
Translate missing/corrupt content to
`AppError("skill_artifact_corrupt", "Skill artifact integrity check failed.", 500)`.

- [ ] **Step 4: Implement the filesystem store**

Define a `Protocol` and filesystem implementation:

~~~python
class SkillArtifactStore(Protocol):
    def initialize(self) -> None:
        raise NotImplementedError
    def put(self, artifact: SkillArtifact) -> None:
        raise NotImplementedError
    def read(self, artifact_key: str, *, maximum_size: int) -> bytes:
        raise NotImplementedError
    def exists(self, artifact_key: str) -> bool:
        raise NotImplementedError
    def delete(self, artifact_key: str) -> None:
        raise NotImplementedError
    def iter_objects(self) -> tuple[SkillArtifactObject, ...]:
        raise NotImplementedError


class FilesystemSkillArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, artifact: SkillArtifact) -> None:
        target = self._target(artifact.artifact_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_existing(target, artifact)
            return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(artifact.archive_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
~~~

`_target()` accepts only `skills/sha256/[0-9a-f]{2}/[0-9a-f]{64}.zip`, resolves the
parent without following symlinks, and asserts it remains under `root`. `read()` checks
`stat().st_size <= maximum_size` before reading and rechecks length afterward.
`iter_objects()` returns only valid canonical Keys plus size and UTC modification time; it
ignores temporary files and rejects symlink entries.

The store is a synchronous low-level filesystem boundary. Async services and startup code
must call `put`, `read`, `iter_objects`, and `delete` through `asyncio.to_thread` so a
50 MiB Artifact cannot block the event loop.

- [ ] **Step 5: Add configuration and validation**

In `Settings` add:

~~~python
skill_artifact_backend: Literal["filesystem"] = Field(
    default="filesystem", validation_alias="SKILL_ARTIFACT_BACKEND"
)
skill_artifact_root: Path | None = Field(
    default=None, validation_alias="SKILL_ARTIFACT_ROOT"
)

@property
def resolved_skill_artifact_root(self) -> Path:
    return (self.skill_artifact_root or self.app_data_dir / "skill-artifacts").resolve()
~~~

Reject a configured root that resolves outside `APP_DATA_DIR`, equals `APP_DATA_DIR`,
or is under `sessions`, `memory`, or `workspaces`. Add the backend/root to
`redacted_summary()` and document defaults in `.env.example`.

Add `tests/test_config.py` assertions that default SQLite plus default filesystem works,
an explicit child root works, unsupported backends fail validation, and unsafe roots fail.

- [ ] **Step 6: Run focused tests**

Run:

~~~bash
uv run pytest tests/test_skill_artifacts.py tests/test_config.py -q
~~~

Expected: all tests pass.

- [ ] **Step 7: Commit**

~~~bash
git add app/skills/artifacts.py app/config.py .env.example \
  tests/test_skill_artifacts.py tests/test_config.py
git commit -m "feat: add deterministic skill artifact storage"
~~~

---

### Task 2: Skill Scope, Immutable Version, Setting, and Platform Role Schema

**Files:**
- Create: `app/db/alembic/versions/rev_0009_global_personal_skills.py`
- Modify: `app/db/models.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_database.py`

**Interfaces:**
- Consumes: Artifact metadata from Task 1.
- Produces ORM models `SkillVersionRecord`, `WorkspaceGlobalSkillSettingRecord`,
  `PlatformRoleBindingRecord`, and `SkillNameLockRecord` plus extended `SkillRecord`.

- [ ] **Step 1: Add RED fresh-schema and upgrade tests**

Extend `test_migrations_create_current_schema_on_fresh_database` to require:

~~~python
assert {
    "skill_versions",
    "workspace_global_skill_settings",
    "platform_role_bindings",
    "skill_name_locks",
} <= tables
assert migration_head == "0009"
assert skill_columns["workspace_id"]["nullable"] is True
assert {"scope", "normalized_name", "current_version_id"} <= skill_columns
~~~

Add `test_rev_0009_preserves_legacy_skill_bytes_for_startup_backfill`. Migrate a
revision-0008 SQLite database containing one `skills` row and one `skill_files` row, then
assert:

~~~python
row = (
    await connection.execute(
        text(
            "SELECT scope, normalized_name, current_version_id, content, bundle_hash "
            "FROM skills WHERE id='legacy-skill'"
        )
    )
).one()
assert row.scope == "workspace"
assert row.normalized_name == "review-changes"
assert row.current_version_id is None
assert row.content == legacy_skill_markdown
assert await connection.scalar(
    text("SELECT length(content_blob) FROM skill_files WHERE skill_id='legacy-skill'")
) == len(legacy_supporting_bytes)
~~~

Add database constraint tests for invalid scope/workspace combinations, invalid version
status, duplicate global active names, duplicate personal active names in the same
Workspace, duplicate version number/hash, and invalid platform role.

- [ ] **Step 2: Run migration tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_migrations.py tests/test_database.py -q
~~~

Expected: failures report missing revision 0009 and missing columns/tables.

- [ ] **Step 3: Add ORM models**

Extend `SkillRecord` with:

~~~python
scope: Mapped[str] = mapped_column(String(16), nullable=False, default="workspace")
workspace_id: Mapped[str | None] = mapped_column(
    String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
)
normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
current_version_id: Mapped[str | None] = mapped_column(
    String(36), ForeignKey("skill_versions.id", ondelete="RESTRICT"), nullable=True
)
created_by: Mapped[str | None] = mapped_column(
    String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
)
~~~

Add:

~~~python
class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
~~~

Model the settings primary key as `(workspace_id, skill_id)`, platform binding primary key
as `(user_id, role)`, and normalized-name lock primary key as `normalized_name`.

- [ ] **Step 4: Implement revision 0009**

The upgrade must perform this exact order:

1. Drop `uq_skills_workspace_active_name`.
2. Batch-alter `skills.workspace_id` and `skills.created_by` nullable and add `scope`,
   `normalized_name`, and nullable `current_version_id`.
3. Backfill `scope='workspace'` and `normalized_name=lower(name)`.
4. Create `skill_versions` without depending on `skills.current_version_id`.
5. Add the `skills.current_version_id -> skill_versions.id` FK in a second batch alter.
6. Create settings, platform roles, and name locks.
7. Add check constraints and partial unique indexes:

~~~sql
CREATE UNIQUE INDEX uq_skills_global_active_name
ON skills (normalized_name)
WHERE archived_at IS NULL AND scope = 'global';

CREATE UNIQUE INDEX uq_skills_workspace_active_name
ON skills (workspace_id, normalized_name)
WHERE archived_at IS NULL AND scope = 'workspace';
~~~

Use Alembic batch operations for SQLite. Downgrade must refuse if any global Skill exists,
then drop new tables/columns and restore the revision-0003 index.

Add checks equivalent to:

~~~sql
CHECK (
  (scope = 'global' AND workspace_id IS NULL AND enabled = TRUE)
  OR
  (scope = 'workspace' AND workspace_id IS NOT NULL)
)
~~~

- [ ] **Step 5: Run focused schema tests**

Run:

~~~bash
uv run pytest tests/test_migrations.py tests/test_database.py -q
~~~

Expected: all tests pass with migration head `0009` and no SQLite FK violations.

- [ ] **Step 6: Run optional PostgreSQL schema gate**

Run when `TEST_POSTGRES_URL` is available:

~~~bash
TEST_POSTGRES_URL="postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace" \
  uv run pytest tests/integration/test_postgres_authority.py -q
~~~

Expected: pass. If the disposable database is unavailable, record the gate as not run;
do not weaken SQLite coverage.

- [ ] **Step 7: Commit**

~~~bash
git add app/db/models.py \
  app/db/alembic/versions/rev_0009_global_personal_skills.py \
  tests/test_migrations.py tests/test_database.py
git commit -m "feat: add global and personal skill schema"
~~~

---

### Task 3: Version-Aware Skill Repository

**Files:**
- Modify: `app/skills/models.py`
- Rewrite: `app/skills/repository.py`
- Create: `tests/test_skill_repository.py`

**Interfaces:**
- Consumes: Task 1 Artifact store and Task 2 ORM models.
- Produces:
  - `SkillVersionRef`
  - `ManagedSkillSummary`
  - `StoredSkill`
  - `SkillCatalog`
  - `ResolvedSkillBundle`
  - `SkillRepository.list_catalog(workspace_id)`
  - `SkillRepository.insert_versioned_skill`
  - `SkillRepository.replace_current_version`
  - `SkillRepository.set_personal_enabled`
  - `SkillRepository.set_global_setting`
  - `SkillRepository.list_effective_versions(workspace_id)`

- [ ] **Step 1: Define RED repository contract tests**

Create `tests/test_skill_repository.py` with a SQLite database containing one personal
Workspace, two users, and a filesystem Artifact store. Add exact tests:

- `test_catalog_defaults_global_enabled_and_separates_personal`;
- `test_workspace_global_setting_disables_only_one_workspace`;
- `test_personal_insert_defaults_enabled_and_persists_ready_version`;
- `test_replace_creates_next_version_and_preserves_personal_enabled`;
- `test_same_hash_replace_is_idempotent`;
- `test_effective_versions_are_sorted_and_pin_current_version`;
- `test_archived_skill_is_absent_from_catalog_but_versions_remain`;
- `test_global_and_personal_name_conflicts_are_serialized`;
- `test_two_personal_workspaces_may_use_the_same_non_global_name`;
- `test_corrupt_or_non_ready_current_version_is_rejected`.

Use this representative assertion:

~~~python
catalog = await repository.list_catalog("personal-a")
assert [(item.name, item.scope, item.enabled) for item in catalog.global_skills] == [
    ("global-review", "global", True)
]
assert [(item.name, item.scope, item.enabled) for item in catalog.personal_skills] == [
    ("my-review", "workspace", True)
]
assert catalog.effective_count == 2
~~~

- [ ] **Step 2: Run repository tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_skill_repository.py -q
~~~

Expected: collection fails on missing managed Skill value objects and repository methods.

- [ ] **Step 3: Add domain value objects**

In `app/skills/models.py` retain `SkillBundle` and add:

~~~python
SkillScope = Literal["global", "workspace"]

@dataclass(frozen=True)
class SkillVersionRef:
    id: str
    version_no: int
    bundle_hash: str
    artifact_key: str
    artifact_sha256: str
    manifest_json: str
    size_bytes: int

@dataclass(frozen=True)
class ManagedSkillSummary:
    id: str
    scope: SkillScope
    workspace_id: str | None
    name: str
    description: str
    enabled: bool
    origin: dict[str, object]
    version: SkillVersionRef
    updated_at: datetime

@dataclass(frozen=True)
class StoredSkill:
    summary: ManagedSkillSummary
    bundle: SkillBundle

@dataclass(frozen=True)
class SkillCatalog:
    global_skills: tuple[ManagedSkillSummary, ...]
    personal_skills: tuple[ManagedSkillSummary, ...]

    @property
    def effective_count(self) -> int:
        return sum(item.enabled for item in (*self.global_skills, *self.personal_skills))

@dataclass(frozen=True)
class ResolvedSkillBundle:
    skill: ManagedSkillSummary
    bundle: SkillBundle
~~~

- [ ] **Step 4: Implement transactional version writes**

Construct `SkillRepository(database, artifacts, limits)`. For insert:

~~~python
async def insert_versioned_skill(
    self,
    *,
    scope: SkillScope,
    workspace_id: str | None,
    created_by: str,
    bundle: SkillBundle,
    artifact: SkillArtifact,
    enabled: bool,
    origin: dict[str, object],
) -> ManagedSkillSummary:
    normalized_name = bundle.name.casefold()
    async with self.database.session() as db, db.begin():
        await self._lock_name(db, normalized_name)
        await self._reject_effective_name_conflict(
            db, scope=scope, workspace_id=workspace_id, normalized_name=normalized_name
        )
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        db.add(
            SkillRecord(
                id=skill_id,
                scope=scope,
                workspace_id=workspace_id,
                name=bundle.name,
                normalized_name=normalized_name,
                description=bundle.description,
                content=bundle.content,
                enabled=enabled,
                bundle_hash=bundle.bundle_hash,
                config_json=_origin_json(origin),
                created_by=created_by,
                current_version_id=None,
                archived_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            SkillVersionRecord(
                id=version_id,
                skill_id=skill_id,
                version_no=1,
                bundle_hash=bundle.bundle_hash,
                artifact_key=artifact.artifact_key,
                artifact_sha256=artifact.artifact_sha256,
                manifest_json=artifact.manifest_json,
                size_bytes=artifact.size_bytes,
                status="ready",
                created_by=created_by,
                created_at=now,
            )
        )
        await db.flush()
        await db.execute(
            update(SkillRecord)
            .where(SkillRecord.id == skill_id)
            .values(current_version_id=version_id)
        )
    return await self.require_summary(skill_id)
~~~

Keep compatibility columns populated with `content=bundle.content` and
`bundle_hash=bundle.bundle_hash` during this release, but never read supporting bytes from
`skill_files` for new writes. `replace_current_version()` checks `expected_hash`, reuses an
existing same-hash version, otherwise inserts `version_no=max+1` and atomically changes the
pointer. Do not mutate old version rows.

`_lock_name()` upserts `SkillNameLockRecord(normalized_name)` and selects it `FOR UPDATE`
on PostgreSQL. The SQLite upsert is itself the single-writer serialization point.

- [ ] **Step 5: Implement grouped reads and effective resolution**

`list_catalog(workspace_id)` must:

1. select active global Skills with ready current versions;
2. left join settings for `workspace_id` and calculate `coalesce(enabled, true)`;
3. select active personal Skills for `workspace_id`;
4. return each group sorted by `normalized_name, id`.

`list_effective_versions(workspace_id)` returns enabled global and personal summaries,
verifies no duplicate normalized name, and raises
`AppError("skill_artifact_corrupt", "Skill version metadata is invalid.", 500)` if a
pointer is missing, points to another
Skill, or is not `ready`.

- [ ] **Step 6: Implement Artifact-backed detail reads**

Add:

~~~python
async def load_bundle(self, summary: ManagedSkillSummary) -> SkillBundle:
    raw = await asyncio.to_thread(
        self.artifacts.read,
        summary.version.artifact_key,
        maximum_size=self.limits.max_total_bytes,
    )
    return load_skill_artifact(
        raw,
        expected_artifact_sha256=summary.version.artifact_sha256,
        expected_bundle_hash=summary.version.bundle_hash,
        limits=self.limits,
    )

async def get_stored(self, skill_id: str) -> StoredSkill | None:
    summary = await self.get_summary(skill_id)
    if summary is None:
        return None
    return StoredSkill(summary=summary, bundle=await self.load_bundle(summary))
~~~

Map store unavailability to `skill_artifact_unavailable` and integrity failures to
`skill_artifact_corrupt` without changing them into name conflicts.

- [ ] **Step 7: Run repository and existing bundle tests**

Run:

~~~bash
uv run pytest tests/test_skill_repository.py tests/test_skill_bundle.py -q
~~~

Expected: all tests pass.

- [ ] **Step 8: Commit**

~~~bash
git add app/skills/models.py app/skills/repository.py \
  tests/test_skill_repository.py
git commit -m "feat: persist immutable skill versions"
~~~

---

### Task 4: Idempotent Legacy BLOB-to-Artifact Migration

**Files:**
- Create: `app/skills/migration.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/bootstrap.py`
- Create: `tests/test_skill_artifact_migration.py`
- Modify: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `SkillArtifactStore` and versioned schema/repository from Tasks 1–3.
- Produces `LegacySkillArtifactMigrator.run() -> SkillArtifactMigrationReport`,
  `SkillArtifactGarbageCollector.run(now: datetime) -> int`, and startup ordering that
  completes Artifact migration/maintenance before Skill bootstrap or requests.

- [ ] **Step 1: Add RED migration tests**

Create `tests/test_skill_artifact_migration.py` with:

~~~python
@pytest.mark.asyncio
async def test_migrator_backfills_legacy_skill_and_is_idempotent(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.migration import LegacySkillArtifactMigrator

    migrator = LegacySkillArtifactMigrator(
        legacy_skill_database, artifact_store, SkillBundleLimits()
    )
    first = await migrator.run()
    second = await migrator.run()
    assert first.migrated == 1
    assert second.migrated == 0
    assert second.skipped == 1

    async with legacy_skill_database.session() as db:
        skill = await db.get(SkillRecord, "legacy-skill")
        version = await db.get(SkillVersionRecord, skill.current_version_id)
    assert version.version_no == 1
    assert version.bundle_hash == skill.bundle_hash
    assert artifact_store.exists(version.artifact_key)
~~~

Add exact tests:

- `test_migrator_preserves_legacy_content_and_skill_files`;
- `test_migrator_resumes_after_artifact_written_before_database_commit`;
- `test_migrator_fails_startup_on_corrupt_legacy_hash`;
- `test_migrator_does_not_mark_complete_while_any_skill_is_unmigrated`;
- `test_application_initializes_artifact_store_and_migrates_before_skill_bootstrap`.
- `test_garbage_collector_deletes_only_unreferenced_objects_older_than_24_hours`;
- `test_garbage_collector_never_deletes_referenced_or_fresh_objects`.

- [ ] **Step 2: Run migration tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_skill_artifact_migration.py tests/test_bootstrap.py -q
~~~

Expected: missing `LegacySkillArtifactMigrator` and missing AppServices fields.

- [ ] **Step 3: Implement the migrator**

Define:

~~~python
@dataclass(frozen=True)
class SkillArtifactMigrationReport:
    migrated: int
    skipped: int


class LegacySkillArtifactMigrator:
    marker = "skill_artifact_versions_v1"

    async def run(self) -> SkillArtifactMigrationReport:
        skill_ids = await self._legacy_skill_ids()
        migrated = 0
        for skill_id in skill_ids:
            stored = await self._read_legacy_bundle(skill_id)
            artifact = build_skill_artifact(stored.bundle)
            await asyncio.to_thread(self.artifacts.put, artifact)
            if await self._publish_version_one(stored, artifact):
                migrated += 1
        await self._mark_complete_if_no_legacy_rows()
        return SkillArtifactMigrationReport(
            migrated=migrated,
            skipped=len(skill_ids) - migrated,
        )
~~~

`_read_legacy_bundle()` uses `skills.content + skill_files` and verifies the existing
`bundle_hash`. `_publish_version_one()` runs one DB transaction, rechecks
`current_version_id IS NULL`, inserts/reuses version 1 by `(skill_id, bundle_hash)`, and
updates the pointer. A written Artifact without a DB reference is safe and reused on retry.

Implement `SkillArtifactGarbageCollector` in the same focused module. It reads every
`skill_versions.artifact_key`, iterates store objects, and deletes only unreferenced
objects whose modification time is at least 24 hours old. A list/read/database error aborts
the sweep without deleting additional objects. Run store listing and deletion through
`asyncio.to_thread`.

- [ ] **Step 4: Wire startup ordering**

Add `skill_artifacts` and `skill_artifact_migrator` to `AppServices`. In
`build_app_services()`:

~~~python
skill_artifacts = FilesystemSkillArtifactStore(settings.resolved_skill_artifact_root)
skill_repository = SkillRepository(database, skill_artifacts, skill_limits)
skills = SkillService(skill_repository, workspace_access, skill_limits)
skill_artifact_migrator = LegacySkillArtifactMigrator(
    database, skill_artifacts, skill_limits
)
skill_artifact_garbage_collector = SkillArtifactGarbageCollector(
    database, skill_artifacts
)
~~~

In `initialize_app_services()` order startup as:

~~~python
settings.app_data_dir.mkdir(parents=True, exist_ok=True)
services.skill_artifacts.initialize()
await services.database.initialize()
await services.skill_artifact_migrator.run()
await services.skill_artifact_garbage_collector.run(datetime.now(UTC))
entries = services.workspaces.scan()
~~~

The Artifact root initialization must occur inside the existing single-instance lock.

- [ ] **Step 5: Run migration and baseline startup tests**

Run:

~~~bash
uv run pytest tests/test_skill_artifact_migration.py tests/test_bootstrap.py \
  tests/test_skill_bootstrap.py -q
~~~

Expected: all tests pass and bootstrap reads versioned Artifacts after migration.

- [ ] **Step 6: Commit**

~~~bash
git add app/skills/migration.py app/api/dependencies.py app/bootstrap.py \
  tests/test_skill_artifact_migration.py tests/test_bootstrap.py
git commit -m "feat: migrate legacy skills to artifacts"
~~~

---

### Task 5: Global/Personal Domain Service and Platform Skill Administration

**Files:**
- Create: `app/skills/access.py`
- Create: `app/skills/admin_cli.py`
- Modify: `app/auth/access.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `app/api/dependencies.py`
- Modify: `app/bootstrap.py`
- Rewrite: `app/skills/service.py`
- Modify: `tests/test_skill_service.py`
- Create: `tests/test_skill_admin_cli.py`
- Modify: `tests/test_skill_bootstrap.py`

**Interfaces:**
- Consumes: version-aware repository and Artifact publishing from Tasks 1–4.
- Produces:
  - `WorkspaceAccessService.require_personal_owner(identity, workspace_id)`
  - `PlatformSkillAccessService.require_skill_admin(identity)`
  - `PlatformSkillAccessService.can_manage_global_skills(identity)`
  - `SkillService.catalog(workspace_id, identity)`
  - `SkillService.import_personal_*()` and `SkillService.import_global_*()`
  - `SkillService.set_personal_enabled()` and `SkillService.set_global_enabled()`
  - `SkillService.resolve_effective_skills(workspace_id, identity)`

- [ ] **Step 1: Convert service fixtures to real personal Workspaces**

In `tests/test_skill_service.py` create personal Workspaces using the same safe sequence as
the existing SQLite constraints: insert as `team`, add exactly one Owner, then promote to
`personal`. Keep a separate team Workspace only for explicit rejection tests.

Add a filesystem Artifact store to the fixture and construct:

~~~python
repository = SkillRepository(database, artifact_store, settings.skill_bundle_limits)
platform_access = PlatformSkillAccessService(database)
service = SkillService(
    repository,
    WorkspaceAccessService(database),
    platform_access,
    settings.skill_bundle_limits,
)
~~~

- [ ] **Step 2: Add RED personal/global behavior tests**

Add exact tests:

- `test_personal_upload_requires_personal_owner_and_defaults_enabled`;
- `test_personal_replace_preserves_disabled_state_and_creates_version`;
- `test_global_catalog_defaults_enabled_and_setting_only_affects_new_resolution`;
- `test_global_publish_requires_platform_skill_admin`;
- `test_workspace_owner_is_not_implicitly_platform_skill_admin`;
- `test_global_archive_requires_platform_skill_admin`;
- `test_personal_upload_conflicts_with_global_name`;
- `test_global_publish_reports_personal_conflict_count_without_user_ids`;
- `test_same_hash_global_upload_is_idempotent`;
- `test_effective_resolution_loads_exact_ready_artifacts_in_name_order`;
- `test_team_workspace_rejects_new_personal_skill_mutations`.

Representative test:

~~~python
created_result = await skill_service.import_personal_bundle(
    "personal", owner, bundle, origin={"type": "browser_directory"}
)
created = created_result.skill
assert created.summary.scope == "workspace"
assert created.summary.enabled is True

await skill_service.set_personal_enabled(
    created.summary.id,
    owner,
    enabled=False,
    expected_hash=created.summary.version.bundle_hash,
)
replacement_result = await skill_service.import_personal_bundle(
    "personal",
    owner,
    changed_bundle,
    on_conflict="overwrite",
    expected_hash=created.summary.version.bundle_hash,
    origin={"type": "archive"},
)
replacement = replacement_result.skill
assert replacement.summary.enabled is False
assert replacement.summary.version.version_no == 2
~~~

- [ ] **Step 3: Run service tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_skill_service.py -q
~~~

Expected: failures show missing platform access and global/personal methods.

- [ ] **Step 4: Add personal-Workspace and platform-role access**

In `app/auth/access.py` add:

~~~python
async def require_personal_owner(
    self, identity: IdentityContext, workspace_id: str
) -> WorkspaceMembership:
    membership = await self.require_owner(identity, workspace_id)
    async with self.database.session() as db:
        workspace = await db.get(WorkspaceRecord, workspace_id)
    if workspace is None:
        raise AppError("workspace_not_found", "Workspace not found.", 404)
    if workspace.kind != "personal":
        raise AppError(
            "skill_scope_invalid",
            "Personal Skills require a personal Workspace.",
            422,
        )
    return membership
~~~

In `app/skills/access.py` implement DB-backed platform access:

~~~python
class PlatformSkillAccessService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def can_manage_global_skills(self, identity: IdentityContext) -> bool:
        async with self.database.session() as db:
            return (
                await db.get(
                    PlatformRoleBindingRecord,
                    (identity.user_id, "skill_admin"),
                )
                is not None
            )

    async def require_skill_admin(self, identity: IdentityContext) -> None:
        if not await self.can_manage_global_skills(identity):
            raise AppError("skill_not_found", "Skill not found.", 404)
~~~

Add `bootstrap_subjects(identity, subjects)` that inserts `skill_admin` exactly once when
`identity.external_subject` is an exact member of the configured tuple.

- [ ] **Step 5: Add bootstrap-admin configuration and operator CLI**

Add:

~~~python
skill_admin_subjects: tuple[str, ...] = Field(
    default=(), validation_alias="APP_SKILL_ADMIN_SUBJECTS"
)
~~~

Normalize by trimming, removing empty values, and rejecting duplicates. In
`initialize_app_services()` call `platform_skill_access.bootstrap_subjects()` immediately
after resolving the bootstrap identity and before global bootstrap.

Implement operator commands:

~~~bash
uv run python -m app.skills.admin_cli grant --subject mock-user
uv run python -m app.skills.admin_cli revoke --subject mock-user
uv run python -m app.skills.admin_cli list
~~~

`grant` and `revoke` resolve an existing `UserRecord.external_subject`; unknown subjects
exit non-zero without creating a phantom user. Add `tests/test_skill_admin_cli.py` for
grant, idempotent grant, revoke, list, and unknown subject.

- [ ] **Step 6: Rewrite SkillService around explicit scopes**

Use these public signatures:

~~~python
async def catalog(
    self, workspace_id: str, identity: IdentityContext
) -> SkillCatalog:
    raise NotImplementedError

async def import_personal_archive(
    self,
    workspace_id: str,
    identity: IdentityContext,
    raw: bytes,
    *,
    on_conflict: SkillConflictPolicy,
    expected_hash: str | None,
    target_name: str | None,
) -> SkillImportResult:
    raise NotImplementedError

async def import_personal_bundle(
    self,
    workspace_id: str,
    identity: IdentityContext,
    bundle: SkillBundle,
    *,
    on_conflict: SkillConflictPolicy = "fail",
    expected_hash: str | None = None,
    target_name: str | None = None,
    origin: dict[str, object],
) -> SkillImportResult:
    raise NotImplementedError

async def import_personal_uploaded_directory(
    self,
    workspace_id: str,
    identity: IdentityContext,
    uploaded: UploadedSkillDirectory,
    *,
    on_conflict: SkillConflictPolicy,
    expected_hash: str | None,
    target_name: str | None,
) -> SkillImportResult:
    raise NotImplementedError

async def import_global_archive(
    self,
    identity: IdentityContext,
    raw: bytes,
    *,
    on_conflict: SkillConflictPolicy,
    expected_hash: str | None,
    target_name: str | None,
) -> SkillImportResult:
    raise NotImplementedError

async def import_global_uploaded_directory(
    self,
    identity: IdentityContext,
    uploaded: UploadedSkillDirectory,
    *,
    on_conflict: SkillConflictPolicy,
    expected_hash: str | None,
    target_name: str | None,
) -> SkillImportResult:
    raise NotImplementedError

async def set_personal_enabled(
    self,
    skill_id: str,
    identity: IdentityContext,
    *,
    enabled: bool,
    expected_hash: str,
) -> ManagedSkillSummary:
    raise NotImplementedError

async def set_global_enabled(
    self,
    workspace_id: str,
    skill_id: str,
    identity: IdentityContext,
    *,
    enabled: bool,
) -> ManagedSkillSummary:
    raise NotImplementedError

async def resolve_effective_skills(
    self, workspace_id: str, identity: IdentityContext
) -> tuple[ResolvedSkillBundle, ...]:
    raise NotImplementedError

async def get_for_workspace(
    self, workspace_id: str, skill_id: str, identity: IdentityContext
) -> StoredSkill:
    raise NotImplementedError

async def archive_personal(
    self,
    workspace_id: str,
    skill_id: str,
    identity: IdentityContext,
    *,
    expected_hash: str,
) -> None:
    raise NotImplementedError

async def archive_global(
    self, skill_id: str, identity: IdentityContext, *, expected_hash: str
) -> None:
    raise NotImplementedError

async def count_effective_by_workspace(
    self, workspace_ids: tuple[str, ...]
) -> dict[str, int]:
    raise NotImplementedError

async def publish_trusted_global_bundle(
    self,
    bundle: SkillBundle,
    *,
    created_by: str,
    origin: dict[str, object],
) -> SkillImportResult:
    raise NotImplementedError
~~~

Every create/replace path calls `build_skill_artifact(bundle)` and
`await asyncio.to_thread(artifacts.put, artifact)` before the repository transaction. New
personal/global inserts pass `enabled=True`. Global
effective enabled state is computed from settings, never by changing `SkillRecord.enabled`.

Retain existing create/update/copy service methods only as compatibility wrappers for
legacy callers; do not expose them in the new UI. They must use versioned Artifact writes
and must not bypass personal/global conflict validation.

- [ ] **Step 7: Convert one-time bootstrap sources to global Skills**

Change `SkillBootstrapService` so trusted Workspace manifest/local-root inputs publish
`scope="global"` exactly once with origin `{"type": "global_bootstrap"}`. Use a new marker
`global_skill_import_v1`. Same-name/same-hash is skipped; a collision with migrated
personal data is reported as `conflict` and does not overwrite either Skill. Failed items
prevent the marker, preserving current retry behavior.

Update `tests/test_skill_bootstrap.py` to assert bootstrap Skills appear in the global
catalog, default enabled, and are not recreated after an administrator replaces or
archives them.

- [ ] **Step 8: Run service, admin, and bootstrap tests**

Run:

~~~bash
uv run pytest tests/test_skill_service.py tests/test_skill_admin_cli.py \
  tests/test_skill_bootstrap.py tests/test_config.py -q
~~~

Expected: all tests pass.

- [ ] **Step 9: Commit**

~~~bash
git add app/skills/access.py app/skills/admin_cli.py app/auth/access.py \
  app/config.py .env.example app/api/dependencies.py app/bootstrap.py \
  app/skills/service.py tests/test_skill_service.py \
  tests/test_skill_admin_cli.py tests/test_skill_bootstrap.py
git commit -m "feat: add global and personal skill services"
~~~

---

### Task 6: Grouped Skill Catalog and Administration HTTP API

**Files:**
- Rewrite: `app/skills/schemas.py`
- Rewrite: `app/skills/routes.py`
- Modify: `app/skills/uploads.py`
- Modify: `app/api/schemas.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_skill_api.py`
- Modify: `tests/test_skill_uploads.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 5 service methods and platform capability.
- Produces grouped catalog JSON, Workspace global setting endpoint, platform admin upload
  endpoints, and `can_manage_global_skills` bootstrap capability.

- [ ] **Step 1: Add RED grouped catalog and capability tests**

Update `tests/test_skill_api.py` with exact cases:

- `test_catalog_groups_global_and_personal_and_reports_effective_count`;
- `test_personal_archive_upload_defaults_enabled`;
- `test_personal_directory_upload_defaults_enabled`;
- `test_global_toggle_is_workspace_specific_and_requires_personal_owner`;
- `test_admin_global_archive_upload_requires_admin_before_reading_body`;
- `test_admin_global_directory_upload_requires_admin_before_reading_body`;
- `test_non_admin_global_archive_returns_fail_closed_404`;
- `test_personal_detail_can_read_artifact_manifest`;
- `test_global_detail_is_visible_but_mutation_is_admin_only`;
- `test_legacy_create_update_copy_routes_are_not_rendered_by_new_catalog`.
- `test_archive_upload_parses_fail_overwrite_and_rename_conflict_fields`.

Assert the exact catalog envelope:

~~~python
payload = (await client.get("/api/workspaces/personal/skills")).json()
assert payload["changes_apply_to"] == "new_sessions"
assert payload["effective_count"] == 2
assert [item["scope"] for item in payload["global"]] == ["global"]
assert [item["scope"] for item in payload["personal"]] == ["workspace"]
assert payload["global"][0]["enabled"] is True
~~~

Extend `tests/test_api.py` so `WorkspaceOut.can_manage_global_skills` is true only for a
bound platform admin and `skill_count` equals the effective catalog count.

- [ ] **Step 2: Run API tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_skill_api.py tests/test_api.py -q
~~~

Expected: response-model and route failures for the new grouped contract.

- [ ] **Step 3: Define response schemas**

Replace the flat list schema with:

~~~python
class SkillOut(BaseModel):
    id: str
    scope: Literal["global", "workspace"]
    workspace_id: str | None
    name: str
    description: str
    enabled: bool
    version_id: str
    version_no: int
    bundle_hash: str
    origin: dict[str, object]
    updated_at: datetime

class SkillCatalogOut(BaseModel):
    global_: list[SkillOut] = Field(serialization_alias="global")
    personal: list[SkillOut]
    effective_count: int
    changes_apply_to: Literal["new_sessions"] = "new_sessions"

class GlobalSkillSettingIn(BaseModel):
    enabled: bool
~~~

`SkillDetailOut` retains read-only `content` and file Manifest fields loaded from the
Artifact. Replace `SkillDirectoryImportOut` with `SkillImportOut` and use it for archive
and directory endpoints with the existing four statuses.

In `app/skills/uploads.py` replace the archive reader's byte-only result with:

~~~python
@dataclass(frozen=True)
class ArchiveUpload:
    raw: bytes
    on_conflict: Literal["fail", "overwrite", "rename"]
    expected_hash: str | None
    target_name: str | None

async def read_multipart_archive(
    request: Request, maximum_size: int
) -> ArchiveUpload:
    raise NotImplementedError
~~~

Apply the same field validation matrix as `DirectoryUpload`. Update
`tests/test_skill_uploads.py` to prove malformed/duplicate control fields fail, excess
control-field bytes are bounded, and authorization tests still stop before reading body
chunks.

- [ ] **Step 4: Implement user routes**

Use:

~~~text
GET    /api/workspaces/{workspace_id}/skills
GET    /api/workspaces/{workspace_id}/skills/{skill_id}
POST   /api/workspaces/{workspace_id}/skills/import
POST   /api/workspaces/{workspace_id}/skills/import-directory
PATCH  /api/workspaces/{workspace_id}/skills/{skill_id}/enabled
DELETE /api/workspaces/{workspace_id}/skills/{skill_id}
PUT    /api/workspaces/{workspace_id}/global-skills/{skill_id}/setting
~~~

All personal upload routes call `require_personal_owner()` before
`read_multipart_archive()` or `read_multipart_directory()`. The global setting route calls
the same personal owner check before changing the setting. Archive bodies continue to
carry `expected_hash`.

Keep old `/api/skills/{skill_id}` routes as compatibility aliases during this release,
but mark them out of the new frontend path and route them through the same scoped service.

- [ ] **Step 5: Implement platform admin routes**

Use:

~~~text
POST   /api/admin/global-skills/import
POST   /api/admin/global-skills/import-directory
GET    /api/admin/global-skills/{skill_id}
DELETE /api/admin/global-skills/{skill_id}
~~~

Call `require_skill_admin()` before consuming any request body. Reuse the exact directory
conflict fields and status mapping. Global archive also requires `expected_hash`.

- [ ] **Step 6: Expose capability and effective counts**

Add `can_manage_global_skills: bool` to `WorkspaceOut`. Convert `_workspace_out()` to
receive the already-computed capability rather than doing asynchronous work inside the
mapper. In Workspace listing:

~~~python
can_manage_global = await services.platform_skill_access.can_manage_global_skills(identity)
counts = await services.skills.count_effective_by_workspace(
    tuple(item.workspace_id for item in memberships)
)
~~~

Return effective global+personal counts. Do not count disabled Skills or archived rows.

- [ ] **Step 7: Run API tests**

Run:

~~~bash
uv run pytest tests/test_skill_api.py tests/test_api.py tests/test_workspaces.py -q
~~~

Expected: all tests pass and authorization-before-body tests prove no tail chunks are read.

- [ ] **Step 8: Commit**

~~~bash
git add app/skills/schemas.py app/skills/routes.py app/skills/uploads.py \
  app/api/schemas.py app/api/routes.py tests/test_skill_api.py \
  tests/test_skill_uploads.py tests/test_api.py
git commit -m "feat: expose global and personal skill APIs"
~~~

---

### Task 7: Session Schema-v3 Skill Pinning and Artifact Materialization

**Files:**
- Modify: `app/sessions/snapshot.py`
- Modify: `app/sessions/service.py`
- Modify: `app/workspaces/materializer.py`
- Modify: `app/sessions/catalog.py`
- Modify: `tests/test_sessions.py`
- Modify: `tests/test_session_catalog.py`
- Modify: `tests/test_skill_service.py`

**Interfaces:**
- Consumes: `SkillService.resolve_effective_skills()` and `ResolvedSkillBundle`.
- Produces schema-v3 Session snapshots containing scope/version/Artifact identity while
  preserving schema-v2 Session reads.

- [ ] **Step 1: Add RED Session invariance tests**

Add exact tests:

- `test_new_session_snapshot_pins_global_and_personal_version_ids`;
- `test_disabling_global_changes_only_later_session`;
- `test_replacing_personal_skill_changes_only_later_session`;
- `test_materializer_verifies_artifact_and_bundle_hash_before_publish`;
- `test_missing_artifact_fails_session_creation_without_partial_workspace`;
- `test_schema_v2_session_catalog_remains_readable`;
- `test_session_autocomplete_uses_snapshot_not_current_catalog`.

Representative assertions:

~~~python
first = await sessions.create("personal", owner)
await skills.set_global_enabled(
    "personal", global_skill.id, owner, enabled=False
)
second = await sessions.create("personal", owner)

first_snapshot = json.loads(first.workspace_snapshot_json)
second_snapshot = json.loads(second.workspace_snapshot_json)
assert first_snapshot["schema_version"] == 3
assert first_snapshot["skills"][0]["version_id"] == global_skill.version.id
assert first_snapshot["skills"][0]["scope"] == "global"
assert second_snapshot["skills"] == []
assert (sessions.session_path(first) / ".claude/skills/global-review/SKILL.md").is_file()
~~~

- [ ] **Step 2: Run Session tests and verify RED**

Run:

~~~bash
uv run pytest tests/test_sessions.py tests/test_session_catalog.py -q
~~~

Expected: snapshot assertions fail because schema version is 2 and version metadata is
missing.

- [ ] **Step 3: Upgrade the snapshot builder**

Change the signature:

~~~python
def build_session_snapshot(
    entry: WorkspaceEntry,
    skills: tuple[ResolvedSkillBundle, ...],
) -> SessionSnapshot:
~~~

Write `schema_version=3` and exact fields `id`, `scope`, `version_id`,
`version_no`, `name`, `description`, `bundle_hash`, `artifact_key`, and file Manifest.
Continue deterministic JSON sorting and include only Artifact metadata, never bytes or
absolute paths.

- [ ] **Step 4: Resolve and materialize exact versions**

Change `SessionService.create()` to call:

~~~python
resolved_skills = await self.skills.resolve_effective_skills(
    workspace_id, identity
)
snapshot = build_session_snapshot(entry, resolved_skills)
materialized = materialize_session_workspace(
    entry,
    session_id,
    self.data_dir,
    snapshot,
    resolved_skills,
    limits=self.skills.limits,
)
~~~

Change `SessionService.create(workspace_id: str, identity: IdentityContext)` and update the
API call site to pass the resolved identity. Do not synthesize an identity from a user ID.

`materialize_session_workspace()` iterates `ResolvedSkillBundle.bundle` and verifies that
the summary version Hash equals the reconstructed Bundle Hash before writing. It continues
to write into the existing temporary Session directory and atomically rename only after
all files verify.

On `skill_artifact_unavailable` or `skill_artifact_corrupt`, remove the temporary
directory and propagate the stable error; do not insert an incomplete Session row.

- [ ] **Step 5: Preserve old Session catalog behavior**

`list_skills()` must accept both schema 2 and schema 3 snapshots:

~~~python
schema_version = int(snapshot.get("schema_version", 0))
if schema_version not in {2, 3}:
    return ()
~~~

For schema 3 it reads names/descriptions from the snapshot and validates the corresponding
materialized `SKILL.md`. It never reads current Skill versions. Keep all existing symlink
rejection behavior.

- [ ] **Step 6: Run Session and runtime option tests**

Run:

~~~bash
uv run pytest tests/test_sessions.py tests/test_session_catalog.py \
  tests/test_skill_service.py -q
uv run pytest tests -q -k "runtime and skills"
~~~

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

~~~bash
git add app/sessions/snapshot.py app/sessions/service.py \
  app/workspaces/materializer.py app/sessions/catalog.py \
  tests/test_sessions.py tests/test_session_catalog.py tests/test_skill_service.py
git commit -m "feat: pin skill versions in session snapshots"
~~~

---

### Task 8: Full-Screen iframe Skill Management View

**Files:**
- Rewrite: `app/web/templates/index.html`
- Rewrite: `app/web/static/skill-manager.js`
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/app.css`
- Rewrite: `tests/js/test_skill_manager.cjs`
- Modify: `tests/test_web_page.py`

**Interfaces:**
- Consumes: grouped catalog and capability API from Task 6.
- Produces a non-dialog view with global/personal lists, scope-specific upload, toggles,
  read-only detail, conflict handling, and return-to-chat navigation.

- [ ] **Step 1: Replace markup expectations with RED full-view tests**

Update `tests/test_web_page.py` to require:

~~~python
required_ids = {
    "skillManagementView",
    "skillManagementBackButton",
    "globalSkillList",
    "personalSkillList",
    "personalSkillImportButton",
    "personalSkillDirectoryInput",
    "personalSkillArchiveInput",
    "globalSkillImportRoot",
    "globalSkillImportButton",
    "globalSkillDirectoryInput",
    "globalSkillArchiveInput",
    "skillDetailPanel",
    "skillDetailManifest",
    "skillChangesNotice",
}
~~~

Assert `skillManagementView` is a normal `section` under the workbench, contains the exact
text “仅对新会话生效”, and `skillManagerDialog`, editor, create, copy, and save IDs no
longer exist.

- [ ] **Step 2: Add RED controller tests**

Rewrite `tests/js/test_skill_manager.cjs` around the new controller. Keep the existing
candidate and Session creation coordinator tests. Add exact cases:

- `open hides app shell and renders grouped catalog`;
- `back restores app shell without changing selected session`;
- `ordinary user never sees global import controls`;
- `platform admin sees global folder and ZIP import controls`;
- `global toggle calls workspace setting endpoint`;
- `personal toggle sends expected hash`;
- `personal archive sends expected hash and refreshes the personal list`;
- `global archive is hidden from ordinary users and calls the admin endpoint for admins`;
- `personal folder upload defaults to the personal route`;
- `global ZIP upload uses the admin route`;
- `same-name conflict retains files and supports overwrite/rename/cancel`;
- `workspace switch invalidates late catalog/import/toggle responses`;
- `detail is read-only and renders version/hash/Manifest`;
- `successful mutation refreshes counts and displays new-session notice`.

Use the exact grouped fixture:

~~~javascript
const catalog = {
  global: [skill({id: "global-1", scope: "global", enabled: true})],
  personal: [skill({id: "personal-1", scope: "workspace", enabled: true})],
  effective_count: 2,
  changes_apply_to: "new_sessions",
};
~~~

- [ ] **Step 3: Run frontend unit tests and verify RED**

Run:

~~~bash
node --test tests/js/test_skill_manager.cjs
uv run pytest tests/test_web_page.py -q
~~~

Expected: failures report old dialog/editor structure and flat list assumptions.

- [ ] **Step 4: Replace the dialog with a view**

In `index.html` keep the top bar and `skillsButton`. Inside the workbench, add a hidden
`section#skillManagementView` adjacent to `div.app-shell`. It contains:

- header with Workspace name, title, new-Session notice, and back button;
- `section#globalSkillSection` with list, loading/empty states, and admin-only import root;
- `section#personalSkillSection` with list, loading/empty states, and personal import;
- read-only detail panel with version, Hash, source, updated time, and file Manifest;
- the existing conflict dialog reduced to upload overwrite/rename/cancel only.

Use separate hidden directory/archive inputs for global and personal scopes so a native
picker response cannot be applied to the wrong scope.

- [ ] **Step 5: Implement view navigation in app.js**

Track `state.activeView = "chat" | "skills"`. Provide:

~~~javascript
function showSkillManagement() {
  state.activeView = "skills";
  elements.appShell.hidden = true;
  elements.skillManagementView.hidden = false;
}

function showChat() {
  state.activeView = "chat";
  elements.skillManagementView.hidden = true;
  elements.appShell.hidden = false;
}
~~~

The Skills button calls controller `open()`; back calls `close()`. Workspace switching
closes the management view and invalidates its lifecycle generation. Do not modify
`state.session` or create a Session during navigation.

- [ ] **Step 6: Implement the grouped controller**

Expose:

~~~javascript
function createController({
  api,
  elements,
  getWorkspace,
  onChanged,
  onEnter,
  onLeave,
  onError,
}) {
  async function open() {}
  function close() {}
  async function refresh() {}
  async function setGlobalEnabled(skillId, enabled) {}
  async function setPersonalEnabled(skill, enabled) {}
  async function importDirectory(scope, files, conflict = {on_conflict: "fail"}) {}
  async function importArchive(scope, file) {}
  return {open, close, refresh, reset};
}
~~~

Global toggles call
`PUT /api/workspaces/{workspaceId}/global-skills/{skillId}/setting`. Personal toggles call
`PATCH /api/workspaces/{workspaceId}/skills/{skillId}/enabled` with
`expected_hash`. Imports select personal Workspace or admin routes by explicit scope.

Retain generation checks, one pending mutation owner, conflict file retention, and input
clearing only after terminal success. Render DOM with `textContent` only; never inject
Skill metadata as HTML.

- [ ] **Step 7: Implement responsive layout**

Desktop uses two stacked catalog sections with a right-side detail panel. At narrow widths,
detail becomes a block below the selected list. Keep the top bar visible, lists scroll
inside the viewport, and preserve keyboard focus when returning from the native picker or
conflict dialog. Do not add a frontend dependency.

- [ ] **Step 8: Run frontend tests**

Run:

~~~bash
node --test tests/js/test_skill_manager.cjs
uv run pytest tests/test_web_page.py -q
~~~

Expected: all tests pass.

- [ ] **Step 9: Commit**

~~~bash
git add app/web/templates/index.html app/web/static/skill-manager.js \
  app/web/static/app.js app/web/static/app.css \
  tests/js/test_skill_manager.cjs tests/test_web_page.py
git commit -m "feat: add full-screen skill management view"
~~~

---

### Task 9: Browser Acceptance, Deployment Documentation, and Full Regression

**Files:**
- Modify: `tests/browser/test_workbench.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/superpowers/specs/2026-08-19-global-personal-skill-management-design.md` only if implementation discoveries require a factual correction

**Interfaces:**
- Consumes: all Tasks 1–8.
- Produces verified end-to-end behavior and operator instructions for SQLite/filesystem
  deployment, persistence, backup, platform admin grant, and future migration.

- [ ] **Step 1: Replace old dialog/editor/copy browser tests**

Remove browser assertions that depend on `skillManagerDialog`, in-browser editing, and
cross-Workspace copy. Add:

- `test_skill_management_is_full_screen_and_returns_to_selected_session`;
- `test_personal_folder_and_zip_upload_default_enabled`;
- `test_global_default_toggle_and_new_session_snapshot`;
- `test_platform_admin_can_upload_global_skill`;
- `test_existing_session_keeps_old_skill_after_replacement`;
- `test_skill_import_conflict_overwrite_rename_and_cancel`.

The navigation test must:

~~~python
await page.locator("#skillsButton").click()
await expect(page.locator("#skillManagementView")).to_be_visible()
await expect(page.locator(".app-shell")).to_be_hidden()
await page.locator("#skillManagementBackButton").click()
await expect(page.locator(".app-shell")).to_be_visible()
await expect(page.locator("#sessionTitle")).to_have_text(original_title)
~~~

The Session invariance test creates Session A, uploads/replaces a Skill, creates Session B,
and asserts `GET /api/sessions/{id}/skills` returns the old description for A and new
description for B.

- [ ] **Step 2: Run browser tests and verify RED**

Run:

~~~bash
uv run pytest tests/browser/test_workbench.py -q -k "skill_management or skill_import"
~~~

Expected before completing fixtures: failures identify remaining old selectors or missing
platform role setup.

- [ ] **Step 3: Complete browser fixtures and pass acceptance**

Seed `PlatformRoleBindingRecord(user_id="manager", role="skill_admin")` only for the admin
browser test. Use the actual temporary filesystem Artifact root from `settings_factory`.
Assert both ZIP and directory inputs upload real bytes rather than mocking service
responses.

Run:

~~~bash
uv run pytest tests/browser/test_workbench.py -q -k "skill_management or skill_import"
~~~

Expected: selected browser tests pass.

- [ ] **Step 4: Update operator documentation**

Document in `README.md`:

~~~env
APP_DATA_DIR=/data/agent-host
SKILL_ARTIFACT_BACKEND=filesystem
SKILL_ARTIFACT_ROOT=/data/agent-host/skill-artifacts
# JSON array used only to bootstrap bindings for matching existing identities:
APP_SKILL_ADMIN_SUBJECTS='["mock-user"]'
~~~

State explicitly:

- omitting `DATABASE_URL` uses `APP_DATA_DIR/app.db`;
- local/UAT filesystem mode requires one Agent Host instance;
- `APP_DATA_DIR` must be a host bind mount or persistent volume, not container rootfs;
- backup must cover SQLite via online backup/stopped writes plus `skill-artifacts`;
- upload/update/toggle applies only to new Sessions;
- future multi-node deployment requires PostgreSQL and a shared Artifact adapter;
- current release ships no OSS/S3/MinIO adapter.

Include grant/revoke/list CLI examples and legacy migration startup behavior.

- [ ] **Step 5: Run static and targeted backend gates**

Run:

~~~bash
uv run ruff check app tests
uv run pytest tests/test_skill_artifacts.py tests/test_migrations.py \
  tests/test_skill_repository.py tests/test_skill_artifact_migration.py \
  tests/test_skill_service.py tests/test_skill_admin_cli.py \
  tests/test_skill_bootstrap.py tests/test_skill_api.py tests/test_sessions.py \
  tests/test_session_catalog.py tests/test_api.py tests/test_web_page.py -q
node --test tests/js/test_skill_manager.cjs
~~~

Expected: all commands pass.

- [ ] **Step 6: Run the complete regression suite**

Run:

~~~bash
uv run pytest -q
node --test tests/js/*.cjs
~~~

Expected: all tests pass. If Playwright browser dependencies are unavailable, report that
environment failure separately and retain the completed controller/API gates.

- [ ] **Step 7: Verify worktree scope and migration cleanliness**

Run:

~~~bash
git status --short
git diff --check
rg -n "skillManagerDialog|skillManagerEditor|skillManagerCopy" app tests
rg -n "content_blob" app/skills app/sessions
~~~

Expected:

- no unrelated files;
- no whitespace errors;
- old dialog/editor/copy selectors absent from active frontend/tests;
- `content_blob` appears only in legacy migration compatibility code, not current read/write
  or Session paths.

- [ ] **Step 8: Commit final acceptance and docs**

~~~bash
git add tests/browser/test_workbench.py README.md .env.example \
  docs/superpowers/specs/2026-08-19-global-personal-skill-management-design.md
git commit -m "test: verify global and personal skill management"
~~~

- [ ] **Step 9: Record final evidence**

Run:

~~~bash
git log --oneline --decorate -12
git status --short --branch
~~~

Record the branch, final commit, SQLite migration head, targeted test counts, full test
counts, Node test count, browser test result, and whether the optional PostgreSQL gate ran.
