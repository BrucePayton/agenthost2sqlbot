import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.agui.deferred_tools import DeferredFrontendToolCall
from app.db.base import Database
from app.db.models import (
    MessageRecord,
    SessionRecord,
    TurnAttemptRecord,
    TurnEventRecord,
    TurnRecord,
)
from app.errors import AppError
from app.sessions.locks import SessionLockRegistry
from app.turns.state_machine import (
    ACTIVE_TURN_STATES,
    TERMINAL_TURN_STATES,
    TurnStateMachine,
)


class TurnRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        # SQLite ignores SELECT FOR UPDATE. Keep local-mode event sequence
        # allocation serialized; PostgreSQL uses the locked Turn row below.
        self._event_locks = SessionLockRegistry()

    async def create_queued(
        self,
        session_id: str,
        client_request_id: str,
        request_payload: dict[str, Any],
        *,
        input_text: str,
    ) -> TurnRecord:
        async with self.database.session() as session:
            existing = await session.scalar(
                select(TurnRecord).where(
                    TurnRecord.session_id == session_id,
                    TurnRecord.client_request_id == client_request_id,
                )
            )
            if existing is not None:
                return existing
            owner_session = await session.scalar(
                select(SessionRecord).where(SessionRecord.id == session_id).with_for_update()
            )
            if owner_session is None:
                raise AppError("session_not_found", "Session not found.", 404)
            if self.database.engine.dialect.name == "sqlite":
                active = await session.scalar(
                    select(TurnRecord.id).where(
                        TurnRecord.session_id == session_id,
                        TurnRecord.status.in_(ACTIVE_TURN_STATES),
                    )
                )
                if active is not None:
                    raise AppError(
                        "session_busy",
                        "This session already has an active turn.",
                        409,
                    )
            now = datetime.now(UTC)
            turn = TurnRecord(
                id=str(uuid.uuid4()),
                session_id=session_id,
                client_request_id=client_request_id,
                status="queued",
                input_text=input_text,
                created_at=now,
            )
            session.add(turn)
            try:
                await session.flush()
                self.add_event_records(
                    session,
                    turn,
                    sequence=1,
                    event_type="message.user",
                    role="user",
                    payload=request_payload,
                    created_at=now,
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                duplicate = await session.scalar(
                    select(TurnRecord).where(
                        TurnRecord.session_id == session_id,
                        TurnRecord.client_request_id == client_request_id,
                    )
                )
                if duplicate is not None:
                    return duplicate
                raise AppError(
                    "session_busy", "This session already has an active turn.", 409
                )
            await session.refresh(turn)
            return turn

    async def transition(
        self,
        turn_id: str,
        expected_statuses: tuple[str, ...] | list[str],
        next_status: str,
        metadata: dict[str, Any],
    ) -> TurnRecord:
        execution_nonce = metadata.get("execution_nonce")
        for current in expected_statuses:
            TurnStateMachine.require_transition(
                current, next_status, execution_nonce=execution_nonce
            )
        now = datetime.now(UTC)
        values: dict[str, Any] = {"status": next_status}
        if next_status == "running":
            values.update(started_at=now, execution_barrier_at=now)
        if next_status in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "failed_before_execution",
            "cancelled_before_execution",
            "outcome_unknown",
            "recovery_required",
        }:
            values["completed_at"] = now
        values.update(
            {
                key: value
                for key, value in metadata.items()
                if key
                in {
                    "effect_state",
                    "finalization_status",
                    "warning_code",
                    "error_code",
                    "error_message",
                    "cancel_requested_at",
                }
            }
        )
        async with self.database.session() as session:
            result = await session.execute(
                update(TurnRecord)
                .where(
                    TurnRecord.id == turn_id,
                    TurnRecord.status.in_(tuple(expected_statuses)),
                )
                .values(**values)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise AppError(
                    "turn_state_conflict",
                    "Turn state changed before the operation completed.",
                    409,
                )
            if next_status == "running":
                attempt_number = (
                    int(
                        (
                            await session.scalar(
                                select(
                                    func.max(TurnAttemptRecord.attempt_number)
                                ).where(TurnAttemptRecord.turn_id == turn_id)
                            )
                        )
                        or 0
                    )
                    + 1
                )
                session.add(
                    TurnAttemptRecord(
                        id=str(metadata.get("attempt_id") or uuid.uuid4()),
                        turn_id=turn_id,
                        attempt_number=attempt_number,
                        execution_nonce=str(execution_nonce),
                        runtime_cohort=str(metadata.get("runtime_cohort") or "local"),
                        status="running",
                        sandbox_generation=metadata.get("sandbox_generation"),
                        sandbox_id=metadata.get("sandbox_id"),
                        assigned_at=now,
                        started_at=now,
                    )
                )
            await session.commit()
            turn = await session.get(TurnRecord, turn_id)
            if turn is None:
                raise AppError("turn_not_found", "Turn not found.", 404)
            return turn

    async def bind_attempt_command(
        self,
        *,
        turn_id: str,
        execution_nonce: str,
        sandbox_generation: int,
        sandbox_id: str,
        command_session_id: str,
        command_execution_id: str,
    ) -> TurnAttemptRecord:
        async with self.database.session() as session:
            attempt = await session.scalar(
                select(TurnAttemptRecord)
                .where(
                    TurnAttemptRecord.turn_id == turn_id,
                    TurnAttemptRecord.execution_nonce == execution_nonce,
                )
                .with_for_update()
            )
            if attempt is None:
                raise AppError("turn_attempt_not_found", "Turn attempt not found.", 404)
            expected_identity = (sandbox_generation, sandbox_id)
            if (attempt.sandbox_generation, attempt.sandbox_id) != expected_identity:
                raise AppError(
                    "turn_attempt_sandbox_conflict",
                    "Turn attempt sandbox changed before command binding.",
                    409,
                )
            command_identity = (command_session_id, command_execution_id)
            current_identity = (
                attempt.command_session_id,
                attempt.command_execution_id,
            )
            if current_identity == command_identity:
                return attempt
            if current_identity != (None, None):
                raise AppError(
                    "turn_attempt_command_conflict",
                    "Turn attempt command changed before binding.",
                    409,
                )
            attempt.command_session_id = command_session_id
            attempt.command_execution_id = command_execution_id
            await session.commit()
            await session.refresh(attempt)
            return attempt

    async def append_event(
        self,
        turn_id: str,
        event_type: str,
        role: str | None,
        payload: dict[str, Any],
    ) -> TurnEventRecord:
        if self.database.engine.dialect.name == "sqlite":
            async with self._event_locks.acquire(turn_id):
                return await self._append_event_record(
                    turn_id, event_type, role, payload
                )
        return await self._append_event_record(turn_id, event_type, role, payload)

    async def _append_event_record(
        self,
        turn_id: str,
        event_type: str,
        role: str | None,
        payload: dict[str, Any],
    ) -> TurnEventRecord:
        async with self.database.session() as session:
            turn = await session.scalar(
                select(TurnRecord).where(TurnRecord.id == turn_id).with_for_update()
            )
            if turn is None:
                raise AppError("turn_not_found", "Turn not found.", 404)
            sequence = (
                int(
                    (
                        await session.scalar(
                            select(func.max(TurnEventRecord.sequence)).where(
                                TurnEventRecord.turn_id == turn_id
                            )
                        )
                    )
                    or 0
                )
                + 1
            )
            event = self.add_event_records(
                session,
                turn,
                sequence=sequence,
                event_type=event_type,
                role=role,
                payload=payload,
                created_at=datetime.now(UTC),
            )
            await session.commit()
            await session.refresh(event)
            return event

    async def request_cancel(self, turn_id: str) -> TurnRecord:
        now = datetime.now(UTC)
        async with self.database.session() as session:
            turn = await session.get(TurnRecord, turn_id)
            if turn is None:
                raise AppError("turn_not_found", "Turn not found.", 404)
            if (
                turn.status not in TERMINAL_TURN_STATES
                and turn.cancel_requested_at is None
            ):
                turn.cancel_requested_at = now
                await session.commit()
                await session.refresh(turn)
            return turn

    async def get_status(self, turn_id: str) -> str:
        async with self.database.session() as session:
            status = await session.scalar(
                select(TurnRecord.status).where(TurnRecord.id == turn_id)
            )
            if status is None:
                raise AppError("turn_not_found", "Turn not found.", 404)
            return str(status)

    async def frontend_tool_recovery(self, session_id: str) -> list[dict[str, Any]]:
        """Project original calls and durable continuation bindings from existing events."""
        async with self.database.session() as db:
            rows = (await db.execute(
                select(TurnEventRecord, TurnRecord.status)
                .join(TurnRecord, TurnRecord.id == TurnEventRecord.turn_id)
                .where(TurnEventRecord.session_id == session_id,
                       TurnEventRecord.event_type.in_(("message.user", "frontend_tool.deferred")))
                .order_by(TurnRecord.created_at, TurnEventRecord.sequence)
            )).all()
        calls: dict[str, dict[str, Any]] = {}
        requests = []
        pages = {}
        for event, status in rows:
            payload = json.loads(event.payload_json)
            if event.event_type == "message.user":
                pages[event.turn_id] = payload.get("page_state", {})
                requests.append((event.turn_id, status, payload.get("tool_results", [])))
                continue
            call = DeferredFrontendToolCall.create(
                thread_id=session_id, origin_run_id=payload.get("origin_run_id") or event.turn_id,
                tool_call_id=payload["tool_use_id"], public_name=payload["name"],
                arguments=payload.get("arguments", {}),
                origin="program" if payload.get("origin") == "program" else "model")
            existing = calls.get(call.tool_call_id)
            if existing and existing["call"] != call:
                raise AppError("TOOL_RESULT_CONFLICT", "历史工具调用身份冲突，需要核对后继续。", 409)
            calls[call.tool_call_id] = {"call": replace(call, recorded_at_monotonic=None),
                "continuation_run_id": None, "continuation_status": None, "tool_result": None,
                "page_state": pages.get(call.origin_run_id, {}),
                "requires_restatement": payload.get("requires_restatement", True)}
        for run_id, status, results in requests:
            for result in results:
                state = calls.get(result["tool_call_id"])
                if state is None:
                    continue
                if state["continuation_run_id"] not in (None, run_id):
                    raise AppError("TOOL_RESULT_CONFLICT", "历史工具结果绑定冲突，需要核对后继续。", 409)
                state.update(continuation_run_id=run_id, continuation_status=status, tool_result=result)
        return list(calls.values())

    async def list_events(
        self, turn_id: str, after_sequence: int = 0
    ) -> list[TurnEventRecord]:
        async with self.database.session() as session:
            return list(
                (
                    await session.scalars(
                        select(TurnEventRecord)
                        .where(
                            TurnEventRecord.turn_id == turn_id,
                            TurnEventRecord.sequence > after_sequence,
                        )
                        .order_by(TurnEventRecord.sequence)
                    )
                ).all()
            )

    @staticmethod
    def add_event_records(
        session,
        turn: TurnRecord,
        *,
        sequence: int,
        event_type: str,
        role: str | None,
        payload: dict[str, Any],
        created_at: datetime,
    ) -> TurnEventRecord:
        payload_json = json.dumps(payload, ensure_ascii=False)
        event_id = str(uuid.uuid4())
        event = TurnEventRecord(
            id=event_id,
            session_id=turn.session_id,
            turn_id=turn.id,
            sequence=sequence,
            event_type=event_type,
            role=role,
            payload_json=payload_json,
            created_at=created_at,
        )
        session.add(event)
        session.add(
            MessageRecord(
                id=event_id,
                session_id=turn.session_id,
                turn_id=turn.id,
                sequence=sequence,
                event_type=event_type,
                role=role,
                payload_json=payload_json,
                created_at=created_at,
            )
        )
        return event
