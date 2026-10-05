import asyncio
import json
from pathlib import Path

import pytest


def runtime_request(tmp_path: Path):
    from app.runtime.contracts import RuntimeRequest

    return RuntimeRequest(
        platform_session_id="platform-session",
        claude_session_id="claude-session",
        cwd=tmp_path / "workspace",
        claude_config_dir=tmp_path / "claude-config",
        memory_scope_key="user:workspace",
        memory_dir=tmp_path / "memory",
        text="hello",
        attachments=(),
        file_references=(),
        workspace_snapshot={"id": "workspace"},
    )


def test_runtime_contracts_are_project_owned_and_json_serializable() -> None:
    from app.runtime.contracts import (
        RuntimeCapabilities,
        RuntimeEvent,
        RuntimeFrontendTool,
        RuntimeResult,
        RuntimeToolResult,
    )

    capabilities = RuntimeCapabilities(
        protocol_version="1",
        supports_resume=True,
        supports_interrupt=True,
        supports_auto_memory=True,
        supports_mcp=True,
        supports_skills=True,
    )
    event = RuntimeEvent(
        type="message.assistant.delta",
        payload={"text": "ok", "parts": [1, True, None]},
        role="assistant",
    )
    result = RuntimeResult(
        status="completed",
        claude_session_id="claude-session",
        duration_ms=12,
        metadata={"subtype": "success"},
    )

    assert capabilities.to_dict() == {
        "protocol_version": "1",
        "supports_resume": True,
        "supports_interrupt": True,
        "supports_auto_memory": True,
        "supports_mcp": True,
        "supports_skills": True,
    }
    assert json.loads(json.dumps(event.to_dict())) == {
        "type": "message.assistant.delta",
        "payload": {"text": "ok", "parts": [1, True, None]},
        "role": "assistant",
    }
    assert result.to_event().to_dict() == {
        "type": "runtime.result",
        "payload": {
            "status": "completed",
            "claude_session_id": "claude-session",
            "duration_ms": 12,
            "subtype": "success",
        },
        "role": None,
    }

    request = runtime_request(Path("/tmp/runtime-contracts"))
    assert request.frontend_tools == ()
    assert request.page_state == {}
    assert request.tool_results == ()
    assert request.run_id is None

    request.frontend_tools = (
        RuntimeFrontendTool(
            name="page.get_context",
            description="Read the current Davinci page.",
            parameters={"type": "object", "additionalProperties": False},
        ),
    )
    request.page_state = {"schemaVersion": "davinci-page-state-v1"}
    request.tool_results = (
        RuntimeToolResult(
            tool_call_id="tool-1",
            content='{"ok":true}',
            is_error=False,
        ),
    )

    assert request.frontend_tools[0].name == "page.get_context"
    assert request.page_state["schemaVersion"] == "davinci-page-state-v1"
    assert request.tool_results[0].tool_call_id == "tool-1"


@pytest.mark.asyncio
async def test_fake_runtime_implements_port_and_returns_terminal_result(
    tmp_path: Path,
) -> None:
    from app.runtime.contracts import AgentRuntimePort
    from app.runtime.fake import FakeAgentRuntime

    runtime = FakeAgentRuntime(chunks=("ok",))

    assert isinstance(runtime, AgentRuntimePort)
    assert runtime.capabilities.supports_resume is True

    events = [
        event
        async for event in runtime.run(runtime_request(tmp_path), asyncio.Event())
    ]

    assert events[-1].type == "runtime.result"
    assert events[-1].payload == {
        "status": "completed",
        "claude_session_id": "claude-session",
        "duration_ms": 1,
    }
