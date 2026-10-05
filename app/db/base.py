import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import event, func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import MessageRecord, SessionRecord, TurnEventRecord, TurnRecord
from app.turns.state_machine import ACTIVE_TURN_STATES


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        engine_options: dict[str, object] = {"future": True}
        if url.startswith("postgresql+asyncpg://"):
            engine_options.update(
                pool_pre_ping=True,
                connect_args={
                    "server_settings": {
                        "application_name": "claude-workspace-mvp",
                        "statement_timeout": "30000",
                    }
                },
            )
        self.engine: AsyncEngine = create_async_engine(url, **engine_options)
        self._session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False
        )
        if url.startswith("sqlite"):
            self._configure_sqlite()

    def _configure_sqlite(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    async def initialize(self) -> None:
        from app.db.migrations import run_migrations

        self._ensure_sqlite_parent()
        if self.engine.dialect.name == "sqlite":
            async with self.engine.connect() as connection:
                await connection.run_sync(run_migrations)
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": 81_985_529_216_486_895},
            )
            await connection.run_sync(run_migrations)

    def _ensure_sqlite_parent(self) -> None:
        prefix = "sqlite+aiosqlite:///"
        if not self.url.startswith(prefix):
            return
        database_path = self.url.removeprefix(prefix)
        if database_path == ":memory:" or not database_path:
            return
        Path(database_path).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    async def interrupt_stale_turns(self) -> None:
        now = datetime.now(UTC)
        async with self.session() as session:
            stale_turns = list(
                (
                    await session.scalars(
                        select(TurnRecord).where(
                            TurnRecord.status.in_(ACTIVE_TURN_STATES)
                        )
                    )
                ).all()
            )
            if not stale_turns:
                return
            stale_session_ids = {turn.session_id for turn in stale_turns}
            for turn in stale_turns:
                max_sequence = int(
                    (
                        await session.scalar(
                            select(func.max(TurnEventRecord.sequence)).where(
                                TurnEventRecord.turn_id == turn.id
                            )
                        )
                    )
                    or 0
                )
                event_id = str(uuid.uuid4())
                payload_json = json.dumps(
                    {
                        "code": "service_restarted",
                        "message": "服务在该 Turn 执行期间退出，任务已中断。",
                        "interrupted_at": now.isoformat(),
                    },
                    ensure_ascii=False,
                )
                event_fields = {
                    "id": event_id,
                    "session_id": turn.session_id,
                    "turn_id": turn.id,
                    "sequence": max_sequence + 1,
                    "event_type": "turn.interrupted",
                    "role": "system",
                    "payload_json": payload_json,
                    "created_at": now,
                }
                session.add(TurnEventRecord(**event_fields))
                session.add(
                    MessageRecord(
                        **event_fields,
                    )
                )
            await session.execute(
                update(TurnRecord)
                .where(TurnRecord.status.in_(ACTIVE_TURN_STATES))
                .values(
                    status="interrupted",
                    error_code="service_restarted",
                    error_message="The service restarted while this turn was active.",
                    completed_at=now,
                )
            )
            await session.execute(
                update(SessionRecord)
                .where(SessionRecord.id.in_(stale_session_ids))
                .values(
                    status="interrupted",
                    last_error_code="service_restarted",
                    updated_at=now,
                )
            )
            await session.commit()

    async def dispose(self) -> None:
        await self.engine.dispose()
