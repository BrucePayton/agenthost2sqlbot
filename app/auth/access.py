from sqlalchemy import select

from app.auth.membership import MembershipProjectionService, MembershipSensitivity
from app.auth.models import IdentityContext, WorkspaceMembership, WorkspaceRole
from app.db.base import Database
from app.db.models import (
    AttachmentRecord,
    SessionRecord,
    TurnRecord,
    WorkspaceMemberRecord,
    WorkspaceRecord,
)
from app.errors import AppError


class WorkspaceAccessService:
    def __init__(
        self,
        database: Database,
        membership_projections: MembershipProjectionService | None = None,
    ) -> None:
        self.database = database
        self.membership_projections = membership_projections

    async def require_member(
        self, identity: IdentityContext, workspace_id: str
    ) -> WorkspaceMembership:
        if self.membership_projections is not None:
            return await self.membership_projections.require_current_membership(
                identity, workspace_id, "read"
            )
        membership = await self._membership(identity.user_id, workspace_id)
        if membership is None:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        return membership

    async def require_manager(
        self, identity: IdentityContext, workspace_id: str
    ) -> WorkspaceMembership:
        membership = await self._require_membership(identity, workspace_id, "write")
        if membership.role not in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        return membership

    async def require_owner(
        self, identity: IdentityContext, workspace_id: str
    ) -> WorkspaceMembership:
        membership = await self._require_membership(identity, workspace_id, "write")
        if membership.role is not WorkspaceRole.OWNER:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        return membership

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

    async def require_session_owner(
        self, identity: IdentityContext, session_id: str
    ) -> SessionRecord:
        record = await self._session_for_owner(identity.user_id, session_id)
        if record is None:
            raise AppError("session_not_found", "Session not found.", 404)
        await self._require_membership(identity, record.workspace_id, "sensitive")
        return record

    async def require_turn_owner(
        self, identity: IdentityContext, turn_id: str
    ) -> TurnRecord:
        record = await self._turn_for_owner(identity.user_id, turn_id)
        if record is None:
            raise AppError("turn_not_found", "Turn not found.", 404)
        session = await self._session_for_owner(identity.user_id, record.session_id)
        if session is None:
            raise AppError("turn_not_found", "Turn not found.", 404)
        await self._require_membership(identity, session.workspace_id, "sensitive")
        return record

    async def require_attachment_owner(
        self, identity: IdentityContext, attachment_id: str
    ) -> AttachmentRecord:
        record = await self._attachment_for_owner(identity.user_id, attachment_id)
        if record is None:
            raise AppError("attachment_not_found", "Attachment not found.", 404)
        session = await self._session_for_owner(identity.user_id, record.session_id)
        if session is None:
            raise AppError("attachment_not_found", "Attachment not found.", 404)
        await self._require_membership(identity, session.workspace_id, "sensitive")
        return record

    async def list_memberships(
        self, identity: IdentityContext
    ) -> list[WorkspaceMembership]:
        if self.membership_projections is not None:
            return await self.membership_projections.list_current_memberships(identity)
        async with self.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(WorkspaceMemberRecord).where(
                            WorkspaceMemberRecord.user_id == identity.user_id
                        )
                    )
                ).all()
            )
        return [
            WorkspaceMembership(
                workspace_id=record.workspace_id,
                user_id=record.user_id,
                role=WorkspaceRole(record.role),
            )
            for record in records
        ]

    async def _require_membership(
        self,
        identity: IdentityContext,
        workspace_id: str,
        sensitivity: MembershipSensitivity,
    ) -> WorkspaceMembership:
        if self.membership_projections is not None:
            return await self.membership_projections.require_current_membership(
                identity, workspace_id, sensitivity
            )
        return await self.require_member(identity, workspace_id)

    async def _membership(
        self, user_id: str, workspace_id: str
    ) -> WorkspaceMembership | None:
        async with self.database.session() as db:
            record = await db.get(WorkspaceMemberRecord, (workspace_id, user_id))
        if record is None:
            return None
        return WorkspaceMembership(
            workspace_id=record.workspace_id,
            user_id=record.user_id,
            role=WorkspaceRole(record.role),
        )

    async def _session_for_owner(
        self, user_id: str, session_id: str
    ) -> SessionRecord | None:
        async with self.database.session() as db:
            if self.membership_projections is not None:
                return await db.scalar(
                    select(SessionRecord).where(
                        SessionRecord.id == session_id,
                        SessionRecord.created_by == user_id,
                    )
                )
            return await db.scalar(
                select(SessionRecord)
                .join(
                    WorkspaceMemberRecord,
                    WorkspaceMemberRecord.workspace_id == SessionRecord.workspace_id,
                )
                .where(
                    SessionRecord.id == session_id,
                    SessionRecord.created_by == user_id,
                    WorkspaceMemberRecord.user_id == user_id,
                )
            )

    async def _turn_for_owner(
        self, user_id: str, turn_id: str
    ) -> TurnRecord | None:
        async with self.database.session() as db:
            if self.membership_projections is not None:
                return await db.scalar(
                    select(TurnRecord)
                    .join(SessionRecord, SessionRecord.id == TurnRecord.session_id)
                    .where(
                        TurnRecord.id == turn_id,
                        SessionRecord.created_by == user_id,
                    )
                )
            return await db.scalar(
                select(TurnRecord)
                .join(SessionRecord, SessionRecord.id == TurnRecord.session_id)
                .join(
                    WorkspaceMemberRecord,
                    WorkspaceMemberRecord.workspace_id == SessionRecord.workspace_id,
                )
                .where(
                    TurnRecord.id == turn_id,
                    SessionRecord.created_by == user_id,
                    WorkspaceMemberRecord.user_id == user_id,
                )
            )

    async def _attachment_for_owner(
        self, user_id: str, attachment_id: str
    ) -> AttachmentRecord | None:
        async with self.database.session() as db:
            if self.membership_projections is not None:
                return await db.scalar(
                    select(AttachmentRecord)
                    .join(SessionRecord, SessionRecord.id == AttachmentRecord.session_id)
                    .where(
                        AttachmentRecord.id == attachment_id,
                        SessionRecord.created_by == user_id,
                    )
                )
            return await db.scalar(
                select(AttachmentRecord)
                .join(SessionRecord, SessionRecord.id == AttachmentRecord.session_id)
                .join(
                    WorkspaceMemberRecord,
                    WorkspaceMemberRecord.workspace_id == SessionRecord.workspace_id,
                )
                .where(
                    AttachmentRecord.id == attachment_id,
                    SessionRecord.created_by == user_id,
                    WorkspaceMemberRecord.user_id == user_id,
                )
            )
