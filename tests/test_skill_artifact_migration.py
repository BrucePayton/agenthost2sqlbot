import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.db.models import (
    AppMetadataRecord,
    SkillFileRecord,
    SkillRecord,
    SkillVersionRecord,
    WorkspaceRecord,
)
from app.errors import AppError
from app.skills.bundle import SkillBundleLimits, build_bundle

LEGACY_SKILL_MD = b"""---
name: legacy-skill
description: Legacy Skill
---
# Legacy body
"""
LEGACY_FILE_PATH = "references/legacy.bin"
LEGACY_FILE_CONTENT = b"\x00legacy-support\xff"


@pytest.fixture
async def legacy_skill_database(tmp_path: Path) -> AsyncIterator[object]:
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    await database.initialize()
    bundle = build_bundle(
        LEGACY_SKILL_MD,
        [(LEGACY_FILE_PATH, LEGACY_FILE_CONTENT)],
    )
    async with database.session() as db, db.begin():
        db.add(
            WorkspaceRecord(
                id="legacy-workspace",
                name="Legacy Workspace",
                kind="team",
                config_json="{}",
            )
        )
        await db.flush()
        db.add(
            SkillRecord(
                id="legacy-skill",
                scope="workspace",
                workspace_id="legacy-workspace",
                name=bundle.name,
                normalized_name=bundle.name.casefold(),
                description=bundle.description,
                content=bundle.content,
                enabled=True,
                bundle_hash=bundle.bundle_hash,
                current_version_id=None,
                config_json="{}",
                created_by=None,
            )
        )
        await db.flush()
        db.add(
            SkillFileRecord(
                skill_id="legacy-skill",
                path=LEGACY_FILE_PATH,
                content_blob=LEGACY_FILE_CONTENT,
                mime_type=bundle.files[0].mime_type,
                size_bytes=len(LEGACY_FILE_CONTENT),
                sha256=bundle.files[0].sha256,
            )
        )
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def artifact_store(tmp_path: Path):
    from app.skills.artifacts import FilesystemSkillArtifactStore

    store = FilesystemSkillArtifactStore(tmp_path / "artifacts")
    store.initialize()
    return store


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
        assert skill is not None
        version = await db.get(SkillVersionRecord, skill.current_version_id)
    assert version is not None
    assert version.version_no == 1
    assert version.bundle_hash == skill.bundle_hash
    assert artifact_store.exists(version.artifact_key)


@pytest.mark.asyncio
async def test_migrator_preserves_legacy_content_and_skill_files(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.migration import LegacySkillArtifactMigrator
    from app.skills.repository import SkillRepository

    await LegacySkillArtifactMigrator(
        legacy_skill_database, artifact_store, SkillBundleLimits()
    ).run()

    stored = await SkillRepository(
        legacy_skill_database, artifact_store, SkillBundleLimits()
    ).get_stored("legacy-skill")
    assert stored is not None
    assert stored.bundle.content.encode() == LEGACY_SKILL_MD
    assert [(item.path, item.content) for item in stored.bundle.files] == [
        (LEGACY_FILE_PATH, LEGACY_FILE_CONTENT)
    ]


@pytest.mark.asyncio
async def test_migrator_resumes_after_artifact_written_before_database_commit(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.artifacts import build_skill_artifact
    from app.skills.migration import LegacySkillArtifactMigrator

    bundle = build_bundle(
        LEGACY_SKILL_MD,
        [(LEGACY_FILE_PATH, LEGACY_FILE_CONTENT)],
    )
    artifact = build_skill_artifact(bundle)
    artifact_store.put(artifact)

    report = await LegacySkillArtifactMigrator(
        legacy_skill_database, artifact_store, SkillBundleLimits()
    ).run()

    assert report.migrated == 1
    async with legacy_skill_database.session() as db:
        skill = await db.get(SkillRecord, "legacy-skill")
        assert skill is not None and skill.current_version_id is not None
        versions = list(
            (
                await db.scalars(
                    select(SkillVersionRecord).where(
                        SkillVersionRecord.skill_id == "legacy-skill"
                    )
                )
            ).all()
        )
    assert len(versions) == 1
    assert versions[0].artifact_key == artifact.artifact_key


@pytest.mark.asyncio
async def test_migrator_fails_startup_on_corrupt_legacy_hash(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.migration import LegacySkillArtifactMigrator

    async with legacy_skill_database.session() as db, db.begin():
        await db.execute(
            update(SkillRecord)
            .where(SkillRecord.id == "legacy-skill")
            .values(bundle_hash="sha256:" + "0" * 64)
        )

    with pytest.raises(AppError) as exc_info:
        await LegacySkillArtifactMigrator(
            legacy_skill_database, artifact_store, SkillBundleLimits()
        ).run()

    assert exc_info.value.code == "skill_artifact_corrupt"
    async with legacy_skill_database.session() as db:
        skill = await db.get(SkillRecord, "legacy-skill")
        marker = await db.get(AppMetadataRecord, "skill_artifact_versions_v1")
    assert skill is not None and skill.current_version_id is None
    assert marker is None


@pytest.mark.asyncio
async def test_migrator_does_not_mark_complete_while_any_skill_is_unmigrated(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.migration import LegacySkillArtifactMigrator

    corrupt_bundle = build_bundle(
        b"---\nname: unmigrated\ndescription: Unmigrated\n---\n# body\n",
        [],
    )
    async with legacy_skill_database.session() as db, db.begin():
        db.add(
            SkillRecord(
                id="unmigrated-skill",
                scope="workspace",
                workspace_id="legacy-workspace",
                name=corrupt_bundle.name,
                normalized_name=corrupt_bundle.name.casefold(),
                description=corrupt_bundle.description,
                content=corrupt_bundle.content,
                enabled=True,
                bundle_hash="sha256:" + "f" * 64,
                current_version_id=None,
                config_json="{}",
                created_by=None,
            )
        )

    with pytest.raises(AppError):
        await LegacySkillArtifactMigrator(
            legacy_skill_database, artifact_store, SkillBundleLimits()
        ).run()

    async with legacy_skill_database.session() as db:
        marker = await db.get(AppMetadataRecord, "skill_artifact_versions_v1")
        pending = await db.get(SkillRecord, "unmigrated-skill")
    assert marker is None
    assert pending is not None and pending.current_version_id is None


def _put_artifact(store, name: str):
    from app.skills.artifacts import build_skill_artifact

    bundle = build_bundle(
        f"---\nname: {name}\ndescription: {name}\n---\n# body\n".encode(),
        [],
    )
    artifact = build_skill_artifact(bundle)
    store.put(artifact)
    return artifact


def _set_modified_at(store, artifact_key: str, modified_at: datetime) -> None:
    path = store.root.joinpath(*artifact_key.split("/"))
    timestamp = modified_at.timestamp()
    os.utime(path, (timestamp, timestamp))


@pytest.mark.asyncio
async def test_garbage_collector_deletes_only_unreferenced_objects_older_than_24_hours(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.migration import (
        LegacySkillArtifactMigrator,
        SkillArtifactGarbageCollector,
    )

    await LegacySkillArtifactMigrator(
        legacy_skill_database, artifact_store, SkillBundleLimits()
    ).run()
    async with legacy_skill_database.session() as db:
        skill = await db.get(SkillRecord, "legacy-skill")
        assert skill is not None
        referenced = await db.get(SkillVersionRecord, skill.current_version_id)
    assert referenced is not None
    old_orphan = _put_artifact(artifact_store, "old-orphan")
    fresh_orphan = _put_artifact(artifact_store, "fresh-orphan")
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    _set_modified_at(artifact_store, referenced.artifact_key, now - timedelta(days=2))
    _set_modified_at(artifact_store, old_orphan.artifact_key, now - timedelta(days=2))
    _set_modified_at(artifact_store, fresh_orphan.artifact_key, now - timedelta(hours=23))

    deleted = await SkillArtifactGarbageCollector(
        legacy_skill_database, artifact_store
    ).run(now)

    assert deleted == 1
    assert artifact_store.exists(referenced.artifact_key)
    assert not artifact_store.exists(old_orphan.artifact_key)
    assert artifact_store.exists(fresh_orphan.artifact_key)


@pytest.mark.asyncio
async def test_garbage_collector_never_deletes_referenced_or_fresh_objects(
    legacy_skill_database, artifact_store
) -> None:
    from app.skills.migration import (
        LegacySkillArtifactMigrator,
        SkillArtifactGarbageCollector,
    )

    await LegacySkillArtifactMigrator(
        legacy_skill_database, artifact_store, SkillBundleLimits()
    ).run()
    async with legacy_skill_database.session() as db:
        skill = await db.get(SkillRecord, "legacy-skill")
        assert skill is not None
        referenced = await db.get(SkillVersionRecord, skill.current_version_id)
    assert referenced is not None
    fresh = _put_artifact(artifact_store, "fresh")
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    _set_modified_at(artifact_store, referenced.artifact_key, now - timedelta(days=30))
    _set_modified_at(artifact_store, fresh.artifact_key, now - timedelta(hours=1))

    deleted = await SkillArtifactGarbageCollector(
        legacy_skill_database, artifact_store
    ).run(now)

    assert deleted == 0
    assert artifact_store.exists(referenced.artifact_key)
    assert artifact_store.exists(fresh.artifact_key)
