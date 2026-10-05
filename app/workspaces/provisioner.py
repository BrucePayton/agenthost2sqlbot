import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.models import IdentityContext, WorkspaceRole
from app.db.base import Database
from app.db.models import WorkspaceMemberRecord, WorkspaceRecord


class PersonalWorkspaceProvisioner:
    def __init__(self, database: Database, template_id: str) -> None:
        self.database = database
        self.template_id = template_id

    async def ensure(self, identity: IdentityContext) -> WorkspaceRecord:
        existing = await self._get(identity.user_id)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        workspace = WorkspaceRecord(
            id=str(uuid.uuid4()),
            name=f"{identity.display_name} 的工作区",
            kind="team",
            config_json=json.dumps(
                {"template_id": self.template_id},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            owner_user_id=identity.user_id,
            template_id=self.template_id,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self.database.session() as db:
                db.add(workspace)
                await db.flush()
                db.add(
                    WorkspaceMemberRecord(
                        workspace_id=workspace.id,
                        user_id=identity.user_id,
                        role=WorkspaceRole.OWNER.value,
                        created_at=now,
                    )
                )
                await db.flush()
                workspace.kind = "personal"
                await db.commit()
                await db.refresh(workspace)
                return workspace
        except IntegrityError:
            existing = await self._get(identity.user_id)
            if existing is None:
                raise
            return existing

    async def _get(self, user_id: str) -> WorkspaceRecord | None:
        async with self.database.session() as db:
            return await db.scalar(
                select(WorkspaceRecord).where(
                    WorkspaceRecord.kind == "personal",
                    WorkspaceRecord.owner_user_id == user_id,
                )
            )
