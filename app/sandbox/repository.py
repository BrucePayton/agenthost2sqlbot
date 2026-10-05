from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import Database
from app.db.models import MemoryScopeLeaseRecord, SessionSandboxRecord, TurnRecord
from app.errors import AppError
from app.sandbox.models import memory_volume_name, session_volume_name


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SandboxRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def claim_next_turn(self) -> TurnRecord | None:
        async with self.database.session() as session:
            statement = (
                select(TurnRecord)
                .where(TurnRecord.status == "queued")
                .order_by(TurnRecord.created_at, TurnRecord.id)
                .limit(1)
            )
            if self.database.engine.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            turn = await session.scalar(statement)
            if turn is None:
                return None
            result = await session.execute(
                update(TurnRecord)
                .where(TurnRecord.id == turn.id, TurnRecord.status == "queued")
                .values(status="waiting_for_memory")
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            await session.refresh(turn)
            return turn

    async def reserve_generation(
        self,
        *,
        session_id: str,
        memory_scope_key: str,
        runtime_cohort: str,
        runner_image: str,
    ) -> SessionSandboxRecord:
        now = datetime.now(UTC)
        async with self.database.session() as session:
            record = await session.scalar(
                select(SessionSandboxRecord)
                .where(SessionSandboxRecord.session_id == session_id)
                .with_for_update()
            )
            if record is None:
                record = SessionSandboxRecord(
                    session_id=session_id,
                    generation=1,
                    sandbox_id=None,
                    session_volume_name=session_volume_name(session_id),
                    memory_volume_name=memory_volume_name(memory_scope_key, "v1"),
                    memory_scope_key=memory_scope_key,
                    status="provisioning",
                    runtime_cohort=runtime_cohort,
                    runner_image=runner_image,
                    created_at=now,
                    heartbeat_at=now,
                    version=1,
                )
                session.add(record)
            elif record.status == "terminated":
                record.generation += 1
                record.sandbox_id = None
                record.status = "provisioning"
                record.runtime_cohort = runtime_cohort
                record.runner_image = runner_image
                record.image_digest = None
                record.active_turn_id = None
                record.active_attempt_id = None
                record.active_command_session_id = None
                record.active_command_execution_id = None
                record.created_at = now
                record.ready_at = None
                record.heartbeat_at = now
                record.idle_since = None
                record.ended_at = None
                record.last_error = None
                record.recovery_reason = None
                record.version += 1
            elif (
                record.memory_scope_key != memory_scope_key
                or record.runtime_cohort != runtime_cohort
                or record.runner_image != runner_image
            ):
                raise AppError(
                    "sandbox_reservation_conflict",
                    "The active sandbox reservation has different immutable inputs.",
                    409,
                )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise AppError(
                    "sandbox_reservation_conflict",
                    "Sandbox generation was reserved concurrently.",
                    409,
                ) from exc
            await session.refresh(record)
            return record

    async def record_sandbox_ready(
        self,
        *,
        session_id: str,
        generation: int,
        expected_version: int,
        sandbox_id: str,
        image_digest: str,
    ) -> SessionSandboxRecord:
        now = datetime.now(UTC)
        return await self._update_sandbox(
            session_id,
            generation,
            expected_version,
            status="ready",
            sandbox_id=sandbox_id,
            image_digest=image_digest,
            ready_at=now,
            heartbeat_at=now,
            idle_since=None,
        )

    async def mark_reaped(
        self, *, session_id: str, generation: int, expected_version: int
    ) -> SessionSandboxRecord:
        now = datetime.now(UTC)
        return await self._update_sandbox(
            session_id,
            generation,
            expected_version,
            status="terminated",
            sandbox_id=None,
            ended_at=now,
            heartbeat_at=now,
            active_turn_id=None,
            active_attempt_id=None,
            active_command_session_id=None,
            active_command_execution_id=None,
        )

    async def mark_busy(
        self,
        *,
        session_id: str,
        generation: int,
        expected_version: int,
        turn_id: str,
        attempt_id: str,
    ) -> SessionSandboxRecord:
        return await self._update_sandbox(
            session_id,
            generation,
            expected_version,
            status="busy",
            active_turn_id=turn_id,
            active_attempt_id=attempt_id,
            idle_since=None,
            heartbeat_at=datetime.now(UTC),
        )

    async def bind_active_command(
        self,
        *,
        session_id: str,
        generation: int,
        expected_version: int,
        command_session_id: str,
        command_execution_id: str,
    ) -> SessionSandboxRecord:
        return await self._update_sandbox(
            session_id,
            generation,
            expected_version,
            active_command_session_id=command_session_id,
            active_command_execution_id=command_execution_id,
            heartbeat_at=datetime.now(UTC),
        )

    async def mark_idle(
        self, *, session_id: str, generation: int, expected_version: int
    ) -> SessionSandboxRecord:
        now = datetime.now(UTC)
        return await self._update_sandbox(
            session_id,
            generation,
            expected_version,
            status="idle",
            active_turn_id=None,
            active_attempt_id=None,
            active_command_session_id=None,
            active_command_execution_id=None,
            heartbeat_at=now,
            idle_since=now,
        )

    async def mark_recovery_required(
        self,
        *,
        session_id: str,
        generation: int,
        expected_version: int,
        reason: str,
    ) -> SessionSandboxRecord:
        return await self._update_sandbox(
            session_id,
            generation,
            expected_version,
            status="recovery_required",
            recovery_reason=reason,
            last_error="Sandbox state requires reconciliation.",
            heartbeat_at=datetime.now(UTC),
        )

    async def list_idle_before(self, cutoff: datetime) -> list[SessionSandboxRecord]:
        async with self.database.session() as session:
            return list(
                (
                    await session.scalars(
                        select(SessionSandboxRecord)
                        .where(
                            SessionSandboxRecord.status == "idle",
                            SessionSandboxRecord.idle_since.is_not(None),
                            SessionSandboxRecord.idle_since <= cutoff,
                            SessionSandboxRecord.sandbox_id.is_not(None),
                        )
                        .order_by(SessionSandboxRecord.idle_since)
                    )
                ).all()
            )

    async def list_busy(self) -> list[SessionSandboxRecord]:
        async with self.database.session() as session:
            return list(
                (
                    await session.scalars(
                        select(SessionSandboxRecord)
                        .where(SessionSandboxRecord.status == "busy")
                        .order_by(SessionSandboxRecord.created_at)
                    )
                ).all()
            )

    async def _update_sandbox(
        self,
        session_id: str,
        generation: int,
        expected_version: int,
        **values: object,
    ) -> SessionSandboxRecord:
        async with self.database.session() as session:
            result = await session.execute(
                update(SessionSandboxRecord)
                .where(
                    SessionSandboxRecord.session_id == session_id,
                    SessionSandboxRecord.generation == generation,
                    SessionSandboxRecord.version == expected_version,
                )
                .values(**values, version=expected_version + 1)
            )
            if result.rowcount != 1:
                await session.rollback()
                raise AppError(
                    "sandbox_state_conflict",
                    "Sandbox state changed before the operation completed.",
                    409,
                )
            await session.commit()
            record = await session.get(SessionSandboxRecord, session_id)
            if record is None:
                raise AppError("sandbox_not_found", "Sandbox state not found.", 404)
            return record

    async def acquire_memory_lease(
        self,
        *,
        scope_key: str,
        session_id: str,
        turn_id: str,
        attempt_id: str | None,
        now: datetime,
        ttl: timedelta,
    ) -> MemoryScopeLeaseRecord | None:
        token = uuid.uuid4().hex
        async with self.database.session() as session:
            existing = await session.scalar(
                select(MemoryScopeLeaseRecord)
                .where(MemoryScopeLeaseRecord.scope_key == scope_key)
                .with_for_update()
            )
            if existing is not None and _as_utc(existing.expires_at) > _as_utc(now):
                return None
            if existing is None:
                lease = MemoryScopeLeaseRecord(
                    scope_key=scope_key,
                    owner_session_id=session_id,
                    owner_turn_id=turn_id,
                    owner_attempt_id=attempt_id,
                    lease_token=token,
                    acquired_at=now,
                    heartbeat_at=now,
                    expires_at=now + ttl,
                    version=1,
                )
                session.add(lease)
            else:
                existing.owner_session_id = session_id
                existing.owner_turn_id = turn_id
                existing.owner_attempt_id = attempt_id
                existing.lease_token = token
                existing.acquired_at = now
                existing.heartbeat_at = now
                existing.expires_at = now + ttl
                existing.version += 1
                lease = existing
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            await session.refresh(lease)
            return lease

    async def release_memory_lease(self, scope_key: str, lease_token: str) -> None:
        async with self.database.session() as session:
            result = await session.execute(
                delete(MemoryScopeLeaseRecord).where(
                    MemoryScopeLeaseRecord.scope_key == scope_key,
                    MemoryScopeLeaseRecord.lease_token == lease_token,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise AppError(
                    "memory_lease_conflict",
                    "Memory lease changed before release.",
                    409,
                )
            await session.commit()

    async def renew_memory_lease(
        self,
        *,
        scope_key: str,
        lease_token: str,
        now: datetime,
        ttl: timedelta,
    ) -> MemoryScopeLeaseRecord:
        async with self.database.session() as session:
            result = await session.execute(
                update(MemoryScopeLeaseRecord)
                .where(
                    MemoryScopeLeaseRecord.scope_key == scope_key,
                    MemoryScopeLeaseRecord.lease_token == lease_token,
                )
                .values(
                    heartbeat_at=now,
                    expires_at=now + ttl,
                    version=MemoryScopeLeaseRecord.version + 1,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise AppError(
                    "memory_lease_conflict",
                    "Memory lease changed before renewal.",
                    409,
                )
            await session.commit()
            record = await session.get(MemoryScopeLeaseRecord, scope_key)
            if record is None:
                raise AppError("memory_lease_not_found", "Memory lease not found.", 404)
            return record
