import asyncio

import pytest


@pytest.mark.asyncio
async def test_execution_disabled_dispatcher_keeps_turn_queued() -> None:
    from app.turns.dispatcher import ExecutionDisabledDispatcher

    dispatcher = ExecutionDisabledDispatcher()

    assert dispatcher.submit("turn-1") is True
    assert await dispatcher.cancel("turn-1") is False
    await dispatcher.wait("turn-1")
    await dispatcher.shutdown()


@pytest.mark.asyncio
async def test_local_dispatcher_submits_once_and_cleans_completed_turn() -> None:
    from app.turns.dispatcher import LocalInlineExecutionDispatcher

    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def execute(turn_id: str, _cancel_event: asyncio.Event) -> None:
        calls.append(turn_id)
        started.set()
        await release.wait()

    dispatcher = LocalInlineExecutionDispatcher(execute)
    assert dispatcher.submit("turn-1") is True
    assert dispatcher.submit("turn-1") is False
    await started.wait()

    release.set()
    await dispatcher.wait("turn-1")

    assert calls == ["turn-1"]
    assert await dispatcher.cancel("turn-1") is False


@pytest.mark.asyncio
async def test_local_dispatcher_preserves_cancel_before_executor_starts() -> None:
    from app.turns.dispatcher import LocalInlineExecutionDispatcher

    observed: list[bool] = []

    async def execute(_turn_id: str, cancel_event: asyncio.Event) -> None:
        observed.append(cancel_event.is_set())

    dispatcher = LocalInlineExecutionDispatcher(execute)
    dispatcher.submit("turn-before")

    assert await dispatcher.cancel("turn-before") is True
    await dispatcher.wait("turn-before")
    assert observed == [True]


@pytest.mark.asyncio
async def test_local_dispatcher_cancels_running_turn_and_shuts_down() -> None:
    from app.turns.dispatcher import LocalInlineExecutionDispatcher

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def execute(_turn_id: str, cancel_event: asyncio.Event) -> None:
        started.set()
        await cancel_event.wait()
        cancelled.set()

    dispatcher = LocalInlineExecutionDispatcher(execute)
    dispatcher.submit("turn-running")
    await started.wait()

    assert await dispatcher.cancel("turn-running") is True
    await dispatcher.wait("turn-running")
    assert cancelled.is_set()

    dispatcher.submit("turn-shutdown")
    await dispatcher.shutdown()
    assert await dispatcher.cancel("turn-shutdown") is False


@pytest.mark.asyncio
async def test_local_dispatcher_wait_propagates_executor_exception() -> None:
    from app.turns.dispatcher import LocalInlineExecutionDispatcher

    async def execute(_turn_id: str, _cancel_event: asyncio.Event) -> None:
        raise RuntimeError("executor failed")

    dispatcher = LocalInlineExecutionDispatcher(execute)
    dispatcher.submit("turn-error")

    with pytest.raises(RuntimeError, match="executor failed"):
        await dispatcher.wait("turn-error")
