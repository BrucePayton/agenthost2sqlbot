import asyncio
import json

import pytest

from tests.test_turns import build_turn_services


@pytest.mark.asyncio
async def test_event_broker_delivers_live_events() -> None:
    from app.runtime.base import RuntimeEvent
    from app.turns.broker import EventBroker, PersistedEvent

    broker = EventBroker()
    async with broker.subscribe("turn-1") as queue:
        envelope = PersistedEvent(
            sequence=2,
            event=RuntimeEvent("message.assistant.delta", {"text": "hi"}),
        )
        await broker.publish("turn-1", envelope)
        assert await asyncio.wait_for(queue.get(), timeout=0.1) == envelope


@pytest.mark.asyncio
async def test_durable_stream_replays_history_without_notification(
    settings_factory,
) -> None:
    from app.turns.sse_store import TurnEventStream

    *_, session, _sessions, _attachments, _runtime, _broker, turns = (
        await build_turn_services(settings_factory, delay=0.05)
    )
    turn = await turns.repository.create_queued(
        session.id, "durable-history", {"text": "hello"}, input_text="hello"
    )
    await turns.repository.append_event(
        turn.id, "turn.progress", "system", {"phase": "persisted"}
    )
    stream = TurnEventStream(turns.repository, turns.notifier)

    event = await anext(
        stream.iter_events(turn.id, after_sequence=1, heartbeat_seconds=0.01)
    )

    assert event is not None and event.sequence == 2
    assert event.event_type == "turn.progress"
    await turns.shutdown()


@pytest.mark.asyncio
async def test_sse_replays_after_last_event_id_and_closes_on_terminal(
    settings_factory,
) -> None:
    from app.turns.sse import stream_turn_events
    from app.turns.sse_store import TurnEventStream

    *_, session, _sessions, _attachments, _runtime, _broker, turns = (
        await build_turn_services(settings_factory)
    )
    turn = await turns.start(session.id, "hello", [], "request-1")
    await turns.wait(turn.id)

    chunks = [
        chunk
        async for chunk in stream_turn_events(
            TurnEventStream(turns.repository, turns.notifier),
            turn.id,
            after_sequence=2,
            heartbeat_seconds=0.01,
        )
    ]

    ids = [
        int(line.removeprefix("id: "))
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("id: ")
    ]
    event_names = [
        line.removeprefix("event: ")
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("event: ")
    ]
    assert ids == sorted(ids)
    assert ids[0] == 3
    assert event_names[-1] == "turn.completed"
    assert all(json.loads(chunk.split("data: ", 1)[1]) for chunk in chunks)
    await turns.shutdown()


@pytest.mark.asyncio
async def test_sse_closes_when_turn_is_interrupted(settings_factory) -> None:
    from app.turns.sse import stream_turn_events
    from app.turns.sse_store import TurnEventStream

    *_, session, _sessions, _attachments, _runtime, _broker, turns = (
        await build_turn_services(settings_factory, delay=0.05)
    )
    turn = await turns.start(session.id, "hello", [], "request-interrupted")

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in stream_turn_events(
                TurnEventStream(turns.repository, turns.notifier),
                turn.id,
                heartbeat_seconds=0.01,
            )
        ]

    stream_task = asyncio.create_task(collect())
    await asyncio.sleep(0.01)
    interrupted = await turns.repository.append_event(
        turn.id,
        "turn.interrupted",
        "system",
        {"code": "service_restarted", "message": "interrupted"},
    )
    await turns.notifier.notify(turn.id, interrupted.sequence)
    chunks = await asyncio.wait_for(stream_task, timeout=1)

    assert "event: turn.interrupted" in chunks[-1]
    await turns.shutdown()
