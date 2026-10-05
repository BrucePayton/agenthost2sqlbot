import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import desc, func, select

from app.db.base import Database
from app.db.models import WorkerHeartbeatRecord
from app.errors import AppError
from app.sandbox.health import BackendHealthProbe

WorkerHeartbeatStatus = Literal["ready", "unavailable"]


@dataclass(frozen=True, slots=True)
class WorkerCompatibility:
    runtime_cohort: str
    protocol_version: str
    runner_runtime: str
    image_digest: str


@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    instance_id: str
    compatibility: WorkerCompatibility


@dataclass(frozen=True, slots=True)
class WorkerAvailabilitySnapshot:
    status: Literal["ready", "degraded"]
    worker: Literal["available", "unavailable"]
    last_seen_at: datetime | None

    def to_health_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "worker": self.worker,
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at else None
            ),
        }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class WorkerHeartbeatRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def touch(
        self,
        identity: WorkerIdentity,
        status: WorkerHeartbeatStatus,
    ) -> datetime:
        async with self.database.session() as db:
            now = _as_utc(await db.scalar(select(func.now())))
            record = await db.get(WorkerHeartbeatRecord, identity.instance_id)
            compatibility = identity.compatibility
            if record is None:
                record = WorkerHeartbeatRecord(
                    instance_id=identity.instance_id,
                    runtime_cohort=compatibility.runtime_cohort,
                    protocol_version=compatibility.protocol_version,
                    runner_runtime=compatibility.runner_runtime,
                    image_digest=compatibility.image_digest,
                    status=status,
                    started_at=now,
                    last_seen_at=now,
                )
                db.add(record)
            else:
                record.runtime_cohort = compatibility.runtime_cohort
                record.protocol_version = compatibility.protocol_version
                record.runner_runtime = compatibility.runner_runtime
                record.image_digest = compatibility.image_digest
                record.status = status
                record.last_seen_at = now
            await db.commit()
            return now

    async def snapshot(
        self,
        compatibility: WorkerCompatibility,
        max_age_seconds: float,
    ) -> WorkerAvailabilitySnapshot:
        async with self.database.session() as db:
            now = _as_utc(await db.scalar(select(func.now())))
            record = await db.scalar(
                select(WorkerHeartbeatRecord)
                .where(
                    WorkerHeartbeatRecord.runtime_cohort
                    == compatibility.runtime_cohort,
                    WorkerHeartbeatRecord.protocol_version
                    == compatibility.protocol_version,
                    WorkerHeartbeatRecord.runner_runtime
                    == compatibility.runner_runtime,
                    WorkerHeartbeatRecord.image_digest == compatibility.image_digest,
                )
                .order_by(desc(WorkerHeartbeatRecord.last_seen_at))
                .limit(1)
            )

        if record is None:
            return WorkerAvailabilitySnapshot("degraded", "unavailable", None)
        last_seen_at = _as_utc(record.last_seen_at)
        available = record.status == "ready" and now - last_seen_at <= timedelta(
            seconds=max_age_seconds
        )
        return WorkerAvailabilitySnapshot(
            "ready" if available else "degraded",
            "available" if available else "unavailable",
            last_seen_at,
        )


class WorkerHeartbeatPublisher:
    def __init__(
        self,
        repository: WorkerHeartbeatRepository,
        identity: WorkerIdentity,
        probe: BackendHealthProbe,
        *,
        interval_seconds: float,
    ) -> None:
        self.repository = repository
        self.identity = identity
        self.probe = probe
        self.interval_seconds = interval_seconds

    async def publish_once(self) -> WorkerAvailabilitySnapshot:
        status: WorkerHeartbeatStatus = (
            "ready" if await self.probe.ready() else "unavailable"
        )
        await self.repository.touch(self.identity, status)
        return await self.repository.snapshot(
            self.identity.compatibility,
            self.interval_seconds * 3,
        )

    async def run(self) -> None:
        while True:
            await self.publish_once()
            await asyncio.sleep(self.interval_seconds)

    async def mark_unavailable(self) -> None:
        await self.repository.touch(self.identity, "unavailable")


class WorkerAvailabilityService:
    def __init__(
        self,
        repository: WorkerHeartbeatRepository,
        compatibility: WorkerCompatibility,
        *,
        stale_seconds: float,
    ) -> None:
        self.repository = repository
        self.compatibility = compatibility
        self.stale_seconds = stale_seconds

    async def snapshot(self) -> WorkerAvailabilitySnapshot:
        return await self.repository.snapshot(
            self.compatibility,
            self.stale_seconds,
        )

    async def require_available(self) -> None:
        if (await self.snapshot()).status != "ready":
            raise AppError(
                "execution_unavailable",
                "Execution service is temporarily unavailable.",
                503,
            )
