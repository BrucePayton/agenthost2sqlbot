import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class SessionLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, session_id: str) -> AsyncIterator[None]:
        async with self._registry_lock:
            lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            yield

    def locked(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return bool(lock and lock.locked())

