import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.auth.models import IdentityContext, WorkspaceRole
from app.config import Settings
from app.db.base import Database
from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
from app.workspaces.models import WorkspaceEntry


class WorkspaceSyncService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.personal_workspace_id = settings.mock_personal_workspace_id
        self.workspace_roles = {
            workspace_id: WorkspaceRole(role)
            for workspace_id, role in settings.mock_workspace_roles.items()
        }

    async def sync(
        self, entries: list[WorkspaceEntry], identity: IdentityContext
    ) -> None:
        now = datetime.now(UTC)
        async with self.database.session() as db:
            user = await db.get(UserRecord, identity.user_id)
            if user is None:
                db.add(
                    UserRecord(
                        id=identity.user_id,
                        external_subject=identity.external_subject,
                        display_name=identity.display_name,
                        provider="mock",
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                user.external_subject = identity.external_subject
                user.display_name = identity.display_name
                user.provider = "mock"
                user.updated_at = now
            await db.flush()

            registry_ids = {entry.id for entry in entries}
            for entry in entries:
                config_json = _normalized_config(entry)
                workspace = await db.get(WorkspaceRecord, entry.id)
                is_personal = entry.id == self.personal_workspace_id
                if workspace is None and is_personal:
                    workspace = WorkspaceRecord(
                        id=entry.id,
                        name=entry.name,
                        kind="team",
                        config_json=config_json,
                        owner_user_id=identity.user_id,
                        template_id=entry.id,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(workspace)
                    await db.flush()
                    db.add(
                        WorkspaceMemberRecord(
                            workspace_id=entry.id,
                            user_id=identity.user_id,
                            role=WorkspaceRole.OWNER.value,
                            created_at=now,
                        )
                    )
                    await db.flush()
                    workspace.kind = "personal"
                    continue
                if workspace is None:
                    workspace = WorkspaceRecord(
                        id=entry.id,
                        name=entry.name,
                        kind="team",
                        config_json=config_json,
                        template_id=entry.id,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(workspace)
                    await db.flush()
                else:
                    workspace.name = entry.name
                    workspace.config_json = config_json
                    workspace.template_id = entry.id
                    workspace.updated_at = now

                if is_personal:
                    workspace.owner_user_id = identity.user_id
                    workspace.template_id = entry.id
                    if workspace.kind == "team":
                        members = list(
                            (
                                await db.scalars(
                                    select(WorkspaceMemberRecord).where(
                                        WorkspaceMemberRecord.workspace_id == entry.id
                                    )
                                )
                            ).all()
                        )
                        if not members:
                            db.add(
                                WorkspaceMemberRecord(
                                    workspace_id=entry.id,
                                    user_id=identity.user_id,
                                    role=WorkspaceRole.OWNER.value,
                                    created_at=now,
                                )
                            )
                            await db.flush()
                        workspace.kind = "personal"
                    continue

                await self._upsert_membership(
                    db, entry.id, identity.user_id, self._role_for(entry.id), now
                )

            await db.flush()
            placeholders = list((await db.scalars(select(WorkspaceRecord))).all())
            for workspace in placeholders:
                if workspace.id not in registry_ids and workspace.kind != "personal":
                    await self._upsert_membership(
                        db,
                        workspace.id,
                        identity.user_id,
                        self._role_for(workspace.id),
                        now,
                    )
            await db.commit()

    def _role_for(self, workspace_id: str) -> WorkspaceRole:
        return self.workspace_roles.get(workspace_id, WorkspaceRole.OWNER)

    @staticmethod
    async def _upsert_membership(
        db,
        workspace_id: str,
        user_id: str,
        role: WorkspaceRole,
        now: datetime,
    ) -> None:
        membership = await db.get(WorkspaceMemberRecord, (workspace_id, user_id))
        if membership is None:
            db.add(
                WorkspaceMemberRecord(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    role=role.value,
                    created_at=now,
                )
            )
        else:
            membership.role = role.value


def _normalized_config(entry: WorkspaceEntry) -> str:
    try:
        config = json.loads(entry.snapshot_json or "{}")
    except json.JSONDecodeError:
        config = {}
    config.pop("skills", None)
    config.pop("skills_root_env", None)
    return json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
