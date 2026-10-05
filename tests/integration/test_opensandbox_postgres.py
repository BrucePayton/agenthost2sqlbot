import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.test_postgres_authority import _create_postgres_session

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_serializes_sandbox_generation_reservation() -> None:
    from app.db.base import Database
    from app.sandbox.repository import SandboxRepository

    database = Database(os.environ["TEST_POSTGRES_URL"])
    await database.initialize()
    session_id = await _create_postgres_session(database)
    first = SandboxRepository(database)
    second = SandboxRepository(database)
    kwargs = {
        "session_id": session_id,
        "memory_scope_key": "scope-" + session_id,
        "runtime_cohort": "phase2a",
        "runner_image": "runner@sha256:" + "a" * 64,
    }
    try:
        results = await asyncio.gather(
            first.reserve_generation(**kwargs),
            second.reserve_generation(**kwargs),
            return_exceptions=True,
        )
        successful = [result for result in results if not isinstance(result, Exception)]
        assert len(successful) >= 1
        assert {result.generation for result in successful} == {1}
        stable = await first.reserve_generation(**kwargs)
        assert stable.generation == 1
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_postgres_allows_only_one_memory_scope_writer() -> None:
    from app.db.base import Database
    from app.sandbox.repository import SandboxRepository

    database = Database(os.environ["TEST_POSTGRES_URL"])
    await database.initialize()
    first = SandboxRepository(database)
    second = SandboxRepository(database)
    scope_key = "scope-" + os.urandom(8).hex()
    now = datetime.now(UTC)
    try:
        results = await asyncio.gather(
            first.acquire_memory_lease(
                scope_key=scope_key,
                session_id="session-a",
                turn_id="turn-a",
                attempt_id=None,
                now=now,
                ttl=timedelta(seconds=30),
            ),
            second.acquire_memory_lease(
                scope_key=scope_key,
                session_id="session-b",
                turn_id="turn-b",
                attempt_id=None,
                now=now,
                ttl=timedelta(seconds=30),
            ),
        )
        assert sum(result is not None for result in results) == 1
    finally:
        await database.dispose()
