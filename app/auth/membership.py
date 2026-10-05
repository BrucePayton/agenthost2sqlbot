import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import delete, select

from app.auth.models import IdentityContext, WorkspaceMembership, WorkspaceRole
from app.db.base import Database
from app.db.models import WorkspaceMembershipProjectionRecord
from app.errors import AppError

MembershipSensitivity = Literal["read", "sensitive", "write"]


@dataclass(frozen=True)
class AuthorityMembership:
    role: str
    source_version: str


class MembershipAuthorityUnavailable(RuntimeError):
    pass


class SpaceMembershipAuthority(Protocol):
    async def fetch_membership(
        self, subject: str, workspace_external_id: str
    ) -> AuthorityMembership | None:
        raise NotImplementedError


class MembershipProjectionService:
    def __init__(
        self,
        database: Database,
        authority: SpaceMembershipAuthority,
        *,
        ttl_seconds: int = 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.authority = authority
        self.ttl_seconds = ttl_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def require_current_membership(
        self,
        identity: IdentityContext,
        workspace_id: str,
        sensitivity: MembershipSensitivity,
    ) -> WorkspaceMembership:
        del sensitivity  # All expired projections fail closed in Phase 1.
        projection = await self._get(workspace_id, identity.user_id)
        if self._is_fresh(projection):
            return self._to_membership(projection)

        lock = await self._lock_for(workspace_id, identity.user_id)
        async with lock:
            projection = await self._get(workspace_id, identity.user_id)
            if self._is_fresh(projection):
                return self._to_membership(projection)
            try:
                current = await self.authority.fetch_membership(
                    identity.external_subject, workspace_id
                )
            except MembershipAuthorityUnavailable as exc:
                raise AppError(
                    "membership_unavailable",
                    "Workspace membership could not be verified.",
                    503,
                ) from exc
            if current is None:
                async with self.database.session() as session:
                    await session.execute(
                        delete(WorkspaceMembershipProjectionRecord).where(
                            WorkspaceMembershipProjectionRecord.workspace_id
                            == workspace_id,
                            WorkspaceMembershipProjectionRecord.user_id
                            == identity.user_id,
                        )
                    )
                    await session.commit()
                raise AppError("workspace_not_found", "Workspace not found.", 404)
            try:
                role = WorkspaceRole(current.role)
            except ValueError as exc:
                raise MembershipAuthorityUnavailable(
                    "Space authority returned an invalid role"
                ) from exc
            now = self._utc(self.now())
            expires_at = now + timedelta(seconds=self.ttl_seconds)
            async with self.database.session() as session:
                stored = await session.get(
                    WorkspaceMembershipProjectionRecord,
                    (workspace_id, identity.user_id),
                )
                if stored is None:
                    stored = WorkspaceMembershipProjectionRecord(
                        workspace_id=workspace_id,
                        user_id=identity.user_id,
                        role=role.value,
                        source_version=current.source_version,
                        expires_at=expires_at,
                        refreshed_at=now,
                    )
                    session.add(stored)
                else:
                    stored.role = role.value
                    stored.source_version = current.source_version
                    stored.expires_at = expires_at
                    stored.refreshed_at = now
                await session.commit()
            return WorkspaceMembership(workspace_id, identity.user_id, role)

    async def list_current_memberships(
        self, identity: IdentityContext
    ) -> list[WorkspaceMembership]:
        now = self._utc(self.now())
        async with self.database.session() as session:
            records = list(
                (
                    await session.scalars(
                        select(WorkspaceMembershipProjectionRecord).where(
                            WorkspaceMembershipProjectionRecord.user_id
                            == identity.user_id,
                            WorkspaceMembershipProjectionRecord.expires_at > now,
                        )
                    )
                ).all()
            )
        return [self._to_membership(record) for record in records]

    async def _get(
        self, workspace_id: str, user_id: str
    ) -> WorkspaceMembershipProjectionRecord | None:
        async with self.database.session() as session:
            return await session.get(
                WorkspaceMembershipProjectionRecord, (workspace_id, user_id)
            )

    def _is_fresh(
        self, projection: WorkspaceMembershipProjectionRecord | None
    ) -> bool:
        return projection is not None and self._utc(projection.expires_at) > self._utc(
            self.now()
        )

    @staticmethod
    def _to_membership(
        projection: WorkspaceMembershipProjectionRecord,
    ) -> WorkspaceMembership:
        return WorkspaceMembership(
            projection.workspace_id,
            projection.user_id,
            WorkspaceRole(projection.role),
        )

    async def _lock_for(self, workspace_id: str, user_id: str) -> asyncio.Lock:
        key = (workspace_id, user_id)
        async with self._locks_guard:
            return self._locks.setdefault(key, asyncio.Lock())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
