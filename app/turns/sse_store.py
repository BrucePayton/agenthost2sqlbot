from collections.abc import AsyncIterator

from app.db.models import TurnEventRecord
from app.turns.notifications import TurnNotifier
from app.turns.repository import TurnRepository

TERMINAL_EVENTS = {
    "turn.completed",
    "turn.failed",
    "turn.cancelled",
    "turn.interrupted",
    "turn.outcome_unknown",
    "turn.recovery_required",
}


class TurnEventStream:
    def __init__(self, repository: TurnRepository, notifier: TurnNotifier) -> None:
        self.repository = repository
        self.notifier = notifier

    async def iter_events(
        self,
        turn_id: str,
        after_sequence: int,
        heartbeat_seconds: float,
    ) -> AsyncIterator[TurnEventRecord | None]:
        seen = after_sequence
        async with self.notifier.subscribe(turn_id) as subscription:
            while True:
                records = await self.repository.list_events(turn_id, seen)
                if records:
                    for record in records:
                        if record.sequence <= seen:
                            continue
                        seen = record.sequence
                        yield record
                        if record.event_type in TERMINAL_EVENTS:
                            return
                    continue
                if not await subscription.wait(heartbeat_seconds):
                    yield None
