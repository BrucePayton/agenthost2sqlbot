import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

TurnExecutor = Callable[[str, asyncio.Event], Awaitable[None]]


class ExecutionDispatcher(Protocol):
    def submit(self, turn_id: str) -> bool: ...

    async def cancel(self, turn_id: str) -> bool: ...

    async def wait(self, turn_id: str) -> None: ...

    async def shutdown(self) -> None: ...


class ExecutionDisabledDispatcher:
    """Durably queues Turns without executing them in Phase 1 replicas."""

    def submit(self, turn_id: str) -> bool:
        return True

    async def cancel(self, turn_id: str) -> bool:
        return False

    async def wait(self, turn_id: str) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class LocalInlineExecutionDispatcher:
    def __init__(self, executor: TurnExecutor) -> None:
        self._executor = executor
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}

    def submit(self, turn_id: str) -> bool:
        if turn_id in self._tasks:
            return False
        cancel_event = asyncio.Event()
        task = asyncio.create_task(
            self._executor(turn_id, cancel_event),
            name=f"turn-{turn_id}",
        )
        self._cancel_events[turn_id] = cancel_event
        self._tasks[turn_id] = task
        task.add_done_callback(
            lambda completed, submitted_turn_id=turn_id: self._cleanup(
                submitted_turn_id, completed
            )
        )
        return True

    async def cancel(self, turn_id: str) -> bool:
        event = self._cancel_events.get(turn_id)
        if event is None:
            return False
        event.set()
        return True

    async def wait(self, turn_id: str) -> None:
        task = self._tasks.get(turn_id)
        if task is not None:
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        for event in tuple(self._cancel_events.values()):
            event.set()
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._cancel_events.clear()

    def _cleanup(self, turn_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(turn_id) is task:
            self._tasks.pop(turn_id, None)
            self._cancel_events.pop(turn_id, None)
        if not task.cancelled():
            task.exception()


class OpenSandboxQueuedDispatcher:
    """Queue-only API dispatcher; a separate Worker owns sandbox execution."""

    def __init__(self, repository, *, poll_seconds: float = 0.25) -> None:
        self.repository = repository
        self.poll_seconds = poll_seconds
        self._shutdown = False

    def submit(self, turn_id: str) -> bool:
        return not self._shutdown

    async def cancel(self, turn_id: str) -> bool:
        turn = await self.repository.request_cancel(turn_id)
        return turn.status not in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "failed_before_execution",
            "cancelled_before_execution",
            "outcome_unknown",
            "recovery_required",
        }

    async def wait(self, turn_id: str) -> None:
        while not self._shutdown:
            status = await self.repository.get_status(turn_id)
            if status in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "failed_before_execution",
                "cancelled_before_execution",
                "outcome_unknown",
                "recovery_required",
            }:
                return
            await asyncio.sleep(self.poll_seconds)

    async def shutdown(self) -> None:
        self._shutdown = True
