import json
import logging
import secrets
import traceback
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, or_, select
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import AppServices, get_services
from app.db.models import (
    IdentityMappingRecord,
    SessionRecord,
    TurnEventRecord,
    TurnRecord,
    UserRecord,
    WorkspaceRecord,
)
from app.errors import AppError
from app.feedback import (
    FeedbackFilters,
    feedback_details,
    feedback_stats,
    list_feedback,
)
from app.inspector.export import Transcript, build_bundle, load_transcript
from app.inspector.export_markdown import render_markdown
from app.inspector.redaction import redact
from app.web.routes import asset_revision, templates

logger = logging.getLogger(__name__)
router = APIRouter()
Services = Annotated[AppServices, Depends(get_services)]
basic_auth = HTTPBasic(auto_error=False)
Credentials = Annotated[HTTPBasicCredentials | None, Depends(basic_auth)]


async def require_inspector_access(
    request: Request,
    credentials: Credentials,
) -> None:
    settings = request.app.state.services.settings
    expected_password = settings.session_inspector_password
    username = credentials.username if credentials is not None else ""
    password = credentials.password if credentials is not None else ""
    username_matches = secrets.compare_digest(
        username.encode("utf-8"),
        settings.session_inspector_username.encode("utf-8"),
    )
    password_matches = expected_password is not None and secrets.compare_digest(
        password.encode("utf-8"),
        expected_password.get_secret_value().encode("utf-8"),
    )
    if not (username_matches and password_matches):
        from fastapi import HTTPException

        raise HTTPException(
            status_code=401,
            detail="Session Inspector authentication required.",
            headers={"WWW-Authenticate": 'Basic realm="Session Inspector"'},
        )


InspectorAccess = Annotated[None, Depends(require_inspector_access)]


@router.get("/api/inspector/dashboardLayoutRuns")
async def inspect_layout_runs(response: Response, services: Services, _access: InspectorAccess,
                              session_id: str = Query(alias="sessionId", min_length=1, max_length=256),
                              tool_call_id: str | None = Query(default=None, alias="toolCallId", max_length=256),
                              limit: int = Query(default=10, ge=1, le=50)):
    """Locate IDs for an operator's known session, bounded to 50 summaries."""
    from app.dashboard_layout.diagnostics import store_for

    response.headers["Cache-Control"] = "no-store"
    store = store_for(services)
    items = await run_in_threadpool(store.list_runs, session_id, tool_call_id, limit) if store else []
    return {"items": items}


@router.get("/api/inspector/dashboardLayoutRuns/{run_id}")
async def inspect_layout_run(run_id: UUID, response: Response,
                             services: Services, _access: InspectorAccess):
    """Read one run through existing operator authentication, without server SSH."""
    from fastapi import HTTPException

    from app.dashboard_layout.diagnostics import store_for

    response.headers["Cache-Control"] = "no-store"
    store = store_for(services)
    record = await run_in_threadpool(store.get, str(run_id)) if store else None
    if record is None:
        raise HTTPException(404, "Layout diagnostic unavailable or expired")
    return record


@router.get("/inspector", response_class=HTMLResponse)
async def inspector_page(
    request: Request,
    _access: InspectorAccess,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="inspector.html",
        context={
            "app_name": "Session Inspector",
            "asset_revision": asset_revision(
                "inspector.css",
                "session-inspector.js",
                "session-inspector-page.js",
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/inspector/sessions")
async def list_sessions(
    response: Response,
    services: Services,
    _access: InspectorAccess,
    query: str = Query(default="", max_length=120),
    workspace_id: str = Query(default="", max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    identity_subjects = _identity_subjects()
    turn_counts = _turn_counts()
    event_counts = _event_counts()
    filters = [SessionRecord.deleted_at.is_(None)]
    normalized_query = query.strip()
    if normalized_query:
        pattern = f"%{normalized_query}%"
        filters.append(
            or_(
                SessionRecord.id.ilike(pattern),
                SessionRecord.title.ilike(pattern),
                UserRecord.display_name.ilike(pattern),
                UserRecord.external_subject.ilike(pattern),
                WorkspaceRecord.id.ilike(pattern),
                WorkspaceRecord.name.ilike(pattern),
                identity_subjects.c.subject.ilike(pattern),
            )
        )
    if workspace_id:
        filters.append(SessionRecord.workspace_id == workspace_id)

    statement = (
        _session_catalog_statement(identity_subjects, turn_counts, event_counts)
        .where(*filters)
        .order_by(SessionRecord.updated_at.desc(), SessionRecord.id)
        .offset(offset)
        .limit(limit)
    )
    count_statement = (
        select(func.count(SessionRecord.id))
        .join(UserRecord, UserRecord.id == SessionRecord.created_by)
        .join(WorkspaceRecord, WorkspaceRecord.id == SessionRecord.workspace_id)
        .outerjoin(
            identity_subjects,
            identity_subjects.c.user_id == UserRecord.id,
        )
        .where(*filters)
    )
    async with services.database.session() as db:
        rows = (await db.execute(statement)).all()
        total = int(await db.scalar(count_statement) or 0)
    return {
        "items": [_session_summary(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/inspector/workspaces")
async def list_inspector_workspaces(
    response: Response,
    services: Services,
    _access: InspectorAccess,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    statement = (
        select(
            WorkspaceRecord.id,
            WorkspaceRecord.name,
            func.count(SessionRecord.id),
        )
        .join(SessionRecord, SessionRecord.workspace_id == WorkspaceRecord.id)
        .where(SessionRecord.deleted_at.is_(None))
        .group_by(WorkspaceRecord.id, WorkspaceRecord.name)
        .order_by(WorkspaceRecord.name)
    )
    async with services.database.session() as db:
        rows = (await db.execute(statement)).all()
    return {
        "items": [
            {
                "workspace_id": row[0],
                "workspace_name": row[1],
                "session_count": int(row[2]),
            }
            for row in rows
        ]
    }


@router.get("/api/inspector/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    response: Response,
    services: Services,
    _access: InspectorAccess,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    identity_subjects = _identity_subjects()
    turn_counts = _turn_counts()
    event_counts = _event_counts()
    statement = _session_catalog_statement(
        identity_subjects,
        turn_counts,
        event_counts,
        include_snapshot=True,
    ).where(
        SessionRecord.id == session_id,
        SessionRecord.deleted_at.is_(None),
    )
    async with services.database.session() as db:
        row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise AppError("session_not_found", "Session not found.", 404)
    return {
        "session": _session_summary(row),
        "context": _session_context(row),
        "feedback": await list_feedback(services.database, session_id, None, include_turn=True),
    }


@router.get("/api/inspector/sessions/{session_id}/events")
async def get_session_events(
    session_id: str,
    response: Response,
    services: Services,
    _access: InspectorAccess,
    limit: int = Query(default=10_000, ge=1, le=10_000),
    turn_id: str = Query(default="", alias="turnId", max_length=64),
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = "no-store"
    async with services.database.session() as db:
        exists = await db.scalar(
            select(SessionRecord.id).where(
                SessionRecord.id == session_id,
                SessionRecord.deleted_at.is_(None),
            )
        )
        if exists is None:
            raise AppError("session_not_found", "Session not found.", 404)
        records = list(
            (
                await db.scalars(
                    select(TurnEventRecord)
                    .where(TurnEventRecord.session_id == session_id)
                    .where(TurnEventRecord.turn_id == turn_id if turn_id else True)
                    .order_by(
                        TurnEventRecord.created_at,
                        TurnEventRecord.sequence,
                    )
                    .limit(limit + 1)
                )
            ).all()
        )
    if len(records) > limit:
        raise AppError(
            "history_too_large",
            "This session contains too many events to display.",
            413,
        )
    return [_event_dict(record) for record in records]


@router.get("/api/inspector/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    services: Services,
    request: Request,
    credentials: Credentials,
    turn: str = Query(default="", max_length=64),
    export_format: str = Query(default="md", alias="format", pattern="^(md|json)$"),
) -> Response:
    """Merge database events with the on-disk transcript into one debug bundle.

    Deliberately left off ``InspectorAccess``: the point is that a locally
    running Codex/Claude can ``curl`` a UAT session while debugging, and the
    browser cannot hand it the Basic credentials. Anyone holding the URL can
    read the whole session, so keep this behind the same network boundary as
    the rest of the inspector.
    """
    # Legacy URL-only exports stay compatible; new user comments require Inspector access.
    if credentials is not None:
        await require_inspector_access(request, credentials)
    identity_subjects = _identity_subjects()
    statement = _session_catalog_statement(
        identity_subjects,
        _turn_counts(),
        _event_counts(),
        include_snapshot=True,
    ).where(
        SessionRecord.id == session_id,
        SessionRecord.deleted_at.is_(None),
    )
    async with services.database.session() as db:
        row = (await db.execute(statement)).one_or_none()
        if row is None:
            raise AppError("session_not_found", "Session not found.", 404)
        record = await db.get(SessionRecord, session_id)
        events = list(
            (
                await db.scalars(
                    select(TurnEventRecord)
                    .where(TurnEventRecord.session_id == session_id)
                    .order_by(TurnEventRecord.created_at, TurnEventRecord.sequence)
                )
            ).all()
        )
        turns = list(
            (
                await db.scalars(
                    select(TurnRecord)
                    .where(TurnRecord.session_id == session_id)
                    .order_by(TurnRecord.created_at)
                )
            ).all()
        )

    transcript = await _load_transcript(services, record)
    try:
        bundle = build_bundle(
            session=_session_summary(row),
            context=_session_context(row),
            events=[_event_dict(event) for event in events],
            turns=[_turn_dict(turn_record) for turn_record in turns],
            transcript=transcript,
            generated_at=datetime.now(UTC),
            turn_id=turn or None,
        )
        if credentials is not None:
            feedback = await list_feedback(services.database, session_id, None, include_turn=True)
            bundle["feedback"] = redact([
                item for item in feedback if not turn or item["turnId"] == turn
            ])
        body = (
            json.dumps(bundle, ensure_ascii=False, indent=2, default=str)
            if export_format == "json"
            else render_markdown(bundle)
        )
    except Exception as exc:
        # A 500 here is invisible: the browser throws the body away and leaves
        # a failed download, so the one artifact that could explain the crash
        # never reaches anyone. Hand back a diagnostic in the requested format
        # instead, and keep the traceback in the log for the request id.
        logger.exception(
            "inspector_export_failed session_id=%s turn=%s", session_id, turn
        )
        body = _export_failure_report(
            session_id=session_id,
            turn=turn,
            export_format=export_format,
            exc=exc,
            events=events,
            turns=turns,
            transcript=transcript,
        )
    media_type = (
        "application/json; charset=utf-8"
        if export_format == "json"
        else "text/markdown; charset=utf-8"
    )
    suffix = f"-turn-{turn[:8]}" if turn else ""
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'attachment; filename="session-{session_id[:8]}{suffix}.{export_format}"'
            ),
        },
    )


def _export_failure_report(
    *,
    session_id: str,
    turn: str,
    export_format: str,
    exc: Exception,
    events: list[TurnEventRecord],
    turns: list[TurnRecord],
    transcript: Transcript,
) -> str:
    facts = {
        "export_failed": True,
        "session_id": session_id,
        "turn_id": turn or None,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "db_events": len(events),
        "db_turns": len(turns),
        "transcript_available": transcript.available,
        "transcript_files": transcript.files,
        "transcript_entries": transcript.entry_count,
        "traceback": traceback.format_exc(),
    }
    if export_format == "json":
        return json.dumps(facts, ensure_ascii=False, indent=2, default=str)
    lines = [
        "# 导出失败",
        "",
        f"Session `{session_id}` 的导出在服务端抛了 `{type(exc).__name__}`。",
        "以下是失败时的现场，把整份文件交给排查者即可。",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 错误 | {type(exc).__name__}: {str(exc)[:300]} |",
        f"| Turn 范围 | {turn or '整个 session'} |",
        f"| 数据库事件 / Turn | {len(events)} / {len(turns)} |",
        f"| transcript 可用 | {transcript.available} |",
        f"| transcript 文件 | {', '.join(transcript.files) or '—'} |",
        f"| transcript 记录数 | {transcript.entry_count} |",
        "",
        "```",
        traceback.format_exc(),
        "```",
    ]
    return "\n".join(lines) + "\n"


async def _load_transcript(
    services: AppServices,
    record: SessionRecord | None,
) -> Transcript:
    if record is None:
        return Transcript(reason="session 记录已不可读")
    try:
        session_path = services.sessions.session_path(record)
    except AppError as error:
        return Transcript(reason=f"session 目录无效：{error}")
    return await run_in_threadpool(load_transcript, session_path)


def _session_context(row) -> dict[str, Any]:
    return redact(
        {
            "session_id": row.id,
            "workspace_id": row.workspace_id,
            "claude_session_id": row.claude_session_id,
            "status": row.status,
            "created_at": _iso_utc(row.created_at),
            "updated_at": _iso_utc(row.updated_at),
            "workspace_snapshot_hash": row.workspace_snapshot_hash,
            "workspace_snapshot": _parse_json(row.workspace_snapshot_json),
        }
    )


def _event_dict(record: TurnEventRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "session_id": record.session_id,
        "turn_id": record.turn_id,
        "sequence": record.sequence,
        "event_type": record.event_type,
        "role": record.role,
        "payload": redact(_parse_json(record.payload_json)),
        "created_at": _iso_utc(record.created_at),
    }


def _turn_dict(record: TurnRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "status": record.status,
        "input_text": record.input_text,
        "input_tokens": record.input_tokens,
        "uncached_input_tokens": record.uncached_input_tokens,
        "cache_read_input_tokens": record.cache_read_input_tokens,
        "cache_creation_input_tokens": record.cache_creation_input_tokens,
        "total_input_tokens": record.total_input_tokens,
        "output_tokens": record.output_tokens,
        "model_api_turns": record.model_api_turns,
        "frontend_tool_calls": record.frontend_tool_calls,
        "tool_search_calls": record.tool_search_calls,
        "tool_set_changes": record.tool_set_changes,
        "catalog_digest_changes": record.catalog_digest_changes,
        "started_at": _iso_utc(record.started_at),
        "completed_at": _iso_utc(record.completed_at),
    }


def _identity_subjects():
    return (
        select(
            IdentityMappingRecord.user_id.label("user_id"),
            func.min(IdentityMappingRecord.subject).label("subject"),
        )
        .where(IdentityMappingRecord.issuer == "davinci")
        .group_by(IdentityMappingRecord.user_id)
        .subquery()
    )


def _turn_counts():
    return (
        select(
            TurnRecord.session_id.label("session_id"),
            func.count(TurnRecord.id).label("turn_count"),
        )
        .group_by(TurnRecord.session_id)
        .subquery()
    )


def _event_counts():
    return (
        select(
            TurnEventRecord.session_id.label("session_id"),
            func.count(TurnEventRecord.id).label("event_count"),
        )
        .group_by(TurnEventRecord.session_id)
        .subquery()
    )


def _session_catalog_statement(
    identity_subjects,
    turn_counts,
    event_counts,
    *,
    include_snapshot: bool = False,
):
    columns = [
        SessionRecord.id,
        SessionRecord.workspace_id,
        SessionRecord.created_by,
        SessionRecord.claude_session_id,
        SessionRecord.title,
        SessionRecord.status,
        SessionRecord.last_error_code,
        SessionRecord.created_at,
        SessionRecord.updated_at,
        SessionRecord.workspace_snapshot_hash,
        UserRecord.display_name.label("user_display_name"),
        func.coalesce(
            identity_subjects.c.subject,
            UserRecord.external_subject,
        ).label("identity_subject"),
        WorkspaceRecord.name.label("workspace_name"),
        func.coalesce(turn_counts.c.turn_count, 0).label("turn_count"),
        func.coalesce(event_counts.c.event_count, 0).label("event_count"),
    ]
    if include_snapshot:
        columns.append(SessionRecord.workspace_snapshot_json)
    return (
        select(*columns)
        .join(UserRecord, UserRecord.id == SessionRecord.created_by)
        .join(WorkspaceRecord, WorkspaceRecord.id == SessionRecord.workspace_id)
        .outerjoin(
            identity_subjects,
            identity_subjects.c.user_id == UserRecord.id,
        )
        .outerjoin(turn_counts, turn_counts.c.session_id == SessionRecord.id)
        .outerjoin(event_counts, event_counts.c.session_id == SessionRecord.id)
    )


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()


def _session_summary(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "workspace_name": row.workspace_name,
        "created_by": row.created_by,
        "user_display_name": row.user_display_name,
        "identity_subject": row.identity_subject,
        "claude_session_id": row.claude_session_id,
        "title": row.title,
        "status": row.status,
        "last_error_code": row.last_error_code,
        "turn_count": int(row.turn_count),
        "event_count": int(row.event_count),
        "created_at": _iso_utc(row.created_at),
        "updated_at": _iso_utc(row.updated_at),
    }


def _parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"invalid_persisted_json": True}



def feedback_filters(
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    actor_id: str = Query(default="", alias="actorId", max_length=64),
    actor_query: str = Query(default="", alias="actorQuery", max_length=120),
    workspace_id: str = Query(default="", alias="workspaceId", max_length=64),
) -> FeedbackFilters:
    """Interpret inclusive dates in Beijing time with a fixed 90-day query bound."""
    end = date_to or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    start = date_from or end - timedelta(days=min(6, end.toordinal() - 1))
    if end == date.max or start == date.min or end < start or (end - start).days >= 90:
        raise AppError("invalid_date_range", "日期范围需顺序正确，且不超过 90 天。", 422)
    return FeedbackFilters(start, end, actor_id, actor_query.strip(), workspace_id)


@router.get("/api/inspector/feedbackStats")
async def get_feedback_stats(
    response: Response, services: Services, _access: InspectorAccess,
    filters: Annotated[FeedbackFilters, Depends(feedback_filters)],
    group_by: Literal["day", "actor", "dayActor"] = Query(default="day", alias="groupBy"),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
) -> dict:
    """Return protected current-vote counts independently of the displayed page."""
    response.headers["Cache-Control"] = "no-store"
    return await feedback_stats(services.database, filters, group_by=group_by, limit=limit, offset=offset)


@router.get("/api/inspector/feedback")
async def get_feedback_details(
    response: Response, services: Services, _access: InspectorAccess,
    filters: Annotated[FeedbackFilters, Depends(feedback_filters)],
    rating: Literal["up", "down"] | None = None,
    session_id: str = Query(default="", alias="sessionId", max_length=64),
    limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
) -> dict:
    """List protected task feedback, retaining reply links without copying conversations."""
    response.headers["Cache-Control"] = "no-store"
    return await feedback_details(services.database, filters, rating=rating, session_id=session_id,
                                  limit=limit, offset=offset)
