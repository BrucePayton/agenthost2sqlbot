from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select


@pytest.fixture
async def turn_repository_context(settings_factory):
    from app.db.base import Database
    from app.db.models import SessionRecord, UserRecord, WorkspaceRecord
    from app.turns.repository import TurnRepository

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
        session.add(
            SessionRecord(
                id="session-1",
                workspace_id="team",
                created_by="owner",
                title="Session",
                title_source="auto",
                status="idle",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/session-1",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    try:
        yield database, TurnRepository(database)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_create_queued_is_idempotent_and_writes_one_user_event(
    turn_repository_context,
) -> None:
    from app.db.models import MessageRecord, TurnEventRecord, TurnRecord

    database, repository = turn_repository_context
    payload = {"text": "hello", "attachments": [], "file_references": []}

    first = await repository.create_queued(
        "session-1", "request-1", payload, input_text="hello"
    )
    second = await repository.create_queued(
        "session-1", "request-1", payload, input_text="hello"
    )

    assert first.id == second.id
    async with database.session() as session:
        assert await session.scalar(select(func.count(TurnRecord.id))) == 1
        assert await session.scalar(select(func.count(TurnEventRecord.id))) == 1
        assert await session.scalar(select(func.count(MessageRecord.id))) == 1


@pytest.mark.asyncio
async def test_repository_transitions_with_compare_and_set(
    turn_repository_context,
) -> None:
    from app.errors import AppError

    _database, repository = turn_repository_context
    turn = await repository.create_queued(
        "session-1", "request-1", {"text": "hello"}, input_text="hello"
    )
    running = await repository.transition(
        turn.id,
        ("queued",),
        "running",
        {"execution_nonce": "nonce-1"},
    )
    assert running.status == "running"
    assert running.execution_barrier_at is not None

    with pytest.raises(AppError, match="state changed"):
        await repository.transition(
            turn.id,
            ("queued",),
            "running",
            {"execution_nonce": "stale-nonce"},
        )


@pytest.mark.asyncio
async def test_running_attempt_binds_one_sandbox_command_with_compare_and_set(
    turn_repository_context,
) -> None:
    from app.db.models import TurnAttemptRecord
    from app.errors import AppError

    database, repository = turn_repository_context
    turn = await repository.create_queued(
        "session-1", "request-1", {"text": "hello"}, input_text="hello"
    )
    await repository.transition(
        turn.id,
        ("queued",),
        "running",
        {
            "execution_nonce": "nonce-1",
            "runtime_cohort": "phase2a",
            "sandbox_generation": 3,
            "sandbox_id": "sandbox-3",
        },
    )

    attempt = await repository.bind_attempt_command(
        turn_id=turn.id,
        execution_nonce="nonce-1",
        sandbox_generation=3,
        sandbox_id="sandbox-3",
        command_session_id="command-session",
        command_execution_id="execution-1",
    )
    assert attempt.command_session_id == "command-session"
    assert attempt.command_execution_id == "execution-1"

    with pytest.raises(AppError, match="command changed"):
        await repository.bind_attempt_command(
            turn_id=turn.id,
            execution_nonce="nonce-1",
            sandbox_generation=3,
            sandbox_id="sandbox-3",
            command_session_id="other-command",
            command_execution_id="execution-2",
        )

    async with database.session() as session:
        stored = await session.get(TurnAttemptRecord, attempt.id)
        assert stored is not None
        assert stored.sandbox_generation == 3
        assert stored.sandbox_id == "sandbox-3"


@pytest.mark.asyncio
async def test_append_event_assigns_monotonic_server_sequence(
    turn_repository_context,
) -> None:
    _database, repository = turn_repository_context
    turn = await repository.create_queued(
        "session-1", "request-1", {"text": "hello"}, input_text="hello"
    )

    second = await repository.append_event(
        turn.id, "turn.progress", "system", {"phase": "preparing"}
    )
    third = await repository.append_event(
        turn.id, "message.assistant.completed", "assistant", {"text": "done"}
    )

    assert (second.sequence, third.sequence) == (2, 3)
    events = await repository.list_events(turn.id)
    assert [event.sequence for event in events] == [1, 2, 3]


@pytest.mark.asyncio
async def test_request_cancel_is_durable_and_idempotent(
    turn_repository_context,
) -> None:
    _database, repository = turn_repository_context
    turn = await repository.create_queued(
        "session-1", "request-1", {"text": "hello"}, input_text="hello"
    )

    first = await repository.request_cancel(turn.id)
    second = await repository.request_cancel(turn.id)

    assert first.cancel_requested_at is not None
    assert second.cancel_requested_at == first.cancel_requested_at
