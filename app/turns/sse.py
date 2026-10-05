import json
from collections.abc import AsyncIterator

from app.runtime.base import RuntimeEvent
from app.turns.broker import PersistedEvent
from app.turns.sse_store import TurnEventStream


async def stream_turn_events(
    event_stream: TurnEventStream,
    turn_id: str,
    *,
    after_sequence: int = 0,
    heartbeat_seconds: float = 15,
) -> AsyncIterator[str]:
    async for record in event_stream.iter_events(
        turn_id, after_sequence, heartbeat_seconds
    ):
        if record is None:
            yield "event: heartbeat\ndata: {}\n\n"
            continue
        event = RuntimeEvent(
            record.event_type, json.loads(record.payload_json), record.role
        )
        yield format_sse(PersistedEvent(record.sequence, event))


def format_sse(persisted: PersistedEvent) -> str:
    data = json.dumps(persisted.event.payload, ensure_ascii=False)
    return (
        f"id: {persisted.sequence}\n"
        f"event: {persisted.event.type}\n"
        f"data: {data}\n\n"
    )
