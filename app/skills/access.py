from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.auth.models import IdentityContext
from app.db.base import Database
from app.db.models import PlatformRoleBindingRecord, UserRecord
from app.errors import AppError


class PlatformSkillAccessService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def can_manage_global_skills(self, identity: IdentityContext) -> bool:
        """Return whether the authenticated identity is a registered user."""
        async with self.database.session() as db:
            return await db.get(UserRecord, identity.user_id) is not None

    async def is_skill_admin(self, identity: IdentityContext) -> bool:
        """Return whether the identity retains the platform Skill admin role."""
        async with self.database.session() as db:
            return (
                await db.get(
                    PlatformRoleBindingRecord,
                    (identity.user_id, "skill_admin"),
                )
                is not None
            )

    async def require_global_contributor(self, identity: IdentityContext) -> None:
        """Reject identities that are not registered global Skill contributors."""
        if not await self.can_manage_global_skills(identity):
            raise AppError("skill_not_found", "Skill not found.", 404)

    async def require_skill_admin(self, identity: IdentityContext) -> None:
        if not await self.is_skill_admin(identity):
            raise AppError("skill_not_found", "Skill not found.", 404)

    async def bootstrap_subjects(
        self, identity: IdentityContext, subjects: tuple[str, ...]
    ) -> None:
        if identity.external_subject not in subjects:
            return
        values = {
            "user_id": identity.user_id,
            "role": "skill_admin",
            "granted_by": None,
            "created_at": datetime.now(UTC),
        }
        async with self.database.session() as db:
            statement = (
                postgresql_insert(PlatformRoleBindingRecord)
                if db.bind is not None and db.bind.dialect.name == "postgresql"
                else sqlite_insert(PlatformRoleBindingRecord)
            )
            await db.execute(
                statement.values(**values).on_conflict_do_nothing(
                    index_elements=("user_id", "role")
                )
            )
            await db.commit()
