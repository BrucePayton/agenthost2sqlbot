"""Persist one evaluation per actor and question, with its complete reply for inspection."""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, case, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.api.schemas import MessageFeedbackIn
from app.db import Database
from app.db.models import (
    MessageFeedbackRecord,
    MessageRecord,
    SessionRecord,
    TurnRecord,
    UserRecord,
)
from app.errors import AppError
from app.feedback_scope import question_roots


def feedback_out(record: MessageFeedbackRecord) -> dict:
    """Serialize the saved vote without copying session execution snapshots."""
    return {
        "taskId": record.question_id,
        "sessionId": record.session_id,
        "messageId": record.message_id,
        "createdAt": record.created_at,
        "rating": record.rating,
        "reasons": json.loads(record.reasons_json),
        "comment": record.comment,
        "updatedAt": record.updated_at,
    }


async def save_feedback(
    database: Database,
    session_id: str,
    message_id: str,
    actor_id: str,
    payload: MessageFeedbackIn,
) -> dict:
    """Atomically replace or delete one question vote, including requests from older clients."""
    now = datetime.now(UTC)
    async with database.session() as db:
        message = await db.get(MessageRecord, message_id)
        if message is None or message.session_id != session_id:
            raise AppError("message_not_found", "Message not found.", 404)
        if (
            message.event_type != "message.assistant.completed"
            or message.role != "assistant"
        ):
            raise AppError(
                "invalid_feedback_target",
                "Only completed assistant replies can be rated.",
                422,
            )
        messages = list((await db.scalars(
            select(MessageRecord).join(TurnRecord, TurnRecord.id == MessageRecord.turn_id)
            .where(MessageRecord.session_id == session_id)
            .order_by(TurnRecord.created_at, TurnRecord.id, MessageRecord.sequence)
        )).all())
        roots = question_roots(messages)
        question_id = roots.get(message.turn_id)
        question_messages = [item for item in messages if roots.get(item.turn_id) == question_id]
        replies = [item for item in question_messages
                   if item.event_type == "message.assistant.completed"
                   and (json.loads(item.payload_json).get("text") or "").strip()]
        last_turn = await db.get(TurnRecord, question_messages[-1].turn_id) if question_messages else None
        pending = set()
        for item in question_messages:
            data = json.loads(item.payload_json)
            if item.event_type == "frontend_tool.deferred":
                pending.add(data.get("tool_use_id"))
            elif item.event_type == "tool.completed":
                pending.discard(data.get("tool_use_id"))
            elif item.event_type == "message.user":
                # Accepted receipts are durable even if a runtime emits no tool.completed trace.
                for result in data.get("tool_results", []):
                    pending.discard(result.get("tool_call_id"))
        if (not question_id or not replies or not last_turn
                or last_turn.status not in {"completed", "failed", "failed_before_execution", "cancelled", "cancelled_before_execution", "interrupted"}
                or (last_turn.status == "completed" and pending)):
            raise AppError("invalid_feedback_target", "Only a finished question can be rated.", 422)
        # Older clients may send an intermediate reply; keep one final anchor for the question.
        message_id = replies[-1].id
        if payload.rating is None:
            await db.execute(
                delete(MessageFeedbackRecord).where(
                    MessageFeedbackRecord.actor_id == actor_id,
                    MessageFeedbackRecord.session_id == session_id,
                    MessageFeedbackRecord.question_id == question_id,
                )
            )
        else:
            values = {
                "message_id": message_id,
                "rating": payload.rating,
                "reasons_json": json.dumps(payload.reasons),
                "comment": payload.comment,
                "updated_at": now,
            }
            insert = (
                sqlite_insert
                if database.engine.dialect.name == "sqlite"
                else postgres_insert
            )
            statement = insert(MessageFeedbackRecord).values(
                actor_id=actor_id,
                session_id=session_id,
                question_id=question_id,
                created_at=now,
                **values,
            )
            record = (
                await db.execute(
                    statement.on_conflict_do_update(
                        index_elements=["actor_id", "question_id"],
                        set_=values,
                    ).returning(MessageFeedbackRecord)
                )
            ).scalar_one()
            result = feedback_out(record)
        await db.commit()
    if payload.rating is not None:
        return result
    return {
        "taskId": question_id,
        "sessionId": session_id,
        "messageId": message_id,
        "createdAt": None,
        "rating": payload.rating,
        "reasons": payload.reasons,
        "comment": payload.comment,
        "updatedAt": now,
    }


async def list_feedback(
    database: Database,
    session_id: str,
    actor_id: str | None,
    *,
    include_turn: bool = False,
) -> list[dict]:
    """Read votes in one authorized session, with message-to-turn links for inspection."""
    query = (
        select(
            MessageFeedbackRecord,
            MessageRecord.turn_id,
            UserRecord.display_name,
            UserRecord.external_subject,
        )
        .join(
            MessageRecord,
            MessageRecord.id == MessageFeedbackRecord.message_id,
        )
        .join(UserRecord, UserRecord.id == MessageFeedbackRecord.actor_id)
        .where(MessageRecord.session_id == session_id)
        .order_by(MessageFeedbackRecord.updated_at)
    )
    if actor_id is not None:
        query = query.where(MessageFeedbackRecord.actor_id == actor_id)
    async with database.session() as db:
        rows = (await db.execute(query)).all()
    return [
        dict(
            feedback_out(record),
            **(
                {
                    "turnId": turn_id,
                    "actorId": record.actor_id,
                    "displayName": display_name,
                    "account": account,
                }
                if include_turn
                else {}
            ),
        )
        for record, turn_id, display_name, account in rows
    ]


@dataclass(frozen=True)
class FeedbackFilters:
    """A bounded date window and exact actor/workspace filters shared by both views."""

    date_from: date
    date_to: date
    actor_id: str = ""
    actor_query: str = ""
    workspace_id: str = ""


def _feedback_base(filters: FeedbackFilters):
    """Join only unique user/session keys so identity aliases cannot multiply votes."""
    start = datetime.combine(
        filters.date_from, time.min, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    end = datetime.combine(
        filters.date_to + timedelta(days=1), time.min, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    vote = MessageFeedbackRecord
    statement = (
        select(
            vote.actor_id,
            vote.session_id,
            vote.question_id,
            vote.message_id,
            vote.rating,
            vote.reasons_json,
            vote.comment,
            vote.created_at,
            vote.updated_at,
            UserRecord.display_name,
            UserRecord.external_subject.label("account"),
            SessionRecord.workspace_id,
            MessageRecord.turn_id,
        )
        .join(UserRecord, UserRecord.id == vote.actor_id)
        .join(SessionRecord, SessionRecord.id == vote.session_id)
        .join(MessageRecord, MessageRecord.id == vote.message_id)
        .where(
            SessionRecord.deleted_at.is_(None),
            vote.created_at >= start.astimezone(UTC),
            vote.created_at < end.astimezone(UTC),
        )
    )
    if filters.actor_id:
        statement = statement.where(vote.actor_id == filters.actor_id)
    if filters.actor_query:
        pattern = (
            "%"
            + filters.actor_query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
            + "%"
        )
        statement = statement.where(
            or_(
                UserRecord.display_name.ilike(pattern, escape="\\"),
                UserRecord.external_subject.ilike(pattern, escape="\\"),
            )
        )
    if filters.workspace_id:
        statement = statement.where(SessionRecord.workspace_id == filters.workspace_id)
    return statement.subquery()


def _vote_counts(base):
    """Aggregate current question votes, not clicks or the legacy message-feedback table."""
    return [
        func.count().label("total"),
        func.coalesce(func.sum(case((base.c.rating == "up", 1), else_=0)), 0).label(
            "up"
        ),
        func.coalesce(func.sum(case((base.c.rating == "down", 1), else_=0)), 0).label(
            "down"
        ),
        func.count(func.distinct(base.c.actor_id)).label("actorCount"),
    ]


def _with_rate(row) -> dict:
    """Represent empty denominators explicitly instead of claiming a zero score."""
    result = dict(row)
    result["positiveRate"] = result["up"] / result["total"] if result["total"] else None
    if "date" in result:
        result["date"] = str(result["date"])
    return result


async def feedback_stats(
    database: Database,
    filters: FeedbackFilters,
    *,
    group_by: str,
    limit: int,
    offset: int,
) -> dict:
    """Compute overview over all matching rows before applying group pagination."""
    base = _feedback_base(filters)
    day = (
        cast(func.timezone("Asia/Shanghai", base.c.created_at), Date)
        if database.engine.dialect.name == "postgresql"
        else func.date(base.c.created_at, "+8 hours")
    )
    columns = []
    if group_by in {"day", "dayActor"}:
        columns.append(day.label("date"))
    if group_by in {"actor", "dayActor"}:
        columns += [
            base.c.actor_id.label("actorId"),
            base.c.display_name.label("displayName"),
            base.c.account,
        ]
    groups = select(*columns, *_vote_counts(base)).select_from(base).group_by(*columns)
    ordering = [day.desc()] if group_by != "actor" else []
    if group_by != "day":
        ordering.append(base.c.actor_id)
    async with database.session() as db:
        summary = (
            (await db.execute(select(*_vote_counts(base)).select_from(base)))
            .mappings()
            .one()
        )
        total_groups = await db.scalar(
            select(func.count()).select_from(groups.subquery())
        )
        rows = (
            (await db.execute(groups.order_by(*ordering).limit(limit).offset(offset)))
            .mappings()
            .all()
        )
    return {
        "timezone": "Asia/Shanghai",
        "dateBasis": "createdAt",
        "groupBy": group_by,
        "dateFrom": filters.date_from,
        "dateTo": filters.date_to,
        "summary": _with_rate(summary),
        "rows": [_with_rate(row) for row in rows],
        "totalGroups": total_groups,
        "limit": limit,
        "offset": offset,
    }


async def feedback_details(
    database: Database,
    filters: FeedbackFilters,
    *,
    rating: str | None,
    session_id: str,
    limit: int,
    offset: int,
) -> dict:
    """Read a page of question evaluations with links back to the original persisted reply."""
    base = _feedback_base(filters)
    statement = select(base)
    if rating:
        statement = statement.where(base.c.rating == rating)
    if session_id:
        statement = statement.where(base.c.session_id == session_id)
    async with database.session() as db:
        total = await db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = (
            (
                await db.execute(
                    statement.order_by(
                        base.c.created_at.desc(), base.c.actor_id, base.c.session_id, base.c.question_id
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .mappings()
            .all()
        )
    items = [
        {
            "actorId": row["actor_id"],
            "displayName": row["display_name"],
            "account": row["account"],
            "taskId": row["question_id"],
            "sessionId": row["session_id"],
            "messageId": row["message_id"],
            "turnId": row["turn_id"],
            "workspaceId": row["workspace_id"],
            "rating": row["rating"],
            "reasons": json.loads(row["reasons_json"]),
            "comment": row["comment"],
            "createdAt": row["created_at"].replace(tzinfo=UTC)
            if row["created_at"].tzinfo is None
            else row["created_at"],
            "updatedAt": row["updated_at"].replace(tzinfo=UTC)
            if row["updated_at"].tzinfo is None
            else row["updated_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
