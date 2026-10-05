# Workspace Skill Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Mock-SSO-backed personal/team Workspace isolation and database-managed Skills whose exact enabled bundles are copied into each new Session.

**Architecture:** Keep `workspace.yaml` and `WorkspaceRegistry` authoritative for non-Skill runtime configuration, while users, Workspace membership, current Skill bundles, and enablement live in SQLite through SQLAlchemy and Alembic. Every request is authorized through one access service, and Session creation reads enabled bundles once, records their manifest in the existing snapshot, and materializes ordinary files for Claude Code.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic Settings, SQLAlchemy asyncio, SQLite/aiosqlite, Alembic, PyYAML, `zipfile`, browser-native JavaScript/CSS, Node `node:test`, pytest, Playwright Chromium.

## Global Constraints

- A Skill belongs to exactly one Workspace; personal and team spaces use the same model.
- Only `owner` and `admin` may mutate Skills. A `member` may list, view, and use enabled Skills.
- A personal Workspace has exactly one owner and no additional members.
- Manual imports and cross-Workspace copies start disabled; migration imports that preserve existing behavior start enabled.
- Copy requires manager access in both source and target Workspaces and creates an independent Skill ID.
- `SKILL.md` frontmatter is canonical for `name` and `description`.
- Bundle hash input is `skill-bundle-v1`, canonical metadata, exact `SKILL.md` bytes, and supporting files sorted by normalized POSIX path; Workspace ID and Skill ID are excluded.
- Reject absolute paths, `.`/`..`, backslashes, NUL bytes, duplicate normalized paths, links, devices, and non-regular archive entries.
- Defaults are 10 MiB per supporting file, 50 MiB total uncompressed bundle bytes, and 200 supporting files.
- New Sessions use regular copied files and remain immutable. Existing legacy symlink Sessions remain loadable and keep legacy behavior.
- Active-Session `/` autocomplete reads the Session snapshot; it never reads the Workspace's live Skill rows.
- `workspace.yaml` remains authoritative for model, tools, MCP, instructions, and seed data. Its Skill list is migration input only.
- Mock settings are the only membership/role administration surface in phase one; member-management APIs and UI are deferred with real SSO integration.
- No releases, version history, rollback, Git import/sync, Marketplace, MCP changes, or real SSO protocol work in this plan.
- Do not stage or modify unrelated `agents.json`, `description.md`, `members.json`, or `squads.json` files.
- The current main worktree has an authorized but uncommitted Claude Agent SDK upgrade in `pyproject.toml` and `uv.lock`. Before executing Task 1, preserve or commit that change separately, or execute in an isolated worktree and carry the `claude-agent-sdk>=0.2.126,<0.3` floor into the dependency update. Never discard it or combine it accidentally with unrelated user files.

---

## File Structure

New backend responsibilities are split as follows:

- `app/auth/models.py`: identity and role value objects.
- `app/auth/provider.py`: `IdentityProvider` protocol and Mock implementation.
- `app/auth/access.py`: all Workspace, Session, Turn, Attachment, and Skill authorization checks.
- `app/workspaces/sync.py`: registry-to-database synchronization and Mock membership seeding.
- `app/skills/models.py`: immutable bundle and service response value objects.
- `app/skills/bundle.py`: frontmatter parsing, path validation, hashing, directory and archive loading.
- `app/skills/repository.py`: Workspace-scoped Skill persistence and consistent bundle reads.
- `app/skills/service.py`: create, update, enable, archive, copy, import, and bootstrap orchestration.
- `app/skills/routes.py`: Skill HTTP endpoints only.
- `app/skills/schemas.py`: Skill request/response schemas only.
- `app/sessions/snapshot.py`: schema-v2 Workspace/Skill snapshot construction.
- `app/web/static/skill-manager.js`: testable Skill-management UI controller.
- `app/db/alembic/`: ordered Alembic baseline and Workspace-Skill schema migrations, packaged with the application wheel.

Existing `app/api/routes.py` remains the home of health, Workspace, Session,
Attachment, Turn, and Session-catalog routes. Skill routes are separate so the
already-large route module does not grow further.

---

### Task 1: Add ordered migrations and Workspace-Skill persistence models

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `alembic.ini`
- Create: `app/db/alembic/__init__.py`
- Create: `app/db/alembic/env.py`
- Create: `app/db/alembic/script.py.mako`
- Create: `app/db/alembic/versions/__init__.py`
- Create: `app/db/alembic/versions/rev_0001_legacy_baseline.py`
- Create: `app/db/alembic/versions/rev_0002_workspace_skills.py`
- Create: `app/db/migrations.py`
- Modify: `app/db/base.py:1-48`
- Modify: `app/db/models.py:1-139`
- Create: `tests/test_migrations.py`
- Modify: `tests/test_database.py:1-92`

**Interfaces:**
- Consumes: `Database(url)` and the existing four-table database layout.
- Produces: `run_migrations(sync_connection) -> None`; ORM models `UserRecord`, `WorkspaceRecord`, `WorkspaceMemberRecord`, `SkillRecord`, `SkillFileRecord`, and `AppMetadataRecord`.

- [ ] **Step 1: Write failing migration tests for fresh and legacy databases**

Create `tests/test_migrations.py` with a fresh-database test and a legacy-adoption
test. The legacy helper creates the current four tables using the exact DDL from
`rev_0001_legacy_baseline.py`, inserts a Session, and omits `alembic_version`; the
migration runner must adopt those tables with `checkfirst=True`, create the new
tables, backfill the Workspace, and retain the Session. The core assertions are:

```python
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.db.alembic.versions.rev_0001_legacy_baseline import create_legacy_schema


@pytest.mark.asyncio
async def test_migrations_create_current_schema_on_fresh_database(tmp_path: Path) -> None:
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    await database.initialize()
    async with database.engine.connect() as connection:
        tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    assert {
        "alembic_version", "sessions", "turns", "messages", "attachments",
        "users", "workspaces", "workspace_members", "skills", "skill_files",
        "app_metadata",
    } <= tables
    await database.dispose()


@pytest.mark.asyncio
async def test_migrations_adopt_legacy_schema_and_preserve_session(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(create_legacy_schema)
        await connection.execute(text("""
            INSERT INTO sessions (
                id, workspace_id, title, title_source, status,
                workspace_snapshot_json, workspace_snapshot_hash, session_dir,
                created_at, updated_at
            ) VALUES (
                'legacy-session', 'legacy-space', 'Legacy', 'auto', 'idle',
                '{}', 'old-hash', 'sessions/legacy-session', CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
        """))
    await engine.dispose()

    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    async with database.engine.connect() as connection:
        workspace = (await connection.execute(text(
            "SELECT id, kind FROM workspaces WHERE id = 'legacy-space'"
        ))).one()
        session_count = await connection.scalar(text(
            "SELECT COUNT(*) FROM sessions WHERE id = 'legacy-session'"
        ))
    assert workspace == ("legacy-space", "team")
    assert session_count == 1
    await database.dispose()
```

- [ ] **Step 2: Run the migration tests and verify they fail**

Run:

```bash
uv run pytest tests/test_migrations.py -q
```

Expected: FAIL because `app.db.migrations` and Alembic configuration do not exist.

- [ ] **Step 3: Add Alembic without losing the pending SDK floor**

Add `"alembic>=1.16.0,<2"` beside SQLAlchemy and keep:

```toml
"claude-agent-sdk>=0.2.126,<0.3",
```

Then regenerate the lockfile:

```bash
uv lock
```

Expected: `uv.lock` contains Alembic and still resolves `claude-agent-sdk` at `0.2.126` or newer within `<0.3`.

Create `alembic.ini` for local operator commands with
`script_location = app/db/alembic` and `prepend_sys_path = .`. Runtime startup
does not depend on the working directory or this top-level file; it constructs
the Alembic configuration from the packaged path below.

- [ ] **Step 4: Implement the migration runner and replace `create_all` startup**

Create `app/db/migrations.py`:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection


SCRIPT_LOCATION = Path(__file__).with_name("alembic")


def run_migrations(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
```

Change `Database.initialize` to:

```python
async def initialize(self) -> None:
    from app.db.migrations import run_migrations

    self._ensure_sqlite_parent()
    async with self.engine.begin() as connection:
        await connection.run_sync(run_migrations)
```

Configure `app/db/alembic/env.py` to use `config.attributes["connection"]` and
`Base.metadata`; enable `render_as_batch=True` for SQLite.

- [ ] **Step 5: Add the exact persistence models**

Extend `app/db/models.py` with these fields and constraints. Keep `SkillRecord.content`
as UTF-8 text and `SkillFileRecord.content_blob` as `LargeBinary`:

Update `SessionRecord.workspace_id` to use
`ForeignKey("workspaces.id", ondelete="RESTRICT")` while retaining its existing
`String(64)` type and index.

```python
class UserRecord(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint("kind IN ('personal','team')", name="ck_workspaces_kind"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkspaceMemberRecord(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner','admin','member')", name="ck_workspace_members_role"),
    )
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillRecord(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bundle_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillFileRecord(Base):
    __tablename__ = "skill_files"
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    content_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(71), nullable=False)


class AppMetadataRecord(Base):
    __tablename__ = "app_metadata"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
```

Add an active-name partial unique index in both ORM metadata and migration:

```python
Index(
    "uq_skills_workspace_active_name",
    SkillRecord.workspace_id,
    SkillRecord.name,
    unique=True,
    sqlite_where=SkillRecord.archived_at.is_(None),
    postgresql_where=SkillRecord.archived_at.is_(None),
)
```

The `0001` revision defines the exact existing four tables and their indexes as
SQLAlchemy `Table` objects, then calls `table.create(bind, checkfirst=True)` in
dependency order. This makes a legacy database adoptable without pretending a
partial schema already ran. `tests/test_migrations.py` imports the same frozen
`create_legacy_schema` helper from the revision module, so its legacy DDL
cannot drift from the revision under test. The `0002` revision creates the six
tables above, inserts placeholder `team`
Workspace rows for distinct legacy `sessions.workspace_id` values, and then
adds `fk_sessions_workspace` with SQLite batch mode. Downgrade removes the FK
before dropping new tables.

Update direct database tests to insert
`WorkspaceRecord(id="example", name="Example", kind="team", config_json="{}")`
before inserting a `SessionRecord`. Do not disable foreign keys in tests.

- [ ] **Step 6: Run migration and database tests**

Run:

```bash
uv run pytest tests/test_migrations.py tests/test_database.py -q
```

Expected: all tests pass; the legacy Session row remains and foreign-key tests still pass.

- [ ] **Step 7: Commit the migration foundation**

```bash
git add alembic.ini app/db/alembic app/db/migrations.py app/db/base.py app/db/models.py \
  tests/test_migrations.py tests/test_database.py pyproject.toml uv.lock
git diff --cached --check
git commit -m "feat: add workspace skill persistence"
```

---

### Task 2: Add Mock identity, Workspace synchronization, and role checks

**Files:**
- Modify: `app/config.py:1-96`
- Create: `app/auth/__init__.py`
- Create: `app/auth/models.py`
- Create: `app/auth/provider.py`
- Create: `app/auth/access.py`
- Create: `app/workspaces/sync.py`
- Modify: `app/api/dependencies.py:1-31`
- Modify: `app/main.py:1-83`
- Modify: `app/api/schemas.py:1-18`
- Modify: `app/api/routes.py:1-75`
- Modify: `.env.example`
- Modify: `tests/conftest.py:1-27`
- Create: `tests/test_auth.py`
- Modify: `tests/test_api.py:1-70`
- Modify: `tests/test_sessions.py`
- Modify: `tests/test_attachments.py`
- Modify: `tests/test_turns.py`

**Interfaces:**
- Consumes: registry `WorkspaceEntry` values and the Task 1 persistence models.
- Produces: `IdentityProvider.resolve(request) -> IdentityContext`,
  `resolve_bootstrap_identity() -> IdentityContext`, `MockIdentityProvider`,
  `WorkspaceAccessService.require_member/require_manager/require_owner`, and
  `WorkspaceSyncService.sync(entries, identity) -> None`.

- [ ] **Step 1: Write failing identity and Workspace filtering tests**

Create `tests/test_auth.py` covering one personal Workspace, one team
Workspace, and an inaccessible Workspace:

```python
@pytest.mark.asyncio
async def test_workspace_access_enforces_roles(database_with_memberships) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.errors import AppError

    access = WorkspaceAccessService(database_with_memberships)
    member = IdentityContext(user_id="member", external_subject="member", display_name="Member")
    assert (await access.require_member(member, "team")).role == "member"
    with pytest.raises(AppError) as exc_info:
        await access.require_manager(member, "team")
    assert (exc_info.value.status_code, exc_info.value.code) == (404, "workspace_not_found")


@pytest.mark.asyncio
async def test_list_workspaces_returns_only_current_user_memberships(api_for_two_users) -> None:
    owner_client, outsider_client = api_for_two_users
    assert [item["id"] for item in (await owner_client.get("/api/workspaces")).json()] == [
        "personal", "team"
    ]
    assert (await outsider_client.get("/api/workspaces")).json() == []


@pytest.mark.asyncio
async def test_missing_identity_uses_stable_401(settings_factory) -> None:
    provider = MissingIdentityProvider(bootstrap_identity=OWNER)
    async with client_with_provider(settings_factory, provider) as client:
        response = await client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_missing"
```

The test helper's `MissingIdentityProvider.resolve` raises `LookupError`, while
`resolve_bootstrap_identity` returns the supplied owner so startup can seed
Workspaces before the request is tested.

- [ ] **Step 2: Run focused tests and verify missing modules fail**

Run:

```bash
uv run pytest tests/test_auth.py tests/test_api.py::test_health_and_workspace_api_do_not_expose_secrets -q
```

Expected: FAIL because the auth and Workspace sync services do not exist.

- [ ] **Step 3: Define identity and role interfaces**

Create `app/auth/models.py`:

```python
from dataclasses import dataclass
from enum import StrEnum


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True)
class IdentityContext:
    user_id: str
    external_subject: str
    display_name: str


@dataclass(frozen=True)
class WorkspaceMembership:
    workspace_id: str
    user_id: str
    role: WorkspaceRole
```

Create `app/auth/provider.py` with an injectable protocol and fixed Mock provider:

```python
from typing import Protocol
from fastapi import Request


class IdentityProvider(Protocol):
    async def resolve(self, request: Request) -> IdentityContext:
        raise NotImplementedError

    async def resolve_bootstrap_identity(self) -> IdentityContext:
        raise NotImplementedError


class MockIdentityProvider:
    def __init__(self, user_id: str, subject: str, display_name: str) -> None:
        self.identity = IdentityContext(user_id, subject, display_name)

    async def resolve(self, request: Request) -> IdentityContext:
        return self.identity

    async def resolve_bootstrap_identity(self) -> IdentityContext:
        return self.identity
```

Add settings with defaults suitable for the checked-in example:

```python
mock_user_id: str = Field(default="mock-user", validation_alias="MOCK_USER_ID")
mock_user_subject: str = Field(default="mock-user", validation_alias="MOCK_USER_SUBJECT")
mock_user_display_name: str = Field(default="Mock User", validation_alias="MOCK_USER_DISPLAY_NAME")
mock_personal_workspace_id: str = Field(default="example", validation_alias="MOCK_PERSONAL_WORKSPACE_ID")
mock_workspace_roles: dict[str, str] = Field(
    default_factory=dict,
    validation_alias="MOCK_WORKSPACE_ROLES",
)
```

Validate every role against `owner/admin/member` and require the personal ID's
role to be `owner`. For backward compatibility with the old single-user app,
the Mock sync gives the Mock user `owner` on every registry Workspace and every
placeholder Workspace already present in the database but not listed in
`MOCK_WORKSPACE_ROLES`; explicit entries override that fallback. This preserves
direct access to legacy Sessions even when their old Workspace is no longer in
the current filesystem registry.

In `settings_factory`, set `MOCK_PERSONAL_WORKSPACE_ID=actual` and
`MOCK_WORKSPACE_ROLES={"actual":"owner"}` so existing test Workspaces have a
deterministic owner. Direct service tests that construct `Database` and
`WorkspaceRegistry` without `create_app` must call
`WorkspaceSyncService.sync(registry.scan(), test_identity)` before creating a
Session; this satisfies the new `sessions.workspace_id` foreign key without
weakening production constraints.

- [ ] **Step 4: Implement Workspace synchronization and access checks**

`WorkspaceSyncService.sync` upserts every registry entry's ID, name, kind, and
normalized non-Skill snapshot; remove `skills` and `skills_root_env` before
writing `WorkspaceRecord.config_json`. `kind` is `personal` only for
`mock_personal_workspace_id`, otherwise `team`. It then upserts the Mock user and
configured membership rows, including fallback owner membership for preexisting
placeholder Workspaces. Do not delete database Workspaces merely because a
registry entry is temporarily invalid or absent.

Implement access methods with Workspace-scoped SQL and identical not-found
behavior:

```python
class WorkspaceAccessService:
    async def require_member(
        self, identity: IdentityContext, workspace_id: str
    ) -> WorkspaceMembership:
        membership = await self._membership(identity.user_id, workspace_id)
        if membership is None:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        return membership

    async def require_manager(self, identity: IdentityContext, workspace_id: str) -> WorkspaceMembership:
        membership = await self.require_member(identity, workspace_id)
        if membership.role not in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        return membership

    async def require_owner(self, identity: IdentityContext, workspace_id: str) -> WorkspaceMembership:
        membership = await self.require_member(identity, workspace_id)
        if membership.role is not WorkspaceRole.OWNER:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        return membership
```

Add `get_identity` and
`Identity = Annotated[IdentityContext, Depends(get_identity)]` to
`app/api/dependencies.py`. If a provider raises its internal no-identity signal,
translate it to `AppError("identity_missing", "Identity is unavailable.", 401)`;
do not fall back to an anonymous or default user inside this dependency.

- [ ] **Step 5: Wire startup and membership-filtered Workspace APIs**

Extend `create_app` with optional `identity_provider` injection for tests. Build
`WorkspaceSyncService` and `WorkspaceAccessService`, add them to `AppServices`,
and order startup as:

```python
await database.initialize()
entries = workspaces.scan()
bootstrap_identity = await identity_provider.resolve_bootstrap_identity()
await workspace_sync.sync(entries, bootstrap_identity)
await database.interrupt_stale_turns()
```

Change `GET /api/workspaces` to query memberships first and then map only those
registry entries. Add `kind`, `role`, and `can_manage_skills` to `WorkspaceOut`.
Change `GET /api/workspaces/{id}` to require membership.

- [ ] **Step 6: Run identity and existing Workspace API tests**

Run:

```bash
uv run pytest tests/test_auth.py tests/test_api.py tests/test_sessions.py \
  tests/test_attachments.py tests/test_turns.py -q
```

Expected: all focused tests pass; secret redaction remains unchanged.

- [ ] **Step 7: Commit the identity boundary**

```bash
git add app/auth app/workspaces/sync.py app/config.py app/api/dependencies.py \
  app/api/routes.py app/api/schemas.py app/main.py tests/conftest.py \
  tests/test_auth.py tests/test_api.py tests/test_sessions.py \
  tests/test_attachments.py tests/test_turns.py .env.example
git commit -m "feat: add workspace identity and roles"
```

---

### Task 3: Enforce membership on every existing Workspace-owned API

**Files:**
- Modify: `app/auth/access.py`
- Modify: `app/api/routes.py:64-220`
- Modify: `tests/test_api.py:70-525`

**Interfaces:**
- Consumes: `Identity`, `WorkspaceAccessService`, and existing Session/Turn/Attachment models.
- Produces: `require_session_member`, `require_turn_member`, and `require_attachment_member`, used by every existing object-ID route.

- [ ] **Step 1: Write failing cross-Workspace API isolation tests**

Add a parametrized test that seeds a Session, Turn, and Attachment in a Workspace
belonging to another user and calls every route by object ID:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/sessions/foreign"),
    ("PATCH", "/api/sessions/foreign"),
    ("DELETE", "/api/sessions/foreign"),
    ("GET", "/api/sessions/foreign/messages"),
    ("GET", "/api/sessions/foreign/skills"),
    ("GET", "/api/sessions/foreign/files"),
    ("GET", "/api/sessions/foreign/attachments"),
    ("POST", "/api/sessions/foreign/attachments"),
    ("POST", "/api/sessions/foreign/turns"),
    ("GET", "/api/turns/foreign-turn"),
    ("GET", "/api/turns/foreign-turn/events"),
    ("POST", "/api/turns/foreign-turn/cancel"),
    ("GET", "/api/attachments/foreign-attachment/content"),
    ("DELETE", "/api/attachments/foreign-attachment"),
])
async def test_existing_resource_routes_hide_foreign_workspace(
    foreign_resource_client, method: str, path: str
) -> None:
    response = await foreign_resource_client.request(method, path, json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] in {
        "workspace_not_found", "session_not_found", "turn_not_found", "attachment_not_found"
    }
```

Use valid request bodies/files in the fixture for POST/PATCH cases so failures
come from authorization rather than request validation.

- [ ] **Step 2: Run the isolation test and verify current routes leak existence**

Run:

```bash
uv run pytest tests/test_api.py -k foreign_workspace -q
```

Expected: multiple failures because current object-ID routes do not resolve membership.

- [ ] **Step 3: Add joined ownership checks**

Implement these joined lookups in `WorkspaceAccessService`:

```python
async def require_session_member(
    self, identity: IdentityContext, session_id: str
) -> SessionRecord:
    record = await self._session_for_member(identity.user_id, session_id)
    if record is None:
        raise AppError("session_not_found", "Session not found.", 404)
    return record

async def require_turn_member(
    self, identity: IdentityContext, turn_id: str
) -> TurnRecord:
    record = await self._turn_for_member(identity.user_id, turn_id)
    if record is None:
        raise AppError("turn_not_found", "Turn not found.", 404)
    return record

async def require_attachment_member(
    self, identity: IdentityContext, attachment_id: str
) -> AttachmentRecord:
    record = await self._attachment_for_member(identity.user_id, attachment_id)
    if record is None:
        raise AppError("attachment_not_found", "Attachment not found.", 404)
    return record
```

Each method performs one query joining the resource through `sessions.workspace_id`
to `workspace_members`, filtered by `identity.user_id`. A missing result raises the
resource-specific 404; it never performs an unrestricted lookup first.

- [ ] **Step 4: Guard every existing route before service work**

Add `identity: Identity` to the Workspace/Session/Turn/Attachment handlers. Call:

```python
await services.access.require_member(identity, workspace_id)
await services.access.require_session_member(identity, session_id)
await services.access.require_turn_member(identity, turn_id)
await services.access.require_attachment_member(identity, attachment_id)
```

before reading messages, opening files, uploading content, starting/cancelling a
Turn, or returning an SSE stream. Preserve all existing response schemas.

- [ ] **Step 5: Run API and SSE regressions**

Run:

```bash
uv run pytest tests/test_api.py tests/test_sse.py tests/test_attachments.py tests/test_turns.py -q
```

Expected: all tests pass, including the new tenant isolation matrix.

- [ ] **Step 6: Commit authorization coverage**

```bash
git add app/auth/access.py app/api/routes.py tests/test_api.py
git commit -m "fix: isolate workspace resources by membership"
```

---

### Task 4: Build the safe Skill bundle parser and deterministic hash

**Files:**
- Modify: `app/config.py`
- Create: `app/skills/__init__.py`
- Create: `app/skills/models.py`
- Create: `app/skills/bundle.py`
- Create: `tests/test_skill_bundle.py`

**Interfaces:**
- Produces: `SkillBundle`, `SkillBundleFile`, `parse_skill_markdown`, `build_bundle`, `load_bundle_from_directory`, and `load_bundle_from_archive`.

- [ ] **Step 1: Write failing hash, binary, and archive-defense tests**

Create tests that establish exact observable behavior:

```python
def test_bundle_hash_is_stable_across_file_order() -> None:
    from app.skills.bundle import build_bundle

    content = b"---\nname: review\ndescription: Review changes\n---\n# Review\n"
    first = build_bundle(content, [("b.bin", b"\x00\x01"), ("a.txt", b"a")])
    second = build_bundle(content, [("a.txt", b"a"), ("b.bin", b"\x00\x01")])
    assert first.bundle_hash == second.bundle_hash
    assert first.files[1].content == b"\x00\x01"


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a\\b", "a/./b", "a//b", "\x00bad"])
def test_bundle_rejects_unsafe_paths(path: str) -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    with pytest.raises(AppError) as exc_info:
        build_bundle(VALID_SKILL_MD, [(path, b"x")])
    assert exc_info.value.code == "invalid_skill_bundle"


def test_archive_rejects_symlink() -> None:
    import io
    import zipfile
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        link = zipfile.ZipInfo("references/link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"../../secret")
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(output.getvalue())
    assert exc_info.value.code == "invalid_skill_bundle"


def test_archive_rejects_uncompressed_limit() -> None:
    import io
    import zipfile
    from app.errors import AppError
    from app.skills.bundle import SkillBundleLimits, load_bundle_from_archive

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
        archive.writestr("large.bin", b"0" * 1025)
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_archive(
            output.getvalue(), SkillBundleLimits(max_total_bytes=1024)
        )
    assert exc_info.value.code == "skill_bundle_too_large"
```

- [ ] **Step 2: Run focused tests and verify missing module failure**

Run:

```bash
uv run pytest tests/test_skill_bundle.py -q
```

Expected: FAIL because `app.skills.bundle` does not exist.

- [ ] **Step 3: Define immutable bundle value objects**

Create `app/skills/models.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SkillBundleFile:
    path: str
    content: bytes
    mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SkillBundle:
    name: str
    description: str
    content: str
    bundle_hash: str
    files: tuple[SkillBundleFile, ...]
```

- [ ] **Step 4: Implement canonical parsing and hashing**

Use a strict simple-name regex and one bounded YAML frontmatter document:

```python
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_VERSION = b"skill-bundle-v1"


def parse_skill_markdown(raw: bytes) -> tuple[str, str, str]:
    text = raw.decode("utf-8")
    if not text.startswith("---\n"):
        raise invalid_bundle("SKILL.md must start with YAML frontmatter.")
    closing = text.find("\n---\n", 4)
    if closing < 0 or closing > 64 * 1024:
        raise invalid_bundle("SKILL.md frontmatter is missing or too large.")
    metadata = yaml.safe_load(text[4:closing])
    name = metadata.get("name") if isinstance(metadata, dict) else None
    description = metadata.get("description") if isinstance(metadata, dict) else None
    if not isinstance(name, str) or not SKILL_NAME_RE.fullmatch(name.strip()):
        raise invalid_bundle("Skill name is invalid.")
    if not isinstance(description, str) or not description.strip():
        raise invalid_bundle("Skill description is required.")
    return name.strip(), description.strip(), text
```

Hash length-prefixed parts so concatenation cannot collide. Sort files by
normalized path, reject duplicates, and use `mimetypes.guess_type(path)` with
`application/octet-stream` fallback.

- [ ] **Step 5: Implement bounded directory and Zip loaders**

`load_bundle_from_archive(raw, limits)` must inspect `ZipInfo` entries before
reading them, require exactly one root `SKILL.md`, reject encrypted entries and
non-regular Unix mode types, sum declared and actual uncompressed sizes, and
never call `extract`/`extractall`.

`load_bundle_from_directory(root, limits)` must reject a symlink at every path
component and read only regular files. Both call `build_bundle` and enforce:

```python
@dataclass(frozen=True)
class SkillBundleLimits:
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    max_files: int = 200
```

Add `MAX_SKILL_FILE_SIZE_MB`, `MAX_SKILL_BUNDLE_SIZE_MB`, and
`MAX_SKILL_FILES` settings with defaults `10`, `50`, and `200`, plus a
`Settings.max_skill_file_size_bytes`, `Settings.max_skill_bundle_size_bytes`,
and `Settings.skill_bundle_limits` properties. The last property returns this
value object using the first two byte values and `max_skill_files`.

- [ ] **Step 6: Run bundle tests**

Run:

```bash
uv run pytest tests/test_skill_bundle.py -q
```

Expected: all path, hash, binary, symlink, count, and size tests pass.

- [ ] **Step 7: Commit the bundle contract**

```bash
git add app/config.py app/skills tests/test_skill_bundle.py
git commit -m "feat: validate skill bundles"
```

---

### Task 5: Implement Skill repository and service behavior

**Files:**
- Create: `app/skills/repository.py`
- Create: `app/skills/service.py`
- Create: `tests/test_skill_service.py`

**Interfaces:**
- Consumes: Task 4 `SkillBundle`; Task 2 `IdentityContext` and access checks.
- Produces: `SkillRepository`, `SkillService.list/get/create/update/set_enabled/archive/copy/import_archive`, and `get_enabled_bundles` for Session creation.

- [ ] **Step 1: Write failing service tests for CRUD, concurrency, copy, and atomic files**

Add tests with two Workspaces and two managers:

```python
@pytest.mark.asyncio
async def test_skill_update_replaces_content_but_preserves_files_and_checks_hash(skill_service) -> None:
    created = await skill_service.create("personal", OWNER, VALID_SKILL_MD.decode())
    updated_md = VALID_SKILL_MD.decode().replace("Review changes", "Review safely")
    updated = await skill_service.update(
        created.id, OWNER, content=updated_md, expected_hash=created.bundle_hash
    )
    assert updated.description == "Review safely"
    with pytest.raises(AppError) as exc_info:
        await skill_service.update(
            created.id, OWNER, content=VALID_SKILL_MD.decode(), expected_hash=created.bundle_hash
        )
    assert exc_info.value.code == "skill_changed"


@pytest.mark.asyncio
async def test_copy_is_independent_disabled_and_requires_both_manager_roles(skill_service) -> None:
    source = await skill_service.import_bundle("personal", OWNER, BUNDLE, enabled=True)
    copied = await skill_service.copy(source.id, "team", OWNER)
    assert copied.id != source.id
    assert copied.bundle_hash == source.bundle_hash
    assert copied.enabled is False
    assert copied.origin["source_skill_id"] == source.id
```

Also test active-name conflict, archive/name reuse, member rejection, binary file
round trip, complete file-set replacement for an imported bundle, and a forced
mid-transaction exception leaving the prior Skill unchanged.

- [ ] **Step 2: Run focused service tests and verify missing service failure**

Run:

```bash
uv run pytest tests/test_skill_service.py -q
```

Expected: FAIL because repository and service classes do not exist.

- [ ] **Step 3: Implement Workspace-scoped repository DTOs and queries**

Define immutable summaries/details:

```python
@dataclass(frozen=True)
class SkillSummary:
    id: str
    workspace_id: str
    name: str
    description: str
    enabled: bool
    bundle_hash: str
    origin: dict[str, object]
    updated_at: datetime


@dataclass(frozen=True)
class StoredSkill:
    id: str
    workspace_id: str
    name: str
    description: str
    content: str
    enabled: bool
    bundle_hash: str
    origin: dict[str, object]
    files: tuple[SkillBundleFile, ...]
    updated_at: datetime
```

Every repository lookup accepts `workspace_id`; ID-only service calls first use
an access-service join to resolve the authorized Workspace. `get_enabled_bundles`
reads Skill rows and all file rows with one `AsyncSession` transaction and
returns fully detached immutable values. Rebuild each bundle from stored bytes
and compare it with `SkillRecord.bundle_hash`; a mismatch raises
`AppError("skill_bundle_corrupt", "Stored Skill bundle is invalid.", 500)` before
Session materialization.

Define and use these repository methods consistently:

- `list_summaries(workspace_id) -> tuple[SkillSummary, ...]`
- `count_active_by_workspace(workspace_ids) -> dict[str, int]`
- `get_stored(workspace_id, skill_id) -> StoredSkill | None`
- `get_for_manager(skill_id, user_id) -> StoredSkill | None`
- `insert_bundle(workspace_id, created_by, bundle, enabled, origin) -> StoredSkill`
- `replace_bundle(skill_id, expected_hash, bundle) -> StoredSkill`
- `set_enabled(skill_id, expected_hash, enabled) -> StoredSkill`
- `archive(skill_id, expected_hash, archived_at) -> bool`
- `get_enabled_bundles(workspace_id) -> tuple[tuple[str, SkillBundle], ...]`

- [ ] **Step 4: Implement service methods with stable errors**

Implement these exact async methods:

- `list(workspace_id, identity) -> tuple[SkillSummary, ...]`
- `get(skill_id, identity) -> StoredSkill`
- `create(workspace_id, identity, content) -> StoredSkill`
- `update(skill_id, identity, *, content, expected_hash) -> StoredSkill`
- `set_enabled(skill_id, identity, *, enabled, expected_hash) -> StoredSkill`
- `archive(skill_id, identity, *, expected_hash) -> None`
- `copy(skill_id, target_workspace_id, identity) -> StoredSkill`
- `import_archive(workspace_id, identity, raw) -> StoredSkill`
- `import_bundle(workspace_id, identity, bundle, *, enabled, origin=None) -> StoredSkill`
- `get_enabled_bundles(workspace_id) -> tuple[tuple[str, SkillBundle], ...]`

The optimistic update path must have this shape:

```python
async def update(
    self,
    skill_id: str,
    identity: IdentityContext,
    *,
    content: str,
    expected_hash: str,
) -> StoredSkill:
    current = await self.repository.get_for_manager(skill_id, identity.user_id)
    if current is None:
        raise AppError("skill_not_found", "Skill not found.", 404)
    if current.bundle_hash != expected_hash:
        raise AppError("skill_changed", "Skill was changed by another manager.", 409)
    bundle = build_bundle(
        content.encode("utf-8"),
        [(file.path, file.content) for file in current.files],
        self.limits,
    )
    return await self.repository.replace_bundle(
        current.id, expected_hash=expected_hash, bundle=bundle
    )
```

Map active unique-index violations to `409 skill_name_conflict`; map hash
mismatch to `409 skill_changed`; use 404 for unauthorized/missing Skill. Manual
create/import and copy use `enabled=False`.
`replace_bundle`, `set_enabled`, and `archive` must include
`WHERE bundle_hash = :expected_hash` in the write itself; a zero-row result maps
to `skill_changed`, preventing content/archive operations from racing past a
bundle change. Two same-hash enable/disable writes use last-writer-wins semantics
in phase one because enablement is not part of the bundle hash.

- [ ] **Step 5: Run service and database tests**

Run:

```bash
uv run pytest tests/test_skill_service.py tests/test_database.py -q
```

Expected: all tests pass with no orphan `skill_files` rows.

- [ ] **Step 6: Commit the domain service**

```bash
git add app/skills/repository.py app/skills/service.py tests/test_skill_service.py
git commit -m "feat: manage workspace skills"
```

---

### Task 6: Expose Skill APIs and stable authorization responses

**Files:**
- Create: `app/skills/schemas.py`
- Create: `app/skills/routes.py`
- Modify: `app/api/schemas.py:1-18`
- Modify: `app/api/routes.py:1-75`
- Modify: `app/api/dependencies.py`
- Modify: `app/main.py:20-99`
- Create: `tests/test_skill_api.py`

**Interfaces:**
- Consumes: Task 5 `SkillService`.
- Produces: the approved `/api/me`, Workspace Skill list/create/import, Skill get/update/copy/archive endpoints.

- [ ] **Step 1: Write failing API contract and role-matrix tests**

Cover JSON create/update, multipart archive import, copy, enable, archive, and
member/outsider behavior:

```python
@pytest.mark.asyncio
async def test_skill_api_create_enable_list_and_archive(manager_client) -> None:
    created = await manager_client.post(
        "/api/workspaces/personal/skills", json={"content": VALID_SKILL_TEXT}
    )
    assert created.status_code == 201
    skill = created.json()
    assert skill["description"] == "Review changes"
    assert skill["enabled"] is False

    enabled = await manager_client.patch(
        f"/api/skills/{skill['id']}",
        json={"expected_hash": skill["bundle_hash"], "enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    archived = await manager_client.request(
        "DELETE", f"/api/skills/{skill['id']}",
        json={"expected_hash": enabled.json()["bundle_hash"]},
    )
    assert archived.status_code == 204


@pytest.mark.asyncio
async def test_member_can_read_but_cannot_mutate(member_client, existing_skill) -> None:
    assert (await member_client.get("/api/workspaces/team/skills")).status_code == 200
    assert (await member_client.get(f"/api/skills/{existing_skill.id}")).status_code == 200
    response = await member_client.patch(
        f"/api/skills/{existing_skill.id}",
        json={"expected_hash": existing_skill.bundle_hash, "enabled": False},
    )
    assert response.status_code == 404
```

Add API assertions that a non-`.skill`/`.zip` filename returns
`422 invalid_skill_bundle`, an invalid archive path returns the same code, and a
stream exceeding `settings.max_skill_bundle_size_bytes` returns
`413 skill_bundle_too_large` without creating a Skill row.

- [ ] **Step 2: Run API tests and verify routes are missing**

Run:

```bash
uv run pytest tests/test_skill_api.py -q
```

Expected: FAIL with 404 for the not-yet-registered routes.

- [ ] **Step 3: Define request/response schemas**

Use discriminated optional update fields while requiring exactly one mutation:

```python
class SkillCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10 * 1024 * 1024)


class SkillUpdate(BaseModel):
    expected_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content: str | None = Field(default=None, min_length=1, max_length=10 * 1024 * 1024)
    enabled: bool | None = None

    @model_validator(mode="after")
    def exactly_one_change(self):
        if (self.content is None) == (self.enabled is None):
            raise ValueError("exactly one of content or enabled is required")
        return self


class SkillArchive(BaseModel):
    expected_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SkillCopy(BaseModel):
    target_workspace_id: str = Field(min_length=2, max_length=64)
```

Responses expose metadata and file manifests, never `content_blob` inline.
`GET /api/skills/{id}` returns `content` plus `{path,mime_type,size_bytes,sha256}`.

- [ ] **Step 4: Implement and register the Skill router**

Register these handlers in `app/skills/routes.py`:

```python
@router.get("/workspaces/{workspace_id}/skills")
@router.post("/workspaces/{workspace_id}/skills", status_code=201)
@router.post("/workspaces/{workspace_id}/skills/import", status_code=201)
@router.get("/skills/{skill_id}")
@router.patch("/skills/{skill_id}")
@router.delete("/skills/{skill_id}", status_code=204)
@router.post("/skills/{skill_id}/copy", status_code=201)
```

Stream `UploadFile` in bounded chunks and abort as soon as compressed upload
bytes exceed `settings.max_skill_bundle_size_bytes`; do not call
`await upload.read()` without
a limit. Accept only `.skill` and `.zip` filenames and reject other archive
types with `422 invalid_skill_bundle`. Add `GET /api/me` to
`app/api/routes.py`. Register the Skill router in
`create_app` and add `skills` to `AppServices`. Replace the registry-manifest
Skill count in `WorkspaceOut` with one grouped
`SkillRepository.count_active_by_workspace(workspace_ids)` query so header counts
reflect managed database Skills without an N+1 query; MCP counts remain from the
validated registry entry.

- [ ] **Step 5: Run Skill and existing API suites**

Run:

```bash
uv run pytest tests/test_skill_api.py tests/test_api.py -q
```

Expected: all tests pass; existing error envelopes retain request IDs.

- [ ] **Step 6: Commit the HTTP API**

```bash
git add app/skills/routes.py app/skills/schemas.py app/api/routes.py \
  app/api/schemas.py app/api/dependencies.py app/main.py tests/test_skill_api.py
git commit -m "feat: expose workspace skill APIs"
```

---

### Task 7: Bootstrap existing Workspace and local Skills idempotently

**Files:**
- Modify: `app/config.py`
- Modify: `app/skills/service.py`
- Modify: `app/workspaces/registry.py`
- Modify: `app/main.py:40-83`
- Create: `tests/test_skill_bootstrap.py`
- Modify: `tests/test_workspaces.py`

**Interfaces:**
- Consumes: registry entries, Task 4 directory loader, `CLAUDE_SKILLS_ROOT`, Mock personal Workspace ID, and `AppMetadataRecord`.
- Produces: `SkillBootstrapService.run(entries, identity, personal_workspace_id, local_root) -> BootstrapReport`.

- [ ] **Step 1: Write failing idempotent and partial-result bootstrap tests**

Create two registry Workspaces sharing an external root. Include one valid
Skill, one conflicting different Skill, and one malformed directory:

```python
@pytest.mark.asyncio
async def test_bootstrap_imports_manifest_and_personal_root_once(bootstrap_fixture) -> None:
    bootstrap, repository, entries, local_root = bootstrap_fixture
    first = await bootstrap.run(entries, OWNER, "personal", local_root)
    second = await bootstrap.run(entries, OWNER, "personal", local_root)

    assert first.created >= 2
    assert first.failed == 1
    assert second.created == 0
    personal = await repository.list_summaries("personal")
    team = await repository.list_summaries("team")
    assert all(item.enabled for item in personal + team)
    assert {item.origin["type"] for item in personal} == {"local_bootstrap"}
```

Assert the completion marker is written only when no failures remain. On retry,
identical name/hash pairs skip; different hashes report conflict without overwrite.

- [ ] **Step 2: Run the bootstrap tests and verify missing service failure**

Run:

```bash
uv run pytest tests/test_skill_bootstrap.py -q
```

Expected: FAIL because `SkillBootstrapService` does not exist.

- [ ] **Step 3: Implement structured per-Skill results**

Add:

```python
@dataclass(frozen=True)
class BootstrapItem:
    workspace_id: str
    name: str
    status: Literal["created", "skipped", "conflict", "failed"]
    message: str = ""


@dataclass(frozen=True)
class BootstrapReport:
    items: tuple[BootstrapItem, ...]

    @property
    def created(self) -> int:
        return sum(item.status == "created" for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.status == "failed" for item in self.items)

    def summary(self) -> dict[str, int]:
        return {
            status: sum(item.status == status for item in self.items)
            for status in ("created", "skipped", "conflict", "failed")
        }
```

Implement the count properties as exact status counts, not stored mutable counters.

- [ ] **Step 4: Implement the one-time import order**

`run` performs:

1. For every available registry entry, import each legacy manifest Skill from
   `entry.skills_source_root` into that Workspace with `enabled=True` and origin
   `{type: "workspace_manifest_bootstrap", source_hash: bundle.bundle_hash}`.
2. Import every direct child containing root `SKILL.md` from
   `CLAUDE_SKILLS_ROOT` into the Mock personal Workspace with `enabled=True` and
   origin `{type: "local_bootstrap", source_hash: bundle.bundle_hash}`.
3. Skip identical active name/hash pairs.
4. Report different active name/hash pairs as conflict.
5. Write `app_metadata["workspace_skill_import_v1"]` only when no item failed;
   conflict is a completed reviewed result, not a retryable parser failure.

Each Skill uses a separate transaction. Log only Workspace ID, Skill name,
status, and safe message; never log source file contents.

- [ ] **Step 5: Stop treating legacy Skill paths as runtime Workspace validity**

Change `WorkspaceRegistry` so `skills` and `skills_root_env` are parsed and their
source root is recorded for bootstrap, but a missing legacy Skill or missing
legacy Skill-root environment variable no longer makes the Workspace invalid.
MCP, model, Workspace path, manifest shape, and tool validation remain unchanged.

Replace the current missing-Skill registry assertion with:

```python
def test_registry_keeps_workspace_available_when_legacy_skill_source_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(root, "actual", skills=("missing",))
    shutil.rmtree(root / "actual/.claude/skills/missing")
    entry = WorkspaceRegistry(root, "model", environ={}).scan()[0]
    assert entry.available is True
    assert entry.skills_source_root == root / "actual/.claude/skills"
```

The bootstrap report, not Workspace availability, now communicates an invalid
legacy Skill. This prevents a deleted old source directory from disabling a
Workspace after managed copies exist.

- [ ] **Step 6: Wire bootstrap after Workspace/user synchronization**

Startup order becomes:

```python
await database.initialize()
entries = workspaces.scan()
bootstrap_identity = await identity_provider.resolve_bootstrap_identity()
await workspace_sync.sync(entries, bootstrap_identity)
report = await skill_bootstrap.run(
    entries,
    bootstrap_identity,
    resolved_settings.mock_personal_workspace_id,
    resolved_settings.claude_skills_root,
)
logger.info("Skill bootstrap completed", extra={"result": report.summary()})
await database.interrupt_stale_turns()
```

If `CLAUDE_SKILLS_ROOT` is unset, manifest-local Skills still migrate; the full
personal-root step is skipped rather than failing startup.

- [ ] **Step 7: Run bootstrap and Workspace regressions**

Run:

```bash
uv run pytest tests/test_skill_bootstrap.py tests/test_workspaces.py tests/test_api.py -q
```

Expected: all tests pass and repeat app startup does not duplicate rows.

- [ ] **Step 8: Commit bootstrap migration**

```bash
git add app/config.py app/skills/service.py app/workspaces/registry.py app/main.py \
  tests/test_skill_bootstrap.py tests/test_workspaces.py
git commit -m "feat: import existing workspace skills"
```

---

### Task 8: Pin enabled Skill bundles into new Session snapshots

**Files:**
- Create: `app/sessions/snapshot.py`
- Modify: `app/workspaces/materializer.py:22-106`
- Modify: `app/sessions/service.py:28-68`
- Modify: `app/sessions/catalog.py:22-175`
- Modify: `app/main.py:35-75`
- Modify: `tests/test_sessions.py:1-137`
- Modify: `tests/test_session_catalog.py`
- Modify: `tests/test_workspaces.py:277-390`

**Interfaces:**
- Consumes: `SkillService.get_enabled_bundles(workspace_id)` and existing `WorkspaceEntry`.
- Produces: `build_session_snapshot(entry, stored_bundles) -> SessionSnapshot`;
  `materialize_session_workspace(entry, session_id, data_dir, snapshot, bundles)`;
  schema-v2 catalog behavior.

- [ ] **Step 1: Write failing Session immutability and enablement tests**

Add a Session test that creates two managed Skills, disables one, creates a
Session, edits the enabled Skill, then creates another Session:

```python
@pytest.mark.asyncio
async def test_sessions_pin_enabled_managed_skill_bundles(session_skill_fixture) -> None:
    sessions, skills, owner, data_dir = session_skill_fixture
    first_skill = await skills.import_bundle("actual", owner, BUNDLE_V1, enabled=True)
    await skills.import_bundle("actual", owner, OTHER_BUNDLE, enabled=False)

    first = await sessions.create("actual")
    first_path = data_dir / first.session_dir / "workspace/.claude/skills/review/SKILL.md"
    assert first_path.read_text(encoding="utf-8") == BUNDLE_V1.content
    assert not (first_path.parent.parent / "disabled-skill").exists()

    await skills.update(
        first_skill.id, owner, content=BUNDLE_V2.content,
        expected_hash=first_skill.bundle_hash,
    )
    second = await sessions.create("actual")
    second_path = data_dir / second.session_dir / "workspace/.claude/skills/review/SKILL.md"

    assert first_path.read_text(encoding="utf-8") == BUNDLE_V1.content
    assert second_path.read_text(encoding="utf-8") == BUNDLE_V2.content
    assert json.loads(first.workspace_snapshot_json)["skills"][0]["bundle_hash"] != (
        json.loads(second.workspace_snapshot_json)["skills"][0]["bundle_hash"]
    )
```

Add forced materializer and database-commit failure tests asserting no temp/final
orphan directory. Add a binary supporting-file byte equality assertion.

- [ ] **Step 2: Run focused tests and verify they fail on legacy manifest materialization**

Run:

```bash
uv run pytest tests/test_sessions.py tests/test_session_catalog.py -q
```

Expected: failures because Session creation still reads `manifest.skills` and external links.

- [ ] **Step 3: Build the versioned snapshot in one focused module**

Create:

```python
@dataclass(frozen=True)
class SessionSnapshot:
    data: dict[str, Any]
    json: str
    sha256: str


def build_session_snapshot(
    entry: WorkspaceEntry,
    skills: tuple[tuple[str, SkillBundle], ...],
) -> SessionSnapshot:
    data = json.loads(entry.snapshot_json or "{}")
    data["schema_version"] = 2
    data.pop("skills_root_env", None)
    data["skills"] = [
        {
            "id": skill_id,
            "name": bundle.name,
            "description": bundle.description,
            "bundle_hash": bundle.bundle_hash,
            "files": [
                {"path": file.path, "sha256": file.sha256, "size_bytes": file.size_bytes}
                for file in bundle.files
            ],
        }
        for skill_id, bundle in sorted(skills, key=lambda item: item[1].name.casefold())
    ]
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return SessionSnapshot(data, raw, hashlib.sha256(raw.encode()).hexdigest())
```

- [ ] **Step 4: Materialize only regular managed bundle files**

Change `materialize_session_workspace` to accept the prepared `SessionSnapshot`
and detached bundles. Remove the loop over `entry.manifest.skills` and all
new-Session symlink creation. For each bundle:

```python
skill_dir = skill_target / bundle.name
skill_dir.mkdir()
(skill_dir / "SKILL.md").write_text(bundle.content, encoding="utf-8")
for file in bundle.files:
    target = skill_dir.joinpath(*PurePosixPath(file.path).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(file.content)
    written_hash = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    if written_hash != file.sha256:
        raise AppError("skill_materialization_failed", "Skill materialization failed.", 500)
if load_bundle_from_directory(skill_dir, limits).bundle_hash != bundle.bundle_hash:
    raise AppError("skill_materialization_failed", "Skill materialization failed.", 500)
```

Write `workspace.snapshot.yaml` from `snapshot.data`. Keep the existing temp
directory, atomic rename, and cleanup behavior.

- [ ] **Step 5: Make Session service read bundles once**

Inject `SkillService` into `SessionService`. `create` calls
`get_enabled_bundles`, builds the snapshot, materializes those exact detached
bytes, and persists `snapshot.json`/`snapshot.sha256`. No later code path calls
the live Skill service for that Session.

Update `list_skills` so schema v2 returns name/description directly from
snapshot manifest dictionaries. Preserve the current bounded filesystem reader
only for legacy string-name snapshots.

- [ ] **Step 6: Run Session, catalog, runtime, and Workspace tests**

Run:

```bash
uv run pytest tests/test_sessions.py tests/test_session_catalog.py \
  tests/test_workspaces.py tests/test_claude_runtime.py -q
```

Expected: managed Sessions are immutable and legacy Session catalog tests still pass.

- [ ] **Step 7: Commit Session pinning**

```bash
git add app/sessions/snapshot.py app/sessions/service.py app/sessions/catalog.py \
  app/workspaces/materializer.py app/main.py tests/test_sessions.py \
  tests/test_session_catalog.py tests/test_workspaces.py
git commit -m "feat: pin skills to session snapshots"
```

---

### Task 9: Add the Workspace Skill management interface

**Files:**
- Create: `app/web/static/skill-manager.js`
- Create: `tests/js/test_skill_manager.cjs`
- Modify: `app/web/templates/index.html:10-145`
- Modify: `app/web/static/app.js:1-265`
- Modify: `app/web/static/app.css:70-985`
- Modify: `tests/test_web_page.py`

**Interfaces:**
- Consumes: Task 6 Skill APIs and Workspace fields `kind`, `role`, `can_manage_skills`.
- Produces: `SkillManager.createController({api, elements, getWorkspace, onChanged, onError})` and a full-screen accessible management dialog.

- [ ] **Step 1: Write failing controller tests for role-aware list and conflicts**

Use the existing fake-DOM pattern in the autocomplete tests:

```javascript
test("manager loads skills and member cannot mutate", async () => {
  const calls = [];
  const elements = createSkillManagerElements();
  const controller = SkillManager.createController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      return path.endsWith("/skills")
        ? [{id: "s1", name: "review", description: "Review changes", enabled: true,
            bundle_hash: "sha256:" + "a".repeat(64), origin: {type: "local_bootstrap"}}]
        : null;
    },
    elements,
    getWorkspace: () => ({id: "team", role: "member", can_manage_skills: false}),
    onChanged: () => {},
    onError: () => {},
  });
  await controller.open();
  assert.equal(elements.newButton.hidden, true);
  assert.equal(elements.list.children[0].textContent.includes("Review changes"), true);
});


test("409 skill_changed preserves editor content", async () => {
  const elements = createSkillManagerElements();
  const errors = [];
  const controller = SkillManager.createController({
    api: async (path, options = {}) => {
      if (!options.method && path.endsWith("/skills")) {
        return [{id: "s1", name: "review", description: "Review", enabled: true,
          bundle_hash: "sha256:" + "a".repeat(64), origin: {type: "local_bootstrap"}}];
      }
      if (!options.method && path.endsWith("/skills/s1")) {
        return {id: "s1", content: "old content", bundle_hash: "sha256:" + "a".repeat(64), files: []};
      }
      const error = new Error("Skill changed");
      error.code = "skill_changed";
      throw error;
    },
    elements,
    getWorkspace: () => ({id: "team", role: "admin", can_manage_skills: true}),
    getWorkspaces: () => [{id: "team", can_manage_skills: true}],
    onChanged: () => {},
    onError: (error) => errors.push(error),
  });
  await controller.open();
  await controller.selectSkill("s1");
  elements.editor.value = "unsaved content";
  await controller.save();
  assert.equal(elements.editor.value, "unsaved content");
  assert.match(elements.conflict.textContent, /其他管理员/u);
  assert.equal(errors.length, 0);
});
```

- [ ] **Step 2: Run Node tests and verify missing module failure**

Run:

```bash
node --test tests/js/test_skill_manager.cjs
```

Expected: FAIL because `skill-manager.js` does not exist.

- [ ] **Step 3: Implement a state-isolated UI controller**

The controller owns `workspaceId`, request generation, selected Skill, editor
hash, and pending state. It must ignore responses for an earlier Workspace.
Expose only:

```javascript
return {
  open,
  close,
  refresh,
  selectSkill,
  createSkill,
  save,
  setEnabled,
  importArchive,
  copyToWorkspace,
  archiveSkill,
};
```

Render with `textContent`, never `innerHTML`. Use `FormData` for imports. On a
successful mutation, refresh the list and call `onChanged(workspaceId)` so
`app.js` updates the header count. On `skill_changed`, keep editor text and show
the conflict element; on Workspace switch, invalidate pending requests.

- [ ] **Step 4: Add accessible management markup and Workspace badges**

In `index.html`:

- replace the plain Skill capability pill with `button#skillsButton`;
- add `span#workspaceKind` next to the selector;
- add `dialog#skillManagerDialog` with list, new/import/copy buttons, editor,
  supporting-file manifest, conflict message, and close/save/archive controls;
- include `/static/skill-manager.js` before `/static/app.js`.

Every input has a label; the list has a loading and empty state; destructive
archive uses a confirmation dialog. Members can open/read the view but mutation
controls are hidden and disabled.

- [ ] **Step 5: Wire `app.js` without growing another domain controller**

Initialize once:

```javascript
state.skillManager = SkillManager.createController({
  api,
  elements: skillManagerElements,
  getWorkspace: () => state.workspace,
  getWorkspaces: () => state.workspaces,
  onChanged: async (workspaceId) => {
    if (state.workspace?.id !== workspaceId) return;
    state.workspaces = await api("/api/workspaces");
    state.workspace = state.workspaces.find((item) => item.id === workspaceId) || null;
    renderWorkspaceOptions();
    renderCapabilitySummary();
  },
  onError: (error) => showToast(error.message),
});
```

On Workspace switch, close/reset the manager. Existing Session autocomplete
continues using `/api/sessions/{id}/skills`; do not replace it with live
Workspace results. When there is no persisted active Session yet, load the
current Workspace Skill list and expose only `enabled === true` items to the
composer. Add a controller test proving that a Workspace switch drops disabled
and previous-Workspace candidates, while an existing Session switch still uses
the Session endpoint.

- [ ] **Step 6: Add compact responsive styles and HTML smoke assertions**

Add styles for a full-height desktop dialog and a single-column mobile view.
Rows show name, one-line ellipsized description with `title`, origin, enabled
state, and actions. Add `tests/test_web_page.py` assertions for all IDs, script
order, accessible labels, and no inline event handlers.

- [ ] **Step 7: Run Node and web-page tests**

Run:

```bash
node --test tests/js/test_skill_manager.cjs tests/js/test_composer_autocomplete.cjs
uv run pytest tests/test_web_page.py -q
```

Expected: all tests pass and existing autocomplete behavior is unchanged.

- [ ] **Step 8: Commit the management UI**

```bash
git add app/web/static/skill-manager.js app/web/static/app.js \
  app/web/static/app.css app/web/templates/index.html \
  tests/js/test_skill_manager.cjs tests/test_web_page.py
git commit -m "feat: add workspace skill manager"
```

---

### Task 10: Add browser acceptance, migration docs, and full verification

**Files:**
- Modify: `tests/browser/test_workbench.py`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: the complete backend and frontend feature.
- Produces: end-to-end evidence for personal import, team copy/enable, member restrictions, Session pinning, and the 77-item scroll regression.

- [ ] **Step 1: Add role-aware live browser fixtures**

Refactor the live fixture to accept an injected `MockIdentityProvider` and
seed personal/team Workspace membership plus 77 valid Skills. Return both the
base URL and test service handles needed to edit a Skill behind the browser.
Keep the existing `live_url` fixture as a compatibility wrapper so unrelated
browser tests do not change.

- [ ] **Step 2: Write the complete manager and Session-pinning browser flow**

Add one end-to-end test that:

1. opens the personal Workspace and the Skill manager;
2. imports a `.skill` Zip through the file input;
3. enables it and confirms the header count changes;
4. creates a Session, types `/`, and sees the imported description;
5. edits the Skill in the manager and creates a second Session;
6. confirms the first Session still shows the old description and the second
   shows the new one;
7. copies the Skill to the team Workspace, confirms it starts disabled, enables
   it as admin, and confirms a team Session can use it.

Use Playwright `expect` assertions against stable IDs/classes. Do not inspect
database rows directly in this browser test.

- [ ] **Step 3: Extend the 77-item regression to Workspace switching**

Preserve the current wheel and repeated-ArrowDown assertions. Add:

```python
await page.locator("#workspaceSelect").select_option("personal")
await page.locator("#newSessionButton").click()
await page.locator("#messageInput").fill("/")
menu = page.locator("#composerAutocomplete")
await expect(menu.locator('[role="option"]')).to_have_count(77)
await menu.hover()
await page.mouse.wheel(0, 5000)
await expect(menu.locator('[role="option"]:last-child')).to_be_in_viewport()

await page.locator("#workspaceSelect").select_option("team")
await page.locator("#newSessionButton").click()
await page.locator("#messageInput").fill("/")
await expect(page.locator("#composerAutocomplete")).not_to_contain_text("personal-only")
```

- [ ] **Step 4: Add a member read-only browser test**

Start an app with a member identity. Assert the team Skill list and descriptions
are visible, every mutation control is absent/disabled, and a team Session can
use enabled Skills. Intercept a direct PATCH request and assert the server still
returns 404.

- [ ] **Step 5: Update operator documentation**

Update README and `.env.example` with:

```env
MOCK_USER_ID=mock-user
MOCK_USER_SUBJECT=mock-user
MOCK_USER_DISPLAY_NAME=Mock User
MOCK_PERSONAL_WORKSPACE_ID=example
MOCK_WORKSPACE_ROLES={"example":"owner"}
MAX_SKILL_FILE_SIZE_MB=10
MAX_SKILL_BUNDLE_SIZE_MB=50
MAX_SKILL_FILES=200
```

Replace the single-user warning with the precise Mock-auth boundary: Mock mode is
for local/trusted deployment and must not be exposed publicly. Document that
`workspace.yaml.skills` and `skills_root_env` are bootstrap inputs only after
migration, new Sessions use managed regular copies, old symlink Sessions keep
legacy behavior, and Skill management is available from the header Skill count.

- [ ] **Step 6: Run focused browser and migration acceptance**

Run:

```bash
uv run pytest tests/browser/test_workbench.py -k "skill_manager or skill_autocomplete" -q
uv run pytest tests/test_migrations.py tests/test_skill_bootstrap.py tests/test_sessions.py -q
```

Expected: all focused browser, migration, bootstrap, and Session tests pass.

- [ ] **Step 7: Run all automated verification**

Run:

```bash
node --test tests/js/*.cjs
uv run pytest -q
uv build
python - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheel = max(Path("dist").glob("*.whl"), key=lambda path: path.stat().st_mtime)
with ZipFile(wheel) as archive:
    names = set(archive.namelist())
assert any(name.endswith("app/db/alembic/env.py") for name in names)
assert any(name.endswith("app/db/alembic/versions/rev_0002_workspace_skills.py") for name in names)
PY
git diff --check
```

Expected: all Node tests and all pytest tests pass, the built wheel contains
the Alembic runtime and revision files, and `git diff --check` prints no output.

- [ ] **Step 8: Inspect the final migration on a copied legacy database**

Copy, never mutate, a local legacy database and run startup against the copy:

```bash
cp data/app.db /tmp/claude-workspace-skill-migration.db
DATABASE_URL=sqlite+aiosqlite:////tmp/claude-workspace-skill-migration.db \
  uv run python -c 'import asyncio; from app.config import Settings; from app.db.base import Database; s=Settings(); d=Database(s.resolved_database_url); asyncio.run(d.initialize())'
sqlite3 /tmp/claude-workspace-skill-migration.db \
  'select version_num from alembic_version; select count(*) from sessions; select count(*) from workspaces;'
```

Expected: Alembic is at head, the pre-migration Session count is unchanged, and
every distinct legacy Session Workspace has a Workspace row. Remove only the
temporary copy afterward.

- [ ] **Step 9: Commit docs and acceptance coverage**

```bash
git add tests/browser/test_workbench.py README.md .env.example
git commit -m "test: cover workspace skill workflows"
```

---

## Final Review Checklist

- [ ] Every Workspace/Session/Turn/Attachment/Skill route goes through the access service.
- [ ] No new Session contains a Skill symlink.
- [ ] Existing Session snapshots and directories are not rewritten.
- [ ] Active Session autocomplete comes only from its persisted snapshot.
- [ ] Member mutation is blocked in both UI and server tests.
- [ ] Import limits are enforced on compressed and uncompressed bytes.
- [ ] Batch bootstrap is idempotent and never silently overwrites a conflict.
- [ ] Skill copy is independent and disabled by default.
- [ ] Current unrelated user files are still untouched.
- [ ] All focused and full verification commands have fresh passing output.
