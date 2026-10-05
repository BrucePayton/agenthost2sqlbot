import json

import pytest

from tests.agui_helpers import dashboard_context, dashboard_tools


def _types(events) -> list[str]:
    return [event.type.value for event in events]


def test_mapper_emits_one_text_message_and_one_terminal() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = []
    events += mapper.map("turn.started", {"turn_id": "turn-1"})
    events += mapper.map("message.assistant.delta", {"text": "南区"})
    events += mapper.map("message.assistant.delta", {"text": "下降"})
    events += mapper.map("message.assistant.completed", {"text": "南区下降"})
    events += mapper.map(
        "turn.completed", {"completed_at": "2026-08-06T16:00:00+08:00"}
    )
    events += mapper.map("turn.completed", {})

    assert _types(events) == [
        "RUN_STARTED",
        "CUSTOM",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "CUSTOM",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]


def test_mapper_uses_completed_text_when_no_deltas_exist() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = mapper.map("message.assistant.completed", {"text": "完整回复"})
    events += mapper.map("turn.completed", {})

    assert _types(events) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "CUSTOM",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[1].delta == "完整回复"


def test_mapper_emits_native_deferred_tool_call_without_legacy_bridge() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = mapper.map(
        "frontend_tool.deferred",
        {
            "tool_use_id": "tool-native-1",
            "name": "dashboard.get_structure",
            "arguments": {"includeLayout": True},
            "origin_run_id": "turn-1",
        },
    )

    assert _types(events) == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "CUSTOM",
    ]
    assert events[0].tool_call_name == "dashboard.get_structure"
    assert json.loads(events[1].delta) == {"includeLayout": True}


@pytest.mark.asyncio
async def test_mapper_emits_exact_public_tool_name_and_valid_json_args() -> None:
    from app.agui.adapter import AgUiEventMapper
    from app.agui.bridge import FrontendToolBridgeRegistry

    bridge = FrontendToolBridgeRegistry().register(
        "session-1", "turn-1", dashboard_context(), dashboard_tools()
    )
    bridge.begin_call("tool-1", "navigateTo", {"destination": "datasets"})
    mapper = AgUiEventMapper("session-1", "turn-1", bridge)

    events = mapper.map(
        "tool.started",
        {
            "tool_use_id": "tool-1",
            "name": "mcp__davinci_ui__navigate_to",
            "input_preview": "not trusted",
        },
    )

    assert _types(events) == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "CUSTOM",
    ]
    assert events[0].tool_call_name == "navigateTo"
    assert json.loads(events[1].delta) == {"destination": "datasets"}


def test_mapper_exposes_safe_runtime_failure_and_maps_progress() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    progress = mapper.map(
        "turn.progress", {"phase": "waiting_model", "message": "等待模型"}
    )
    failed = mapper.map(
        "turn.failed",
        {
            "code": "mcp_unavailable",
            "message": "MCP servers failed to connect: openaiDeveloperDocs.",
        },
    )

    assert progress[0].name == "workspace.progress"
    assert progress[0].value == {
        "phase": "waiting_model",
        "message": "等待模型",
    }
    # 终态的 trace 排在核心事件之前——客户端在 RUN_ERROR 之后拒收任何事件。
    assert _types(failed) == ["CUSTOM", "RUN_ERROR"]
    assert failed[1].code == "mcp_unavailable"
    assert failed[1].message == ("MCP servers failed to connect: openaiDeveloperDocs.")


def test_mapper_redacts_unknown_runtime_failure() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    failed = mapper.map(
        "turn.failed",
        {"code": "secret-error", "message": "api-key=secret"},
    )
    duplicate = mapper.map("turn.cancelled", {})

    assert failed[1].code == "RUN_ERROR"
    assert "secret" not in failed[1].message
    assert duplicate == []


def test_mapper_closes_text_before_cancel_terminal() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = mapper.map("message.assistant.delta", {"text": "partial"})
    events += mapper.map("turn.cancelled", {})

    assert _types(events) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "CUSTOM",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[-1].outcome.type == "interrupt"


def test_mapper_appends_trace_for_backend_tool_events() -> None:
    from datetime import UTC, datetime

    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    at = datetime(2026, 8, 30, 1, 0, tzinfo=UTC)
    events = mapper.map(
        "tool.completed",
        {
            "tool_use_id": "tool-1",
            "name": "catalog.search_datasets",
            "output_preview": "3 行",
            "is_error": False,
            "duration_ms": 1200,
        },
        at,
    )

    assert _types(events) == ["CUSTOM"]
    assert events[0].name == "workspace.trace"
    assert events[0].value["event_type"] == "tool.completed"
    assert events[0].value["turn_id"] == "turn-1"
    assert events[0].value["at"] == at.isoformat()
    assert events[0].value["payload"]["name"] == "catalog.search_datasets"


def test_mapper_traces_alongside_the_existing_run_lifecycle_events() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    started = mapper.map("turn.started", {"turn_id": "turn-1"})
    finished = mapper.map(
        "turn.completed", {"completed_at": "2026-08-30T01:00:00+00:00"}
    )

    assert _types(started) == ["RUN_STARTED", "CUSTOM"]
    # 终态反过来：trace 必须在 RUN_FINISHED 之前，否则客户端拒收。
    assert _types(finished) == ["CUSTOM", "RUN_FINISHED"]


def test_mapper_does_not_trace_assistant_text_deltas() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = mapper.map("message.assistant.delta", {"text": "正文"})

    assert _types(events) == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]


def test_mapper_stops_tracing_after_a_terminal_event() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    mapper.map("turn.completed", {})
    events = mapper.map("tool.started", {"tool_use_id": "late", "name": "Read"})

    assert events == []


def test_mapper_traces_thinking_without_an_occurred_at() -> None:
    from app.agui.adapter import AgUiEventMapper

    mapper = AgUiEventMapper("session-1", "turn-1", bridge=None)
    events = mapper.map(
        "message.assistant.thinking.delta", {"index": 0, "text": "推理片段"}
    )

    assert _types(events) == ["CUSTOM"]
    assert events[0].value["at"] is None
