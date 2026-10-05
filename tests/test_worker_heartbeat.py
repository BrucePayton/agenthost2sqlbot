from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import update

from app.db.base import Database
from app.db.models import WorkerHeartbeatRecord
from app.sandbox.heartbeat import (
    WorkerCompatibility,
    WorkerHeartbeatRepository,
    WorkerIdentity,
)


class SequenceProbe:
    def __init__(self, results: list[bool]) -> None:
        self._results = iter(results)

    async def ready(self) -> bool:
        return next(self._results)


@pytest.mark.asyncio
async def test_opensandbox_health_probe_accepts_only_http_200() -> None:
    from app.sandbox.health import OpenSandboxHealthProbe

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200 if request.url.path == "/health" else 404
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        probe = OpenSandboxHealthProbe(
            "http://opensandbox-server:8080",
            client=client,
        )
        assert await probe.ready() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [404, 503])
async def test_opensandbox_health_probe_rejects_non_200(status_code: int) -> None:
    from app.sandbox.health import OpenSandboxHealthProbe

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status_code)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        probe = OpenSandboxHealthProbe(
            "http://opensandbox-server:8080",
            client=client,
        )
        assert await probe.ready() is False


@pytest.mark.asyncio
async def test_opensandbox_health_probe_maps_connection_error_to_false() -> None:
    from app.sandbox.health import OpenSandboxHealthProbe

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    transport = httpx.MockTransport(fail)
    async with httpx.AsyncClient(transport=transport) as client:
        probe = OpenSandboxHealthProbe(
            "http://opensandbox-server:8080/private-endpoint",
            client=client,
        )
        assert await probe.ready() is False


@pytest.mark.asyncio
async def test_heartbeat_requires_exact_compatibility_and_expires(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    await database.initialize()
    repository = WorkerHeartbeatRepository(database)
    expected = WorkerCompatibility(
        "docker-web",
        "1",
        "fake",
        "sha256:" + "a" * 64,
    )
    identity = WorkerIdentity("worker-1", expected)

    try:
        recorded_at = await repository.touch(identity, "ready")
        assert (await repository.snapshot(expected, 15)).status == "ready"
        assert (
            await repository.snapshot(
                replace(expected, runner_runtime="claude"),
                15,
            )
        ).status == "degraded"

        async with database.session() as db:
            await db.execute(
                update(WorkerHeartbeatRecord)
                .where(WorkerHeartbeatRecord.instance_id == "worker-1")
                .values(last_seen_at=recorded_at - timedelta(seconds=16))
            )
            await db.commit()
        assert (await repository.snapshot(expected, 15)).status == "degraded"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_unavailable_heartbeat_is_not_ready(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    await database.initialize()
    repository = WorkerHeartbeatRepository(database)
    compatibility = WorkerCompatibility(
        "docker-web",
        "1",
        "fake",
        "sha256:" + "b" * 64,
    )

    try:
        await repository.touch(
            WorkerIdentity("worker-1", compatibility),
            "unavailable",
        )
        snapshot = await repository.snapshot(compatibility, 15)
        assert snapshot.status == "degraded"
        assert snapshot.worker == "unavailable"
        assert snapshot.last_seen_at is not None
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_publisher_records_backend_state(tmp_path) -> None:
    from app.sandbox.heartbeat import WorkerHeartbeatPublisher

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    await database.initialize()
    repository = WorkerHeartbeatRepository(database)
    identity = WorkerIdentity(
        "worker-1",
        WorkerCompatibility(
            "docker-web",
            "1",
            "fake",
            "sha256:" + "c" * 64,
        ),
    )
    publisher = WorkerHeartbeatPublisher(
        repository,
        identity,
        SequenceProbe([False, True]),
        interval_seconds=5,
    )

    try:
        assert (await publisher.publish_once()).status == "degraded"
        assert (await publisher.publish_once()).status == "ready"
        await publisher.mark_unavailable()
        assert (
            await repository.snapshot(identity.compatibility, 15)
        ).status == "degraded"
    finally:
        await database.dispose()


def test_heartbeat_stale_window_must_cover_two_intervals(settings_factory) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="HEARTBEAT_STALE"):
        settings_factory(
            worker_heartbeat_interval_seconds=5,
            worker_heartbeat_stale_seconds=9,
        )


@pytest.mark.asyncio
async def test_availability_service_rejects_when_no_compatible_worker(
    tmp_path,
) -> None:
    from app.errors import AppError
    from app.sandbox.heartbeat import WorkerAvailabilityService

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    await database.initialize()
    compatibility = WorkerCompatibility(
        "docker-web",
        "1",
        "fake",
        "sha256:" + "f" * 64,
    )
    service = WorkerAvailabilityService(
        WorkerHeartbeatRepository(database),
        compatibility,
        stale_seconds=15,
    )

    try:
        assert (await service.snapshot()).status == "degraded"
        with pytest.raises(AppError) as error:
            await service.require_available()
        assert error.value.code == "execution_unavailable"
        assert error.value.status_code == 503
    finally:
        await database.dispose()
