"""Runtime latency attribution must distinguish SDK startup from model output."""

import asyncio
import logging

import pytest
from claude_agent_sdk import ResultMessage, StreamEvent, SystemMessage

from app.errors import AppError
from app.runtime import claude
from tests.test_claude_runtime import FakeClaudeSdkClient
from tests.test_runtime_events import runtime_request


async def test_runtime_records_startup_and_first_model_content_separately(
    settings_factory, tmp_path, monkeypatch, caplog
):
    """An SDK init frame must not be mistaken for the model's first content."""
    clock = [0.0]
    monkeypatch.setattr(claude, "perf_counter", lambda: clock[0], raising=False)

    class TimedClient(FakeClaudeSdkClient):
        """Advance a deterministic clock at actual SDK boundaries."""

        async def connect(self):
            """Simulate one second of SDK initialization."""
            clock[0] += 1

        async def query(self, messages):
            """Consume the prompt before acknowledging submission."""
            await super().query(messages)
            clock[0] += 0.25

        async def receive_response(self):
            """Emit initialization, real model content, and a terminal result."""
            clock[0] += 0.5
            yield SystemMessage(subtype="init", data={"session_id": "session"})
            clock[0] += 1.5
            yield StreamEvent(
                uuid="s",
                session_id="session",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Done"},
                },
            )
            clock[0] += 3
            yield ResultMessage(
                subtype="success",
                duration_ms=5100,
                duration_api_ms=4000,
                is_error=False,
                num_turns=1,
                session_id="session",
                result="Done",
            )

        async def disconnect(self):
            """Make cleanup visible independently from result duration."""
            clock[0] += 0.75

    runtime = claude.ClaudeAgentRuntime(
        settings_factory(),
        environ={"PATH": "/usr/bin"},
        client_factory=lambda _options: TimedClient(),
    )
    with caplog.at_level(logging.INFO, logger=claude.__name__):
        events = [
            event
            async for event in runtime.run(runtime_request(tmp_path), asyncio.Event())
        ]

    usage = next(event.payload for event in events if event.type == "usage.updated")
    timing = usage["runtime_timing"]
    assert timing["connect_ms"] == 1000
    assert timing["query_submit_ms"] == 250
    assert timing["query_to_first_sdk_message_ms"] == 750
    assert timing["query_to_first_model_content_ms"] == 2250
    assert timing["query_to_result_ms"] == 5250
    assert usage["sdk_duration_ms"] == 5100
    assert usage["sdk_duration_api_ms"] == 4000
    record = next(
        record for record in caplog.records if record.msg == "claude_runtime_timing"
    )
    assert record.runtime_timing["disconnect_ms"] == 750
    assert record.runtime_timing["total_ms"] == 7000
    assert record.runtime_timing["resumed"] is True


async def test_runtime_logs_timing_when_connect_fails(
    settings_factory, tmp_path, caplog
):
    """Startup failures still get stage attribution and perform cleanup."""

    class FailingClient(FakeClaudeSdkClient):
        """Fail before query to exercise startup cleanup."""

        async def connect(self):
            """Simulate a transport connection failure."""
            raise RuntimeError("connection unavailable")

    runtime = claude.ClaudeAgentRuntime(
        settings_factory(),
        environ={"PATH": "/usr/bin"},
        client_factory=lambda _options: FailingClient(),
    )
    with (
        caplog.at_level(logging.INFO, logger=claude.__name__),
        pytest.raises(AppError, match="unavailable"),
    ):
        _ = [
            event
            async for event in runtime.run(runtime_request(tmp_path), asyncio.Event())
        ]
    record = next(
        record for record in caplog.records if record.msg == "claude_runtime_timing"
    )
    assert record.runtime_timing["last_stage"] == "connect"
    assert record.runtime_timing["received_result"] is False
    assert record.runtime_timing["disconnect_ms"] >= 0


def test_unchanged_memory_settings_avoid_redundant_fsync(
    settings_factory, tmp_path, monkeypatch
):
    """Continuations should not rewrite the exact same private settings file."""
    runtime = claude.ClaudeAgentRuntime(
        settings_factory(), environ={"PATH": "/usr/bin"}
    )
    request = runtime_request(tmp_path)
    runtime.build_options(request)
    writes = []
    monkeypatch.setattr(claude.os, "fsync", lambda fd: writes.append(fd))
    runtime.build_options(request)
    assert writes == []

    # Changed memory ownership/configuration still replaces the file atomically.
    request.memory_dir = tmp_path / "other-memory"
    request.memory_dir.mkdir()
    runtime.build_options(request)
    assert len(writes) == 1
