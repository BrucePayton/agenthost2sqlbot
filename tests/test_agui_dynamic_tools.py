import asyncio
import json
from types import SimpleNamespace

import pytest


class RecordingBridge:
    def __init__(self, public_tool_names: tuple[str, ...]) -> None:
        self.public_tool_names = public_tool_names
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def claim_and_wait(self, public_name: str, arguments: dict[str, object]):
        from app.agui.bridge import ToolSubmission

        self.calls.append((public_name, arguments))
        return ToolSubmission(
            content=json.dumps({"ok": True, "action": public_name}),
            error=None,
        )


@pytest.mark.asyncio
async def test_generated_handlers_bind_each_canonical_frontend_action() -> None:
    from app.agui.catalog import DEFAULT_REGISTRY
    from app.agui.claude_tools import create_frontend_tool_handlers

    contracts = (
        DEFAULT_REGISTRY.get("page.get_context"),
        DEFAULT_REGISTRY.get("ui.open_dashboard"),
    )
    bridge = RecordingBridge(tuple(item.action for item in contracts))

    handlers = create_frontend_tool_handlers(contracts, bridge)

    assert [handler.name for handler in handlers] == [
        "page__get_context",
        "ui__open_dashboard",
    ]
    assert handlers[0].input_schema == dict(contracts[0].input_schema)
    assert handlers[1].input_schema == dict(contracts[1].input_schema)

    first, second = await asyncio.gather(
        handlers[0].handler({}),
        handlers[1].handler({"resourceRef": "dashboard:1024"}),
    )

    assert bridge.calls == [
        ("page.get_context", {}),
        ("ui.open_dashboard", {"resourceRef": "dashboard:1024"}),
    ]
    assert first["is_error"] is False
    assert second["is_error"] is False


def test_davinci_ui_server_rejects_host_contracts() -> None:
    from app.agui.catalog import DEFAULT_REGISTRY
    from app.agui.claude_tools import create_davinci_ui_mcp_server

    bridge = RecordingBridge(("dashboard.get_widget_data",))

    with pytest.raises(ValueError, match="frontend contracts"):
        create_davinci_ui_mcp_server(
            (DEFAULT_REGISTRY.get("dashboard.get_widget_data"),), bridge
        )


def test_v1_system_prompt_uses_only_the_active_canonical_actions() -> None:
    from app.agui.claude_tools import frontend_tool_system_prompt

    bridge = RecordingBridge(("page.get_context", "ui.open_dataset_marketplace"))

    prompt = frontend_tool_system_prompt(bridge)

    assert "page.get_context" in prompt
    assert "ui.open_dataset_marketplace" in prompt
    assert "navigateTo" not in prompt


def test_v1_system_prompt_leaves_tool_selection_to_the_agent() -> None:
    from app.agui.claude_tools import frontend_tool_system_prompt

    bridge = RecordingBridge(
        ("page.get_context", "dashboard.capture_current_view")
    )
    bridge.host_context = SimpleNamespace(
        supported_actions=("dashboard.get_widget_data",)
    )

    prompt = frontend_tool_system_prompt(bridge)

    assert "Choose and compose" in prompt
    assert "structured Tool Results" in prompt
    assert "first call dashboard.capture_current_view" not in prompt
