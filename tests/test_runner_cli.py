import asyncio
import json

import pytest

from tests.test_runner_protocol import valid_request_payload


@pytest.mark.asyncio
async def test_runner_maps_real_runtime_events_to_one_jsonl_terminal() -> None:
    from app.runner.claude_runner import run_request
    from app.runner.protocol import RunnerRequest
    from app.runtime.fake import FakeAgentRuntime

    runtime = FakeAgentRuntime(chunks=("hello ", "world"), emit_tool=True)
    emitted: list[str] = []

    result = await run_request(
        RunnerRequest.model_validate(valid_request_payload()),
        runtime,
        emitted.append,
        asyncio.Event(),
    )

    frames = [json.loads(line) for line in emitted]
    assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))
    assert [frame["kind"] for frame in frames].count("terminal") == 1
    assert any(frame["kind"] == "assistant_delta" for frame in frames)
    assert any(frame["kind"] == "tool_event" for frame in frames)
    assert any(frame["kind"] == "usage" for frame in frames)
    assert frames[-1]["kind"] == "terminal"
    assert frames[-1]["payload"]["status"] == "completed"
    assert result.status == "completed"
    assert runtime.requests[0].cwd.as_posix() == "/session/workspace"


@pytest.mark.asyncio
async def test_runner_emits_redacted_error_and_nonzero_result() -> None:
    from app.runner.claude_runner import run_request
    from app.runner.protocol import RunnerRequest
    from app.runtime.fake import FakeAgentRuntime

    emitted: list[str] = []
    result = await run_request(
        RunnerRequest.model_validate(valid_request_payload()),
        FakeAgentRuntime(fail_code="proxy-secret-failed"),
        emitted.append,
        asyncio.Event(),
    )

    frame = json.loads(emitted[-1])
    assert result.status == "failed"
    assert frame["kind"] == "terminal"
    assert frame["payload"] == {
        "status": "failed",
        "code": "runtime_failed",
        "message": "The sandbox runtime failed.",
    }
    assert "proxy-secret" not in json.dumps(frame)


def test_runner_selects_fake_runtime_from_request() -> None:
    from app.runner.main import _runtime_for
    from app.runner.protocol import RunnerRequest
    from app.runtime.fake import FakeAgentRuntime

    request = RunnerRequest.model_validate(valid_request_payload())
    assert isinstance(_runtime_for(request), FakeAgentRuntime)
