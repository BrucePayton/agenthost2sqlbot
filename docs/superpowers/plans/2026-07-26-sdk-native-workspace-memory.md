# SDK-Native Workspace Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each authenticated user automatic Claude Code memory across private Sessions in the same Workspace while keeping Sessions and memory isolated from other users and Workspaces.

**Architecture:** Keep the existing SQLite page history and Claude Session `resume` flow. Add creator ownership to Sessions, derive one server-controlled Auto Memory directory per `(user_id, workspace_id)`, serialize same-scope Turns, and pass a private settings file containing `autoMemoryDirectory` to the Claude Agent SDK. Team-wide knowledge remains the existing Workspace `CLAUDE.md`, Skills, and MCP configuration.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, Alembic, SQLite, Claude Agent SDK `>=0.2.126,<0.3`, pytest, vanilla JavaScript, Playwright.

## Global Constraints

- A personal data-center space and a team data-center space each map to one Workspace.
- Every Session has one non-null creator and remains visible only to that creator; do not add publishing or sharing fields.
- Auto Memory is personal and scoped by both user and Workspace; do not implement cross-Workspace or team-writable memory.
- Team guidance continues to come from Workspace `CLAUDE.md`, Skills, and MCP services.
- Keep SQLite and one mounted `APP_DATA_DIR` persistent volume; do not add PostgreSQL, object storage, Redis, Mem0, Graphiti, Letta, embeddings, or vector search.
- Keep each Session's existing `CLAUDE_CONFIG_DIR`, `cwd`, and exact SDK `resume` behavior.
- Pass `autoMemoryDirectory` through a server-created settings JSON file because Python SDK `ClaudeAgentOptions.settings` is a file path.
- Derive memory scope only from `IdentityContext.user_id` and the authorized Session's `workspace_id`; never accept a memory path, user ID, or Workspace scope from the browser or model.
- Serialize Turns that share one `(user_id, workspace_id)` memory scope in the single-process release. Different scopes remain concurrent.
- `APP_DATA_DIR` and its memory root must be writable at startup. Do not silently fall back to host `~/.claude` memory.
- Keep Mock identity clearly documented as trusted-development-only. Do not claim hostile multi-tenant production readiness until filesystem/container isolation and the real data-center `IdentityProvider` exist.
- Existing uncommitted changes in `app/runtime/claude.py` and `tests/test_claude_runtime.py` are a separate Skill compatibility fix. Preserve them and either commit them before execution or carry them deliberately into the isolated implementation worktree.
- Preserve unrelated untracked `agents.json`, `description.md`, `members.json`, and `squads.json`.

---

## File Structure

### New files

- `app/db/alembic/versions/rev_0004_session_ownership.py` — add non-null Session creator ownership and a safe legacy-owner bridge.
- `app/memory/__init__.py` — memory package boundary.
- `app/memory/scopes.py` — create, validate, and readiness-check opaque user/Workspace memory directories.
- `app/memory/locks.py` — serialize Turns that can write the same Auto Memory files.
- `tests/test_memory_scopes.py` — path isolation, permissions, symlink rejection, and readiness tests.
- `tests/test_memory_locks.py` — same-scope serialization and different-scope concurrency tests.
- `tests/live/test_claude_memory.py` — opt-in real SDK cross-Session Auto Memory smoke test.

### Modified files

- `app/db/models.py` — add `SessionRecord.created_by` and its query index.
- `app/sessions/service.py` — require a creator on creation, filter Workspace lists by creator, and claim legacy Sessions after identity sync.
- `app/auth/access.py` — replace membership-only resource checks with creator-aware Session, Turn, and Attachment checks.
- `app/api/routes.py` — pass trusted identity into Session creation/listing and use creator-aware checks.
- `app/api/schemas.py` — report that personal Workspace memory is enabled without exposing paths.
- `app/api/dependencies.py` — expose `MemoryScopeService` through `AppServices`.
- `app/runtime/base.py` — add trusted `memory_scope_key` and `memory_dir` fields to `RuntimeRequest`.
- `app/runtime/claude.py` — atomically create the private SDK settings file and pass it through `ClaudeAgentOptions.settings`.
- `app/turns/service.py` — resolve the owner scope and hold its memory lock for the complete SDK run.
- `app/main.py` — construct memory services, initialize readiness, claim legacy ownership, and wire dependencies.
- `app/web/static/app.js` — render a non-interactive `个人记忆` capability pill.
- `README.md` — document private Sessions, memory layout, persistence, backup, and single-instance limits.
- `tests/test_migrations.py` — verify revision `0004`, creator FK/index, and legacy preservation.
- `tests/test_database.py` — seed creator rows for direct Session model fixtures.
- `tests/test_sessions.py` — verify creator persistence and owner-filtered listing.
- `tests/test_auth.py` — verify same-Workspace members cannot access each other's Sessions.
- `tests/test_api.py` — verify all protected routes, health output, and creator-scoped lists.
- `tests/test_attachments.py` — pass creator IDs to direct Session creation calls.
- `tests/test_turns.py` — pass creator IDs and verify resolved memory scope on runtime requests.
- `tests/test_runtime_events.py` — construct `RuntimeRequest` with memory scope fields.
- `tests/test_claude_runtime.py` — verify settings-file contents, permissions, isolation, and stable errors.
- `tests/test_web_page.py` — verify the personal-memory indicator is shipped.
- `tests/live/test_claude_smoke.py` — update direct service construction for creator and memory dependencies.

---

### Task 1: Persist Session Creator Ownership Safely

**Files:**
- Create: `app/db/alembic/versions/rev_0004_session_ownership.py`
- Modify: `app/db/models.py`
- Modify: `app/sessions/service.py`
- Modify: `app/main.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_sessions.py`
- Modify: `tests/test_attachments.py`
- Modify: `tests/test_turns.py`
- Modify: `tests/test_api.py`
- Modify: `tests/live/test_claude_smoke.py`

**Interfaces:**
- Consumes: existing `UserRecord`, `SessionRecord`, `IdentityContext.user_id`, and startup order `database.initialize()` then `workspace_sync.sync(...)`.
- Produces: `SessionRecord.created_by: str`, `SessionService.create(workspace_id: str, created_by: str)`, `SessionService.claim_legacy_sessions(created_by: str) -> int`, and constant `LEGACY_SESSION_OWNER_ID` exported from the migration or a shared literal documented in `SessionService`.

- [ ] **Step 1: Add failing fresh-schema and legacy-upgrade assertions**

Update `tests/test_migrations.py` so the fresh schema expects revision `0004`, a non-null `sessions.created_by` column, FK `fk_sessions_creator`, and composite index `ix_sessions_creator_workspace_updated`.

```python
session_columns = {
    column["name"]: column
    for column in await connection.run_sync(
        lambda sync: inspect(sync).get_columns("sessions")
    )
}
session_indexes = await connection.run_sync(
    lambda sync: inspect(sync).get_indexes("sessions")
)
session_foreign_keys = await connection.run_sync(
    lambda sync: inspect(sync).get_foreign_keys("sessions")
)
assert session_columns["created_by"]["nullable"] is False
assert {
    index["name"] for index in session_indexes
} >= {"ix_sessions_creator_workspace_updated"}
assert {
    foreign_key["name"]: foreign_key["constrained_columns"]
    for foreign_key in session_foreign_keys
} == {
    "fk_sessions_workspace": ["workspace_id"],
    "fk_sessions_creator": ["created_by"],
}
assert migration_head == "0004"
```

Extend the legacy test to assert that the old Session survives and has the reserved creator:

```python
legacy_creator = await connection.scalar(
    text("SELECT created_by FROM sessions WHERE id = 'legacy-session'")
)
assert legacy_creator == "legacy-session-owner"
```

- [ ] **Step 2: Run migration tests and verify the expected failure**

Run:

```bash
uv run pytest tests/test_migrations.py::test_migrations_create_current_schema_on_fresh_database tests/test_migrations.py::test_migrations_adopt_legacy_schema_and_preserve_session -q
```

Expected: FAIL because revision `0004` and `sessions.created_by` do not exist.

- [ ] **Step 3: Add the model field and index**

Add this field to `SessionRecord` in `app/db/models.py`:

```python
created_by: Mapped[str] = mapped_column(
    String(36),
    ForeignKey("users.id", ondelete="RESTRICT"),
    nullable=False,
)
```

Replace the existing Session list index declaration with both query shapes:

```python
__table_args__ = (
    Index("ix_sessions_workspace_updated", "workspace_id", "updated_at"),
    Index(
        "ix_sessions_creator_workspace_updated",
        "created_by",
        "workspace_id",
        "updated_at",
    ),
)
```

- [ ] **Step 4: Implement migration revision `0004`**

Create `app/db/alembic/versions/rev_0004_session_ownership.py` with this sequence:

```python
"""Add private Session creator ownership.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

LEGACY_SESSION_OWNER_ID = "legacy-session-owner"


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("created_by", sa.String(36), nullable=True))

    bind = op.get_bind()
    session_count = bind.execute(sa.text("SELECT count(*) FROM sessions")).scalar_one()
    if session_count:
        existing = bind.execute(
            sa.text("SELECT 1 FROM users WHERE id = :id"),
            {"id": LEGACY_SESSION_OWNER_ID},
        ).first()
        if existing is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO users (
                        id, external_subject, display_name, provider,
                        created_at, updated_at
                    ) VALUES (
                        :id, :subject, 'Legacy Session Owner', 'migration',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": LEGACY_SESSION_OWNER_ID,
                    "subject": "migration:legacy-session-owner",
                },
            )
        bind.execute(
            sa.text("UPDATE sessions SET created_by = :id WHERE created_by IS NULL"),
            {"id": LEGACY_SESSION_OWNER_ID},
        )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column("created_by", nullable=False)
        batch_op.create_foreign_key(
            "fk_sessions_creator",
            "users",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_sessions_creator_workspace_updated",
        "sessions",
        ["created_by", "workspace_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_creator_workspace_updated", table_name="sessions")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("fk_sessions_creator", type_="foreignkey")
        batch_op.drop_column("created_by")
    op.get_bind().execute(
        sa.text("DELETE FROM users WHERE id = :id"),
        {"id": LEGACY_SESSION_OWNER_ID},
    )
```

- [ ] **Step 5: Require creator identity on new Sessions**

Change `SessionService.create` to:

```python
async def create(self, workspace_id: str, created_by: str) -> SessionRecord:
    entry = self.registry.get(workspace_id)
    bundles = await self.skills.get_enabled_bundles(workspace_id)
    snapshot = build_session_snapshot(entry, bundles)
    session_id = str(uuid.uuid4())
    materialized = materialize_session_workspace(
        entry,
        session_id,
        self.data_dir,
        snapshot,
        bundles,
        limits=self.skills.limits,
    )
    now = datetime.now(UTC)
    record = SessionRecord(
        id=session_id,
        workspace_id=workspace_id,
        created_by=created_by,
        title="新会话",
        title_source="auto",
        status="idle",
        workspace_snapshot_json=snapshot.json,
        workspace_snapshot_hash=snapshot.sha256,
        session_dir=materialized.relative_session_dir,
        created_at=now,
        updated_at=now,
    )
    committed = False
    try:
        async with self.database.session() as db:
            db.add(record)
            await db.commit()
            committed = True
    except Exception:
        if not committed:
            shutil.rmtree(materialized.session_dir, ignore_errors=True)
        raise
    return record
```

Change `POST /api/workspaces/{workspace_id}/sessions` to call:

```python
return SessionOut.model_validate(
    await services.sessions.create(workspace_id, identity.user_id)
)
```

Update every direct `sessions.create(...)` test call in the files listed for this task to pass the synchronized bootstrap user ID. Use `settings.mock_user_id` in fixtures that already hold settings and the literal `"mock-user"` only in isolated tests whose settings use defaults.

In `tests/test_database.py`, import `UserRecord`, insert the creator before each direct `SessionRecord`, and set `created_by`:

```python
db.add(
    UserRecord(
        id="owner",
        external_subject="owner",
        display_name="Owner",
        provider="test",
        created_at=now,
        updated_at=now,
    )
)
db.add(
    WorkspaceRecord(
        id="example", name="Example", kind="team", config_json="{}"
    )
)
await db.flush()
db.add(
    SessionRecord(
        id="session-1",
        workspace_id="example",
        created_by="owner",
        title="Session",
        title_source="auto",
        status="idle",
        workspace_snapshot_json="{}",
        workspace_snapshot_hash="hash",
        session_dir="sessions/session-1",
        created_at=now,
        updated_at=now,
    )
)
```

Set `created_by="outsider"` on the foreign Session fixture in `tests/test_api.py`, and `created_by=settings.mock_user_id` on the active-Session fixture created inside the normal application lifespan.

- [ ] **Step 6: Claim reserved legacy Sessions after identity synchronization**

Add to `SessionService`:

```python
LEGACY_SESSION_OWNER_ID = "legacy-session-owner"

async def claim_legacy_sessions(self, created_by: str) -> int:
    async with self.database.session() as db:
        result = await db.execute(
            update(SessionRecord)
            .where(SessionRecord.created_by == LEGACY_SESSION_OWNER_ID)
            .values(created_by=created_by)
        )
        claimed = int(result.rowcount or 0)
        if claimed:
            legacy = await db.get(UserRecord, LEGACY_SESSION_OWNER_ID)
            if legacy is not None:
                await db.delete(legacy)
        await db.commit()
    return claimed
```

Import `update` and `UserRecord`. In `app/main.py`, call it immediately after Workspace synchronization and before Skill bootstrap:

```python
await workspace_sync.sync(entries, bootstrap_identity)
await sessions.claim_legacy_sessions(bootstrap_identity.user_id)
```

- [ ] **Step 7: Add creator persistence and startup handoff tests**

Add to `tests/test_sessions.py`:

```python
created = await sessions.create("actual", owner.user_id)
assert created.created_by == owner.user_id
assert (await sessions.get(created.id)).created_by == owner.user_id
```

Add this startup handoff shape to `tests/test_api.py` (use the file's existing `write_workspace` helper and runtime fixture):

```python
@pytest.mark.asyncio
async def test_startup_claims_legacy_sessions_for_bootstrap_user(
    settings_factory,
) -> None:
    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    database = Database(settings.resolved_database_url)
    await database.initialize()
    now = datetime.now(UTC)
    async with database.session() as db:
        db.add(
            UserRecord(
                id=LEGACY_SESSION_OWNER_ID,
                external_subject="migration:legacy-session-owner",
                display_name="Legacy Session Owner",
                provider="migration",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            WorkspaceRecord(
                id="actual",
                name="Actual",
                kind="team",
                config_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            SessionRecord(
                id="legacy-session",
                workspace_id="actual",
                created_by=LEGACY_SESSION_OWNER_ID,
                title="Legacy",
                title_source="auto",
                status="idle",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/legacy-session",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    await database.dispose()

    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app):
        async with app.state.services.database.session() as db:
            session = await db.get(SessionRecord, "legacy-session")
            legacy_user = await db.get(UserRecord, LEGACY_SESSION_OWNER_ID)
        assert session is not None
        assert session.created_by == settings.mock_user_id
        assert legacy_user is None
```

- [ ] **Step 8: Run ownership tests**

Run:

```bash
uv run pytest tests/test_migrations.py tests/test_database.py tests/test_sessions.py tests/test_attachments.py tests/test_turns.py tests/test_sse.py tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit Session ownership persistence**

```bash
git add app/db/models.py app/db/alembic/versions/rev_0004_session_ownership.py app/sessions/service.py app/main.py app/api/routes.py tests/test_migrations.py tests/test_database.py tests/test_sessions.py tests/test_attachments.py tests/test_turns.py tests/test_api.py tests/live/test_claude_smoke.py
git commit -m "feat: persist private session ownership"
```

---

### Task 2: Enforce Creator-Private Sessions Across Every API

**Files:**
- Modify: `app/auth/access.py`
- Modify: `app/sessions/service.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `SessionRecord.created_by` from Task 1 and existing Workspace membership checks.
- Produces: `WorkspaceAccessService.require_session_owner`, `require_turn_owner`, `require_attachment_owner`, and `SessionService.list_for_workspace(workspace_id: str, created_by: str)`.

- [ ] **Step 1: Add a same-Workspace cross-user denial test**

Extend the two-user API fixture so `member` has a `WorkspaceMemberRecord` for `team`, while the owner creates a Session in that Workspace. Assert the member sees an empty Session list:

```python
owner_session = await owner_client.post("/api/workspaces/team/sessions")
assert owner_session.status_code == 201

member_list = await member_client.get(
    "/api/workspaces/team/sessions",
    headers={"X-Test-User": "member"},
)
assert member_list.status_code == 200
assert member_list.json() == []
```

Parameterize all Session-derived routes using that Session ID and assert `404` with the existing resource-specific not-found code for the member.

- [ ] **Step 2: Run the new API isolation test and verify it fails**

Run:

```bash
uv run pytest tests/test_api.py -k "same_workspace and private" -q
```

Expected: FAIL because membership currently exposes every Session in the team Workspace.

- [ ] **Step 3: Filter Session lists by creator**

Change `SessionService.list_for_workspace` to:

```python
async def list_for_workspace(
    self,
    workspace_id: str,
    created_by: str,
    limit: int = 200,
) -> list[SessionRecord]:
    async with self.database.session() as db:
        return list(
            (
                await db.scalars(
                    select(SessionRecord)
                    .where(
                        SessionRecord.workspace_id == workspace_id,
                        SessionRecord.created_by == created_by,
                    )
                    .order_by(SessionRecord.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        )
```

Pass `identity.user_id` from `GET /api/workspaces/{workspace_id}/sessions`.

- [ ] **Step 4: Replace membership-only resource queries with owner-aware queries**

In `WorkspaceAccessService`, use these predicates:

```python
.where(
    SessionRecord.id == session_id,
    SessionRecord.created_by == identity.user_id,
    WorkspaceMemberRecord.user_id == identity.user_id,
)
```

For Turns and Attachments, join `SessionRecord` and add:

```python
SessionRecord.created_by == identity.user_id
```

Expose methods named:

```python
require_session_owner(identity, session_id)
require_turn_owner(identity, turn_id)
require_attachment_owner(identity, attachment_id)
```

Keep the current `session_not_found`, `turn_not_found`, and `attachment_not_found` responses so ownership cannot be enumerated.

- [ ] **Step 5: Route every derived resource through owner-aware checks**

Replace calls in `app/api/routes.py` for:

```text
GET/PATCH/DELETE /sessions/{session_id}
GET /sessions/{session_id}/messages
GET /sessions/{session_id}/skills
GET /sessions/{session_id}/files
GET/POST /sessions/{session_id}/attachments
POST /sessions/{session_id}/turns
GET /turns/{turn_id}
GET /turns/{turn_id}/events
POST /turns/{turn_id}/cancel
GET/DELETE /attachments/{attachment_id}
```

Use only `require_*_owner`; do not leave a membership-only alias on any public route.

- [ ] **Step 6: Add service-level owner filtering tests**

In `tests/test_sessions.py`, create two users and two Sessions in one team Workspace, then assert:

```python
assert [item.id for item in await service.list_for_workspace("team", "owner")] == [
    owner_session.id
]
assert [item.id for item in await service.list_for_workspace("team", "member")] == [
    member_session.id
]
```

In `tests/test_auth.py`, assert a member passes `require_member` for the team Workspace but receives `session_not_found` for the owner's Session.

- [ ] **Step 7: Run private Session tests**

Run:

```bash
uv run pytest tests/test_auth.py tests/test_sessions.py tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit private authorization**

```bash
git add app/auth/access.py app/sessions/service.py app/api/routes.py tests/test_auth.py tests/test_api.py tests/test_sessions.py
git commit -m "feat: isolate sessions by creator"
```

---

### Task 3: Create Safe Persistent Memory Scopes and Readiness

**Files:**
- Create: `app/memory/__init__.py`
- Create: `app/memory/scopes.py`
- Create: `tests/test_memory_scopes.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/main.py`
- Modify: `app/api/routes.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `Settings.app_data_dir`, trusted internal user ID, and authorized Workspace ID.
- Produces: `MemoryScope(key: str, directory: Path)`, `MemoryScopeService.initialize() -> None`, `MemoryScopeService.resolve(user_id: str, workspace_id: str) -> MemoryScope`, and `MemoryScopeService.ready: bool`.

- [ ] **Step 1: Write failing scope-isolation tests**

Create `tests/test_memory_scopes.py` with these core assertions:

```python
def test_memory_scope_is_stable_and_isolated(tmp_path: Path) -> None:
    service = MemoryScopeService(tmp_path / "data")
    service.initialize()

    first = service.resolve("user-a", "workspace-a")
    same = service.resolve("user-a", "workspace-a")
    other_user = service.resolve("user-b", "workspace-a")
    other_workspace = service.resolve("user-a", "workspace-b")

    assert first == same
    assert len({first.directory, other_user.directory, other_workspace.directory}) == 3
    assert first.directory.is_relative_to(service.root)
    assert "user-a" not in str(first.directory)
    assert "workspace-a" not in str(first.directory)
```

Add tests that hostile strings such as `"../../other"` remain opaque and that a symlink at `APP_DATA_DIR/memories` raises `AppError` with code `memory_unavailable`.

- [ ] **Step 2: Run scope tests and verify they fail**

Run:

```bash
uv run pytest tests/test_memory_scopes.py -q
```

Expected: FAIL because `app.memory.scopes` does not exist.

- [ ] **Step 3: Implement opaque scope keys and directory containment**

Create `app/memory/scopes.py` around these interfaces:

```python
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import uuid

from app.errors import AppError


@dataclass(frozen=True)
class MemoryScope:
    key: str
    directory: Path


def _opaque_component(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


class MemoryScopeService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / "memories"
        self.ready = False

    def initialize(self) -> None:
        if self.root.is_symlink():
            raise self._unavailable()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        probe = self.root / f".write-probe-{uuid.uuid4().hex}"
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            probe.unlink()
        except OSError as exc:
            probe.unlink(missing_ok=True)
            raise self._unavailable() from exc
        self.ready = True

    def resolve(self, user_id: str, workspace_id: str) -> MemoryScope:
        if not self.ready:
            raise self._unavailable()
        user_key = _opaque_component("u", user_id)
        workspace_key = _opaque_component("w", workspace_id)
        users_root = self.root / "users"
        user_root = users_root / user_key
        workspaces_root = user_root / "workspaces"
        directory = workspaces_root / workspace_key
        for path in (users_root, user_root, workspaces_root, directory):
            if path.is_symlink():
                raise self._unavailable()
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path, 0o700)
        resolved = directory.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise self._unavailable()
        return MemoryScope(key=f"{user_key}/{workspace_key}", directory=resolved)

    @staticmethod
    def _unavailable() -> AppError:
        return AppError(
            "memory_unavailable",
            "Personal Workspace memory is unavailable.",
            503,
        )
```

Keep `app/memory/__init__.py` empty so callers import concrete types from their defining modules.

- [ ] **Step 4: Wire readiness into application startup and health**

Add `memory_scopes: MemoryScopeService` to `AppServices`. In `create_app`, construct one instance from `resolved_settings.app_data_dir`, store it in services, and call this inside lifespan after `APP_DATA_DIR` creation and before serving requests:

```python
memory_scopes.initialize()
```

Extend `/api/health` with:

```python
"memory": "ok" if services.memory_scopes.ready else "unavailable",
```

Update the exact health assertion in `tests/test_api.py` to include `"memory": "ok"`.

- [ ] **Step 5: Test readiness failure without relying on host chmod behavior**

Monkeypatch `os.open` in `tests/test_memory_scopes.py` to raise `PermissionError` only for `.write-probe-*` and assert:

```python
with pytest.raises(AppError) as exc_info:
    service.initialize()
assert exc_info.value.code == "memory_unavailable"
assert service.ready is False
```

- [ ] **Step 6: Run memory scope and API tests**

Run:

```bash
uv run pytest tests/test_memory_scopes.py tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit memory scopes**

```bash
git add app/memory/__init__.py app/memory/scopes.py app/api/dependencies.py app/main.py app/api/routes.py tests/test_memory_scopes.py tests/test_api.py
git commit -m "feat: add isolated workspace memory scopes"
```

---

### Task 4: Configure Claude Code Auto Memory Through a Private Settings File

**Files:**
- Modify: `app/runtime/base.py`
- Modify: `app/runtime/claude.py`
- Modify: `tests/test_runtime_events.py`
- Modify: `tests/test_claude_runtime.py`

**Interfaces:**
- Consumes: `MemoryScope.key`, `MemoryScope.directory`, Session-specific `claude_config_dir`, and existing `ClaudeAgentOptions` creation.
- Produces: `RuntimeRequest.memory_scope_key: str`, `RuntimeRequest.memory_dir: Path`, and `ClaudeAgentRuntime._write_memory_settings(request: RuntimeRequest) -> Path`.

- [ ] **Step 1: Extend the runtime test request with trusted memory fields**

Change the `RuntimeRequest` helper in `tests/test_runtime_events.py` to create a memory directory and pass both fields:

```python
memory_dir = tmp_path / ".personal-memory"
memory_dir.mkdir(exist_ok=True)
return RuntimeRequest(
    platform_session_id="platform-session",
    claude_session_id="claude-session",
    cwd=tmp_path,
    claude_config_dir=tmp_path / ".claude-config",
    memory_scope_key="u-test/w-test",
    memory_dir=memory_dir,
    text="hello",
    attachments=(),
    file_references=(),
    workspace_snapshot={
        "allowed_tools": ["Read"],
        "mcp_servers": {},
        "model": "qwen3.7-plus",
        "skills": [],
    },
)
```

- [ ] **Step 2: Add failing settings-file tests**

In `tests/test_claude_runtime.py`, extend `test_build_options_injects_only_required_environment`:

```python
settings_path = Path(options.settings)
assert settings_path == request.claude_config_dir / "memory-settings.json"
assert json.loads(settings_path.read_text(encoding="utf-8")) == {
    "autoMemoryDirectory": str(request.memory_dir),
    "autoMemoryEnabled": True,
}
assert settings_path.stat().st_mode & 0o777 == 0o600
```

Add one test passing a relative `memory_dir` and one monkeypatching `os.replace` to raise `OSError`; both must raise `AppError(code="memory_unavailable")` and must not create an options object that falls back to host memory. The replacement-failure test must also assert that no `.memory-settings-*.tmp` file remains.

- [ ] **Step 3: Run runtime tests and verify failure**

Run:

```bash
uv run pytest tests/test_claude_runtime.py tests/test_runtime_events.py -q
```

Expected: FAIL because `RuntimeRequest` lacks memory fields and `options.settings` is unset.

- [ ] **Step 4: Add memory fields to `RuntimeRequest`**

In `app/runtime/base.py` add after `claude_config_dir`:

```python
memory_scope_key: str
memory_dir: Path
```

Do not make either field optional. A Turn without an application-resolved memory scope must fail before the runtime starts.

- [ ] **Step 5: Atomically write the SDK settings file**

Add this focused helper to `ClaudeAgentRuntime`:

```python
def _write_memory_settings(self, request: RuntimeRequest) -> Path:
    if (
        not request.memory_dir.is_absolute()
        or request.memory_dir.is_symlink()
        or not request.memory_dir.is_dir()
    ):
        raise AppError(
            "memory_unavailable",
            "Personal Workspace memory is unavailable.",
            503,
        )
    if request.claude_config_dir.is_symlink():
        raise AppError(
            "memory_unavailable",
            "Personal Workspace memory is unavailable.",
            503,
        )
    request.claude_config_dir.mkdir(parents=True, exist_ok=True)
    target = request.claude_config_dir / "memory-settings.json"
    if target.is_symlink():
        raise AppError(
            "memory_unavailable",
            "Personal Workspace memory is unavailable.",
            503,
        )
    temporary = request.claude_config_dir / (
        f".memory-settings-{uuid.uuid4().hex}.tmp"
    )
    payload = {
        "autoMemoryDirectory": str(request.memory_dir),
        "autoMemoryEnabled": True,
    }
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AppError(
            "memory_unavailable",
            "Personal Workspace memory is unavailable.",
            503,
        ) from exc
    return target
```

Add `import uuid` to `app/runtime/claude.py`. The unique `O_EXCL` temporary file and the symlink check prevent a pre-created file from redirecting the settings write.

Call it once at the start of `build_options` and pass:

```python
settings=str(memory_settings_path),
```

Do not add `autoMemoryDirectory` to the materialized project settings and do not change `setting_sources=["project"]`.

- [ ] **Step 6: Verify settings do not disturb Skill and MCP options**

Keep the existing assertions for `options.skills`, `allowed_tools`, MCP server conversion, `resume`, `cwd`, and the child environment. Add:

```python
assert options.env["CLAUDE_CONFIG_DIR"] == str(request.claude_config_dir)
assert Path(options.settings).parent == request.claude_config_dir
```

- [ ] **Step 7: Run runtime tests**

Run:

```bash
uv run pytest tests/test_claude_runtime.py tests/test_runtime_events.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit SDK Auto Memory configuration**

```bash
git add app/runtime/base.py app/runtime/claude.py tests/test_runtime_events.py tests/test_claude_runtime.py
git commit -m "feat: configure sdk auto memory"
```

---

### Task 5: Resolve and Serialize Memory Scope During Turns

**Files:**
- Create: `app/memory/locks.py`
- Create: `tests/test_memory_locks.py`
- Modify: `app/turns/service.py`
- Modify: `app/main.py`
- Modify: `tests/test_turns.py`
- Modify: `tests/live/test_claude_smoke.py`

**Interfaces:**
- Consumes: `MemoryScopeService.resolve`, `RuntimeRequest.memory_scope_key`, and `RuntimeRequest.memory_dir`.
- Produces: `MemoryScopeLockRegistry.acquire(scope_key: str) -> AsyncIterator[None]`; `TurnService` constructor arguments `memory_scopes` and `memory_locks`.

- [ ] **Step 1: Write lock behavior tests**

Create `tests/test_memory_locks.py` with two async tests. The first holds one scope and proves a second acquisition waits; the second proves another scope enters immediately:

```python
@pytest.mark.asyncio
async def test_same_memory_scope_serializes() -> None:
    registry = MemoryScopeLockRegistry()
    attempting = asyncio.Event()
    entered = asyncio.Event()

    async def contender() -> None:
        attempting.set()
        async with registry.acquire("u-a/w-a"):
            entered.set()

    async with registry.acquire("u-a/w-a"):
        task = asyncio.create_task(contender())
        await asyncio.wait_for(attempting.wait(), timeout=1)
        await asyncio.sleep(0)
        assert entered.is_set() is False
    await asyncio.wait_for(task, timeout=1)
    assert entered.is_set() is True


@pytest.mark.asyncio
async def test_different_memory_scopes_remain_concurrent() -> None:
    registry = MemoryScopeLockRegistry()
    entered = asyncio.Event()

    async def contender() -> None:
        async with registry.acquire("u-b/w-a"):
            entered.set()

    async with registry.acquire("u-a/w-a"):
        task = asyncio.create_task(contender())
        await asyncio.wait_for(entered.wait(), timeout=1)
    await task
    assert entered.is_set() is True
```

- [ ] **Step 2: Run lock tests and verify failure**

Run:

```bash
uv run pytest tests/test_memory_locks.py -q
```

Expected: FAIL because `MemoryScopeLockRegistry` does not exist.

- [ ] **Step 3: Implement the in-process scope lock**

Create `app/memory/locks.py`:

```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class MemoryScopeLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, scope_key: str) -> AsyncIterator[None]:
        async with self._registry_lock:
            lock = self._locks.setdefault(scope_key, asyncio.Lock())
        async with lock:
            yield

    def locked(self, scope_key: str) -> bool:
        lock = self._locks.get(scope_key)
        return bool(lock and lock.locked())
```

- [ ] **Step 4: Resolve the memory scope in `_runtime_request`**

Add `memory_scopes: MemoryScopeService` and `memory_locks: MemoryScopeLockRegistry` to `TurnService.__init__`. After loading the Session in `_runtime_request`, derive:

```python
memory_scope = self.memory_scopes.resolve(
    session.created_by,
    session.workspace_id,
)
```

Populate:

```python
memory_scope_key=memory_scope.key,
memory_dir=memory_scope.directory,
```

No public Turn API accepts these values.

- [ ] **Step 5: Hold the memory lock for the complete SDK run**

In `_run_turn`, append a progress event after request construction and acquire the lock before entering the existing runtime timeout:

```python
await self._append_event(
    turn_id,
    progress_event("waiting_memory", "正在准备个人 Workspace 记忆"),
)
async with self.memory_locks.acquire(request.memory_scope_key):
    if cancel_event.is_set():
        raise RuntimeCancelled()
    async with asyncio.timeout(self.timeout_seconds):
        async for event in self.runtime.run(request, cancel_event):
            if event.type == "runtime.result":
                result_payload = event.payload
                continue
            if event.type == "usage.updated":
                usage_payload = event.payload
            await self._append_event(turn_id, event)
```

Keep `turn_timeout_seconds` around only the runtime execution, not time waiting for another same-scope Turn. The currently running Turn has its own timeout and will release the lock.

- [ ] **Step 6: Wire one registry into the application**

In `app/main.py` create:

```python
memory_locks = MemoryScopeLockRegistry()
```

Pass the existing `memory_scopes` and the new lock registry to `TurnService`. Update direct constructors in `tests/test_turns.py` and both live test files to use the same interfaces.

- [ ] **Step 7: Verify request scoping in Turn tests**

Extend the successful Turn test:

```python
request = runtime.requests[0]
expected = turns.memory_scopes.resolve(session.created_by, session.workspace_id)
assert request.memory_scope_key == expected.key
assert request.memory_dir == expected.directory
```

Add a test that creates two Sessions for the same creator and Workspace, starts both against a gated fake runtime, and asserts the runtime's maximum concurrent call count is `1`. Create a second owner/Workspace pair and assert its Turn can enter while the first scope is still running.

- [ ] **Step 8: Run Turn and lock tests**

Run:

```bash
uv run pytest tests/test_memory_locks.py tests/test_turns.py tests/test_sse.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit memory Turn wiring**

```bash
git add app/memory/locks.py app/turns/service.py app/main.py tests/test_memory_locks.py tests/test_turns.py tests/live/test_claude_smoke.py
git commit -m "feat: serialize workspace memory access"
```

---

### Task 6: Surface the Personal Memory Capability Without Adding Management UI

**Files:**
- Modify: `app/api/schemas.py`
- Modify: `app/api/routes.py`
- Modify: `app/web/static/app.js`
- Modify: `tests/test_api.py`
- Modify: `tests/test_web_page.py`
- Modify: `tests/browser/test_workbench.py`

**Interfaces:**
- Consumes: `MemoryScopeService.ready` and existing `WorkspaceOut` capability summary.
- Produces: `WorkspaceOut.personal_memory_enabled: bool` and a non-interactive `个人记忆` pill.

- [ ] **Step 1: Add failing API and page tests**

In `tests/test_api.py`, assert every available Workspace response includes:

```python
assert workspace["personal_memory_enabled"] is True
```

In `tests/test_web_page.py`, assert `app.js` contains the exact user-facing copy `个人记忆` and `仅当前用户和当前 Workspace 可用`.

- [ ] **Step 2: Run UI contract tests and verify failure**

Run:

```bash
uv run pytest tests/test_api.py::test_health_and_workspace_api_do_not_expose_secrets tests/test_web_page.py -q
```

Expected: FAIL because the field and pill do not exist.

- [ ] **Step 3: Add the Workspace response field**

Add to `WorkspaceOut`:

```python
personal_memory_enabled: bool
```

Populate it in `_workspace_out`:

```python
personal_memory_enabled=services.memory_scopes.ready,
```

Do not expose the memory root, scope key, owner ID, settings path, or any memory content.

- [ ] **Step 4: Render a non-interactive capability pill**

After the MCP pill in `renderCapabilitySummary`, append:

```javascript
if (state.workspace.personal_memory_enabled) {
  const memory = document.createElement("span");
  memory.className = "capability-pill";
  memory.textContent = "个人记忆";
  memory.title = "仅当前用户和当前 Workspace 可用";
  elements.capabilitySummary.append(memory);
}
```

Do not add a click handler, dialog, edit button, or team-memory wording.

- [ ] **Step 5: Add a browser assertion**

In the existing workbench browser setup, select an available Workspace and assert:

```python
memory_pill = page.get_by_text("个人记忆", exact=True)
await expect(memory_pill).to_be_visible()
await expect(memory_pill).to_have_attribute(
    "title",
    "仅当前用户和当前 Workspace 可用",
)
```

- [ ] **Step 6: Run API, page, and browser tests**

Run:

```bash
uv run pytest tests/test_api.py tests/test_web_page.py tests/browser/test_workbench.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit the capability indicator**

```bash
git add app/api/schemas.py app/api/routes.py app/web/static/app.js tests/test_api.py tests/test_web_page.py tests/browser/test_workbench.py
git commit -m "feat: show personal workspace memory"
```

---

### Task 7: Prove Cross-Session Auto Memory With the Real SDK

**Files:**
- Create: `tests/live/test_claude_memory.py`

**Interfaces:**
- Consumes: creator-aware `SessionService`, `MemoryScopeService`, `MemoryScopeLockRegistry`, `TurnService`, and the real `ClaudeAgentRuntime`.
- Produces: an opt-in live acceptance test proving same-user/same-Workspace recall across distinct Sessions.

- [ ] **Step 1: Build the live fixture with production-equivalent startup order**

In `tests/live/test_claude_memory.py`, use the existing `RUN_LIVE_CLAUDE_TESTS=1` skip marker. Build services in this order:

```python
await database.initialize()
entries = registry.scan()
identity = IdentityContext("live-user", "live-user", "Live User")
await WorkspaceSyncService(database, settings).sync(entries, identity)
memory_scopes = MemoryScopeService(settings.app_data_dir)
memory_scopes.initialize()
memory_locks = MemoryScopeLockRegistry()
```

Construct `SessionService`, `AttachmentService`, and `TurnService` with those dependencies. Use one available Workspace and create both Sessions with `identity.user_id`.

- [ ] **Step 2: Write the first real-memory Turn**

Use a unique marker and explicit instruction so Auto Memory writing is observable:

```python
marker = f"WORKSPACE-MEMORY-{uuid.uuid4().hex}"
first_session = await sessions.create(workspace.id, identity.user_id)
first_turn = await turns.start(
    first_session.id,
    f"请把精确标记 {marker} 写入 Auto Memory，完成后只回复 SAVED。",
    [],
    "memory-write",
)
await turns.wait(first_turn.id)
assert (await turns.get(first_turn.id)).status == "completed"
```

Read the resolved scope directory and assert at least one UTF-8 Markdown file contains the marker:

```python
scope = memory_scopes.resolve(identity.user_id, workspace.id)
memory_files = list(scope.directory.rglob("*.md"))
assert memory_files
assert any(
    marker in path.read_text(encoding="utf-8")
    for path in memory_files
)
```

- [ ] **Step 3: Recall from a distinct Session**

Create a second Session with the same user and Workspace and ask:

```python
second_session = await sessions.create(workspace.id, identity.user_id)
second_turn = await turns.start(
    second_session.id,
    "读取 Auto Memory，只回复之前保存的 WORKSPACE-MEMORY 标记。",
    [],
    "memory-read",
)
await turns.wait(second_turn.id)
```

Load `message.assistant` events for the second Session and assert their serialized payload contains the exact marker. Also assert the two Session `claude-config` directories differ while resolving their owner/Workspace pairs produces the same memory scope:

```python
second_messages = await sessions.list_messages(second_session.id)
assistant_payloads = [
    message.payload_json
    for message in second_messages
    if message.event_type == "message.assistant.completed"
]
assert marker in "\n".join(assistant_payloads)
assert (
    sessions.session_path(first_session) / "claude-config"
    != sessions.session_path(second_session) / "claude-config"
)
assert (
    memory_scopes.resolve(first_session.created_by, first_session.workspace_id)
    == memory_scopes.resolve(second_session.created_by, second_session.workspace_id)
)
```

- [ ] **Step 4: Run the live memory test explicitly**

Run:

```bash
RUN_LIVE_CLAUDE_TESTS=1 uv run pytest tests/live/test_claude_memory.py -q -s
```

Expected: PASS with two completed real-model Turns, a persisted Markdown memory file, and exact-marker recall from the second Session. This command incurs model usage.

- [ ] **Step 5: Run the existing live smoke test**

Run:

```bash
RUN_LIVE_CLAUDE_TESTS=1 uv run pytest tests/live/test_claude_smoke.py -q -s
```

Expected: PASS for existing exact Session resume and image input behavior.

- [ ] **Step 6: Commit live verification**

```bash
git add tests/live/test_claude_memory.py
git commit -m "test: verify cross-session auto memory"
```

---

### Task 8: Document Persistence, Limits, and Release Gates

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the implemented storage layout and runtime behavior from Tasks 1–7.
- Produces: operator and user documentation that does not overstate team memory or production isolation.

- [ ] **Step 1: Update the capability list and identity boundary**

Add these exact concepts to the README capability section:

```markdown
- Session 在团队 Workspace 中仍只对创建者可见。
- 同一用户在同一 Workspace 的不同 Session 共享 Claude Code Auto Memory。
- Auto Memory 不跨 Workspace，也不会自动写入团队共享知识。
```

Keep the existing warning that Mock identity is for trusted deployments only.

- [ ] **Step 2: Document the complete persistent layout**

Extend the Session section with:

```text
APP_DATA_DIR/
├── app.db
├── sessions/<session-id>/
│   ├── workspace/
│   └── claude-config/
└── memories/users/<opaque-user>/workspaces/<opaque-workspace>/
    ├── MEMORY.md
    └── <topic>.md
```

State explicitly that `APP_DATA_DIR` must be a mounted persistent volume and that backup/restore must cover the complete directory, not only SQLite.

- [ ] **Step 3: Document first-release operational limits**

Add a subsection stating:

```markdown
- 当前只支持一个应用实例。
- 多实例前必须迁移数据库、Claude transcript、Memory 存储和作用域锁。
- Auto Memory 文件是明文；数巢多人部署前必须完成可信身份与运行时文件系统隔离。
- 当前没有团队动态记忆、跨 Workspace 记忆或独立记忆管理页面。
```

- [ ] **Step 4: Run documentation and full regression checks**

Run:

```bash
git diff --check
uv run pytest -q
```

Expected: no whitespace errors; the full suite passes with live tests skipped unless explicitly enabled.

- [ ] **Step 5: Verify migration and browser paths separately**

Run:

```bash
uv run pytest tests/test_migrations.py -q
uv run pytest tests/browser/test_workbench.py -q
```

Expected: both commands PASS.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md
git commit -m "docs: document workspace memory persistence"
```

---

## Final Acceptance Checklist

- [ ] `git status --short` contains no unexpected staged or modified files from this feature.
- [ ] `uv run pytest -q` passes.
- [ ] `RUN_LIVE_CLAUDE_TESTS=1 uv run pytest tests/live/test_claude_memory.py tests/live/test_claude_smoke.py -q -s` passes against the configured real runtime.
- [ ] Two Sessions owned by the same user in one Workspace share Auto Memory but not Claude transcript directories.
- [ ] The same user in two Workspaces has different Auto Memory directories.
- [ ] Two users in one team Workspace cannot list or access each other's Sessions and have different Auto Memory directories.
- [ ] The SDK settings file is mode `0600`, names only the current scope, and never points at host default memory.
- [ ] The memory root is mode `0700` where supported and application readiness fails if it is unavailable.
- [ ] Restarting the service with the same mounted `APP_DATA_DIR` preserves Session resume and Auto Memory recall.
- [ ] The UI says `个人记忆` and never claims team memory or cross-Workspace recall.
- [ ] README continues to state that Mock identity and the current runtime are trusted-deployment-only.
