import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.runtime.base import RuntimeEvent


@dataclass(frozen=True)
class PersistedEvent:
    sequence: int
    event: RuntimeEvent


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[PersistedEvent]]] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def subscribe(
        self, turn_id: str
    ) -> AsyncIterator[asyncio.Queue[PersistedEvent]]:
        queue: asyncio.Queue[PersistedEvent] = asyncio.Queue(maxsize=1_000)
        async with self._lock:
            self._subscribers.setdefault(turn_id, set()).add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                queues = self._subscribers.get(turn_id)
                if queues is not None:
                    queues.discard(queue)
                    if not queues:
                        self._subscribers.pop(turn_id, None)

    async def publish(self, turn_id: str, event: PersistedEvent) -> None:
        async with self._lock:
            queues = tuple(self._subscribers.get(turn_id, ()))
        for queue in queues:
            await queue.put(event)

