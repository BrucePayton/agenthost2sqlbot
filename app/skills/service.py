from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.auth.access import WorkspaceAccessService
from app.auth.models import IdentityContext
from app.db.base import Database
from app.db.models import AppMetadataRecord, SkillRecord, WorkspaceRecord
from app.errors import AppError
from app.skills.access import PlatformSkillAccessService
from app.skills.artifacts import SkillArtifact, build_skill_artifact
from app.skills.bundle import (
    SkillBundleLimits,
    UploadedSkillDirectory,
    build_bundle,
    invalid_bundle,
    load_bundle_from_archive,
    load_bundle_from_directory,
    rename_bundle,
)
from app.skills.models import (
    ManagedSkillSummary,
    ResolvedSkillBundle,
    SkillBundle,
    SkillCatalog,
    StoredSkill,
)
from app.skills.repository import (
    SkillMutationAuthorization,
    SkillRepository,
    SkillSummary,
)
from app.workspaces.models import WorkspaceEntry

logger = logging.getLogger(__name__)

_BOOTSTRAP_MARKER = "global_skill_import_v1"
_BOOTSTRAP_SOURCE_PREFIX = "global_skill_import_source_v1:"
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

SkillImportStatus = Literal[
    "created",
    "overwritten",
    "renamed",
    "already_imported",
]
SkillConflictPolicy = Literal["fail", "overwrite", "rename"]
BootstrapSourceKind = Literal["workspace_manifest", "local_root"]


@dataclass(frozen=True)
class SkillImportResult:
    status: SkillImportStatus
    skill: StoredSkill


@dataclass(frozen=True)
class _SourceMarker:
    """What one trusted bootstrap source reconciled, and from which content."""

    name: str
    bundle_hash: str | None


@dataclass(frozen=True)
class BootstrapItem:
    workspace_id: str
    name: str
    status: Literal["created", "skipped", "conflict", "failed"]
    message: str = ""
    blocking: bool = False


@dataclass(frozen=True)
class BootstrapReport:
    items: tuple[BootstrapItem, ...]

    @property
    def created(self) -> int:
        return sum(item.status == "created" for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.status == "failed" for item in self.items)

    @property
    def blocks_completion(self) -> bool:
        return any(item.status == "failed" or item.blocking for item in self.items)

    def summary(self) -> dict[str, int]:
        return {
            status: sum(item.status == status for item in self.items)
            for status in ("created", "skipped", "conflict", "failed")
        }


class SkillService:
    def __init__(
        self,
        repository: SkillRepository,
        access: WorkspaceAccessService,
        platform_access: PlatformSkillAccessService | SkillBundleLimits | None = None,
        limits: SkillBundleLimits | None = None,
    ) -> None:
        if isinstance(platform_access, SkillBundleLimits):
            if limits is not None:
                raise TypeError("Skill bundle limits were provided twice.")
            limits = platform_access
            platform_access = None
        self.repository = repository
        self.access = access
        self.platform_access = platform_access or PlatformSkillAccessService(
            repository.database
        )
        self.limits = limits or repository.limits
        if self.limits != repository.limits:
            raise ValueError("Skill service and repository limits must match.")

    async def catalog(
        self, workspace_id: str, identity: IdentityContext
    ) -> SkillCatalog:
        await self.access.require_personal_owner(identity, workspace_id)
        return await self.repository.list_catalog(workspace_id)

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
        await self.access.require_personal_owner(identity, workspace_id)
        bundle = load_bundle_from_archive(raw, self.limits)
        return await self._import_personal_bundle(
            workspace_id,
            identity,
            bundle,
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
            origin={"type": "archive"},
        )

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
        await self.access.require_personal_owner(identity, workspace_id)
        return await self._import_personal_bundle(
            workspace_id,
            identity,
            self._verify_bundle(bundle),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
            origin=origin,
        )

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
        await self.access.require_personal_owner(identity, workspace_id)
        return await self._import_personal_bundle(
            workspace_id,
            identity,
            self._verify_bundle(uploaded.bundle),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
            origin={
                "type": "browser_directory",
                "source_name": uploaded.source_name,
            },
        )

    async def import_global_archive(
        self,
        identity: IdentityContext,
        raw: bytes,
        *,
        on_conflict: SkillConflictPolicy,
        expected_hash: str | None,
        target_name: str | None,
    ) -> SkillImportResult:
        await self.platform_access.require_global_contributor(identity)
        bundle = load_bundle_from_archive(raw, self.limits)
        return await self._import_global_bundle(
            bundle,
            created_by=identity.user_id,
            authorization=SkillMutationAuthorization.global_contributor(
                identity.user_id
            ),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
            origin={"type": "archive"},
        )

    async def import_global_bundle(
        self,
        identity: IdentityContext,
        bundle: SkillBundle,
        *,
        on_conflict: SkillConflictPolicy = "fail",
        expected_hash: str | None = None,
        target_name: str | None = None,
        origin: dict[str, object],
    ) -> SkillImportResult:
        await self.platform_access.require_global_contributor(identity)
        return await self._import_global_bundle(
            self._verify_bundle(bundle),
            created_by=identity.user_id,
            authorization=SkillMutationAuthorization.global_contributor(
                identity.user_id
            ),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
            origin=origin,
        )

    async def import_global_uploaded_directory(
        self,
        identity: IdentityContext,
        uploaded: UploadedSkillDirectory,
        *,
        on_conflict: SkillConflictPolicy,
        expected_hash: str | None,
        target_name: str | None,
    ) -> SkillImportResult:
        await self.platform_access.require_global_contributor(identity)
        return await self._import_global_bundle(
            self._verify_bundle(uploaded.bundle),
            created_by=identity.user_id,
            authorization=SkillMutationAuthorization.global_contributor(
                identity.user_id
            ),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
            origin={
                "type": "browser_directory",
                "source_name": uploaded.source_name,
            },
        )

    async def set_personal_enabled(
        self,
        skill_id: str,
        identity: IdentityContext,
        *,
        enabled: bool,
        expected_hash: str,
    ) -> ManagedSkillSummary:
        async with self.repository.database.session() as db:
            skill_location = (
                await db.execute(
                    select(SkillRecord.scope, SkillRecord.workspace_id).where(
                        SkillRecord.id == skill_id,
                        SkillRecord.archived_at.is_(None),
                    )
                )
            ).first()
        if skill_location is None or skill_location.scope != "workspace":
            raise AppError("skill_not_found", "Skill not found.", 404)
        await self.access.require_personal_owner(
            identity, skill_location.workspace_id or ""
        )
        return await self.repository.set_personal_enabled(
            skill_id,
            expected_hash,
            enabled,
            authorization=SkillMutationAuthorization.personal_owner(
                identity.user_id, skill_location.workspace_id or ""
            ),
        )

    async def set_global_enabled(
        self,
        workspace_id: str,
        skill_id: str,
        identity: IdentityContext,
        *,
        enabled: bool,
    ) -> ManagedSkillSummary:
        await self.access.require_personal_owner(identity, workspace_id)
        return await self.repository.set_global_setting(
            workspace_id,
            skill_id,
            enabled,
            updated_by=identity.user_id,
            authorization=SkillMutationAuthorization.personal_owner(
                identity.user_id, workspace_id
            ),
        )

    async def resolve_effective_skills(
        self, workspace_id: str, identity: IdentityContext
    ) -> tuple[ResolvedSkillBundle, ...]:
        await self.access.require_member(identity, workspace_id)
        async with self.repository.database.session() as db:
            workspace = await db.get(WorkspaceRecord, workspace_id)
        if workspace is None:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        if workspace.kind != "personal":
            return ()
        await self.access.require_personal_owner(identity, workspace_id)
        summaries = await self.repository.list_effective_versions(workspace_id)
        resolved: list[ResolvedSkillBundle] = []
        for summary in summaries:
            resolved.append(
                ResolvedSkillBundle(
                    skill=summary,
                    bundle=await self.repository.load_bundle(summary),
                )
            )
        return tuple(resolved)

    async def get_for_workspace(
        self, workspace_id: str, skill_id: str, identity: IdentityContext
    ) -> StoredSkill:
        await self.access.require_personal_owner(identity, workspace_id)
        catalog = await self.repository.list_catalog(workspace_id)
        summary = next(
            (
                item
                for item in (*catalog.global_skills, *catalog.personal_skills)
                if item.id == skill_id
            ),
            None,
        )
        if summary is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        return StoredSkill(summary, await self.repository.load_bundle(summary))

    async def archive_personal(
        self,
        workspace_id: str,
        skill_id: str,
        identity: IdentityContext,
        *,
        expected_hash: str,
    ) -> None:
        await self.access.require_personal_owner(identity, workspace_id)
        summary = await self.repository.get_summary(skill_id)
        if (
            summary is None
            or summary.scope != "workspace"
            or summary.workspace_id != workspace_id
        ):
            raise AppError("skill_not_found", "Skill not found.", 404)
        await self.repository.archive_skill(
            skill_id,
            expected_hash,
            datetime.now(UTC),
            authorization=SkillMutationAuthorization.personal_owner(
                identity.user_id, workspace_id
            ),
        )

    async def archive_global(
        self, skill_id: str, identity: IdentityContext, *, expected_hash: str
    ) -> None:
        await self.platform_access.require_global_contributor(identity)
        summary = await self.repository.get_summary(skill_id)
        if summary is None or summary.scope != "global":
            raise AppError("skill_not_found", "Skill not found.", 404)
        await self.repository.archive_skill(
            skill_id,
            expected_hash,
            datetime.now(UTC),
            authorization=SkillMutationAuthorization.global_contributor(
                identity.user_id
            ),
        )

    async def count_effective_by_workspace(
        self, workspace_ids: tuple[str, ...]
    ) -> dict[str, int]:
        return await self.repository.count_effective_by_workspace(workspace_ids)

    async def publish_trusted_global_bundle(
        self,
        bundle: SkillBundle,
        *,
        created_by: str | None,
        origin: dict[str, object],
        allow_update: bool = False,
    ) -> SkillImportResult:
        verified = self._verify_bundle(bundle)
        on_conflict: SkillConflictPolicy = "fail"
        expected_hash: str | None = None
        if allow_update:
            # A trusted source that changed on disk is an update, not a collision.
            # The caller decides who owns the Skill; here we only take the current
            # hash so the replacement stays optimistic about concurrent writers.
            existing = await self._get_active_global_by_name(verified.name)
            if (
                existing is not None
                and existing.bundle_hash != verified.bundle_hash
                and existing.origin.get("type") == "global_bootstrap"
            ):
                on_conflict = "overwrite"
                expected_hash = existing.bundle_hash
        return await self._import_global_bundle(
            verified,
            created_by=created_by,
            authorization=SkillMutationAuthorization.trusted(),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=None,
            origin=origin,
        )

    async def has_archived_trusted_global(self, name: str) -> bool:
        async with self.repository.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(SkillRecord).where(
                            SkillRecord.scope == "global",
                            SkillRecord.archived_at.is_not(None),
                            SkillRecord.normalized_name == name.casefold(),
                        )
                    )
                ).all()
            )
        return any(
            _origin(record.config_json).get("type") == "global_bootstrap"
            for record in records
        )

    async def _import_personal_bundle(
        self,
        workspace_id: str,
        identity: IdentityContext,
        bundle: SkillBundle,
        *,
        on_conflict: SkillConflictPolicy,
        expected_hash: str | None,
        target_name: str | None,
        origin: dict[str, object],
    ) -> SkillImportResult:
        self._validate_import_policy(on_conflict, expected_hash, target_name)
        incoming = (
            rename_bundle(bundle, target_name or "", self.limits)
            if on_conflict == "rename"
            else bundle
        )
        existing = await self.repository.get_active_by_name(workspace_id, incoming.name)
        if on_conflict == "rename":
            if existing is not None:
                raise _import_conflict(existing, incoming)
            return await self._insert_personal(
                workspace_id,
                identity,
                incoming,
                origin,
                status="renamed",
                idempotent=False,
            )
        if existing is None:
            if on_conflict == "overwrite":
                raise _skill_changed()
            return await self._insert_personal(
                workspace_id,
                identity,
                incoming,
                origin,
                status="created",
                idempotent=True,
            )
        if on_conflict == "overwrite" and existing.bundle_hash != expected_hash:
            raise _skill_changed()
        if existing.bundle_hash == incoming.bundle_hash:
            return SkillImportResult("already_imported", existing)
        if on_conflict == "fail":
            raise _import_conflict(existing, incoming)
        artifact = await self._publish_artifact(incoming)
        summary = await self.repository.replace_current_version(
            existing.id,
            expected_hash=expected_hash or "",
            bundle=incoming,
            artifact=artifact,
            created_by=identity.user_id,
            origin=origin,
            authorization=SkillMutationAuthorization.personal_owner(
                identity.user_id, workspace_id
            ),
        )
        return SkillImportResult("overwritten", StoredSkill(summary, incoming))

    async def _insert_personal(
        self,
        workspace_id: str,
        identity: IdentityContext,
        bundle: SkillBundle,
        origin: dict[str, object],
        *,
        status: Literal["created", "renamed"],
        idempotent: bool,
    ) -> SkillImportResult:
        artifact = await self._publish_artifact(bundle)
        try:
            summary = await self.repository.insert_versioned_skill(
                scope="workspace",
                workspace_id=workspace_id,
                created_by=identity.user_id,
                bundle=bundle,
                artifact=artifact,
                enabled=True,
                origin=origin,
                authorization=SkillMutationAuthorization.personal_owner(
                    identity.user_id, workspace_id
                ),
            )
        except AppError as exc:
            if exc.code != "skill_name_conflict":
                raise
            existing = await self.repository.get_active_by_name(
                workspace_id, bundle.name
            )
            if existing is None:
                raise
            if idempotent and existing.bundle_hash == bundle.bundle_hash:
                return SkillImportResult("already_imported", existing)
            raise _import_conflict(existing, bundle) from exc
        return SkillImportResult(status, StoredSkill(summary, bundle))

    async def _import_global_bundle(
        self,
        bundle: SkillBundle,
        *,
        created_by: str | None,
        authorization: SkillMutationAuthorization,
        on_conflict: SkillConflictPolicy,
        expected_hash: str | None,
        target_name: str | None,
        origin: dict[str, object],
    ) -> SkillImportResult:
        self._validate_import_policy(on_conflict, expected_hash, target_name)
        incoming = (
            rename_bundle(bundle, target_name or "", self.limits)
            if on_conflict == "rename"
            else bundle
        )
        existing = await self._get_active_global_by_name(incoming.name)
        if on_conflict == "rename":
            if existing is not None:
                raise _import_conflict(existing, incoming)
            return await self._insert_global(
                incoming,
                created_by=created_by,
                authorization=authorization,
                origin=origin,
                status="renamed",
                idempotent=False,
            )
        if existing is None:
            if on_conflict == "overwrite":
                raise _skill_changed()
            return await self._insert_global(
                incoming,
                created_by=created_by,
                authorization=authorization,
                origin=origin,
                status="created",
                idempotent=True,
            )
        if on_conflict == "overwrite" and existing.bundle_hash != expected_hash:
            raise _skill_changed()
        if existing.bundle_hash == incoming.bundle_hash:
            return SkillImportResult("already_imported", existing)
        if on_conflict == "fail":
            raise _import_conflict(existing, incoming)
        conflict_count = await self._personal_conflict_count(
            incoming.name, exclude_skill_id=existing.id
        )
        if conflict_count:
            raise _global_name_conflict(conflict_count)
        artifact = await self._publish_artifact(incoming)
        try:
            summary = await self.repository.replace_current_version(
                existing.id,
                expected_hash=expected_hash or "",
                bundle=incoming,
                artifact=artifact,
                created_by=created_by,
                origin=origin,
                authorization=authorization,
            )
        except AppError as exc:
            if exc.code == "skill_name_conflict":
                conflict_count = await self._personal_conflict_count(incoming.name)
                if conflict_count:
                    raise _global_name_conflict(conflict_count) from exc
            raise
        return SkillImportResult("overwritten", StoredSkill(summary, incoming))

    async def _insert_global(
        self,
        bundle: SkillBundle,
        *,
        created_by: str | None,
        authorization: SkillMutationAuthorization,
        origin: dict[str, object],
        status: Literal["created", "renamed"],
        idempotent: bool,
    ) -> SkillImportResult:
        conflict_count = await self._personal_conflict_count(bundle.name)
        if conflict_count:
            raise _global_name_conflict(conflict_count)
        artifact = await self._publish_artifact(bundle)
        try:
            summary = await self.repository.insert_versioned_skill(
                scope="global",
                workspace_id=None,
                created_by=created_by,
                bundle=bundle,
                artifact=artifact,
                enabled=True,
                origin=origin,
                authorization=authorization,
            )
        except AppError as exc:
            if exc.code != "skill_name_conflict":
                raise
            conflict_count = await self._personal_conflict_count(bundle.name)
            if conflict_count:
                raise _global_name_conflict(conflict_count) from exc
            existing = await self._get_active_global_by_name(bundle.name)
            if existing is None:
                raise
            if idempotent and existing.bundle_hash == bundle.bundle_hash:
                return SkillImportResult("already_imported", existing)
            raise _import_conflict(existing, bundle) from exc
        return SkillImportResult(status, StoredSkill(summary, bundle))

    async def _get_active_global_by_name(self, name: str) -> StoredSkill | None:
        async with self.repository.database.session() as db:
            skill_id = await db.scalar(
                select(SkillRecord.id).where(
                    SkillRecord.scope == "global",
                    SkillRecord.archived_at.is_(None),
                    SkillRecord.normalized_name == name.casefold(),
                )
            )
        return None if skill_id is None else await self.repository.get_stored(skill_id)

    async def _personal_conflict_count(
        self, name: str, *, exclude_skill_id: str | None = None
    ) -> int:
        statement = select(func.count(func.distinct(SkillRecord.workspace_id))).where(
            SkillRecord.scope == "workspace",
            SkillRecord.archived_at.is_(None),
            SkillRecord.normalized_name == name.casefold(),
        )
        if exclude_skill_id is not None:
            statement = statement.where(SkillRecord.id != exclude_skill_id)
        async with self.repository.database.session() as db:
            return int(await db.scalar(statement) or 0)

    async def _publish_artifact(self, bundle: SkillBundle) -> SkillArtifact:
        artifact = build_skill_artifact(bundle)
        try:
            await asyncio.to_thread(self.repository.artifacts.put, artifact)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "skill_artifact_unavailable",
                "Skill artifact store is unavailable.",
                503,
            ) from exc
        return artifact

    def _verify_bundle(self, bundle: SkillBundle) -> SkillBundle:
        return build_bundle(
            bundle.content.encode("utf-8"),
            [(file.path, file.content) for file in bundle.files],
            self.limits,
        )

    @staticmethod
    def _validate_import_policy(
        on_conflict: str,
        expected_hash: str | None,
        target_name: str | None,
    ) -> None:
        if on_conflict not in {"fail", "overwrite", "rename"}:
            raise invalid_bundle("Skill import conflict policy is invalid.")
        if on_conflict == "fail" and (
            expected_hash is not None or target_name is not None
        ):
            raise invalid_bundle("Skill import conflict fields are invalid.")
        if on_conflict == "overwrite" and (
            expected_hash is None
            or not _HASH_RE.fullmatch(expected_hash)
            or target_name is not None
        ):
            raise invalid_bundle("Skill overwrite fields are invalid.")
        if on_conflict == "rename" and (
            target_name is None or expected_hash is not None
        ):
            raise invalid_bundle("Skill rename fields are invalid.")

    # Phase 1 compatibility surface. The product API uses explicit scopes above.
    async def list(
        self, workspace_id: str, identity: IdentityContext
    ) -> tuple[SkillSummary, ...]:
        await self.access.require_member(identity, workspace_id)
        return await self.repository.list_summaries(workspace_id)

    async def get(self, skill_id: str, identity: IdentityContext) -> StoredSkill:
        skill = await self.repository.get_for_member(skill_id, identity.user_id)
        if skill is not None:
            return skill
        raise AppError("skill_not_found", "Skill not found.", 404)

    async def create(
        self, workspace_id: str, identity: IdentityContext, content: str
    ) -> StoredSkill:
        await self.access.require_manager(identity, workspace_id)
        bundle = build_bundle(content.encode("utf-8"), (), self.limits)
        return await self.repository.insert_bundle(
            workspace_id,
            identity.user_id,
            bundle,
            enabled=False,
            origin={"type": "manual"},
        )

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
            raise _skill_changed()
        bundle = build_bundle(
            content.encode("utf-8"),
            [(file.path, file.content) for file in current.files],
            self.limits,
        )
        return await self.repository.replace_bundle(
            current.id,
            expected_hash=expected_hash,
            bundle=bundle,
            user_id=identity.user_id,
        )

    async def set_enabled(
        self,
        skill_id: str,
        identity: IdentityContext,
        *,
        enabled: bool,
        expected_hash: str,
    ) -> StoredSkill:
        current = await self.repository.get_for_manager(skill_id, identity.user_id)
        if current is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        if current.bundle_hash != expected_hash:
            raise _skill_changed()
        return await self.repository.set_enabled(
            current.id, expected_hash, enabled, user_id=identity.user_id
        )

    async def archive(
        self, skill_id: str, identity: IdentityContext, *, expected_hash: str
    ) -> None:
        current = await self.repository.get_for_manager(skill_id, identity.user_id)
        if current is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        if current.bundle_hash != expected_hash:
            raise _skill_changed()
        await self.repository.archive(
            current.id, expected_hash, datetime.now(UTC), user_id=identity.user_id
        )

    async def copy(
        self, skill_id: str, target_workspace_id: str, identity: IdentityContext
    ) -> StoredSkill:
        source = await self.repository.get_for_manager(skill_id, identity.user_id)
        if source is None:
            raise AppError("skill_not_found", "Skill not found.", 404)
        await self.access.require_manager(identity, target_workspace_id)
        bundle = build_bundle(
            source.content.encode("utf-8"),
            [(file.path, file.content) for file in source.files],
            self.limits,
        )
        return await self.repository.insert_bundle(
            target_workspace_id,
            identity.user_id,
            bundle,
            enabled=False,
            origin={
                "type": "workspace_copy",
                "source_skill_id": source.id,
                "source_hash": source.bundle_hash,
            },
            manager_workspace_ids=(source.workspace_id or "", target_workspace_id),
        )

    async def import_archive(
        self, workspace_id: str, identity: IdentityContext, raw: bytes
    ) -> StoredSkill:
        await self.access.require_manager(identity, workspace_id)
        bundle = load_bundle_from_archive(raw, self.limits)
        return await self.repository.insert_bundle(
            workspace_id,
            identity.user_id,
            bundle,
            enabled=False,
            origin={"type": "archive"},
        )

    async def import_bundle(
        self,
        workspace_id: str,
        identity: IdentityContext,
        bundle: SkillBundle,
        *,
        enabled: bool,
        origin: dict[str, object] | None = None,
    ) -> StoredSkill:
        await self.access.require_manager(identity, workspace_id)
        return await self.repository.insert_bundle(
            workspace_id,
            identity.user_id,
            self._verify_bundle(bundle),
            enabled=enabled,
            origin=origin or {"type": "import"},
        )

    async def import_uploaded_directory(
        self,
        workspace_id: str,
        identity: IdentityContext,
        uploaded: UploadedSkillDirectory,
        *,
        on_conflict: SkillConflictPolicy,
        expected_hash: str | None = None,
        target_name: str | None = None,
    ) -> SkillImportResult:
        await self.access.require_manager(identity, workspace_id)
        self._validate_import_policy(on_conflict, expected_hash, target_name)
        origin: dict[str, object] = {
            "type": "browser_directory",
            "source_name": uploaded.source_name,
        }
        incoming = (
            rename_bundle(uploaded.bundle, target_name or "", self.limits)
            if on_conflict == "rename"
            else self._verify_bundle(uploaded.bundle)
        )
        existing = await self.repository.get_active_by_name(workspace_id, incoming.name)
        if on_conflict == "rename":
            if existing is not None:
                raise _import_conflict(existing, incoming)
            return await self._insert_compatibility_bundle(
                workspace_id,
                identity,
                incoming,
                origin,
                status="renamed",
                idempotent=False,
            )
        if existing is None:
            if on_conflict == "overwrite":
                raise _skill_changed()
            return await self._insert_compatibility_bundle(
                workspace_id,
                identity,
                incoming,
                origin,
                status="created",
                idempotent=True,
            )
        if existing.bundle_hash == incoming.bundle_hash:
            return SkillImportResult("already_imported", existing)
        if on_conflict == "fail":
            raise _import_conflict(existing, incoming)
        if existing.bundle_hash != expected_hash:
            raise _skill_changed()
        replaced = await self.repository.replace_bundle(
            existing.id,
            expected_hash=expected_hash or "",
            bundle=incoming,
            user_id=identity.user_id,
            origin=origin,
        )
        return SkillImportResult("overwritten", replaced)

    async def _insert_compatibility_bundle(
        self,
        workspace_id: str,
        identity: IdentityContext,
        bundle: SkillBundle,
        origin: dict[str, object],
        *,
        status: Literal["created", "renamed"],
        idempotent: bool,
    ) -> SkillImportResult:
        try:
            stored = await self.repository.insert_bundle(
                workspace_id,
                identity.user_id,
                bundle,
                enabled=False,
                origin=origin,
            )
        except AppError as exc:
            if exc.code != "skill_name_conflict":
                raise
            existing = await self.repository.get_active_by_name(
                workspace_id, bundle.name
            )
            if existing is None:
                raise
            if idempotent and existing.bundle_hash == bundle.bundle_hash:
                return SkillImportResult("already_imported", existing)
            raise _import_conflict(existing, bundle) from exc
        return SkillImportResult(status, stored)

    async def get_enabled_bundles(
        self, workspace_id: str
    ) -> tuple[tuple[str, SkillBundle], ...]:
        return await self.repository.get_enabled_bundles(workspace_id)


class SkillBootstrapService:
    def __init__(self, database: Database, skills: SkillService) -> None:
        self.database = database
        self.skills = skills

    async def run(
        self,
        entries: list[WorkspaceEntry],
        identity: IdentityContext | None,
        personal_workspace_id: str,
        local_root: Path | None,
    ) -> BootstrapReport:
        aggregate_completed = await self._is_completed()
        items: list[BootstrapItem] = []
        for entry in entries:
            if not entry.available or entry.manifest is None:
                continue
            for skill_name in entry.manifest.skills:
                items.append(
                    await self._import_directory(
                        entry.id,
                        skill_name,
                        (
                            entry.skills_source_root / skill_name
                            if entry.skills_source_root is not None
                            else None
                        ),
                        identity,
                        source_kind="workspace_manifest",
                        source_identity=_manifest_source_identity(entry),
                    )
                )

        if local_root is not None:
            try:
                local_skill_directories = self._local_skill_directories(local_root)
            except OSError:
                items.append(
                    self._result(
                        personal_workspace_id,
                        "local-root",
                        "failed",
                        "Local Skill root could not be read.",
                    )
                )
                local_skill_directories = ()
            for skill_directory in local_skill_directories:
                items.append(
                    await self._import_directory(
                        personal_workspace_id,
                        skill_directory.name,
                        skill_directory,
                        identity,
                        source_kind="local_root",
                        source_identity=str(local_root.expanduser().resolve()),
                    )
                )

        # The aggregate marker only records a prior report. Source-level markers
        # decide whether each current trusted source still needs reconciliation.
        report = BootstrapReport(
            tuple(
                item
                for item in items
                if not (aggregate_completed and item.status == "skipped")
            )
        )
        if not report.blocks_completion:
            await self._mark_completed(report)
        return report

    async def _is_completed(self) -> bool:
        async with self.database.session() as db:
            return await db.get(AppMetadataRecord, _BOOTSTRAP_MARKER) is not None

    async def _import_directory(
        self,
        workspace_id: str,
        source_name: str,
        directory: Path | None,
        identity: IdentityContext | None,
        *,
        source_kind: BootstrapSourceKind,
        source_identity: str,
    ) -> BootstrapItem:
        # The bundle is read before the marker so the marker can be compared
        # against the content on disk: a trusted source whose Skill changed must
        # be reconciled again, or the repository silently keeps serving the
        # version installed by the first bootstrap.
        bundle = None
        if directory is not None:
            try:
                bundle = load_bundle_from_directory(directory, self.skills.limits)
            except (AppError, OSError):
                bundle = None
        marker = await self._source_marker(
            workspace_id,
            source_kind,
            source_identity,
            source_name,
        )
        if marker is not None and (
            bundle is None or marker.bundle_hash == bundle.bundle_hash
        ):
            return self._result(
                workspace_id,
                marker.name,
                "skipped",
                "Trusted bootstrap source was already processed.",
            )
        if directory is None:
            return self._result(
                workspace_id, source_name, "failed", "Skill source is unavailable."
            )
        if bundle is None:
            return self._result(
                workspace_id, source_name, "failed", "Skill bundle could not be loaded."
            )
        if await self.skills.has_archived_trusted_global(bundle.name):
            await self._mark_source_completed(
                workspace_id,
                source_kind,
                source_identity,
                source_name,
                bundle.name,
                "archived",
                bundle.bundle_hash,
            )
            return self._result(
                workspace_id,
                bundle.name,
                "skipped",
                "Archived bootstrap Skill was already published.",
            )
        try:
            result = await self.skills.publish_trusted_global_bundle(
                bundle,
                created_by=identity.user_id if identity is not None else None,
                origin={"type": "global_bootstrap"},
                # Only this source's own Skill may be replaced. A same-named
                # Skill from another source stays a conflict.
                allow_update=marker is not None and marker.name == bundle.name,
            )
        except AppError as exc:
            if exc.code in {"skill_name_conflict", "skill_import_conflict"}:
                personal_collision = bool(
                    exc.code == "skill_name_conflict"
                    and exc.details
                    and exc.details.get("workspace_count")
                )
                if not personal_collision:
                    await self._mark_source_completed(
                        workspace_id,
                        source_kind,
                        source_identity,
                        source_name,
                        bundle.name,
                        "conflict",
                        bundle.bundle_hash,
                    )
                return self._result(
                    workspace_id,
                    bundle.name,
                    "conflict",
                    "Existing active Skill has the same name.",
                    blocking=personal_collision,
                )
            return self._result(
                workspace_id, bundle.name, "failed", "Skill could not be stored."
            )
        if result.status == "already_imported":
            await self._mark_source_completed(
                workspace_id,
                source_kind,
                source_identity,
                source_name,
                bundle.name,
                "already_imported",
                bundle.bundle_hash,
            )
            return self._result(
                workspace_id,
                bundle.name,
                "skipped",
                "Existing active Skill has the same bundle.",
            )
        await self._mark_source_completed(
            workspace_id,
            source_kind,
            source_identity,
            source_name,
            bundle.name,
            "created",
            bundle.bundle_hash,
        )
        return self._result(workspace_id, bundle.name, "created")

    @staticmethod
    def _local_skill_directories(local_root: Path) -> tuple[Path, ...]:
        return tuple(
            child
            for child in sorted(local_root.iterdir(), key=lambda path: path.name)
            if child.is_dir() and (child / "SKILL.md").is_file()
        )

    async def _mark_completed(self, report: BootstrapReport) -> None:
        now = datetime.now(UTC)
        value_json = json.dumps(report.summary(), sort_keys=True)
        async with self.database.session() as db:
            values = {
                "key": _BOOTSTRAP_MARKER,
                "value_json": value_json,
                "updated_at": now,
            }
            if self.database.engine.dialect.name == "sqlite":
                statement = sqlite_insert(AppMetadataRecord).values(**values)
            elif self.database.engine.dialect.name == "postgresql":
                statement = postgresql_insert(AppMetadataRecord).values(**values)
            else:
                raise RuntimeError(
                    "Skill bootstrap metadata requires SQLite or PostgreSQL."
                )
            await db.execute(
                statement.on_conflict_do_update(
                    index_elements=[AppMetadataRecord.key],
                    set_={"value_json": value_json, "updated_at": now},
                )
            )
            await db.commit()

    async def _source_marker(
        self,
        workspace_id: str,
        source_kind: BootstrapSourceKind,
        source_identity: str,
        source_name: str,
    ) -> _SourceMarker | None:
        async with self.database.session() as db:
            record = await db.get(
                AppMetadataRecord,
                _bootstrap_source_key(
                    workspace_id,
                    source_kind,
                    source_identity,
                    source_name,
                ),
            )
        if record is None:
            return None
        try:
            value = json.loads(record.value_json)
        except json.JSONDecodeError:
            return _SourceMarker(source_name, None)
        if not isinstance(value, dict):
            return _SourceMarker(source_name, None)
        name = value.get("name")
        stored_hash = value.get("bundle_hash")
        return _SourceMarker(
            name if isinstance(name, str) and name else source_name,
            stored_hash if isinstance(stored_hash, str) else None,
        )

    async def _mark_source_completed(
        self,
        workspace_id: str,
        source_kind: BootstrapSourceKind,
        source_identity: str,
        source_name: str,
        skill_name: str,
        status: str,
        bundle_hash: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        payload: dict[str, str] = {"name": skill_name, "status": status}
        if bundle_hash is not None:
            payload["bundle_hash"] = bundle_hash
        values = {
            "key": _bootstrap_source_key(
                workspace_id,
                source_kind,
                source_identity,
                source_name,
            ),
            "value_json": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "updated_at": now,
        }
        async with self.database.session() as db:
            statement = (
                postgresql_insert(AppMetadataRecord)
                if db.bind is not None and db.bind.dialect.name == "postgresql"
                else sqlite_insert(AppMetadataRecord)
            )
            # The marker records which content was reconciled, so a later run
            # with different content must be able to replace it.
            await db.execute(
                statement.values(**values).on_conflict_do_update(
                    index_elements=("key",),
                    set_={
                        "value_json": values["value_json"],
                        "updated_at": now,
                    },
                )
            )
            await db.commit()

    @staticmethod
    def _result(
        workspace_id: str,
        name: str,
        status: Literal["created", "skipped", "conflict", "failed"],
        message: str = "",
        *,
        blocking: bool = False,
    ) -> BootstrapItem:
        logger.info(
            "Skill bootstrap item completed",
            extra={
                "workspace_id": workspace_id,
                "skill_name": name,
                "status": status,
                "detail": message,
            },
        )
        return BootstrapItem(workspace_id, name, status, message, blocking)


def _origin(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _manifest_source_identity(entry: WorkspaceEntry) -> str:
    if entry.skills_source_root is not None:
        return str(entry.skills_source_root.expanduser().resolve())
    if entry.manifest is not None and entry.manifest.skills_root_env is not None:
        return f"unresolved-env:{entry.manifest.skills_root_env}"
    return str((entry.directory / ".claude" / "skills").expanduser().resolve())


def _bootstrap_source_key(
    workspace_id: str,
    source_kind: BootstrapSourceKind,
    source_identity: str,
    source_name: str,
) -> str:
    source = json.dumps(
        [source_kind, source_identity, workspace_id, source_name],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{_BOOTSTRAP_SOURCE_PREFIX}{hashlib.sha256(source).hexdigest()}"


def _import_conflict(existing: StoredSkill, incoming: SkillBundle) -> AppError:
    return AppError(
        "skill_import_conflict",
        "An active Skill uses this name with different content.",
        409,
        details={
            "skill_id": existing.id,
            "existing_hash": existing.bundle_hash,
            "incoming_hash": incoming.bundle_hash,
            "incoming_name": incoming.name,
        },
    )


def _global_name_conflict(workspace_count: int) -> AppError:
    return AppError(
        "skill_name_conflict",
        "An active personal Skill already uses this name.",
        409,
        details={"workspace_count": workspace_count},
    )


def _skill_changed() -> AppError:
    return AppError("skill_changed", "Skill was changed by another manager.", 409)
