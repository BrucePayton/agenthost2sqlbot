import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.base import Database
from app.db.models import (
    AppMetadataRecord,
    SkillFileRecord,
    SkillRecord,
    SkillVersionRecord,
)
from app.errors import AppError
from app.skills.artifacts import (
    SkillArtifact,
    SkillArtifactStore,
    build_skill_artifact,
)
from app.skills.bundle import SkillBundleLimits, build_bundle
from app.skills.models import SkillBundle


@dataclass(frozen=True)
class SkillArtifactMigrationReport:
    migrated: int
    skipped: int


@dataclass(frozen=True)
class _LegacyStoredSkill:
    skill_id: str
    created_by: str | None
    bundle: SkillBundle


class LegacySkillArtifactMigrator:
    marker = "skill_artifact_versions_v1"

    def __init__(
        self,
        database: Database,
        artifacts: SkillArtifactStore,
        limits: SkillBundleLimits,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.limits = limits

    async def run(self) -> SkillArtifactMigrationReport:
        skill_ids, total_skills = await self._legacy_skill_state()
        if skill_ids:
            await self._clear_completion_marker()
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
            skipped=total_skills - migrated,
        )

    async def _legacy_skill_state(self) -> tuple[tuple[str, ...], int]:
        async with self.database.session() as db:
            skill_ids = tuple(
                (
                    await db.scalars(
                        select(SkillRecord.id)
                        .where(SkillRecord.current_version_id.is_(None))
                        .order_by(SkillRecord.id)
                    )
                ).all()
            )
            total_skills = int(
                (await db.scalar(select(func.count()).select_from(SkillRecord))) or 0
            )
        return skill_ids, total_skills

    async def _clear_completion_marker(self) -> None:
        async with self.database.session() as db, db.begin():
            await db.execute(
                delete(AppMetadataRecord).where(
                    AppMetadataRecord.key == self.marker
                )
            )

    async def _read_legacy_bundle(self, skill_id: str) -> _LegacyStoredSkill:
        async with self.database.session() as db:
            record = await db.get(SkillRecord, skill_id)
            if record is None:
                raise _artifact_corrupt()
            files = tuple(
                (
                    await db.scalars(
                        select(SkillFileRecord)
                        .where(SkillFileRecord.skill_id == skill_id)
                        .order_by(SkillFileRecord.path)
                    )
                ).all()
            )
        try:
            bundle = build_bundle(
                record.content.encode("utf-8"),
                ((item.path, bytes(item.content_blob)) for item in files),
                self.limits,
            )
        except AppError as exc:
            raise _artifact_corrupt() from exc
        if bundle.bundle_hash != record.bundle_hash:
            raise _artifact_corrupt()
        return _LegacyStoredSkill(
            skill_id=record.id,
            created_by=record.created_by,
            bundle=bundle,
        )

    async def _publish_version_one(
        self, stored: _LegacyStoredSkill, artifact: SkillArtifact
    ) -> bool:
        now = datetime.now(UTC)
        async with self.database.session() as db, db.begin():
            record = await db.get(SkillRecord, stored.skill_id)
            if record is None:
                raise _artifact_corrupt()
            if record.current_version_id is not None:
                return False
            if record.bundle_hash != stored.bundle.bundle_hash:
                raise _artifact_corrupt()
            version = await db.scalar(
                select(SkillVersionRecord).where(
                    SkillVersionRecord.skill_id == stored.skill_id,
                    SkillVersionRecord.bundle_hash == stored.bundle.bundle_hash,
                )
            )
            if version is None:
                version = SkillVersionRecord(
                    id=str(uuid.uuid4()),
                    skill_id=stored.skill_id,
                    version_no=1,
                    bundle_hash=stored.bundle.bundle_hash,
                    artifact_key=artifact.artifact_key,
                    artifact_sha256=artifact.artifact_sha256,
                    manifest_json=artifact.manifest_json,
                    size_bytes=artifact.size_bytes,
                    status="ready",
                    created_by=stored.created_by,
                    created_at=now,
                )
                db.add(version)
                await db.flush()
            elif not _version_matches(version, artifact):
                raise _artifact_corrupt()
            result = await db.execute(
                update(SkillRecord)
                .where(
                    SkillRecord.id == stored.skill_id,
                    SkillRecord.current_version_id.is_(None),
                    SkillRecord.bundle_hash == stored.bundle.bundle_hash,
                )
                .values(current_version_id=version.id, updated_at=now)
            )
            if getattr(result, "rowcount", None) != 1:
                raise _artifact_corrupt()
        return True

    async def _mark_complete_if_no_legacy_rows(self) -> None:
        now = datetime.now(UTC)
        async with self.database.session() as db, db.begin():
            remaining = int(
                (
                    await db.scalar(
                        select(func.count())
                        .select_from(SkillRecord)
                        .where(SkillRecord.current_version_id.is_(None))
                    )
                )
                or 0
            )
            if remaining:
                return
            values = {
                "key": self.marker,
                "value_json": json.dumps(
                    {"completed_at": now.isoformat()},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "updated_at": now,
            }
            statement = (
                postgresql_insert(AppMetadataRecord)
                if db.bind is not None and db.bind.dialect.name == "postgresql"
                else sqlite_insert(AppMetadataRecord)
            )
            await db.execute(
                statement.values(**values).on_conflict_do_nothing(
                    index_elements=(AppMetadataRecord.key,),
                )
            )


class SkillArtifactGarbageCollector:
    retention = timedelta(hours=24)

    def __init__(self, database: Database, artifacts: SkillArtifactStore) -> None:
        self.database = database
        self.artifacts = artifacts

    async def run(self, now: datetime) -> int:
        async with self.database.session() as db:
            referenced = frozenset(
                (await db.scalars(select(SkillVersionRecord.artifact_key))).all()
            )
        objects = await asyncio.to_thread(self.artifacts.iter_objects)
        cutoff = _as_utc(now) - self.retention
        candidates = tuple(
            item
            for item in objects
            if item.artifact_key not in referenced
            and _as_utc(item.modified_at) <= cutoff
        )
        deleted = 0
        for item in candidates:
            await asyncio.to_thread(self.artifacts.delete, item.artifact_key)
            deleted += 1
        return deleted


def _version_matches(version: SkillVersionRecord, artifact: SkillArtifact) -> bool:
    return (
        version.version_no == 1
        and version.artifact_key == artifact.artifact_key
        and version.artifact_sha256 == artifact.artifact_sha256
        and version.manifest_json == artifact.manifest_json
        and version.size_bytes == artifact.size_bytes
        and version.status == "ready"
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _artifact_corrupt() -> AppError:
    return AppError(
        "skill_artifact_corrupt",
        "Legacy Skill artifact migration integrity check failed.",
        500,
    )
