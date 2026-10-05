from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import ColumnElement

from app.db.base import Database
from app.db.models import (
    PlatformRoleBindingRecord,
    SkillNameLockRecord,
    SkillRecord,
    SkillVersionRecord,
    UserRecord,
    WorkspaceGlobalSkillSettingRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)
from app.errors import AppError
from app.skills.artifacts import (
    FilesystemSkillArtifactStore,
    SkillArtifact,
    SkillArtifactStore,
    build_skill_artifact,
    load_skill_artifact,
)
from app.skills.bundle import SkillBundleLimits
from app.skills.models import (
    ManagedSkillSummary,
    SkillBundle,
    SkillCatalog,
    SkillScope,
    SkillVersionRef,
    StoredSkill,
)

SkillSummary = ManagedSkillSummary


def _global_enabled_expression(
    workspace_id: str | ColumnElement[str],
) -> ColumnElement[bool]:
    """Use the current ID's choice, then the latest archived global name's choice."""
    historical_skill = aliased(SkillRecord)
    setting = aliased(WorkspaceGlobalSkillSettingRecord)
    current = (
        select(setting.enabled)
        .where(
            setting.workspace_id == workspace_id,
            setting.skill_id == SkillRecord.id,
        )
        .correlate(SkillRecord, WorkspaceRecord)
        .scalar_subquery()
    )
    choice = (
        select(setting.enabled)
        .join(historical_skill, historical_skill.id == setting.skill_id)
        .where(
            setting.workspace_id == workspace_id,
            historical_skill.scope == "global",
            historical_skill.normalized_name == SkillRecord.normalized_name,
            historical_skill.archived_at.is_not(None),
        )
        # A new ID must not silently undo a workspace's explicit opt-out.
        .order_by(
            setting.updated_at.desc(),
            historical_skill.created_at.desc(),
            historical_skill.id.desc(),
        )
        .limit(1)
        .correlate(SkillRecord, WorkspaceRecord)
        .scalar_subquery()
    )
    return func.coalesce(current, choice, True)


@dataclass(frozen=True)
class SkillMutationAuthorization:
    mode: Literal[
        "personal_owner",
        "global_contributor",
        "skill_admin",
        "workspace_manager",
        "trusted",
    ]
    user_id: str | None
    workspace_ids: tuple[str, ...] = ()

    @classmethod
    def personal_owner(
        cls, user_id: str, workspace_id: str
    ) -> SkillMutationAuthorization:
        return cls("personal_owner", user_id, (workspace_id,))

    @classmethod
    def global_contributor(cls, user_id: str) -> SkillMutationAuthorization:
        """Authorize a registered user to mutate global Skills."""
        return cls("global_contributor", user_id)

    @classmethod
    def skill_admin(cls, user_id: str) -> SkillMutationAuthorization:
        return cls("skill_admin", user_id)

    @classmethod
    def workspace_manager(
        cls, user_id: str, workspace_ids: tuple[str, ...]
    ) -> SkillMutationAuthorization:
        return cls("workspace_manager", user_id, workspace_ids)

    @classmethod
    def trusted(cls) -> SkillMutationAuthorization:
        return cls("trusted", None)


class SkillRepository:
    def __init__(
        self,
        database: Database,
        artifacts: SkillArtifactStore | SkillBundleLimits | None = None,
        limits: SkillBundleLimits | None = None,
    ) -> None:
        if isinstance(artifacts, SkillBundleLimits):
            if limits is not None:
                raise TypeError("Skill bundle limits were provided twice.")
            limits = artifacts
            artifacts = None
        self.database = database
        self.artifacts = artifacts or _compatibility_artifact_store(database)
        self.limits = limits or SkillBundleLimits()

    async def list_catalog(self, workspace_id: str) -> SkillCatalog:
        async with self.database.session() as db:
            global_rows = (
                await db.execute(
                    select(
                        SkillRecord, SkillVersionRecord,
                        _global_enabled_expression(workspace_id),
                    )
                    .join(
                        SkillVersionRecord,
                        SkillRecord.current_version_id == SkillVersionRecord.id,
                    )
                    .where(
                        SkillRecord.scope == "global",
                        SkillRecord.archived_at.is_(None),
                        SkillVersionRecord.skill_id == SkillRecord.id,
                        SkillVersionRecord.status == "ready",
                    )
                    .order_by(SkillRecord.normalized_name, SkillRecord.id)
                )
            ).all()
            personal_rows = (
                await db.execute(
                    select(SkillRecord, SkillVersionRecord)
                    .join(
                        SkillVersionRecord,
                        SkillRecord.current_version_id == SkillVersionRecord.id,
                    )
                    .where(
                        SkillRecord.scope == "workspace",
                        SkillRecord.workspace_id == workspace_id,
                        SkillRecord.archived_at.is_(None),
                        SkillVersionRecord.skill_id == SkillRecord.id,
                        SkillVersionRecord.status == "ready",
                    )
                    .order_by(SkillRecord.normalized_name, SkillRecord.id)
                )
            ).all()
        return SkillCatalog(
            tuple(
                _summary(record, version, enabled=bool(enabled))
                for record, version, enabled in global_rows
            ),
            tuple(_summary(record, version) for record, version in personal_rows),
        )

    async def insert_versioned_skill(
        self,
        *,
        scope: SkillScope,
        workspace_id: str | None,
        created_by: str | None,
        bundle: SkillBundle,
        artifact: SkillArtifact,
        enabled: bool,
        origin: dict[str, object],
        authorization: SkillMutationAuthorization | None = None,
    ) -> ManagedSkillSummary:
        _validate_scope(scope, workspace_id, enabled)
        _validate_artifact_metadata(bundle, artifact)
        normalized_name = bundle.name.casefold()
        now = datetime.now(UTC)
        skill_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        summary: ManagedSkillSummary | None = None
        try:
            async with self.database.session() as db, db.begin():
                if created_by is None and (
                    authorization is None or authorization.mode != "trusted"
                ):
                    raise AppError("skill_not_found", "Skill not found.", 404)
                await self._require_mutation_authorization(db, authorization)
                _require_insert_authorized(
                    authorization, scope=scope, workspace_id=workspace_id
                )
                await self._lock_name(db, normalized_name)
                await self._reject_effective_name_conflict(
                    db,
                    scope=scope,
                    workspace_id=workspace_id,
                    normalized_name=normalized_name,
                )
                record = SkillRecord(
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
                db.add(record)
                await db.flush()
                version = SkillVersionRecord(
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
                db.add(version)
                await db.flush()
                await db.execute(
                    update(SkillRecord)
                    .where(SkillRecord.id == skill_id)
                    .values(current_version_id=version_id)
                )
                summary = _summary(record, version)
        except IntegrityError as exc:
            if _is_name_conflict(exc):
                raise _name_conflict() from exc
            raise
        if summary is None:
            raise _invalid_version_metadata()
        return summary

    async def replace_current_version(
        self,
        skill_id: str,
        *,
        expected_hash: str,
        bundle: SkillBundle,
        artifact: SkillArtifact,
        created_by: str,
        origin: dict[str, object] | None = None,
        authorization: SkillMutationAuthorization | None = None,
    ) -> ManagedSkillSummary:
        _validate_artifact_metadata(bundle, artifact)
        now = datetime.now(UTC)
        current_name = await self._active_skill_name(skill_id)
        if current_name is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        normalized_name = bundle.name.casefold()
        summary: ManagedSkillSummary | None = None
        try:
            async with self.database.session() as db, db.begin():
                await self._require_mutation_authorization(db, authorization)
                for name in sorted({current_name, normalized_name}):
                    await self._lock_name(db, name)
                record = await db.get(SkillRecord, skill_id)
                if record is None or record.archived_at is not None:
                    raise _skill_changed()
                _require_record_authorized(authorization, record)
                current = await self._require_current_version(db, record)
                if current.bundle_hash != expected_hash:
                    raise _skill_changed()
                await self._reject_effective_name_conflict(
                    db,
                    scope=record.scope,
                    workspace_id=record.workspace_id,
                    normalized_name=normalized_name,
                    exclude_skill_id=record.id,
                )
                existing = await db.scalar(
                    select(SkillVersionRecord).where(
                        SkillVersionRecord.skill_id == record.id,
                        SkillVersionRecord.bundle_hash == bundle.bundle_hash,
                    )
                )
                if existing is not None:
                    if existing.status != "ready":
                        raise _invalid_version_metadata()
                    selected_version_id = existing.id
                    selected_version = existing
                else:
                    next_version = (
                        await db.scalar(
                            select(func.max(SkillVersionRecord.version_no)).where(
                                SkillVersionRecord.skill_id == record.id
                            )
                        )
                        or 0
                    ) + 1
                    selected_version_id = str(uuid.uuid4())
                    selected_version = SkillVersionRecord(
                        id=selected_version_id,
                        skill_id=record.id,
                        version_no=next_version,
                        bundle_hash=bundle.bundle_hash,
                        artifact_key=artifact.artifact_key,
                        artifact_sha256=artifact.artifact_sha256,
                        manifest_json=artifact.manifest_json,
                        size_bytes=artifact.size_bytes,
                        status="ready",
                        created_by=created_by,
                        created_at=now,
                    )
                    db.add(selected_version)
                    await db.flush()
                if selected_version_id != current.id:
                    values: dict[str, object] = {
                        "name": bundle.name,
                        "normalized_name": normalized_name,
                        "description": bundle.description,
                        "content": bundle.content,
                        "bundle_hash": bundle.bundle_hash,
                        "current_version_id": selected_version_id,
                        "updated_at": now,
                    }
                    if origin is not None:
                        values["config_json"] = _origin_json(origin)
                    result = await db.execute(
                        update(SkillRecord)
                        .where(
                            SkillRecord.id == record.id,
                            SkillRecord.current_version_id == current.id,
                            SkillRecord.bundle_hash == expected_hash,
                            SkillRecord.archived_at.is_(None),
                        )
                        .values(**values)
                    )
                    _require_one_cas_row(result)
                    await db.refresh(record)
                summary = _summary(record, selected_version)
        except IntegrityError as exc:
            if _is_name_conflict(exc):
                raise _name_conflict() from exc
            raise
        if summary is None:
            raise _invalid_version_metadata()
        return summary

    async def set_personal_enabled(
        self,
        skill_id: str,
        expected_hash: str,
        enabled: bool,
        *,
        authorization: SkillMutationAuthorization | None = None,
    ) -> ManagedSkillSummary:
        now = datetime.now(UTC)
        current_name = await self._active_skill_name(skill_id)
        if current_name is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        summary: ManagedSkillSummary | None = None
        async with self.database.session() as db, db.begin():
            await self._require_mutation_authorization(db, authorization)
            await self._lock_name(db, current_name)
            record = await db.get(SkillRecord, skill_id)
            if (
                record is None
                or record.scope != "workspace"
                or record.archived_at is not None
            ):
                raise _skill_changed()
            _require_record_authorized(authorization, record)
            version = await self._require_current_version(db, record)
            if version.bundle_hash != expected_hash:
                raise _skill_changed()
            result = await db.execute(
                update(SkillRecord)
                .where(
                    SkillRecord.id == skill_id,
                    SkillRecord.current_version_id == version.id,
                    SkillRecord.bundle_hash == expected_hash,
                    SkillRecord.archived_at.is_(None),
                )
                .values(enabled=enabled, updated_at=now)
            )
            _require_one_cas_row(result)
            await db.refresh(record)
            summary = _summary(record, version)
        if summary is None:
            raise _invalid_version_metadata()
        return summary

    async def set_global_setting(
        self,
        workspace_id: str,
        skill_id: str,
        enabled: bool,
        *,
        updated_by: str,
        authorization: SkillMutationAuthorization | None = None,
    ) -> ManagedSkillSummary:
        now = datetime.now(UTC)
        async with self.database.session() as db, db.begin():
            await self._require_mutation_authorization(db, authorization)
            record = await db.get(SkillRecord, skill_id)
            if (
                record is None
                or record.scope != "global"
                or record.archived_at is not None
            ):
                raise AppError("skill_not_found", "Skill not found.", 404)
            await self._require_current_version(db, record)
            values = {
                "workspace_id": workspace_id,
                "skill_id": skill_id,
                "enabled": enabled,
                "updated_by": updated_by,
                "updated_at": now,
            }
            statement = (
                postgresql_insert(WorkspaceGlobalSkillSettingRecord)
                if db.bind is not None and db.bind.dialect.name == "postgresql"
                else sqlite_insert(WorkspaceGlobalSkillSettingRecord)
            )
            await db.execute(
                statement.values(**values).on_conflict_do_update(
                    index_elements=("workspace_id", "skill_id"),
                    set_={
                        "enabled": enabled,
                        "updated_by": updated_by,
                        "updated_at": now,
                    },
                )
            )
        return replace(await self.require_summary(skill_id), enabled=enabled)

    async def list_effective_versions(
        self, workspace_id: str
    ) -> tuple[ManagedSkillSummary, ...]:
        async with self.database.session() as db:
            records = list(
                (
                    await db.execute(
                        select(SkillRecord, _global_enabled_expression(workspace_id))
                        .where(
                            SkillRecord.archived_at.is_(None),
                            (SkillRecord.scope == "global")
                            | (
                                (SkillRecord.scope == "workspace")
                                & (SkillRecord.workspace_id == workspace_id)
                            ),
                        )
                        .order_by(SkillRecord.normalized_name, SkillRecord.id)
                    )
                ).all()
            )
            summaries: list[ManagedSkillSummary] = []
            seen_names: set[str] = set()
            for record, global_enabled in records:
                version = await self._require_current_version(db, record)
                is_enabled = (
                    bool(global_enabled)
                    if record.scope == "global"
                    else record.enabled
                )
                if not is_enabled:
                    continue
                if record.normalized_name in seen_names:
                    raise _invalid_version_metadata()
                seen_names.add(record.normalized_name)
                summaries.append(_summary(record, version, enabled=True))
        return tuple(summaries)

    async def count_effective_by_workspace(
        self, workspace_ids: Sequence[str]
    ) -> dict[str, int]:
        counts = {workspace_id: 0 for workspace_id in dict.fromkeys(workspace_ids)}
        if not counts:
            return counts
        async with self.database.session() as db:
            rows = (
                await db.execute(
                    select(WorkspaceRecord.id, func.count(SkillRecord.id))
                    .select_from(WorkspaceRecord)
                    .join(
                        SkillRecord,
                        or_(
                            SkillRecord.scope == "global",
                            and_(
                                SkillRecord.scope == "workspace",
                                SkillRecord.workspace_id == WorkspaceRecord.id,
                            ),
                        ),
                    )
                    .join(
                        SkillVersionRecord,
                        and_(
                            SkillVersionRecord.id == SkillRecord.current_version_id,
                            SkillVersionRecord.skill_id == SkillRecord.id,
                        ),
                    )
                    .where(
                        WorkspaceRecord.id.in_(counts),
                        WorkspaceRecord.kind == "personal",
                        SkillRecord.archived_at.is_(None),
                        SkillVersionRecord.status == "ready",
                        or_(
                            and_(
                                SkillRecord.scope == "workspace",
                                SkillRecord.enabled.is_(True),
                            ),
                            and_(
                                SkillRecord.scope == "global",
                                _global_enabled_expression(WorkspaceRecord.id).is_(True),
                            ),
                        ),
                    )
                    .group_by(WorkspaceRecord.id)
                )
            ).all()
        counts.update({workspace_id: int(count) for workspace_id, count in rows})
        return counts

    async def load_bundle(self, summary: ManagedSkillSummary) -> SkillBundle:
        try:
            raw = await asyncio.to_thread(
                self.artifacts.read,
                summary.version.artifact_key,
                maximum_size=self.limits.max_total_bytes,
            )
        except AppError as exc:
            if exc.code == "skill_artifact_corrupt":
                raise
            raise _artifact_unavailable() from exc
        except Exception as exc:
            raise _artifact_unavailable() from exc
        return load_skill_artifact(
            raw,
            expected_artifact_sha256=summary.version.artifact_sha256,
            expected_bundle_hash=summary.version.bundle_hash,
            limits=self.limits,
        )

    async def get_summary(self, skill_id: str) -> ManagedSkillSummary | None:
        async with self.database.session() as db:
            record = await db.get(SkillRecord, skill_id)
            if record is None or record.archived_at is not None:
                return None
            version = await self._require_current_version(db, record)
            return _summary(record, version)

    async def get_active_workspace_id(self, skill_id: str) -> str | None:
        async with self.database.session() as db:
            return await db.scalar(
                select(SkillRecord.workspace_id).where(
                    SkillRecord.id == skill_id,
                    SkillRecord.scope == "workspace",
                    SkillRecord.archived_at.is_(None),
                )
            )

    async def require_summary(self, skill_id: str) -> ManagedSkillSummary:
        summary = await self.get_summary(skill_id)
        if summary is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        return summary

    async def get_stored(self, *args: str) -> StoredSkill | None:
        if len(args) == 1:
            workspace_id = None
            skill_id = args[0]
        elif len(args) == 2:
            workspace_id, skill_id = args
        else:
            raise TypeError("get_stored expects skill_id or workspace_id, skill_id")
        summary = await self.get_summary(skill_id)
        if summary is None or (
            workspace_id is not None and summary.workspace_id != workspace_id
        ):
            return None
        return StoredSkill(summary=summary, bundle=await self.load_bundle(summary))

    async def archive_skill(
        self,
        skill_id: str,
        expected_hash: str,
        archived_at: datetime,
        *,
        authorization: SkillMutationAuthorization | None = None,
    ) -> None:
        current_name = await self._active_skill_name(skill_id)
        if current_name is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        async with self.database.session() as db, db.begin():
            await self._require_mutation_authorization(db, authorization)
            await self._lock_name(db, current_name)
            record = await db.get(SkillRecord, skill_id)
            if record is None or record.archived_at is not None:
                raise _skill_changed()
            _require_record_authorized(authorization, record)
            version = await self._require_current_version(db, record)
            if version.bundle_hash != expected_hash:
                raise _skill_changed()
            result = await db.execute(
                update(SkillRecord)
                .where(
                    SkillRecord.id == skill_id,
                    SkillRecord.current_version_id == version.id,
                    SkillRecord.bundle_hash == expected_hash,
                    SkillRecord.archived_at.is_(None),
                )
                .values(
                    archived_at=archived_at,
                    enabled=record.scope == "global",
                    updated_at=archived_at,
                )
            )
            _require_one_cas_row(result)

    # Phase 1 compatibility surface. These wrappers use Artifacts and immutable versions.
    async def list_summaries(
        self, workspace_id: str
    ) -> tuple[ManagedSkillSummary, ...]:
        return (await self.list_catalog(workspace_id)).personal_skills

    async def count_active_by_workspace(
        self, workspace_ids: Sequence[str]
    ) -> dict[str, int]:
        counts = {workspace_id: 0 for workspace_id in workspace_ids}
        if not counts:
            return counts
        async with self.database.session() as db:
            rows = (
                await db.execute(
                    select(SkillRecord.workspace_id, func.count())
                    .where(
                        SkillRecord.scope == "workspace",
                        SkillRecord.workspace_id.in_(counts),
                        SkillRecord.archived_at.is_(None),
                    )
                    .group_by(SkillRecord.workspace_id)
                )
            ).all()
        counts.update({workspace_id: int(count) for workspace_id, count in rows})
        return counts

    async def get_archived_bootstrap(
        self, workspace_id: str, name: str
    ) -> ManagedSkillSummary | None:
        async with self.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(SkillRecord)
                        .where(
                            SkillRecord.scope == "workspace",
                            SkillRecord.workspace_id == workspace_id,
                            SkillRecord.archived_at.is_not(None),
                            SkillRecord.normalized_name == name.casefold(),
                        )
                        .order_by(SkillRecord.updated_at.desc(), SkillRecord.id)
                    )
                ).all()
            )
            for record in records:
                if _origin(record.config_json).get("type") not in {
                    "workspace_manifest_bootstrap",
                    "local_bootstrap",
                }:
                    continue
                return _summary(record, await self._require_current_version(db, record))
        return None

    async def get_for_manager(self, skill_id: str, user_id: str) -> StoredSkill | None:
        summary = await self._get_authorized_summary(
            skill_id, user_id, roles=("owner", "admin")
        )
        if summary is None:
            return None
        return StoredSkill(summary, await self.load_bundle(summary))

    async def get_for_member(self, skill_id: str, user_id: str) -> StoredSkill | None:
        summary = await self._get_authorized_summary(skill_id, user_id)
        if summary is None:
            return None
        return StoredSkill(summary, await self.load_bundle(summary))

    async def get_active_by_name(
        self, workspace_id: str, name: str
    ) -> StoredSkill | None:
        async with self.database.session() as db:
            skill_id = await db.scalar(
                select(SkillRecord.id).where(
                    SkillRecord.scope == "workspace",
                    SkillRecord.workspace_id == workspace_id,
                    SkillRecord.archived_at.is_(None),
                    SkillRecord.normalized_name == name.casefold(),
                )
            )
        return None if skill_id is None else await self.get_stored(skill_id)

    async def insert_bundle(
        self,
        workspace_id: str,
        created_by: str,
        bundle: SkillBundle,
        enabled: bool,
        origin: dict[str, object],
        *,
        manager_workspace_ids: tuple[str, ...] | None = None,
    ) -> StoredSkill:
        required_workspaces = tuple(
            dict.fromkeys((workspace_id, *(manager_workspace_ids or ())))
        )
        for candidate in required_workspaces:
            if not await self._is_manager(created_by, candidate):
                raise AppError("skill_not_found", "Skill not found.", 404)
        artifact = build_skill_artifact(bundle)
        await self._put_artifact(artifact)
        summary = await self.insert_versioned_skill(
            scope="workspace",
            workspace_id=workspace_id,
            created_by=created_by,
            bundle=bundle,
            artifact=artifact,
            enabled=enabled,
            origin=origin,
            authorization=SkillMutationAuthorization.workspace_manager(
                created_by, required_workspaces
            ),
        )
        return StoredSkill(summary, bundle)

    async def replace_bundle(
        self,
        skill_id: str,
        expected_hash: str,
        bundle: SkillBundle,
        *,
        user_id: str,
        origin: dict[str, object] | None = None,
    ) -> StoredSkill:
        current = await self.get_for_manager(skill_id, user_id)
        if current is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        if current.bundle_hash != expected_hash:
            raise _skill_changed()
        artifact = build_skill_artifact(bundle)
        await self._put_artifact(artifact)
        summary = await self.replace_current_version(
            skill_id,
            expected_hash=expected_hash,
            bundle=bundle,
            artifact=artifact,
            created_by=user_id,
            origin=origin,
            authorization=SkillMutationAuthorization.workspace_manager(
                user_id, (current.workspace_id or "",)
            ),
        )
        return StoredSkill(summary, bundle)

    async def set_enabled(
        self, skill_id: str, expected_hash: str, enabled: bool, *, user_id: str
    ) -> StoredSkill:
        current = await self.get_for_manager(skill_id, user_id)
        if current is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        summary = await self.set_personal_enabled(
            skill_id,
            expected_hash,
            enabled,
            authorization=SkillMutationAuthorization.workspace_manager(
                user_id, (current.workspace_id or "",)
            ),
        )
        return StoredSkill(summary, current.bundle)

    async def archive(
        self, skill_id: str, expected_hash: str, archived_at: datetime, *, user_id: str
    ) -> bool:
        current = await self.get_for_manager(skill_id, user_id)
        if current is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        await self.archive_skill(
            skill_id,
            expected_hash,
            archived_at,
            authorization=SkillMutationAuthorization.workspace_manager(
                user_id, (current.workspace_id or "",)
            ),
        )
        return True

    async def get_enabled_bundles(
        self, workspace_id: str
    ) -> tuple[tuple[str, SkillBundle], ...]:
        summaries = await self.list_effective_versions(workspace_id)
        bundles: list[tuple[str, SkillBundle]] = []
        for summary in summaries:
            bundles.append((summary.id, await self.load_bundle(summary)))
        return tuple(bundles)

    async def _put_artifact(self, artifact: SkillArtifact) -> None:
        try:
            await asyncio.to_thread(self.artifacts.put, artifact)
        except AppError:
            raise
        except Exception as exc:
            raise _artifact_unavailable() from exc

    async def _active_skill_name(self, skill_id: str) -> str | None:
        async with self.database.session() as db:
            return await db.scalar(
                select(SkillRecord.normalized_name).where(
                    SkillRecord.id == skill_id,
                    SkillRecord.archived_at.is_(None),
                )
            )

    async def _get_authorized_summary(
        self,
        skill_id: str,
        user_id: str,
        *,
        roles: tuple[str, ...] | None = None,
    ) -> ManagedSkillSummary | None:
        statement = (
            select(SkillRecord)
            .join(
                WorkspaceMemberRecord,
                WorkspaceMemberRecord.workspace_id == SkillRecord.workspace_id,
            )
            .where(
                SkillRecord.id == skill_id,
                SkillRecord.scope == "workspace",
                SkillRecord.archived_at.is_(None),
                WorkspaceMemberRecord.user_id == user_id,
            )
        )
        if roles is not None:
            statement = statement.where(WorkspaceMemberRecord.role.in_(roles))
        async with self.database.session() as db:
            record = await db.scalar(statement)
            if record is None:
                return None
            version = await self._require_current_version(db, record)
            return _summary(record, version)

    async def _require_mutation_authorization(
        self,
        db: AsyncSession,
        authorization: SkillMutationAuthorization | None,
    ) -> None:
        if authorization is None or authorization.mode == "trusted":
            return
        if authorization.user_id is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        if authorization.mode == "global_contributor":
            if db.bind is not None and db.bind.dialect.name == "sqlite":
                # SQLite ignores FOR UPDATE and defers BEGIN until the first write.
                # Acquire its write reservation before reading the contributor so a
                # concurrent deprovision cannot commit between authorization and the
                # Skill mutation guarded by this transaction.
                await db.execute(text("BEGIN IMMEDIATE"))
            contributor = await db.scalar(
                select(UserRecord)
                .where(UserRecord.id == authorization.user_id)
                .with_for_update()
            )
            if contributor is None:
                raise AppError("skill_not_found", "Skill not found.", 404)
            return
        if authorization.mode == "skill_admin":
            binding = await db.scalar(
                select(PlatformRoleBindingRecord)
                .where(
                    PlatformRoleBindingRecord.user_id == authorization.user_id,
                    PlatformRoleBindingRecord.role == "skill_admin",
                )
                .with_for_update()
            )
            if binding is None:
                raise AppError("skill_not_found", "Skill not found.", 404)
            return
        if authorization.mode == "personal_owner":
            if len(authorization.workspace_ids) != 1:
                raise AppError("skill_not_found", "Skill not found.", 404)
            workspace_id = authorization.workspace_ids[0]
            workspace = await db.scalar(
                select(WorkspaceRecord)
                .where(WorkspaceRecord.id == workspace_id)
                .with_for_update()
            )
            membership = await db.scalar(
                select(WorkspaceMemberRecord)
                .where(
                    WorkspaceMemberRecord.workspace_id == workspace_id,
                    WorkspaceMemberRecord.user_id == authorization.user_id,
                    WorkspaceMemberRecord.role == "owner",
                )
                .with_for_update()
            )
            if workspace is None or membership is None:
                raise AppError("workspace_not_found", "Workspace not found.", 404)
            if workspace.kind != "personal":
                raise AppError(
                    "skill_scope_invalid",
                    "Personal Skills require a personal Workspace.",
                    422,
                )
            return
        if authorization.mode != "workspace_manager":
            raise AppError("skill_not_found", "Skill not found.", 404)
        for workspace_id in sorted(set(authorization.workspace_ids)):
            membership = await db.scalar(
                select(WorkspaceMemberRecord)
                .where(
                    WorkspaceMemberRecord.workspace_id == workspace_id,
                    WorkspaceMemberRecord.user_id == authorization.user_id,
                    WorkspaceMemberRecord.role.in_(("owner", "admin")),
                )
                .with_for_update()
            )
            if membership is None:
                raise AppError("workspace_not_found", "Workspace not found.", 404)

    async def _lock_name(self, db, normalized_name: str) -> None:
        statement = (
            postgresql_insert(SkillNameLockRecord)
            if db.bind is not None and db.bind.dialect.name == "postgresql"
            else sqlite_insert(SkillNameLockRecord)
        )
        await db.execute(
            statement.values(normalized_name=normalized_name).on_conflict_do_nothing(
                index_elements=("normalized_name",)
            )
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            await db.scalar(
                select(SkillNameLockRecord)
                .where(SkillNameLockRecord.normalized_name == normalized_name)
                .with_for_update()
            )

    async def _reject_effective_name_conflict(
        self,
        db,
        *,
        scope: str,
        workspace_id: str | None,
        normalized_name: str,
        exclude_skill_id: str | None = None,
    ) -> None:
        statement = select(SkillRecord.id).where(
            SkillRecord.archived_at.is_(None),
            SkillRecord.normalized_name == normalized_name,
        )
        if exclude_skill_id is not None:
            statement = statement.where(SkillRecord.id != exclude_skill_id)
        if scope == "workspace":
            statement = statement.where(
                (SkillRecord.scope == "global")
                | (
                    (SkillRecord.scope == "workspace")
                    & (SkillRecord.workspace_id == workspace_id)
                )
            )
        if await db.scalar(statement.limit(1)) is not None:
            raise _name_conflict()

    @staticmethod
    async def _require_current_version(db, record: SkillRecord) -> SkillVersionRecord:
        if record.current_version_id is None:
            raise _invalid_version_metadata()
        version = await db.get(SkillVersionRecord, record.current_version_id)
        if (
            version is None
            or version.skill_id != record.id
            or version.status != "ready"
        ):
            raise _invalid_version_metadata()
        return version

    async def _is_manager(self, user_id: str, workspace_id: str) -> bool:
        async with self.database.session() as db:
            return (
                await db.scalar(
                    select(WorkspaceMemberRecord.workspace_id).where(
                        WorkspaceMemberRecord.workspace_id == workspace_id,
                        WorkspaceMemberRecord.user_id == user_id,
                        WorkspaceMemberRecord.role.in_(("owner", "admin")),
                    )
                )
                is not None
            )

    async def _is_member(self, user_id: str, workspace_id: str) -> bool:
        async with self.database.session() as db:
            return (
                await db.get(WorkspaceMemberRecord, (workspace_id, user_id)) is not None
            )


def _require_insert_authorized(
    authorization: SkillMutationAuthorization | None,
    *,
    scope: str,
    workspace_id: str | None,
) -> None:
    if authorization is None:
        return
    if authorization.mode in {"global_contributor", "skill_admin", "trusted"}:
        authorized = scope == "global" and workspace_id is None
    else:
        authorized = (
            scope == "workspace"
            and workspace_id is not None
            and workspace_id in authorization.workspace_ids
        )
    if not authorized:
        raise AppError("skill_not_found", "Skill not found.", 404)


def _require_record_authorized(
    authorization: SkillMutationAuthorization | None, record: SkillRecord
) -> None:
    if authorization is None:
        return
    if authorization.mode in {"global_contributor", "skill_admin", "trusted"}:
        authorized = record.scope == "global" and record.workspace_id is None
    else:
        authorized = (
            record.scope == "workspace"
            and record.workspace_id is not None
            and record.workspace_id in authorization.workspace_ids
        )
    if not authorized:
        raise AppError("skill_not_found", "Skill not found.", 404)


def _summary(
    record: SkillRecord,
    version: SkillVersionRecord,
    *,
    enabled: bool | None = None,
) -> ManagedSkillSummary:
    return ManagedSkillSummary(
        id=record.id,
        scope=record.scope,
        workspace_id=record.workspace_id,
        name=record.name,
        description=record.description,
        enabled=record.enabled if enabled is None else enabled,
        origin=_origin(record.config_json),
        version=SkillVersionRef(
            id=version.id,
            version_no=version.version_no,
            bundle_hash=version.bundle_hash,
            artifact_key=version.artifact_key,
            artifact_sha256=version.artifact_sha256,
            manifest_json=version.manifest_json,
            size_bytes=version.size_bytes,
        ),
        updated_at=_as_utc(record.updated_at),
    )


def _origin(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _origin_json(origin: dict[str, object]) -> str:
    return json.dumps(origin, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_scope(scope: str, workspace_id: str | None, enabled: bool) -> None:
    if (
        scope not in {"global", "workspace"}
        or (scope == "global" and (workspace_id is not None or not enabled))
        or (scope == "workspace" and workspace_id is None)
    ):
        raise AppError("skill_scope_invalid", "Skill scope is invalid.", 422)


def _validate_artifact_metadata(bundle: SkillBundle, artifact: SkillArtifact) -> None:
    if artifact.bundle_hash != bundle.bundle_hash:
        raise _artifact_corrupt()


def _compatibility_artifact_store(database: Database) -> SkillArtifactStore:
    existing = getattr(database, "_compatibility_skill_artifacts", None)
    if existing is not None:
        return existing
    if database.engine.dialect.name == "postgresql":
        raise ValueError("PostgreSQL requires an explicit Skill Artifact store.")
    database_path = database.engine.url.database
    if database_path and database_path != ":memory:":
        root = Path(database_path).expanduser().resolve().parent / "skill-artifacts"
    else:
        root = Path(
            tempfile.mkdtemp(prefix="claude-workspace-skill-artifacts-")
        ).resolve()
    store = FilesystemSkillArtifactStore(root)
    database._compatibility_skill_artifacts = store
    return store


def _is_name_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig)
    return any(
        marker in message
        for marker in (
            "uq_skills_workspace_active_name",
            "uq_skills_global_active_name",
            "UNIQUE constraint failed: skills.workspace_id, skills.normalized_name",
            "UNIQUE constraint failed: skills.normalized_name",
        )
    )


def _name_conflict() -> AppError:
    return AppError(
        "skill_name_conflict", "An active Skill already uses this name.", 409
    )


def _skill_changed() -> AppError:
    return AppError("skill_changed", "Skill was changed by another manager.", 409)


def _require_one_cas_row(result: object) -> None:
    if getattr(result, "rowcount", None) != 1:
        raise _skill_changed()


def _invalid_version_metadata() -> AppError:
    return AppError("skill_artifact_corrupt", "Skill version metadata is invalid.", 500)


def _artifact_corrupt() -> AppError:
    return AppError(
        "skill_artifact_corrupt", "Skill artifact integrity check failed.", 500
    )


def _artifact_unavailable() -> AppError:
    return AppError(
        "skill_artifact_unavailable", "Skill artifact store is unavailable.", 503
    )
