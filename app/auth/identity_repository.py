import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.base import Database
from app.db.models import IdentityMappingRecord, UserRecord


@dataclass(frozen=True)
class ResolvedIdentity:
    user_id: str
    display_name: str


class IdentityRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def resolve_or_create(
        self,
        issuer: str,
        subject: str,
        profile: Mapping[str, str | None],
        *,
        provider: str,
    ) -> ResolvedIdentity:
        existing = await self._find(issuer, subject)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        user_id = str(uuid.uuid4())
        display_name = profile.get("name") or profile.get("email") or subject
        opaque_subject = provider + ":" + hashlib.sha256(
            f"{issuer}\0{subject}".encode()
        ).hexdigest()
        try:
            async with self.database.session() as session:
                session.add(
                    UserRecord(
                        id=user_id,
                        external_subject=opaque_subject,
                        display_name=display_name,
                        provider=provider,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.flush()
                session.add(
                    IdentityMappingRecord(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        issuer=issuer,
                        subject=subject,
                        profile_json=json.dumps(dict(profile), ensure_ascii=False),
                        created_at=now,
                        updated_at=now,
                    )
                )
                await session.commit()
        except IntegrityError:
            winner = await self._find(issuer, subject)
            if winner is None:
                raise
            return winner
        return ResolvedIdentity(user_id=user_id, display_name=display_name)

    async def _find(self, issuer: str, subject: str) -> ResolvedIdentity | None:
        async with self.database.session() as session:
            row = (
                await session.execute(
                    select(IdentityMappingRecord.user_id, UserRecord.display_name)
                    .join(UserRecord, UserRecord.id == IdentityMappingRecord.user_id)
                    .where(
                        IdentityMappingRecord.issuer == issuer,
                        IdentityMappingRecord.subject == subject,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return ResolvedIdentity(user_id=row.user_id, display_name=row.display_name)
