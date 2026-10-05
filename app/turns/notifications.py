import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import asyncpg

from app.runtime.base import RuntimeEvent
from app.turns.broker import EventBroker, PersistedEvent

CHANNEL = "workspace_turn_events"


class TurnNotificationSubscription(Protocol):
    async def wait(self, timeout_seconds: float) -> bool:
        raise NotImplementedError


class TurnNotifier(Protocol):
    async def notify(self, turn_id: str, sequence: int) -> None:
        raise NotImplementedError

    @asynccontextmanager
    async def subscribe(
        self, turn_id: str
    ) -> AsyncIterator[TurnNotificationSubscription]:
        raise NotImplementedError


class _QueueSubscription:
    def __init__(self, queue: asyncio.Queue[object]) -> None:
        self.queue = queue

    async def wait(self, timeout_seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.queue.get(), timeout_seconds)
        except TimeoutError:
            return False
        return True


class InProcessTurnNotifier:
    """Development-only wake-up adapter backed by the legacy event broker."""

    def __init__(self, broker: EventBroker) -> None:
        self.broker = broker

    async def notify(self, turn_id: str, sequence: int) -> None:
        await self.broker.publish(
            turn_id,
            PersistedEvent(sequence, RuntimeEvent("notification", {})),
        )

    @asynccontextmanager
    async def subscribe(
        self, turn_id: str
    ) -> AsyncIterator[TurnNotificationSubscription]:
        async with self.broker.subscribe(turn_id) as queue:
            yield _QueueSubscription(queue)


class PostgresTurnNotifier:
    def __init__(self, database_url: str) -> None:
        self.dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async def notify(self, turn_id: str, sequence: int) -> None:
        payload = json.dumps(
            {"turn_id": turn_id, "sequence": sequence}, separators=(",", ":")
        )
        connection = await asyncpg.connect(self.dsn)
        try:
            await connection.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
        finally:
            await connection.close()

    @asynccontextmanager
    async def subscribe(
        self, turn_id: str
    ) -> AsyncIterator[TurnNotificationSubscription]:
        connection = await asyncpg.connect(self.dsn)
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=1_000)

        def listener(_connection, _pid, _channel, payload: str) -> None:
            try:
                decoded = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                return
            if decoded.get("turn_id") != turn_id:
                return
            try:
                queue.put_nowait(decoded.get("sequence"))
            except asyncio.QueueFull:
                pass  # Durable history polling closes any notification gap.

        await connection.add_listener(CHANNEL, listener)
        try:
            yield _QueueSubscription(queue)
        finally:
            await connection.remove_listener(CHANNEL, listener)
            await connection.close()
