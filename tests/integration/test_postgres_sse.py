import os

import pytest

from tests.integration.test_postgres_authority import _create_postgres_session

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_notification_wakes_stream_on_another_engine() -> None:
    import asyncio

    from app.db.base import Database
    from app.turns.notifications import PostgresTurnNotifier
    from app.turns.repository import TurnRepository
    from app.turns.sse_store import TurnEventStream

    writer_database = Database(os.environ["TEST_POSTGRES_URL"])
    reader_database = Database(os.environ["TEST_POSTGRES_URL"])
    await writer_database.initialize()
    await reader_database.initialize()
    writer = TurnRepository(writer_database)
    reader = TurnRepository(reader_database)
    writer_notifier = PostgresTurnNotifier(os.environ["TEST_POSTGRES_URL"])
    reader_notifier = PostgresTurnNotifier(os.environ["TEST_POSTGRES_URL"])
    try:
        session_id = await _create_postgres_session(writer_database)
        turn = await writer.create_queued(
            session_id, "request-1", {"text": "hello"}, input_text="hello"
        )
        stream = TurnEventStream(reader, reader_notifier)
        iterator = stream.iter_events(turn.id, after_sequence=1, heartbeat_seconds=2)
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0.05)
        event = await writer.append_event(
            turn.id, "turn.progress", "system", {"phase": "preparing"}
        )
        await writer_notifier.notify(turn.id, event.sequence)

        received = await asyncio.wait_for(pending, timeout=2)

        assert received is not None
        assert received.sequence == 2
        assert received.event_type == "turn.progress"
        await iterator.aclose()
    finally:
        await writer_database.dispose()
        await reader_database.dispose()
