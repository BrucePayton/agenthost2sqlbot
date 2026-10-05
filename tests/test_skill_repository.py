from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, update


def _bundle(name: str, *, body: str = "body", files=()):
    from app.skills.bundle import build_bundle

    return build_bundle(
        (
            f"---\nname: {name}\ndescription: {name} description\n---\n# {body}\n"
        ).encode(),
        files,
    )


async def _insert(
    repository,
    store,
    bundle,
    *,
    scope: str,
    workspace_id: str | None,
    enabled: bool = True,
):
    from app.skills.artifacts import build_skill_artifact

    artifact = build_skill_artifact(bundle)
    await asyncio.to_thread(store.put, artifact)
    return await repository.insert_versioned_skill(
        scope=scope,
        workspace_id=workspace_id,
        created_by="owner-a",
        bundle=bundle,
        artifact=artifact,
        enabled=enabled,
        origin={"type": "test"},
    )


@pytest.fixture
async def repository_fixture(
    tmp_path: Path,
) -> AsyncIterator[tuple[object, object, object]]:
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
    from app.skills.artifacts import FilesystemSkillArtifactStore
    from app.skills.bundle import SkillBundleLimits
    from app.skills.repository import SkillRepository

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'repository.db'}")
    await database.initialize()
    async with database.session() as db:
        db.add_all(
            [
                UserRecord(
                    id="owner-a",
                    external_subject="owner-a",
                    display_name="Owner A",
                    provider="test",
                ),
                UserRecord(
                    id="owner-b",
                    external_subject="owner-b",
                    display_name="Owner B",
                    provider="test",
                ),
                WorkspaceRecord(
                    id="personal-a", name="Personal A", kind="team", config_json="{}"
                ),
                WorkspaceRecord(
                    id="personal-b", name="Personal B", kind="team", config_json="{}"
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                WorkspaceMemberRecord(
                    workspace_id="personal-a", user_id="owner-a", role="owner"
                ),
                WorkspaceMemberRecord(
                    workspace_id="personal-b", user_id="owner-b", role="owner"
                ),
            ]
        )
        await db.flush()
        await db.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == "personal-a")
            .values(kind="personal", owner_user_id="owner-a", template_id="default")
        )
        await db.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == "personal-b")
            .values(kind="personal", owner_user_id="owner-b", template_id="default")
        )
        await db.commit()
    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    await asyncio.to_thread(store.initialize)
    repository = SkillRepository(database, store, SkillBundleLimits())
    try:
        yield repository, store, database
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_catalog_defaults_global_enabled_and_separates_personal(
    repository_fixture,
) -> None:
    repository, store, _database = repository_fixture
    await _insert(
        repository,
        store,
        _bundle("global-review"),
        scope="global",
        workspace_id=None,
    )
    await _insert(
        repository,
        store,
        _bundle("my-review"),
        scope="workspace",
        workspace_id="personal-a",
    )

    catalog = await repository.list_catalog("personal-a")

    assert [
        (item.name, item.scope, item.enabled) for item in catalog.global_skills
    ] == [("global-review", "global", True)]
    assert [
        (item.name, item.scope, item.enabled) for item in catalog.personal_skills
    ] == [("my-review", "workspace", True)]
    assert catalog.effective_count == 2


@pytest.mark.asyncio
async def test_workspace_global_setting_disables_only_one_workspace(
    repository_fixture,
) -> None:
    repository, store, _database = repository_fixture
    skill = await _insert(
        repository,
        store,
        _bundle("global-review"),
        scope="global",
        workspace_id=None,
    )

    changed = await repository.set_global_setting(
        "personal-a", skill.id, False, updated_by="owner-a"
    )

    assert changed.enabled is False
    assert (await repository.list_catalog("personal-a")).global_skills[
        0
    ].enabled is False
    assert (await repository.list_catalog("personal-b")).global_skills[
        0
    ].enabled is True


@pytest.mark.asyncio
async def test_recreated_global_skill_inherits_workspace_choice(repository_fixture):
    """Recreating a global name preserves choices in catalog, counts and sessions."""
    repository, store, _database = repository_fixture
    old = await _insert(repository, store, _bundle("data-discovery"),
                        scope="global", workspace_id=None)
    await repository.set_global_setting(
        "personal-a", old.id, False, updated_by="owner-a"
    )
    await repository.archive_skill(old.id, old.bundle_hash, datetime.now(UTC))
    replacement = await _insert(
        repository, store, _bundle("Data-Discovery", body="replacement"),
        scope="global", workspace_id=None,
    )
    assert replacement.id != old.id
    catalog = await repository.list_catalog("personal-a")
    assert [(item.id, item.enabled) for item in catalog.global_skills] == [
        (replacement.id, False)
    ]
    assert await repository.list_effective_versions("personal-a") == ()
    assert await repository.count_effective_by_workspace(
        ["personal-a", "personal-b"]
    ) == {"personal-a": 0, "personal-b": 1}
    # An explicit choice on the replacement wins and survives another recreation.
    await repository.set_global_setting(
        "personal-a", replacement.id, True, updated_by="owner-a"
    )
    assert (await repository.list_catalog("personal-a")).global_skills[0].enabled
    await repository.archive_skill(
        replacement.id, replacement.bundle_hash, datetime.now(UTC)
    )
    newest = await _insert(repository, store, _bundle("data-discovery", body="third"),
                           scope="global", workspace_id=None)
    assert [item.id for item in await repository.list_effective_versions(
        "personal-a"
    )] == [newest.id]


@pytest.mark.asyncio
async def test_personal_insert_defaults_enabled_and_persists_ready_version(
    repository_fixture,
) -> None:
    from app.db.models import SkillRecord, SkillVersionRecord

    repository, store, database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("my-review", files=(("reference.txt", b"support"),)),
        scope="workspace",
        workspace_id="personal-a",
    )

    assert created.enabled is True
    assert created.version.version_no == 1
    stored = await repository.get_stored(created.id)
    assert stored is not None
    assert stored.bundle.files[0].content == b"support"
    async with database.session() as db:
        record = await db.get(SkillRecord, created.id)
        version = await db.get(SkillVersionRecord, created.version.id)
        assert record is not None and record.normalized_name == "my-review"
        assert record.content == stored.bundle.content
        assert record.bundle_hash == stored.bundle.bundle_hash
        assert version is not None and version.status == "ready"


@pytest.mark.asyncio
async def test_replace_creates_next_version_and_preserves_personal_enabled(
    repository_fixture,
) -> None:
    from app.skills.artifacts import build_skill_artifact

    repository, store, _database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("my-review"),
        scope="workspace",
        workspace_id="personal-a",
    )
    disabled = await repository.set_personal_enabled(
        created.id, created.version.bundle_hash, False
    )
    replacement_bundle = _bundle("my-review", body="changed")
    replacement_artifact = build_skill_artifact(replacement_bundle)
    await asyncio.to_thread(store.put, replacement_artifact)

    replaced = await repository.replace_current_version(
        created.id,
        expected_hash=disabled.version.bundle_hash,
        bundle=replacement_bundle,
        artifact=replacement_artifact,
        created_by="owner-a",
        origin={"type": "replacement"},
    )

    assert replaced.enabled is False
    assert replaced.version.version_no == 2
    assert replaced.version.bundle_hash == replacement_bundle.bundle_hash


@pytest.mark.asyncio
async def test_same_hash_replace_is_idempotent(repository_fixture) -> None:
    from app.db.models import SkillVersionRecord
    from app.skills.artifacts import build_skill_artifact

    repository, store, database = repository_fixture
    bundle = _bundle("my-review")
    created = await _insert(
        repository,
        store,
        bundle,
        scope="workspace",
        workspace_id="personal-a",
    )
    artifact = build_skill_artifact(bundle)

    replaced = await repository.replace_current_version(
        created.id,
        expected_hash=created.version.bundle_hash,
        bundle=bundle,
        artifact=artifact,
        created_by="owner-a",
        origin={"type": "retry"},
    )

    assert replaced.version == created.version
    async with database.session() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == created.id)
            )
        ) == 1


@pytest.mark.asyncio
async def test_same_hash_replace_returns_its_pinned_version_during_concurrent_change(
    repository_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.skills.artifacts import build_skill_artifact

    repository, store, _database = repository_fixture
    original_bundle = _bundle("my-review")
    created = await _insert(
        repository,
        store,
        original_bundle,
        scope="workspace",
        workspace_id="personal-a",
    )
    replacement_bundle = _bundle("my-review", body="replacement")
    replacement_artifact = build_skill_artifact(replacement_bundle)
    await asyncio.to_thread(store.put, replacement_artifact)

    original_require_summary = repository.require_summary
    idempotent_post_commit = asyncio.Event()
    allow_idempotent_post_read = asyncio.Event()

    async def delay_old_post_commit_read(skill_id: str):
        if asyncio.current_task() is idempotent_task:
            idempotent_post_commit.set()
            await allow_idempotent_post_read.wait()
        return await original_require_summary(skill_id)

    monkeypatch.setattr(repository, "require_summary", delay_old_post_commit_read)
    idempotent_task = asyncio.create_task(
        repository.replace_current_version(
            created.id,
            expected_hash=created.version.bundle_hash,
            bundle=original_bundle,
            artifact=build_skill_artifact(original_bundle),
            created_by="owner-a",
        )
    )
    post_commit_waiter = asyncio.create_task(idempotent_post_commit.wait())
    done, _pending = await asyncio.wait(
        {idempotent_task, post_commit_waiter},
        return_when=asyncio.FIRST_COMPLETED,
    )
    replacement = await repository.replace_current_version(
        created.id,
        expected_hash=created.version.bundle_hash,
        bundle=replacement_bundle,
        artifact=replacement_artifact,
        created_by="owner-a",
    )
    if post_commit_waiter in done:
        allow_idempotent_post_read.set()
    else:
        post_commit_waiter.cancel()
    idempotent = await idempotent_task

    assert idempotent.version.id == created.version.id
    assert idempotent.version.bundle_hash == created.version.bundle_hash
    assert replacement.version.bundle_hash == replacement_bundle.bundle_hash


@pytest.mark.asyncio
async def test_concurrent_replacements_serialize_before_reading_current_pointer(
    repository_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db.models import SkillVersionRecord
    from app.errors import AppError
    from app.skills.artifacts import build_skill_artifact

    repository, store, database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("my-review"),
        scope="workspace",
        workspace_id="personal-a",
    )
    replacements = (
        _bundle("my-review", body="replacement-a"),
        _bundle("my-review", body="replacement-b"),
    )
    artifacts = tuple(build_skill_artifact(bundle) for bundle in replacements)
    for artifact in artifacts:
        await asyncio.to_thread(store.put, artifact)

    original_lock_name = repository._lock_name
    both_ready = asyncio.Event()
    ready_count = 0

    async def synchronize_before_lock(db, normalized_name: str) -> None:
        nonlocal ready_count
        ready_count += 1
        if ready_count == 2:
            both_ready.set()
        await both_ready.wait()
        await original_lock_name(db, normalized_name)

    monkeypatch.setattr(repository, "_lock_name", synchronize_before_lock)

    results = await asyncio.gather(
        *(
            repository.replace_current_version(
                created.id,
                expected_hash=created.version.bundle_hash,
                bundle=bundle,
                artifact=artifact,
                created_by="owner-a",
            )
            for bundle, artifact in zip(replacements, artifacts, strict=True)
        ),
        return_exceptions=True,
    )

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, AppError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "skill_changed"
    current = await repository.require_summary(created.id)
    assert successes[0].version.id == current.version.id
    async with database.session() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == created.id)
            )
        ) == 2


@pytest.mark.asyncio
async def test_replacement_rolls_back_new_version_when_pointer_cas_loses(
    repository_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.sql.dml import Update

    from app.db.models import SkillVersionRecord
    from app.errors import AppError
    from app.skills.artifacts import build_skill_artifact

    repository, store, database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("my-review"),
        scope="workspace",
        workspace_id="personal-a",
    )
    replacement = _bundle("my-review", body="replacement")
    artifact = build_skill_artifact(replacement)
    await asyncio.to_thread(store.put, artifact)
    session_class = database._session_factory.class_
    original_execute = session_class.execute

    class ZeroRows:
        rowcount = 0

    async def lose_pointer_cas(self, statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "skills":
            return ZeroRows()
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(session_class, "execute", lose_pointer_cas)

    with pytest.raises(AppError) as exc_info:
        await repository.replace_current_version(
            created.id,
            expected_hash=created.version.bundle_hash,
            bundle=replacement,
            artifact=artifact,
            created_by="owner-a",
        )
    assert exc_info.value.code == "skill_changed"

    async with database.session() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == created.id)
            )
        ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["enable", "archive"])
async def test_enable_and_archive_report_lost_pointer_cas(
    repository_fixture, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from sqlalchemy.sql.dml import Update

    from app.errors import AppError

    repository, store, database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("my-review"),
        scope="workspace",
        workspace_id="personal-a",
    )
    session_class = database._session_factory.class_
    original_execute = session_class.execute

    class ZeroRows:
        rowcount = 0

    async def lose_pointer_cas(self, statement, *args, **kwargs):
        if isinstance(statement, Update) and statement.table.name == "skills":
            return ZeroRows()
        return await original_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(session_class, "execute", lose_pointer_cas)

    with pytest.raises(AppError) as exc_info:
        if operation == "enable":
            await repository.set_personal_enabled(
                created.id, created.version.bundle_hash, False
            )
        else:
            await repository.archive_skill(
                created.id, created.version.bundle_hash, datetime.now(UTC)
            )
    assert exc_info.value.code == "skill_changed"


@pytest.mark.asyncio
async def test_effective_versions_are_sorted_and_pin_current_version(
    repository_fixture,
) -> None:
    from app.skills.artifacts import build_skill_artifact

    repository, store, _database = repository_fixture
    zulu = await _insert(
        repository,
        store,
        _bundle("Zulu"),
        scope="global",
        workspace_id=None,
    )
    await _insert(
        repository,
        store,
        _bundle("alpha"),
        scope="workspace",
        workspace_id="personal-a",
    )
    changed = _bundle("Zulu", body="changed")
    artifact = build_skill_artifact(changed)
    await asyncio.to_thread(store.put, artifact)
    replaced = await repository.replace_current_version(
        zulu.id,
        expected_hash=zulu.version.bundle_hash,
        bundle=changed,
        artifact=artifact,
        created_by="owner-a",
    )

    effective = await repository.list_effective_versions("personal-a")

    assert [item.name for item in effective] == ["alpha", "Zulu"]
    assert effective[1].version.id == replaced.version.id
    assert effective[1].version.version_no == 2


@pytest.mark.asyncio
async def test_archived_skill_is_absent_from_catalog_but_versions_remain(
    repository_fixture,
) -> None:
    from app.db.models import SkillVersionRecord

    repository, store, database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("my-review"),
        scope="workspace",
        workspace_id="personal-a",
    )

    await repository.archive_skill(
        created.id, created.version.bundle_hash, datetime.now(UTC)
    )

    assert (await repository.list_catalog("personal-a")).personal_skills == ()
    async with database.session() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == created.id)
            )
        ) == 1


@pytest.mark.asyncio
async def test_global_and_personal_name_conflicts_are_serialized(
    repository_fixture,
) -> None:
    from app.errors import AppError

    repository, store, _database = repository_fixture

    results = await asyncio.gather(
        _insert(
            repository,
            store,
            _bundle("Shared"),
            scope="global",
            workspace_id=None,
        ),
        _insert(
            repository,
            store,
            _bundle("shared"),
            scope="workspace",
            workspace_id="personal-a",
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(errors) == 1
    assert errors[0].code == "skill_name_conflict"


@pytest.mark.asyncio
async def test_two_personal_workspaces_may_use_the_same_non_global_name(
    repository_fixture,
) -> None:
    repository, store, _database = repository_fixture

    first, second = await asyncio.gather(
        _insert(
            repository,
            store,
            _bundle("shared"),
            scope="workspace",
            workspace_id="personal-a",
        ),
        _insert(
            repository,
            store,
            _bundle("Shared"),
            scope="workspace",
            workspace_id="personal-b",
        ),
    )

    assert first.workspace_id == "personal-a"
    assert second.workspace_id == "personal-b"


@pytest.mark.asyncio
async def test_corrupt_or_non_ready_current_version_is_rejected(
    repository_fixture,
) -> None:
    from app.db.models import SkillRecord, SkillVersionRecord
    from app.errors import AppError

    repository, store, database = repository_fixture
    first = await _insert(
        repository,
        store,
        _bundle("first"),
        scope="workspace",
        workspace_id="personal-a",
    )
    second = await _insert(
        repository,
        store,
        _bundle("second"),
        scope="workspace",
        workspace_id="personal-a",
    )
    async with database.session() as db:
        await db.execute(
            update(SkillVersionRecord)
            .where(SkillVersionRecord.id == first.version.id)
            .values(status="failed")
        )
        await db.commit()

    with pytest.raises(AppError) as not_ready:
        await repository.list_effective_versions("personal-a")
    assert not_ready.value.code == "skill_artifact_corrupt"
    assert not_ready.value.message == "Skill version metadata is invalid."

    async with database.session() as db:
        await db.execute(
            update(SkillVersionRecord)
            .where(SkillVersionRecord.id == first.version.id)
            .values(status="ready")
        )
        await db.execute(
            update(SkillRecord)
            .where(SkillRecord.id == first.id)
            .values(current_version_id=second.version.id)
        )
        await db.commit()

    with pytest.raises(AppError) as wrong_skill:
        await repository.list_effective_versions("personal-a")
    assert wrong_skill.value.code == "skill_artifact_corrupt"
    assert wrong_skill.value.message == "Skill version metadata is invalid."


@pytest.mark.asyncio
async def test_unauthorized_corrupt_skill_is_hidden_before_version_validation(
    repository_fixture,
) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.models import SkillVersionRecord
    from app.errors import AppError
    from app.skills.service import SkillService

    repository, store, database = repository_fixture
    created = await _insert(
        repository,
        store,
        _bundle("private-review"),
        scope="workspace",
        workspace_id="personal-a",
    )
    async with database.session() as db:
        await db.execute(
            update(SkillVersionRecord)
            .where(SkillVersionRecord.id == created.version.id)
            .values(status="failed")
        )
        await db.commit()

    assert await repository.get_for_member(created.id, "owner-b") is None
    assert await repository.get_for_manager(created.id, "owner-b") is None
    service = SkillService(repository, WorkspaceAccessService(database))
    unauthorized = IdentityContext("owner-b", "owner-b", "Owner B")
    with pytest.raises(AppError) as exc_info:
        await service.get(created.id, unauthorized)
    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "skill_not_found",
    )


@pytest.mark.asyncio
async def test_implicit_sqlite_artifact_store_is_restart_persistent(
    repository_fixture,
) -> None:
    from app.db.base import Database
    from app.skills.artifacts import FilesystemSkillArtifactStore
    from app.skills.repository import SkillRepository

    _repository, _store, database = repository_fixture
    restarted_database = Database(database.url)
    try:
        first = SkillRepository(database)
        restarted = SkillRepository(restarted_database)

        assert isinstance(first.artifacts, FilesystemSkillArtifactStore)
        assert isinstance(restarted.artifacts, FilesystemSkillArtifactStore)
        assert first.artifacts.root == restarted.artifacts.root
        assert first.artifacts.root.name == "skill-artifacts"
        created = await first.insert_bundle(
            "personal-a",
            "owner-a",
            _bundle("restart-safe", files=(("reference.txt", b"persistent"),)),
            enabled=True,
            origin={"type": "compatibility"},
        )
        reloaded = await restarted.get_stored(created.id)
        assert reloaded is not None
        assert reloaded.bundle.files[0].content == b"persistent"
    finally:
        await restarted_database.dispose()


@pytest.mark.asyncio
async def test_postgres_requires_an_explicit_artifact_store() -> None:
    from app.db.base import Database
    from app.skills.repository import SkillRepository

    database = Database("postgresql+asyncpg://workspace:test@localhost/workspace")
    try:
        with pytest.raises(ValueError, match="Artifact store"):
            SkillRepository(database)
    finally:
        await database.dispose()
