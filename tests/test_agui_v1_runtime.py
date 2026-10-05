import asyncio
import json

import pytest
from ag_ui.core import ToolMessage


def v1_context(version: int = 4):
    from app.agui.models import HostContext

    return HostContext.model_validate(
        {
            "protocolVersion": "1.0",
            "pageInstanceId": "workbench-1",
            "contextVersion": version,
            "route": {"view": "dashboard", "dashboardId": "1024"},
            "viewMode": {"isViewAs": False},
            "permissions": {
                "canRead": True,
                "canOperate": True,
                "canPersist": True,
                "isOwner": True,
                "isSnapshot": False,
                "isLinkShare": False,
            },
            "pageState": {"busy": False, "dirty": False},
            "supportedActions": [
                "page.get_context",
                "workspace.list_dashboards",
                "ui.open_dashboard",
                "ui.open_dataset_marketplace",
            ],
        }
    )


def test_v1_context_projects_only_browser_executable_tools() -> None:
    from app.agui.models import validate_frontend_tools

    context = v1_context()
    tools = validate_frontend_tools(context, [])

    assert [item.name for item in tools] == [
        "page.get_context",
        "workspace.list_dashboards",
        "ui.open_dashboard",
        "ui.open_dataset_marketplace",
    ]


@pytest.mark.asyncio
async def test_v1_read_tool_result_resolves_the_waiting_handler() -> None:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.agui.models import validate_frontend_tools

    context = v1_context()
    tools = validate_frontend_tools(context, [])
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.2)
    bridge = registry.register("thread-1", "run-1", context, tools)
    bridge.begin_call("tool-1", "page.get_context", {})
    waiter = asyncio.create_task(bridge.claim_and_wait("page.get_context", {}))
    await asyncio.sleep(0)

    outcome = await registry.submit(
        "thread-1",
        "run-1",
        ToolMessage(
            id="message-1",
            toolCallId="tool-1",
            content=json.dumps({"summary": "当前页面：dashboard", "contextVersion": 4}),
        ),
        context,
        tools,
    )

    submission = await waiter
    assert outcome.status == "accepted"
    assert json.loads(submission.content)["summary"] == "当前页面：dashboard"


@pytest.mark.asyncio
async def test_v1_read_result_accepts_monotonic_page_context_advance() -> None:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.agui.models import validate_frontend_tools

    initial_context = v1_context(version=4)
    next_context = v1_context(version=5)
    tools = validate_frontend_tools(initial_context, [])
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.2)
    bridge = registry.register("thread-1", "run-1", initial_context, tools)
    bridge.begin_call("tool-1", "page.get_context", {})
    waiter = asyncio.create_task(bridge.claim_and_wait("page.get_context", {}))
    await asyncio.sleep(0)

    outcome = await registry.submit(
        "thread-1",
        "run-1",
        ToolMessage(
            id="message-1",
            toolCallId="tool-1",
            content=json.dumps({"summary": "当前页面：dashboard", "contextVersion": 5}),
        ),
        next_context,
        validate_frontend_tools(next_context, []),
    )

    assert outcome.status == "accepted"
    assert (await waiter).error is None
    assert bridge.host_context == next_context


@pytest.mark.asyncio
async def test_v1_navigation_accepts_route_settling_after_command_ack() -> None:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.agui.models import validate_frontend_tools

    initial_context = v1_context(version=4)
    next_context = v1_context(version=6).model_copy(
        update={"route": {"view": "dataset-marketplace"}}
    )
    tools = validate_frontend_tools(initial_context, [])
    registry = FrontendToolBridgeRegistry(tool_timeout_seconds=0.2)
    bridge = registry.register("thread-1", "run-1", initial_context, tools)
    bridge.begin_call("tool-1", "ui.open_dataset_marketplace", {})
    waiter = asyncio.create_task(
        bridge.claim_and_wait("ui.open_dataset_marketplace", {})
    )
    await asyncio.sleep(0)

    outcome = await registry.submit(
        "thread-1",
        "run-1",
        ToolMessage(
            id="message-1",
            toolCallId="tool-1",
            content=json.dumps({"status": "opened", "contextVersion": 5}),
        ),
        next_context,
        validate_frontend_tools(next_context, []),
    )

    assert outcome.status == "accepted"
    assert (await waiter).error is None
    assert bridge.host_context == next_context
