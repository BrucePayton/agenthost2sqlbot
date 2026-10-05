import asyncio
import json

import pytest
from ag_ui.core import ToolMessage

from tests.agui_helpers import (
    dashboard_context,
    dashboard_tools,
    dataset_context,
    navigation_tool_message,
    snapshot_payload,
    snapshot_tool_message,
)


def load_bridge_types():
    from app.agui.bridge import (
        FrontendToolBridgeError,
        FrontendToolBridgeRegistry,
    )

    return FrontendToolBridgeError, FrontendToolBridgeRegistry


def test_default_timeout_covers_dashboard_capture_wait_budget() -> None:
    _error, registry_type = load_bridge_types()

    assert registry_type().tool_timeout_seconds >= 70


@pytest.mark.asyncio
async def test_result_resolves_only_matching_claim() -> None:
    _error, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.2)
    bridge = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    bridge.begin_call("tool-1", "dashboard.capture_current_view", {})
    waiter = asyncio.create_task(
        bridge.claim_and_wait("dashboard.capture_current_view", {})
    )
    await asyncio.sleep(0)

    outcome = await registry.submit(
        "thread-1", "run-1", snapshot_tool_message("tool-1")
    )
    result = await waiter

    assert outcome.status == "accepted"
    assert outcome.tool_call_id == "tool-1"
    assert json.loads(result.content)["metrics"]["itemCount"] == 4734
    assert result.error is None
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_identical_replay_is_idempotent_and_conflict_is_rejected() -> None:
    error_type, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.2)
    bridge = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    bridge.begin_call("tool-1", "dashboard.capture_current_view", {})
    message = snapshot_tool_message("tool-1")

    first = await registry.submit("thread-1", "run-1", message)
    replay = await registry.submit("thread-1", "run-1", message)
    changed_payload = snapshot_payload()
    changed_payload["metrics"]["itemCount"] = 4735
    changed = message.model_copy(
        update={"content": json.dumps(changed_payload, ensure_ascii=False)}
    )

    with pytest.raises(error_type) as exc_info:
        await registry.submit("thread-1", "run-1", changed)

    assert first.status == "accepted"
    assert replay.status == "replayed"
    assert exc_info.value.code == "TOOL_RESULT_CONFLICT"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_claim_times_out_and_cleanup_removes_pending_calls() -> None:
    error_type, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.02)
    bridge = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    bridge.begin_call("tool-1", "navigateTo", {"destination": "datasets"})

    with pytest.raises(error_type) as exc_info:
        await bridge.claim_and_wait("navigateTo", {"destination": "datasets"})

    assert exc_info.value.code == "TOOL_TIMEOUT"
    assert registry.pending_count == 0
    await registry.remove("thread-1", "run-1", code="IFRAME_CLOSED")
    assert registry.get("thread-1", "run-1") is None


@pytest.mark.asyncio
async def test_layout_wait_timeout_reports_bounded_correlated_diagnostics() -> None:
    from ag_ui.core import Tool

    from app.agui.bridge import FrontendToolBridgeError, RunFrontendToolBridge

    bridge = RunFrontendToolBridge(
        "session-1",
        "run-1",
        dashboard_context(),
        (Tool(
            name="dashboard.set_widget_layout",
            description="layout",
            parameters={"type": "object"},
        ),),
        tool_timeout_seconds=0.01,
        registration_grace_seconds=0.01,
    )
    arguments = {"preset": {"mode": "compact", "sizing": "content"}}
    bridge.begin_call("layout-call", "dashboard.set_widget_layout", arguments)

    with pytest.raises(FrontendToolBridgeError) as exc_info:
        await bridge.claim_and_wait("dashboard.set_widget_layout", arguments)

    assert exc_info.value.code == "TOOL_TIMEOUT"
    assert exc_info.value.details == {
        "stage": "frontend_wait",
        "code": "TOOL_TIMEOUT",
        "retryable": False,
        "writeDispatched": None,
        "sessionId": "session-1",
        "toolCallId": "layout-call",
        "layoutRunId": None,
    }


@pytest.mark.asyncio
async def test_layout_result_validation_error_keeps_call_diagnostics_and_cleans_pending() -> None:
    from ag_ui.core import Tool

    from app.agui.bridge import FrontendToolBridgeError, RunFrontendToolBridge

    bridge = RunFrontendToolBridge(
        "session-1",
        "run-1",
        dashboard_context(),
        (Tool(
            name="dashboard.set_widget_layout",
            description="layout",
            parameters={"type": "object"},
        ),),
        tool_timeout_seconds=0.2,
        registration_grace_seconds=0.01,
    )
    arguments = {"preset": {"mode": "compact", "sizing": "content"}}
    bridge.begin_call("layout-invalid-result", "dashboard.set_widget_layout", arguments)
    waiter = asyncio.create_task(
        bridge.claim_and_wait("dashboard.set_widget_layout", arguments)
    )
    await asyncio.sleep(0)

    with pytest.raises(FrontendToolBridgeError) as submit_exc:
        await bridge.submit(ToolMessage(
            id="result-invalid",
            content="not-json",
            toolCallId="layout-invalid-result",
        ))
    with pytest.raises(FrontendToolBridgeError) as waiter_exc:
        await waiter

    expected = {
        "stage": "result_validation",
        "code": "RUN_ERROR",
        "retryable": False,
        "writeDispatched": None,
        "sessionId": "session-1",
        "toolCallId": "layout-invalid-result",
        "layoutRunId": None,
    }
    assert submit_exc.value.details == expected
    assert waiter_exc.value.details == expected
    assert bridge.pending_count == 0


@pytest.mark.asyncio
async def test_fail_all_builds_correlated_error_for_each_layout_call() -> None:
    from ag_ui.core import Tool

    from app.agui.bridge import FrontendToolBridgeError, RunFrontendToolBridge

    bridge = RunFrontendToolBridge(
        "session-1",
        "run-1",
        dashboard_context(),
        (Tool(
            name="dashboard.set_widget_layout",
            description="layout",
            parameters={"type": "object"},
        ),),
        tool_timeout_seconds=0.2,
        registration_grace_seconds=0.01,
    )
    first_args = {"preset": {"mode": "compact", "sizing": "content"}}
    second_args = {"preset": {"mode": "align", "sizing": "preserve"}}
    bridge.begin_call("layout-1", "dashboard.set_widget_layout", first_args)
    bridge.begin_call("layout-2", "dashboard.set_widget_layout", second_args)
    waiters = [
        asyncio.create_task(bridge.claim_and_wait("dashboard.set_widget_layout", args))
        for args in (first_args, second_args)
    ]
    await asyncio.sleep(0)

    await bridge.fail_all("IFRAME_CLOSED", "Iframe unloaded.")

    errors = []
    for waiter in waiters:
        with pytest.raises(FrontendToolBridgeError) as exc_info:
            await waiter
        errors.append(exc_info.value)
    assert errors[0] is not errors[1]
    for error, tool_call_id in zip(errors, ("layout-1", "layout-2"), strict=True):
        assert error.details == {
            "stage": "run_termination",
            "code": "IFRAME_CLOSED",
            "retryable": False,
            "writeDispatched": None,
            "sessionId": "session-1",
            "toolCallId": tool_call_id,
            "layoutRunId": None,
        }


@pytest.mark.asyncio
async def test_fail_all_before_claim_preserves_original_call_error() -> None:
    from ag_ui.core import Tool

    from app.agui.bridge import FrontendToolBridgeError, RunFrontendToolBridge

    bridge = RunFrontendToolBridge(
        "session-1",
        "run-1",
        dashboard_context(),
        (Tool(
            name="dashboard.set_widget_layout",
            description="layout",
            parameters={"type": "object"},
        ),),
        tool_timeout_seconds=0.2,
        registration_grace_seconds=0.01,
    )
    arguments = {"preset": {"mode": "compact", "sizing": "content"}}
    bridge.begin_call(
        "layout-before-claim",
        "dashboard.set_widget_layout",
        arguments,
    )

    await bridge.fail_all("IFRAME_CLOSED", "Iframe unloaded.")

    with pytest.raises(FrontendToolBridgeError) as exc_info:
        await bridge.claim_and_wait("dashboard.set_widget_layout", arguments)

    assert exc_info.value.code == "IFRAME_CLOSED"
    assert exc_info.value.details == {
        "stage": "run_termination",
        "code": "IFRAME_CLOSED",
        "retryable": False,
        "writeDispatched": None,
        "sessionId": "session-1",
        "toolCallId": "layout-before-claim",
        "layoutRunId": None,
    }


@pytest.mark.asyncio
async def test_cleanup_fails_claimed_waiter_and_is_idempotent() -> None:
    error_type, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.2)
    bridge = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    bridge.begin_call("tool-1", "navigateTo", {"destination": "datasets"})
    waiter = asyncio.create_task(
        bridge.claim_and_wait("navigateTo", {"destination": "datasets"})
    )
    await asyncio.sleep(0)

    await registry.remove(
        "thread-1", "run-1", code="IFRAME_CLOSED", message="Iframe unloaded."
    )
    await registry.remove("thread-1", "run-1", code="IFRAME_CLOSED")

    with pytest.raises(error_type) as exc_info:
        await waiter
    assert exc_info.value.code == "IFRAME_CLOSED"
    assert registry.pending_count == 0


@pytest.mark.asyncio
async def test_early_result_waits_for_hook_registration() -> None:
    _error, registry_type = load_bridge_types()
    registry = registry_type(
        tool_timeout_seconds=0.2,
        registration_grace_seconds=0.1,
    )
    bridge = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    submission = asyncio.create_task(
        registry.submit("thread-1", "run-1", snapshot_tool_message("tool-early"))
    )
    await asyncio.sleep(0)

    bridge.begin_call("tool-early", "dashboard.capture_current_view", {})
    result = await bridge.claim_and_wait("dashboard.capture_current_view", {})

    assert (await submission).status == "accepted"
    assert json.loads(result.content)["metrics"]["itemCount"] == 4734


@pytest.mark.asyncio
async def test_registry_rejects_session_run_and_tool_mismatches() -> None:
    error_type, registry_type = load_bridge_types()
    registry = registry_type(
        tool_timeout_seconds=0.2,
        registration_grace_seconds=0.01,
    )
    registry.register("thread-1", "run-1", dashboard_context(), dashboard_tools())

    with pytest.raises(error_type) as active_exc:
        registry.register("thread-1", "run-2", dashboard_context(), dashboard_tools())
    with pytest.raises(error_type) as run_exc:
        await registry.submit("thread-1", "run-2", snapshot_tool_message("tool-1"))
    with pytest.raises(error_type) as tool_exc:
        await registry.submit(
            "thread-1", "run-1", snapshot_tool_message("unknown-tool")
        )

    assert active_exc.value.code == "SESSION_MISMATCH"
    assert run_exc.value.code == "SESSION_MISMATCH"
    assert tool_exc.value.code == "SESSION_MISMATCH"
    assert active_exc.value.details == {
        "stage": "session_registration",
        "code": "SESSION_MISMATCH",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "thread-1",
        "toolCallId": None,
        "layoutRunId": None,
    }
    assert run_exc.value.details == {
        "stage": "result_submission",
        "code": "SESSION_MISMATCH",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "thread-1",
        "toolCallId": "tool-1",
        "layoutRunId": None,
    }
    assert tool_exc.value.details == {
        "stage": "result_submission",
        "code": "SESSION_MISMATCH",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "thread-1",
        "toolCallId": "unknown-tool",
        "layoutRunId": None,
    }


@pytest.mark.asyncio
async def test_result_context_and_navigation_target_are_bound_to_call() -> None:
    error_type, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.2)
    bridge = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    bridge.begin_call("tool-stale", "dashboard.capture_current_view", {})
    stale_waiter = asyncio.create_task(
        bridge.claim_and_wait("dashboard.capture_current_view", {})
    )
    await asyncio.sleep(0)
    stale_payload = snapshot_payload(context_version=2)
    stale_message = ToolMessage(
        id="tool-result-tool-stale",
        content=json.dumps(stale_payload, ensure_ascii=False),
        toolCallId="tool-stale",
    )
    with pytest.raises(error_type) as stale_exc:
        await registry.submit("thread-1", "run-1", stale_message)
    assert stale_exc.value.code == "CONTEXT_STALE"
    with pytest.raises(error_type) as waiter_exc:
        await stale_waiter
    assert waiter_exc.value.code == "CONTEXT_STALE"

    bridge.begin_call("tool-nav", "navigateTo", {"destination": "datasets"})
    wrong_target = navigation_tool_message(
        destination="dashboard", path="/dashboard/1024"
    )
    with pytest.raises(error_type) as target_exc:
        await registry.submit("thread-1", "run-1", wrong_target)
    assert target_exc.value.code == "TARGET_NOT_FOUND"


@pytest.mark.asyncio
async def test_navigation_advances_context_before_same_run_dashboard_capture() -> None:
    _error, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.2)
    bridge = registry.register(
        "thread-1", "run-1", dataset_context(version=2), dashboard_tools()
    )
    bridge.begin_call("tool-nav", "navigateTo", {"destination": "dashboard"})
    navigation_waiter = asyncio.create_task(
        bridge.claim_and_wait("navigateTo", {"destination": "dashboard"})
    )
    await asyncio.sleep(0)

    navigation = navigation_tool_message(
        destination="dashboard",
        path="/dashboard/1024",
        context_version=3,
    )
    await registry.submit(
        "thread-1",
        "run-1",
        navigation,
        dashboard_context(version=3),
        dashboard_tools(),
    )
    assert (await navigation_waiter).error is None
    assert bridge.host_context == dashboard_context(version=3)

    bridge.begin_call("tool-capture", "dashboard.capture_current_view", {})
    capture_waiter = asyncio.create_task(
        bridge.claim_and_wait("dashboard.capture_current_view", {})
    )
    await asyncio.sleep(0)
    capture_message = ToolMessage(
        id="tool-result-tool-capture",
        content=json.dumps(snapshot_payload(context_version=3), ensure_ascii=False),
        toolCallId="tool-capture",
    )
    await registry.submit(
        "thread-1",
        "run-1",
        capture_message,
        dashboard_context(version=3),
        dashboard_tools(),
    )

    assert json.loads((await capture_waiter).content)["metrics"]["itemCount"] == 4734


@pytest.mark.asyncio
async def test_shutdown_fails_all_runs_and_clears_registry() -> None:
    error_type, registry_type = load_bridge_types()
    registry = registry_type(tool_timeout_seconds=0.2)
    first = registry.register(
        "thread-1", "run-1", dashboard_context(), dashboard_tools()
    )
    second = registry.register(
        "thread-2", "run-2", dashboard_context(), dashboard_tools()
    )
    first.begin_call("tool-1", "navigateTo", {"destination": "datasets"})
    second.begin_call("tool-2", "navigateTo", {"destination": "datasets"})
    first_waiter = asyncio.create_task(
        first.claim_and_wait("navigateTo", {"destination": "datasets"})
    )
    second_waiter = asyncio.create_task(
        second.claim_and_wait("navigateTo", {"destination": "datasets"})
    )
    await asyncio.sleep(0)

    await registry.shutdown()

    for waiter in (first_waiter, second_waiter):
        with pytest.raises(error_type) as exc_info:
            await waiter
        assert exc_info.value.code == "RUN_ERROR"
    assert registry.pending_count == 0
    assert registry.active_run_count == 0
