import asyncio
import json

import pytest

from app.runtime.contracts import RuntimeFrontendTool


def _tool(
    name: str,
    parameters: dict | None = None,
) -> RuntimeFrontendTool:
    return RuntimeFrontendTool(
        name=name,
        description=name,
        parameters=parameters
        or {"type": "object", "additionalProperties": False},
    )


def test_page_catalog_is_one_resident_plan_in_input_order() -> None:
    from app.agui.claude_tools import plan_native_tools

    tools = [
        _tool("page.get_context"),
        _tool("space.list"),
        _tool("space.member.get_context"),
        _tool("space.dashboard.create_and_open"),
    ]
    plan = plan_native_tools(tools)

    assert plan.resident == tuple(tools)
    assert list(plan.tools) == [
        "mcp__davinci_ui__page__get_context",
        "mcp__davinci_ui__space__list",
        "mcp__davinci_ui__space__member__get_context",
        "mcp__davinci_ui__space__dashboard__create_and_open",
    ]


def test_duplicate_frontend_tool_is_rejected() -> None:
    from app.agui.claude_tools import plan_native_tools

    with pytest.raises(ValueError, match="Duplicate frontend tool"):
        plan_native_tools([_tool("a.b"), _tool("a.b")])


def test_sdk_qualification_collision_is_rejected() -> None:
    from app.agui.claude_tools import plan_native_tools

    with pytest.raises(ValueError, match="collide"):
        plan_native_tools([_tool("a-b"), _tool("a_b")])


def test_subscription_uses_five_native_tools_without_host_entry(settings_factory, tmp_path) -> None:
    from app.runtime.claude import ClaudeAgentRuntime
    from tests.test_runtime_events import runtime_request

    request = runtime_request(tmp_path)
    names = ["get_context", "search_options", "start_draft", "apply_draft", "review_draft"]
    request.frontend_tools = tuple(_tool("space.message_rule." + name) for name in names)
    runtime = ClaudeAgentRuntime(settings_factory(), environ={"PATH": "/usr/bin"})
    plan = runtime._native_tool_plan(request)

    assert plan.resident == request.frontend_tools
    assert [tool.name for tool in plan.tools.values()] == ["space.message_rule." + name for name in names]
    assert all(tool.name != "subscription.configure" for tool in plan.tools.values())


@pytest.mark.asyncio
async def test_one_always_loaded_server_projects_top_level_unions() -> None:
    from mcp import types

    from app.agui.claude_tools import (
        build_deferred_davinci_mcp_server,
        plan_native_tools,
    )

    union_schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "templateRef": {"type": "string"},
        },
        "oneOf": [
            {"required": ["mode"]},
            {"required": ["mode", "templateRef"]},
        ],
        "additionalProperties": False,
    }
    server = build_deferred_davinci_mcp_server(
        plan_native_tools([
            _tool("space.message_rule.start_draft", union_schema),
            _tool("space.message_rule.review_draft"),
        ])
    )
    handler = server["instance"].request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))

    assert server["alwaysLoad"] is True
    assert [tool.name for tool in result.root.tools] == [
        "space__message_rule__start_draft",
        "space__message_rule__review_draft",
    ]
    assert result.root.tools[0].inputSchema == {
        "type": "object",
        "properties": {
            "mode": {"type": "string"},
            "templateRef": {"type": "string"},
        },
        "required": ["mode"],
        "additionalProperties": False,
    }
    assert plan_native_tools([
        _tool("space.message_rule.start_draft", union_schema)
    ]).resident[0].parameters == union_schema


@pytest.mark.asyncio
async def test_semantic_planner_public_schema_accepts_only_strategy() -> None:
    from mcp import types

    from app.agui.claude_tools import build_semantic_grouping_mcp_server

    async def planner(arguments):
        return {"strategy": arguments["strategy"]}

    server = build_semantic_grouping_mcp_server(planner)
    handler = server["instance"].request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))
    advertised = result.root.tools[0]

    assert advertised.inputSchema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {"strategy": {"type": "string", "enum": ["A", "B", "C"]}},
        "required": ["strategy"],
    }
    assert "Host extracts" in advertised.description
    assert "cards" not in advertised.inputSchema["properties"]


@pytest.mark.asyncio
async def test_bridge_layout_handler_rejects_missing_parameters_before_waiting() -> None:
    from ag_ui.core import Tool

    from app.agui.bridge import RunFrontendToolBridge
    from app.agui.claude_tools import _tool_response
    from tests.agui_helpers import dashboard_context

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
    bridge.begin_call("layout-invalid", "dashboard.set_widget_layout", {})

    result = await _tool_response(bridge, "dashboard.set_widget_layout", {})
    payload = json.loads(result["content"][0]["text"])

    assert result["is_error"] is True
    assert payload["error"] == {
        "code": "INVALID_ARGUMENT",
        "message": "Frontend tool arguments failed validation.",
        "retryable": False,
        "layer": "host",
    }
    assert payload["diagnostics"] == {
        "stage": "argument_validation",
        "code": "INVALID_ARGUMENT",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "session-1",
        "toolCallId": "layout-invalid",
        "layoutRunId": None,
    }
    assert bridge.pending_count == 0


@pytest.mark.asyncio
async def test_layout_wait_timeout_is_logged_with_terminal_diagnostics(caplog) -> None:
    from ag_ui.core import Tool

    from app.agui.bridge import RunFrontendToolBridge
    from app.agui.claude_tools import _tool_response
    from tests.agui_helpers import dashboard_context

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
    arguments = {"preset": {"mode": "compact"}}
    bridge.begin_call("layout-call", "dashboard.set_widget_layout", arguments)

    with caplog.at_level("WARNING", logger="app.agui.claude_tools"):
        result = await _tool_response(
            bridge, "dashboard.set_widget_layout", arguments,
        )

    payload = json.loads(result["content"][0]["text"])
    assert result["is_error"] is True
    assert payload["diagnostics"] == {
        "stage": "frontend_wait",
        "code": "TOOL_TIMEOUT",
        "retryable": False,
        "writeDispatched": None,
        "sessionId": "session-1",
        "toolCallId": "layout-call",
        "layoutRunId": None,
    }
    record = next(item for item in caplog.records if item.message == "frontend_layout_failure")
    assert record.layout_diagnostics == payload["diagnostics"]


def test_layout_failure_diagnostics_scans_all_issues_for_terminal_failure() -> None:
    from app.agui.claude_tools import layout_failure_diagnostics

    layout_run_id = "00000000-0000-4000-8000-000000000123"
    diagnostics = layout_failure_diagnostics(
        {
            "status": "error",
            "error": {
                "code": "OUTER_RETRYABLE",
                "message": "outer",
                "retryable": True,
                "layer": "page",
            },
            "issues": [
                {
                    "code": "FIRST_RETRYABLE",
                    "message": "first",
                    "retryable": True,
                    "constraints": {
                        "preflightStage": "measurement",
                        "writeDispatched": False,
                    },
                },
                {
                    "code": "SECOND_TERMINAL",
                    "message": "second",
                    "retryable": False,
                    "constraints": {
                        "preflightStage": "candidate_search",
                        "writeDispatched": False,
                        "layoutRunId": layout_run_id,
                    },
                },
            ],
        },
        session_id="session-1",
        tool_call_id="layout-call",
    )

    assert diagnostics == {
        "stage": "candidate_search",
        "code": "SECOND_TERMINAL",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "session-1",
        "toolCallId": "layout-call",
        "layoutRunId": layout_run_id,
    }


def test_layout_failure_diagnostics_preserves_bounded_direct_frontend_error() -> None:
    from app.agui.claude_tools import layout_failure_diagnostics

    diagnostics = layout_failure_diagnostics(
        {
            "status": "error",
            "error": {
                "code": "EXECUTION_FAILED",
                "message": "Content measurement stalled before flat-group planning",
                "layer": "frontend",
                "cause": "MEASUREMENT_DEADLINE",
                "phase": "grouping_preflight",
                "lastStage": "content_measurement",
                "retryable": False,
            },
            "diagnostics": {"writeDispatched": False},
            "issues": [],
        },
        session_id="session-1",
        tool_call_id="layout-call",
    )

    assert diagnostics == {
        "stage": "grouping_preflight",
        "code": "EXECUTION_FAILED",
        "message": "Content measurement stalled before flat-group planning",
        "layer": "frontend",
        "cause": "MEASUREMENT_DEADLINE",
        "lastStage": "content_measurement",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "session-1",
        "toolCallId": "layout-call",
        "layoutRunId": None,
    }


def test_layout_failure_diagnostics_keeps_direct_error_when_issue_is_present() -> None:
    from app.agui.claude_tools import layout_failure_diagnostics

    diagnostics = layout_failure_diagnostics(
        {
            "status": "error",
            "error": {
                "code": "EXECUTION_FAILED",
                "message": "remote solver unavailable",
                "layer": "page",
                "retryable": False,
                "details": {
                    "code": "LAYOUT_SOLVER_UNAVAILABLE",
                    "lastStage": "solveLayout",
                    "cause": '{"code":"SOLVER_DOWN"}',
                    "writeDispatched": False,
                },
            },
            "issues": [{
                "code": "LAYOUT_SOLVER_UNAVAILABLE",
                "message": "remote solver unavailable",
                "retryable": False,
                "constraints": {
                    "solverFinal": True,
                    "lastStage": "solveLayout",
                    "writeDispatched": False,
                },
            }],
        },
        session_id="session-1",
        tool_call_id="layout-call",
    )

    assert diagnostics is not None
    assert diagnostics["code"] == "LAYOUT_SOLVER_UNAVAILABLE"
    assert diagnostics["message"] == "remote solver unavailable"
    assert diagnostics["layer"] == "page"
    assert diagnostics["cause"] == '{"code":"SOLVER_DOWN"}'
    assert diagnostics["lastStage"] == "solveLayout"
    assert diagnostics["writeDispatched"] is False


@pytest.mark.asyncio
async def test_real_bridge_layout_failure_reaches_user_facing_formatter() -> None:
    from ag_ui.core import Tool, ToolMessage

    from app.agui.bridge import RunFrontendToolBridge
    from app.agui.claude_tools import _tool_response
    from tests.agui_helpers import dashboard_context

    bridge = RunFrontendToolBridge(
        "session-123",
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
    bridge.begin_call("tool-456", "dashboard.set_widget_layout", arguments)
    response_task = asyncio.create_task(
        _tool_response(bridge, "dashboard.set_widget_layout", arguments)
    )
    await asyncio.sleep(0)
    layout_run_id = "00000000-0000-4000-8000-000000000789"
    page_receipt = {
        "status": "error",
        "error": {
            "code": "LAYOUT_SOLVER_INFEASIBLE_CANDIDATES",
            "message": "secret card-15666 payload",
            "retryable": False,
            "layer": "page",
        },
        "issues": [{
            "code": "LAYOUT_SOLVER_INFEASIBLE_CANDIDATES",
            "message": "secret card-15666 payload",
            "retryable": False,
            "constraints": {
                "preflightStage": "candidate_search",
                "writeDispatched": False,
                "layoutRunId": layout_run_id,
            },
        }],
    }
    await bridge.submit(ToolMessage(
        id="result-tool-456",
        toolCallId="tool-456",
        error="LAYOUT_SOLVER_INFEASIBLE_CANDIDATES",
        content=json.dumps(page_receipt),
    ))

    response = await response_task
    receipt = json.loads(response["content"][0]["text"])
    assert receipt["diagnostics"] == {
        "stage": "candidate_search",
        "code": "LAYOUT_SOLVER_INFEASIBLE_CANDIDATES",
        "retryable": False,
        "writeDispatched": False,
        "sessionId": "session-123",
        "toolCallId": "tool-456",
        "layoutRunId": layout_run_id,
    }

    script = """
import {formatToolReceipt} from './web/embed/user-facing-error.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const outputPreview = JSON.parse(input);
process.stdout.write(formatToolReceipt({
  name: 'dashboard.set_widget_layout', state: 'error', outputPreview
}));
"""
    process = await asyncio.create_subprocess_exec(
        "node",
        "--input-type=module",
        "-e",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(json.dumps(response).encode())
    assert process.returncode == 0, stderr.decode()
    rendered = stdout.decode()
    for value in (
        "code=LAYOUT_SOLVER_INFEASIBLE_CANDIDATES",
        "stage=candidate_search",
        "retryable=false",
        "writeDispatched=false",
        "sessionId=session-123",
        "toolCallId=tool-456",
        f"layoutRunId={layout_run_id}",
    ):
        assert value in rendered
    assert "secret" not in rendered
    assert "15666" not in rendered


def test_v2_layout_receipt_allows_only_bounded_top_level_diagnostics() -> None:
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    from app.agui.contracts import CONTRACT_PATH, load_contract_registry

    output = load_contract_registry(
        CONTRACT_PATH.with_name("davinci-agent-v2.json")
    ).get("dashboard.set_widget_layout").output_schema
    receipt = {
        "status": "error",
        "error": {
            "code": "INVALID_ARGUMENT",
            "message": "invalid",
            "retryable": False,
            "layer": "host",
        },
        "issues": [],
        "diagnostics": {
            "stage": "argument_validation",
            "code": "INVALID_ARGUMENT",
            "retryable": False,
            "writeDispatched": False,
            "sessionId": "session-1",
            "toolCallId": "tool-1",
            "layoutRunId": None,
        },
    }
    validator = Draft202012Validator(output)

    validator.validate(receipt)
    untrusted = deepcopy(receipt)
    untrusted["diagnostics"]["privateCardId"] = "15666"
    assert list(validator.iter_errors(untrusted))
    incomplete = deepcopy(receipt)
    incomplete["diagnostics"].pop("toolCallId")
    assert list(validator.iter_errors(incomplete))


@pytest.mark.asyncio
async def test_all_resident_tools_have_model_compatible_top_level_schemas() -> None:
    from copy import deepcopy

    from jsonschema import Draft202012Validator
    from mcp import types

    from app.agui.claude_tools import (
        build_deferred_davinci_mcp_server,
        plan_native_tools,
    )
    from app.agui.contracts import CONTRACT_PATH, load_contract_registry

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    resident = [
        RuntimeFrontendTool(name=contract.action, description=contract.description,
                            parameters=deepcopy(dict(contract.input_schema)))
        for contract in registry.public_contracts if contract.executor == "frontend"
    ]
    originals = deepcopy([item.parameters for item in resident])
    server = build_deferred_davinci_mcp_server(plan_native_tools(resident))
    handler = server["instance"].request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))
    advertised = {item.name: item for item in result.root.tools}
    assert len(advertised) == len(resident)
    incompatible = {
        name: sorted({"oneOf", "anyOf", "allOf"}.intersection(item.inputSchema))
        for name, item in advertised.items()
        if {"oneOf", "anyOf", "allOf"}.intersection(item.inputSchema)
    }
    assert incompatible == {}
    layout = advertised["dashboard__set_widget_layout"]
    assert "groups" in layout.inputSchema["properties"]["preset"]["properties"]
    assert "expectedResourceRevision is mandatory" in layout.description
    assert "3A/3B/3C" in layout.description
    assert "Omit items entirely" in layout.description
    assert len(layout.description) < 2_000
    assert "description" not in layout.inputSchema["properties"]["preset"]
    assert [item.parameters for item in resident] == originals
    from app.agui.claude_tools import (
        _model_facing_frontend_tool,
    )
    for item in resident:
        if not item.name.startswith("space.message_rule."):
            actual = advertised[item.name.replace(".", "__").replace("-", "_")]
            projected = _model_facing_frontend_tool(item)
            assert actual.inputSchema == projected.parameters
            assert actual.description == projected.description
    canonical = registry.get("dashboard.set_widget_layout").input_schema
    validator = Draft202012Validator(canonical)
    validator.validate({"preset": {"mode": "compact", "sizing": "content"}})
    grouped = {"preset": {"mode": "reorder", "orderedWidgetIds": ["a", "b"],
                           "groups": [{"title": "Summary", "widgetIds": ["a", "b"]}],
                           "groupingConfirmed": True, "sizing": "content"},
               "expectedResourceRevision": 8}
    validator.validate(grouped)
    assert list(validator.iter_errors({"preset": grouped["preset"]}))
    grouped["preset"].pop("groupingConfirmed")
    assert list(validator.iter_errors(grouped))


@pytest.mark.asyncio
async def test_real_resident_union_tools_keep_canonical_validation() -> None:
    from mcp import types

    from app.agui.claude_tools import (
        build_deferred_davinci_mcp_server,
        plan_native_tools,
    )
    from app.agui.contracts import CONTRACT_PATH, load_contract_registry

    registry = load_contract_registry(
        CONTRACT_PATH.with_name("davinci-agent-v2.json")
    )
    expected_required = {
        "dashboard.get_widget_config": [],
        "dashboard.apply_widget_spec": ["spec"],
        "space.create": ["type", "name"],
        "space.member.get_context": ["mode"],
        "space.member.apply_changes": ["operation"],
        "space.menu.apply_changes": ["operation"],
        "space.message_rule.search_options": ["kind"],
        "space.message_rule.start_draft": ["mode"],
        "dataset.editor.open": ["mode"],
    }
    resident = [
        RuntimeFrontendTool(
            name=action,
            description=registry.get(action).description,
            parameters=dict(registry.get(action).input_schema),
        )
        for action in expected_required
    ]
    plan = plan_native_tools(resident)
    server = build_deferred_davinci_mcp_server(plan)
    handler = server["instance"].request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest(method="tools/list"))
    advertised = {tool.name: tool for tool in result.root.tools}

    for action, required in expected_required.items():
        sdk_name = action.replace(".", "__")
        assert "oneOf" not in advertised[sdk_name].inputSchema
        assert advertised[sdk_name].inputSchema.get("required", []) == required
    member_description = advertised["space__member__apply_changes"].description
    assert (
        "Parameters by operation: invite -> required [candidateRefs, role], "
        "optional [none]; set_role -> required [memberRef, role], optional "
        "[none]; remove -> required [memberRef], optional "
        "[notifyMember (default true)]."
        in member_description
    )
    assert "Only the fields listed for the selected operation are allowed." in (
        member_description
    )
    option_description = advertised[
        "space__message_rule__search_options"
    ].description
    assert (
        "template -> required [none], optional [query, scene]" in option_description
    )
    assert (
        "field -> required [datasetRef], optional [query, offset, role, isEmployeeAccount]"
        in option_description
    )
    editor_open_description = advertised["dataset__editor__open"].description
    assert "edit -> required [datasetRef]" in editor_open_description
    assert "create -> required [none]" in editor_open_description
    assert (
        "Provide exactly one parameter set: required [widgetId] or required "
        "[widgetIds]."
        in advertised["dashboard__get_widget_config"].description
    )
    assert (
        "Provide exactly one parameter set: required [widgetId] or required "
        "[create]."
        in advertised["dashboard__apply_widget_spec"].description
    )
    menu_tool = advertised["space__menu__apply_changes"]
    assert "create_page" not in menu_tool.inputSchema["properties"][
        "operation"
    ]["enum"]
    assert "space.dashboard.create_and_open" in menu_tool.description
    assert "create_page ->" not in menu_tool.description
    assert all("oneOf" in tool.parameters for tool in plan.resident)


async def _subscription_advertised_tools():
    """Read the actual MCP catalog produced from the current canonical contracts."""
    from copy import deepcopy

    from mcp import types

    from app.agui.claude_tools import (
        build_deferred_davinci_mcp_server,
        plan_native_tools,
    )
    from app.agui.contracts import CONTRACT_PATH, load_contract_registry

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    contracts = [contract for contract in registry.public_contracts
                 if contract.action.startswith("space.message_rule.")]
    resident = [_tool(contract.action, deepcopy(dict(contract.input_schema))) for contract in contracts]
    originals = deepcopy([item.parameters for item in resident])
    server = build_deferred_davinci_mcp_server(plan_native_tools(resident))
    result = await server["instance"].request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list"))
    assert [item.parameters for item in resident] == originals
    return registry, {tool.name: tool for tool in result.root.tools}


@pytest.mark.asyncio
async def test_subscription_model_projection_hides_compatibility_only_and_keeps_all_operations():
    """Modern writes retain their native constraints while legacy calls remain valid."""
    import json
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    from app.agui.claude_tools import _model_compatible_schema

    registry, advertised = await _subscription_advertised_tools()
    legacy = {"richTextMarkdown", "richTextBindings", "includeDataTable", "dataTableQueryRef",
              "dataTableFieldRefs", "dataTableOutputRefs", "dashboardRefs", "widgetRefs"}
    for action in ("start_draft", "apply_draft"):
        canonical = dict(registry.get("space.message_rule." + action).input_schema)
        schema = advertised["space__message_rule__" + action].inputSchema
        Draft202012Validator.check_schema(schema)
        assert "presentation" not in schema["properties"]
        actual = {branch["properties"]["operation"]["const"]: branch
                  for branch in schema["properties"]["operations"]["items"]["oneOf"]}
        original = {branch["properties"]["operation"]["const"]: branch
                    for branch in canonical["properties"]["operations"]["items"]["oneOf"]}
        assert actual.keys() == original.keys()
        assert not legacy.intersection(actual["set_content"]["properties"])
        assert actual["set_content"]["required"] == original["set_content"]["required"]
        assert actual["set_content"]["properties"]["components"] == original["set_content"]["properties"]["components"]
        for name in original.keys() - {"set_content", "set_schedule"}:
            assert actual[name] == original[name]
        assert actual["set_schedule"]["allOf"] == original["set_schedule"]["allOf"]
        assert "daily [times, dailyMode]" in actual["set_schedule"]["description"]
        assert "hourly [hourlyMinute, hourWindowStart, hourWindowEnd]" in actual["set_schedule"]["description"]
        previous = _model_compatible_schema(deepcopy(canonical))
        assert len(json.dumps(schema)) < len(json.dumps(previous)) * .93
        arguments = {"mode": "blank", "scene": "dashboard-push"} if action == "start_draft" else {"expectedRevision": 1}
        arguments["operations"] = [{"operation": "set_content", "richTextMarkdown": "旧正文"}]
        arguments["presentation"] = {"step": "content"}
        Draft202012Validator(canonical).validate(arguments)
        assert list(Draft202012Validator(schema).iter_errors(arguments))


@pytest.mark.asyncio
async def test_subscription_model_projection_accepts_modern_examples_and_seven_content_types():
    """Validate actual documented operations and every native content capability."""
    import json
    from copy import deepcopy
    from pathlib import Path

    from jsonschema import Draft202012Validator

    registry, advertised = await _subscription_advertised_tools()
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "workspaces/davinci-dashboard/.claude/skills/"
                       "configure-subscription-rule/evals/cases.json").read_text())
    for case in cases["nativeExamples"]:
        for call in case["calls"]:
            arguments = deepcopy(call["arguments"])
            arguments.pop("presentation", None)
            schema = advertised[call["action"].replace(".", "__")].inputSchema
            Draft202012Validator(schema).validate(arguments)
            Draft202012Validator(dict(registry.get(call["action"]).input_schema)).validate(arguments)
    components = [
        {"type": "richtext", "markdown": "订单日报"},
        {"type": "data-table", "queryRef": "orders", "outputRefs": ["amount"]},
        {"type": "dashboard-screenshot", "dashboardRef": "dashboard"},
        {"type": "dashboard-chart", "widgetRef": "ai-widget"},
        {"type": "image", "imageRef": "uploaded-image"},
        {"type": "button", "text": "查看", "actionType": "dashboard", "dashboardRef": "dashboard"},
        {"type": "button-group", "items": [{"text": "下载", "actionType": "download",
                                             "queryRef": "orders", "outputRefs": ["amount"]}]},
    ]
    args = {"expectedRevision": 2, "operations": [
        {"operation": "set_content", "title": "日报", "components": components},
        {"operation": "patch_content", "edits": [{"componentRef": "existing", "position": 0}]},
    ]}
    Draft202012Validator(advertised["space__message_rule__apply_draft"].inputSchema).validate(args)
    Draft202012Validator(dict(registry.get("space.message_rule.apply_draft").input_schema)).validate(args)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    {"operation": "set_schedule", "frequency": "daily", "times": ["09:00"], "weekdays": [1]},
    {"operation": "upsert_dataset_query", "datasetRef": "orders",
     "metrics": [{"fieldRef": "amount", "outputKey": "amount"}]},
    {"operation": "set_content", "components": [{"type": "data-table", "outputRefs": ["total"]}]},
    {"operation": "set_content", "components": [{"type": "button", "text": "下载", "actionType": "download"}]},
    {"operation": "set_push_mode", "mode": "group", "queryRef": "orders"},
])
async def test_subscription_model_projection_does_not_weaken_modern_business_boundaries(operation):
    """Both model schema and canonical execution reject malformed modern writes."""
    from jsonschema import Draft202012Validator

    registry, advertised = await _subscription_advertised_tools()
    args = {"expectedRevision": 1, "operations": [operation]}
    canonical = dict(registry.get("space.message_rule.apply_draft").input_schema)
    projected = advertised["space__message_rule__apply_draft"].inputSchema
    assert list(Draft202012Validator(canonical).iter_errors(args))
    assert list(Draft202012Validator(projected).iter_errors(args))


@pytest.mark.parametrize("constraint", [
    {"required": ["legacy"]},
    {"dependentRequired": {"modern": ["legacy"]}},
    {"if": {"required": ["legacy"]}, "then": {"required": ["modern"]}, "else": {"required": ["other"]}},
    {"not": {"properties": {"legacy": {"const": "value"}}}},
])
def test_subscription_model_projection_rejects_unknown_hidden_field_constraints(constraint):
    """A new annotation cannot silently erase a required or unfamiliar rule."""
    from app.agui.subscription_tool_schema import project_subscription_schema

    schema = {"type": "object", "additionalProperties": False,
              "properties": {"legacy": {"type": "string", "x-model-hidden": True},
                             "modern": {"type": "string"}}, **constraint}
    with pytest.raises(ValueError, match="[Hh]idden"):
        project_subscription_schema(schema)


def test_public_name_round_trips_only_resident_prefix() -> None:
    from app.agui.claude_tools import public_name_for_sdk_tool

    assert (
        public_name_for_sdk_tool(
            "mcp__davinci_ui__dashboard__get_structure"
        )
        == "dashboard.get_structure"
    )
    with pytest.raises(ValueError):
        public_name_for_sdk_tool(
            "mcp__davinci_ui_more__dashboard__get_structure"
        )


def test_system_prompt_describes_resident_tools_without_search() -> None:
    from app.agui.claude_tools import native_frontend_system_prompt

    prompt = native_frontend_system_prompt()
    assert "mcp__davinci_ui__*" in prompt
    assert "already resident" in prompt
    assert "Business decisions come before tool preparation" in prompt
    assert "user has already named distinguishable alternatives" in prompt
    assert "before any tool call, including Skill or a list lookup" in prompt
    assert "explicitly requests independent work first" in prompt
    assert "Discovery-only and already-confirmed tasks" in prompt
    assert "ToolSearch" not in prompt
    assert "davinci_ui_more" not in prompt
