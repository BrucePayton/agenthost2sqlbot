import json

import pytest
from pydantic import ValidationError


def valid_request_payload() -> dict:
    return {
        "protocol_version": "1",
        "runtime_kind": "fake",
        "user_id": "user-1",
        "workspace_id": "workspace-1",
        "platform_session_id": "session-1",
        "turn_id": "turn-1",
        "attempt_id": "attempt-1",
        "generation": 2,
        "claude_session_id": None,
        "memory_scope_key": "memory-scope",
        "text": "hello",
        "attachments": [
            {
                "id": "attachment-1",
                "original_filename": "input.txt",
                "mime_type": "text/plain",
                "relative_path": "attachments/input.txt",
            }
        ],
        "file_references": ["docs/readme.md"],
        "workspace_snapshot": {"skills": [], "mcp": {}},
        "frontend_tools": [
            {
                "name": "page.get_context",
                "description": "Read the current Davinci page.",
                "parameters": {"type": "object", "additionalProperties": False},
            }
        ],
        "page_state": {"schemaVersion": "davinci-page-state-v1"},
        "tool_results": [
            {
                "tool_call_id": "tool-1",
                "content": '{"ok":true}',
                "is_error": False,
                "origin": "model",
            }
        ],
        "context_items": [
            {
                "description": "dashboard_structure",
                "value": "仪表盘：海外数据；组件数：9",
            }
        ],
    }


def test_request_rejects_unknown_runtime_kind() -> None:
    from app.runner.protocol import RunnerRequest

    with pytest.raises(ValidationError, match="runtime_kind"):
        RunnerRequest.model_validate(
            {**valid_request_payload(), "runtime_kind": "custom"}
        )


def test_request_uses_fixed_container_paths_and_canonical_json() -> None:
    from app.runner.protocol import RunnerRequest

    request = RunnerRequest.model_validate(valid_request_payload())
    runtime = request.to_runtime_request()

    assert runtime.cwd.as_posix() == "/session/workspace"
    assert runtime.claude_config_dir.as_posix() == "/session/claude-config"
    assert runtime.memory_dir.as_posix() == "/memory"
    assert runtime.attachments[0].path.as_posix() == (
        "/session/workspace/attachments/input.txt"
    )
    assert runtime.frontend_tools[0].name == "page.get_context"
    assert runtime.page_state == {"schemaVersion": "davinci-page-state-v1"}
    assert runtime.tool_results[0].tool_call_id == "tool-1"
    assert runtime.context_items[0].description == "dashboard_structure"
    assert runtime.context_items[0].value == "仪表盘：海外数据；组件数：9"
    assert runtime.run_id == "turn-1"
    assert json.loads(request.to_bytes()) == valid_request_payload()
    assert request.to_bytes() == request.to_bytes()


def test_legacy_receipt_without_origin_remains_a_model_result() -> None:
    """Older persisted requests must retain genuine SDK tool-use semantics."""
    from app.runner.protocol import RunnerRequest

    payload = valid_request_payload()
    payload["tool_results"][0].pop("origin")
    runtime = RunnerRequest.model_validate(payload).to_runtime_request()
    assert runtime.tool_results[0].origin == "model"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("protocol_version", "2", "protocol"),
        ("generation", 0, "generation"),
        ("text", "x" * (2 * 1024 * 1024), "size"),
    ],
    ids=("protocol", "generation", "request-size"),
)
def test_request_rejects_incompatible_or_oversized_input(
    field: str, value, message: str
) -> None:
    from app.runner.protocol import RunnerRequest

    with pytest.raises(ValidationError, match=message):
        RunnerRequest.model_validate({**valid_request_payload(), field: value})


@pytest.mark.parametrize(
    "relative_path",
    ["../secret", "/etc/passwd", "attachments/../../secret", ""],
)
def test_request_rejects_attachment_path_escape(relative_path: str) -> None:
    from app.runner.protocol import RunnerRequest

    payload = valid_request_payload()
    payload["attachments"][0]["relative_path"] = relative_path
    with pytest.raises(ValidationError, match="relative path"):
        RunnerRequest.model_validate(payload)


def test_frame_stream_rejects_sequence_regression_and_multiple_terminals() -> None:
    from app.runner.protocol import RunnerFrame, RunnerFrameValidator

    validator = RunnerFrameValidator()
    validator.accept(
        RunnerFrame(sequence=1, kind="phase", payload={"phase": "preparing"})
    )
    validator.accept(
        RunnerFrame(sequence=2, kind="terminal", payload={"status": "completed"})
    )

    with pytest.raises(ValueError, match="sequence"):
        validator.accept(
            RunnerFrame(sequence=2, kind="heartbeat", payload={"alive": True})
        )
    with pytest.raises(ValueError, match="terminal"):
        validator.accept(
            RunnerFrame(sequence=3, kind="terminal", payload={"status": "failed"})
        )


def test_frame_rejects_unknown_kind_and_oversized_payload() -> None:
    from app.runner.protocol import RunnerFrame

    with pytest.raises(ValidationError, match="kind"):
        RunnerFrame(sequence=1, kind="unknown", payload={})
    with pytest.raises(ValueError, match="size"):
        RunnerFrame(
            sequence=1,
            kind="error",
            payload={"message": "x" * (1024 * 1024)},
        ).to_line()
