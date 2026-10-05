import asyncio

import pytest


@pytest.mark.asyncio
async def test_same_memory_scope_serializes() -> None:
    from app.memory.locks import MemoryScopeLockRegistry

    registry = MemoryScopeLockRegistry()
    attempting = asyncio.Event()
    entered = asyncio.Event()

    async def contender() -> None:
        attempting.set()
        async with registry.acquire("u-a/w-a"):
            entered.set()

    async with registry.acquire("u-a/w-a"):
        task = asyncio.create_task(contender())
        await asyncio.wait_for(attempting.wait(), timeout=1)
        await asyncio.sleep(0)
        assert entered.is_set() is False
        assert registry.locked("u-a/w-a") is True
    await asyncio.wait_for(task, timeout=1)
    assert entered.is_set() is True


@pytest.mark.asyncio
async def test_different_memory_scopes_remain_concurrent() -> None:
    from app.memory.locks import MemoryScopeLockRegistry

    registry = MemoryScopeLockRegistry()
    entered = asyncio.Event()

    async def contender() -> None:
        async with registry.acquire("u-b/w-a"):
            entered.set()

    async with registry.acquire("u-a/w-a"):
        task = asyncio.create_task(contender())
        await asyncio.wait_for(entered.wait(), timeout=1)
    await task
    assert entered.is_set() is True
