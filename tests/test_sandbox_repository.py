from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
async def sandbox_repository_context(settings_factory):
    from app.db.base import Database
    from app.db.models import SessionRecord, UserRecord, WorkspaceRecord
    from app.sandbox.repository import SandboxRepository

    database = Database(settings_factory().resolved_database_url)
    await database.initialize()
    now = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            UserRecord(
                id="owner",
                external_subject="owner",
                display_name="Owner",
                provider="test",
            )
        )
        session.add(
            WorkspaceRecord(id="team", name="Team", kind="team", config_json="{}")
        )
        await session.flush()
        for session_id in ("session-1", "session-2"):
            session.add(
                SessionRecord(
                    id=session_id,
                    workspace_id="team",
                    created_by="owner",
                    title=session_id,
                    title_source="auto",
                    status="idle",
                    workspace_snapshot_json="{}",
                    workspace_snapshot_hash="hash",
                    session_dir=f"sessions/{session_id}",
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.commit()
    try:
        yield database, SandboxRepository(database)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_reserve_generation_is_idempotent_until_sandbox_ends(
    sandbox_repository_context,
) -> None:
    _database, repository = sandbox_repository_context

    first = await repository.reserve_generation(
        session_id="session-1",
        memory_scope_key="memory-scope",
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
    )
    repeated = await repository.reserve_generation(
        session_id="session-1",
        memory_scope_key="memory-scope",
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
    )

    assert first.generation == repeated.generation == 1
    assert first.session_volume_name == repeated.session_volume_name
    assert first.version == repeated.version == 1

    ready = await repository.record_sandbox_ready(
        session_id="session-1",
        generation=1,
        expected_version=1,
        sandbox_id="sandbox-1",
        image_digest="sha256:" + "b" * 64,
    )
    assert ready.status == "ready"
    assert ready.version == 2

    reaped = await repository.mark_reaped(
        session_id="session-1", generation=1, expected_version=2
    )
    assert reaped.status == "terminated"

    recreated = await repository.reserve_generation(
        session_id="session-1",
        memory_scope_key="memory-scope",
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
    )
    assert recreated.generation == 2
    assert recreated.session_volume_name == first.session_volume_name


@pytest.mark.asyncio
async def test_stale_sandbox_compare_and_set_is_rejected(
    sandbox_repository_context,
) -> None:
    from app.errors import AppError

    _database, repository = sandbox_repository_context
    await repository.reserve_generation(
        session_id="session-1",
        memory_scope_key="memory-scope",
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
    )

    with pytest.raises(AppError, match="state changed"):
        await repository.record_sandbox_ready(
            session_id="session-1",
            generation=1,
            expected_version=99,
            sandbox_id="sandbox-1",
            image_digest="sha256:" + "b" * 64,
        )


@pytest.mark.asyncio
async def test_memory_lease_serializes_scope_and_rejects_stale_release(
    sandbox_repository_context,
) -> None:
    from app.errors import AppError

    _database, repository = sandbox_repository_context
    now = datetime(2026, 7, 31, 0, 0, tzinfo=UTC)

    first = await repository.acquire_memory_lease(
        scope_key="memory-scope",
        session_id="session-1",
        turn_id="turn-1",
        attempt_id=None,
        now=now,
        ttl=timedelta(seconds=30),
    )
    assert first is not None

    blocked = await repository.acquire_memory_lease(
        scope_key="memory-scope",
        session_id="session-2",
        turn_id="turn-2",
        attempt_id=None,
        now=now + timedelta(seconds=1),
        ttl=timedelta(seconds=30),
    )
    assert blocked is None

    reclaimed = await repository.acquire_memory_lease(
        scope_key="memory-scope",
        session_id="session-2",
        turn_id="turn-2",
        attempt_id=None,
        now=now + timedelta(seconds=31),
        ttl=timedelta(seconds=30),
    )
    assert reclaimed is not None
    assert reclaimed.lease_token != first.lease_token

    with pytest.raises(AppError, match="lease changed"):
        await repository.release_memory_lease("memory-scope", first.lease_token)
    await repository.release_memory_lease("memory-scope", reclaimed.lease_token)


@pytest.mark.asyncio
async def test_memory_lease_can_only_be_renewed_by_current_owner(
    sandbox_repository_context,
) -> None:
    from datetime import UTC, datetime, timedelta

    _database, repository = sandbox_repository_context
    now = datetime.now(UTC)
    lease = await repository.acquire_memory_lease(
        scope_key="renew-scope",
        session_id="session-1",
        turn_id="turn-1",
        attempt_id="attempt-1",
        now=now,
        ttl=timedelta(seconds=30),
    )
    assert lease is not None

    renewed = await repository.renew_memory_lease(
        scope_key="renew-scope",
        lease_token=lease.lease_token,
        now=now + timedelta(seconds=10),
        ttl=timedelta(seconds=30),
    )
    assert renewed.expires_at.replace(tzinfo=UTC) >= now + timedelta(seconds=40)
    with pytest.raises(Exception, match="(?i)memory lease changed"):
        await repository.renew_memory_lease(
            scope_key="renew-scope",
            lease_token="stale",
            now=now,
            ttl=timedelta(seconds=30),
        )
    await repository.release_memory_lease("renew-scope", lease.lease_token)
