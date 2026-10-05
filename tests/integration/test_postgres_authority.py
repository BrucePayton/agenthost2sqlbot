import os
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_serializes_concurrent_migrations(monkeypatch) -> None:
    import asyncio

    from app.db import migrations
    from app.db.base import Database

    def slow_migration(connection) -> None:
        connection.exec_driver_sql("SELECT pg_sleep(0.2)")

    monkeypatch.setattr(migrations, "run_migrations", slow_migration)
    first = Database(os.environ["TEST_POSTGRES_URL"])
    second = Database(os.environ["TEST_POSTGRES_URL"])
    started_at = time.monotonic()
    try:
        await asyncio.gather(first.initialize(), second.initialize())
    finally:
        await first.dispose()
        await second.dispose()

    assert time.monotonic() - started_at >= 0.35


@pytest.mark.asyncio
async def test_postgres_initializes_and_serves_queries() -> None:
    from app.db.base import Database

    database = Database(os.environ["TEST_POSTGRES_URL"])
    try:
        await database.initialize()
        async with database.session() as session:
            assert await session.scalar(text("SELECT 1")) == 1
        async with database.engine.connect() as connection:
            tables = set(
                await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            )
            indexes = await connection.run_sync(
                lambda sync: inspect(sync).get_indexes("turns")
            )
            workspace_columns = await connection.run_sync(
                lambda sync: inspect(sync).get_columns("workspaces")
            )
            workspace_indexes = await connection.run_sync(
                lambda sync: inspect(sync).get_indexes("workspaces")
            )
        assert {
            "identity_mappings",
            "workspace_membership_projections",
            "config_snapshots",
            "turn_attempts",
            "turn_events",
        } <= tables
        assert "uq_turns_one_active_per_session" in {index["name"] for index in indexes}
        assert {"owner_user_id", "template_id"} <= {
            column["name"] for column in workspace_columns
        }
        assert "uq_workspaces_personal_owner" in {
            index["name"] for index in workspace_indexes
        }
    finally:
        await database.dispose()


async def _create_postgres_session(database) -> str:
    from app.db.models import SessionRecord, UserRecord, WorkspaceRecord

    suffix = uuid.uuid4().hex
    user_id = str(uuid.uuid4())
    workspace_id = f"workspace-{suffix}"
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            UserRecord(
                id=user_id,
                external_subject=f"subject-{suffix}",
                display_name="Integration User",
                provider="test",
            )
        )
        session.add(
            WorkspaceRecord(
                id=workspace_id,
                name="Integration Workspace",
                kind="team",
                config_json="{}",
            )
        )
        await session.flush()
        session.add(
            SessionRecord(
                id=session_id,
                workspace_id=workspace_id,
                created_by=user_id,
                title="Integration Session",
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
    return session_id


@pytest.mark.asyncio
async def test_postgres_enforces_idempotency_and_one_active_turn() -> None:
    import asyncio

    from app.db.base import Database
    from app.errors import AppError
    from app.turns.repository import TurnRepository

    database = Database(os.environ["TEST_POSTGRES_URL"])
    await database.initialize()
    repository = TurnRepository(database)
    try:
        idempotent_session = await _create_postgres_session(database)
        duplicate_results = await asyncio.gather(
            repository.create_queued(
                idempotent_session,
                "same-request",
                {"text": "hello"},
                input_text="hello",
            ),
            repository.create_queued(
                idempotent_session,
                "same-request",
                {"text": "hello"},
                input_text="hello",
            ),
        )
        assert duplicate_results[0].id == duplicate_results[1].id

        active_session = await _create_postgres_session(database)
        active_results = await asyncio.gather(
            repository.create_queued(
                active_session, "request-a", {"text": "a"}, input_text="a"
            ),
            repository.create_queued(
                active_session, "request-b", {"text": "b"}, input_text="b"
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in active_results) == 1
        errors = [result for result in active_results if isinstance(result, AppError)]
        assert len(errors) == 1 and errors[0].code == "session_busy"
    finally:
        await database.dispose()
