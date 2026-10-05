import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class MemoryScopeLockRegistry:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, scope_key: str) -> AsyncIterator[None]:
        async with self._registry_lock:
            lock = self._locks.setdefault(scope_key, asyncio.Lock())
        async with lock:
            yield

    def locked(self, scope_key: str) -> bool:
        lock = self._locks.get(scope_key)
        return bool(lock and lock.locked())
