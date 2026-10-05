from sqlalchemy import select

from app.db.base import Database
from app.db.models import WorkspaceMemberRecord, WorkspaceRecord


class WorkspaceRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, workspace_id: str) -> WorkspaceRecord | None:
        async with self.database.session() as db:
            return await db.get(WorkspaceRecord, workspace_id)

    async def list_for_user(self, user_id: str) -> tuple[WorkspaceRecord, ...]:
        async with self.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(WorkspaceRecord)
                        .join(
                            WorkspaceMemberRecord,
                            WorkspaceMemberRecord.workspace_id == WorkspaceRecord.id,
                        )
                        .where(WorkspaceMemberRecord.user_id == user_id)
                        .order_by(WorkspaceRecord.created_at, WorkspaceRecord.id)
                    )
                ).all()
            )
        return tuple(records)

    async def get_personal_for_owner(
        self, user_id: str
    ) -> WorkspaceRecord | None:
        async with self.database.session() as db:
            return await db.scalar(
                select(WorkspaceRecord).where(
                    WorkspaceRecord.kind == "personal",
                    WorkspaceRecord.owner_user_id == user_id,
                )
            )
