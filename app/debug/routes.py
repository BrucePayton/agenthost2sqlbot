from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.dependencies import AppServices, get_services
from app.db.models import (
    IdentityMappingRecord,
    SessionRecord,
    UserRecord,
    WorkspaceRecord,
)
from app.errors import AppError

router = APIRouter(prefix="/api/debug")
Services = Annotated[AppServices, Depends(get_services)]


@router.get("/users")
async def list_debug_users(services: Services) -> list[dict]:
    async with services.database.session() as db:
        rows = (
            await db.execute(
                select(
                    IdentityMappingRecord.subject,
                    UserRecord.display_name,
                    func.count(func.distinct(WorkspaceRecord.id)),
                    func.count(func.distinct(SessionRecord.id)),
                )
                .join(UserRecord, UserRecord.id == IdentityMappingRecord.user_id)
                .outerjoin(
                    WorkspaceRecord,
                    WorkspaceRecord.owner_user_id == UserRecord.id,
                )
                .outerjoin(SessionRecord, SessionRecord.created_by == UserRecord.id)
                .where(
                    IdentityMappingRecord.issuer == "davinci",
                    IdentityMappingRecord.subject.like("obid:%"),
                )
                .group_by(
                    IdentityMappingRecord.subject,
                    UserRecord.display_name,
                )
                .order_by(IdentityMappingRecord.subject)
            )
        ).all()
    return [
        {
            "ob_id": subject.removeprefix("obid:"),
            "display_name": display_name,
            "workspace_count": int(workspace_count),
            "session_count": int(session_count),
        }
        for subject, display_name, workspace_count, session_count in rows
    ]


@router.get("/sessions/{session_id}")
async def locate_debug_session(session_id: str, services: Services) -> dict:
    async with services.database.session() as db:
        row = (
            await db.execute(
                select(
                    SessionRecord.id,
                    SessionRecord.workspace_id,
                    SessionRecord.title,
                    IdentityMappingRecord.subject,
                )
                .join(UserRecord, UserRecord.id == SessionRecord.created_by)
                .join(
                    IdentityMappingRecord,
                    IdentityMappingRecord.user_id == UserRecord.id,
                )
                .where(
                    SessionRecord.id == session_id,
                    IdentityMappingRecord.issuer == "davinci",
                    IdentityMappingRecord.subject.like("obid:%"),
                )
            )
        ).one_or_none()
    if row is None:
        raise AppError("session_not_found", "Session not found.", 404)
    return {
        "session_id": row.id,
        "workspace_id": row.workspace_id,
        "title": row.title,
        "ob_id": row.subject.removeprefix("obid:"),
    }
