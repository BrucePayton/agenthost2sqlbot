import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig
from jsonschema import Draft202012Validator

from app.agui.bridge import FrontendToolBridgeError, RunFrontendToolBridge
from app.agui.catalog import DEFAULT_REGISTRY
from app.agui.contracts import CONTRACT_PATH, ToolContract, load_contract_registry
from app.agui.models import CAPTURE_TOOL, NAVIGATE_TOOL
from app.agui.snapshot_artifacts import (
    SnapshotArtifactNotFound,
    SnapshotArtifactStore,
    read_widget_data,
)
from app.agui.subscription_tool_schema import project_subscription_schema
from app.runtime.contracts import RuntimeFrontendTool

DAVINCI_SERVER_NAME = "davinci_ui"
DAVINCI_CORE_SERVER_NAME = "davinci_core"
DAVINCI_PLANNER_SERVER_NAME = "davinci_planner"
LEGACY_PUBLIC_TO_SDK_TOOL = {NAVIGATE_TOOL.name: "navigate_to"}
NATIVE_FRONTEND_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_V2_CONTRACT_PATH = CONTRACT_PATH.with_name("davinci-agent-v2.json")
logger = logging.getLogger(__name__)
_CANONICAL_LAYOUT_PARAMETERS = dict(
    load_contract_registry(_V2_CONTRACT_PATH).get(
        "dashboard.set_widget_layout"
    ).input_schema
)
_CANONICAL_LAYOUT_VALIDATOR = Draft202012Validator(_CANONICAL_LAYOUT_PARAMETERS)

READ_ONLY_FRONTEND_TOOLS: frozenset[str] = frozenset(
    action
    for registry in (
        load_contract_registry(),
        load_contract_registry(_V2_CONTRACT_PATH),
    )
    for action, contract in registry.contracts_by_action.items()
    if contract.risk == "read"
)

_LAYOUT_MODEL_DESCRIPTION = """Execute one atomic dashboard layout operation. Use preset only for beautification; omit root-level items entirely (never send items:[] with preset). Compact uses {mode:compact,sizing:content}; align uses {mode:align,sizing:preserve}; organize uses {mode:organize,sizing:content}. For 3A/3B/3C, read the complete structure once, then call the Host semantic planner and use its single result immediately with preset:{mode:reorder,sizing:content,orderedWidgetIds,groups,groupingConfirmed:true}. Choosing 3A/3B/3C already authorizes rebuilding native flat groups, so do not ask a second grouping or regroup question. Put every existing native flat container ID in regroup.containerWidgetIds with confirmed:true; exclude Tabs and preserve their membership. orderedWidgetIds must contain every effective root exactly once. Group membership comes from the structure receipt's compact data-configuration profiles and actual card types, never from a business-dimension whitelist, title similarity, stage name or numeric card limit. stageRank controls reading order only. Group members must be unique and contiguous. If the user explicitly says only reorder/no grouping, omit groups, groupingConfirmed and regroup. expectedResourceRevision is mandatory whenever groups or regroup are present. Never calculate coordinates. sizing:content reuses the existing compact algorithm inside every new flat group. One group fills 24 columns; only two consecutive eligible narrow groups may share a row at 12 columns each. The frontend validates, computes geometry and saves once. Provide exactly one parameter set: items or preset; preset means Omit items entirely."""


def native_frontend_sdk_name(public_name: str) -> str:
    if not NATIVE_FRONTEND_TOOL_NAME.fullmatch(public_name):
        raise ValueError(f"Invalid frontend tool name: {public_name!r}")
    sdk_name = public_name.replace(".", "__").replace("-", "_")
    return f"mcp__{DAVINCI_SERVER_NAME}__{sdk_name}"


@dataclass(frozen=True, slots=True)
class NativeToolPlan:
    """Materialize one page-scoped resident frontend tool catalog."""

    tools: dict[str, RuntimeFrontendTool]
    resident: tuple[RuntimeFrontendTool, ...]


def plan_native_tools(
    frontend_tools: Sequence[RuntimeFrontendTool],
) -> NativeToolPlan:
    current: dict[str, RuntimeFrontendTool] = {}
    tools: dict[str, RuntimeFrontendTool] = {}
    for frontend_tool in frontend_tools:
        if frontend_tool.name in current:
            raise ValueError(f"Duplicate frontend tool: {frontend_tool.name}")
        current[frontend_tool.name] = frontend_tool
        qualified = native_frontend_sdk_name(frontend_tool.name)
        if qualified in tools:
            raise ValueError("Frontend tool names collide after SDK qualification")
        tools[qualified] = frontend_tool
    return NativeToolPlan(tools=tools, resident=tuple(frontend_tools))


def native_frontend_tool_map(
    frontend_tools: Sequence[RuntimeFrontendTool],
) -> dict[str, RuntimeFrontendTool]:
    return plan_native_tools(frontend_tools).tools


def layout_argument_validation_error(arguments: Mapping[str, Any]) -> str | None:
    """Validate layout writes against the Host-owned canonical contract."""
    error = next(_CANONICAL_LAYOUT_VALIDATOR.iter_errors(arguments), None)
    if error is None:
        return None
    return "Frontend tool arguments failed validation."


def _layout_error_payload(
    *,
    code: str,
    message: str,
    stage: str,
    retryable: bool,
    write_dispatched: bool | None,
    session_id: str | None,
    tool_call_id: str | None,
    layout_run_id: str | None = None,
) -> dict[str, Any]:
    diagnostics = {
        "stage": stage,
        "code": code,
        "retryable": retryable,
        "writeDispatched": write_dispatched,
        "sessionId": session_id,
        "toolCallId": tool_call_id,
        "layoutRunId": layout_run_id,
    }
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "layer": "host",
        },
        "issues": [],
        "diagnostics": diagnostics,
    }


def _log_layout_failure(diagnostics: Mapping[str, Any]) -> None:
    logger.warning(
        "frontend_layout_failure",
        extra={"layout_diagnostics": dict(diagnostics)},
    )


def _diagnostic_text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


def layout_failure_diagnostics(
    content: str | Mapping[str, Any],
    *,
    session_id: str | None,
    tool_call_id: str | None,
    fallback_code: str | None = None,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(content) if isinstance(content, str) else dict(content)
    except (TypeError, ValueError):
        return None
    if payload.get("status") != "error":
        return None
    error = payload.get("error")
    error = error if isinstance(error, Mapping) else {}
    error_details = error.get("details")
    error_details = error_details if isinstance(error_details, Mapping) else {}
    issues = payload.get("issues")
    issue_items = (
        [issue for issue in issues if isinstance(issue, Mapping)]
        if isinstance(issues, list)
        else []
    )
    first_issue = issue_items[0] if issue_items else None
    blocking_issue = next(
        (issue for issue in issue_items if issue.get("retryable") is False),
        None,
    )
    selected_issue = blocking_issue or first_issue
    constraints = (
        selected_issue.get("constraints")
        if isinstance(selected_issue, Mapping)
        and isinstance(selected_issue.get("constraints"), Mapping)
        else {}
    )
    supplied = payload.get("diagnostics")
    supplied = supplied if isinstance(supplied, Mapping) else {}
    data = payload.get("data")
    summary = data.get("summary") if isinstance(data, Mapping) else None
    issue_constraints = [
        issue.get("constraints")
        for issue in issue_items
        if isinstance(issue.get("constraints"), Mapping)
    ]
    layout_run_id = (
        constraints.get("layoutRunId")
        or next((
            item.get("layoutRunId")
            for item in issue_constraints
            if item.get("layoutRunId")
        ), None)
        or (summary.get("layoutRunId") if isinstance(summary, Mapping) else None)
        or supplied.get("layoutRunId")
    )
    retryable_values = [
        value
        for value in (
            error.get("retryable"),
            *(issue.get("retryable") for issue in issue_items),
            supplied.get("retryable"),
        )
        if isinstance(value, bool)
    ]
    retryable = (
        False if False in retryable_values
        else True if True in retryable_values
        else None
    )
    write_values = [
        value
        for value in (
            *(item.get("writeDispatched") for item in issue_constraints),
            error_details.get("writeDispatched"),
            supplied.get("writeDispatched"),
        )
        if isinstance(value, bool)
    ]
    write_dispatched = (
        True if True in write_values
        else False if False in write_values
        else None
    )
    diagnostics = {
        "stage": constraints.get("preflightStage") or (
            error.get("phase")
        ) or supplied.get("stage") or "frontend_result",
        "code": (
            blocking_issue.get("code") if blocking_issue is not None else None
        ) or error.get("code") or (
            first_issue.get("code") if first_issue is not None else None
        ) or supplied.get("code") or fallback_code,
        "retryable": retryable,
        "writeDispatched": write_dispatched,
        "sessionId": session_id or supplied.get("sessionId"),
        "toolCallId": tool_call_id or supplied.get("toolCallId"),
        "layoutRunId": layout_run_id,
    }
    direct_fields = {
        "message": _diagnostic_text(
            error.get("message")
            or (selected_issue.get("message") if selected_issue else None)
            or error_details.get("message")
            or supplied.get("message")
        ),
        "layer": _diagnostic_text(
            error.get("layer") or error_details.get("layer") or supplied.get("layer"),
            80,
        ),
        "cause": _diagnostic_text(
            error.get("cause") or error_details.get("cause") or supplied.get("cause"),
            200,
        ),
        "lastStage": _diagnostic_text(
            error.get("lastStage")
            or error_details.get("lastStage")
            or constraints.get("lastStage")
            or supplied.get("lastStage"),
            100,
        ),
    }
    if not issue_items or error_details:
        diagnostics.update({key: value for key, value in direct_fields.items() if value})
    return diagnostics


def _create_deferred_frontend_handler(
    frontend_tool: RuntimeFrontendTool,
) -> SdkMcpTool[Any]:
    sdk_name = native_frontend_sdk_name(frontend_tool.name).removeprefix(
        f"mcp__{DAVINCI_SERVER_NAME}__"
    )
    if frontend_tool.name.startswith("space.message_rule."):
        parameters = deepcopy(frontend_tool.parameters)
        parameters = project_subscription_schema(parameters)
        description = _model_compatible_description(
            frontend_tool.description,
            parameters,
        )
        parameters = _model_compatible_schema(parameters)
    else:
        model_tool = _model_facing_frontend_tool(frontend_tool)
        parameters = deepcopy(model_tool.parameters)
        description = model_tool.description

    @tool(sdk_name, description, parameters)
    async def deferred_handler(_arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Frontend tool execution was not deferred by the runtime.",
                }
            ],
            "is_error": True,
        }

    return deferred_handler


def _without_schema_annotations(value: Any) -> Any:
    """Remove prose that duplicates the skill while retaining validation shape."""
    if isinstance(value, dict):
        return {
            key: _without_schema_annotations(item)
            for key, item in value.items()
            if key not in {"description", "$comment", "examples"}
        }
    if isinstance(value, list):
        return [_without_schema_annotations(item) for item in value]
    return value


def _model_facing_frontend_tool(
    frontend_tool: RuntimeFrontendTool,
) -> RuntimeFrontendTool:
    """Project verbose canonical docs to the smallest unambiguous model surface."""
    parameters = deepcopy(frontend_tool.parameters)
    description = frontend_tool.description
    if frontend_tool.name == "dashboard.set_widget_layout":
        description = _LAYOUT_MODEL_DESCRIPTION
        parameters = _without_schema_annotations(parameters)
    compatible_description = _model_compatible_description(description, parameters)
    compatible_parameters = _model_compatible_schema(parameters)
    return RuntimeFrontendTool(
        name=frontend_tool.name,
        description=compatible_description,
        parameters=compatible_parameters,
    )


def _model_field_label(
    name: str,
    properties: Mapping[str, Any],
    root_properties: Any,
) -> str:
    """Include a canonical default without duplicating branch prose."""
    schema = properties.get(name, {})
    if not isinstance(schema, Mapping) or "default" not in schema:
        schema = (
            root_properties.get(name, {})
            if isinstance(root_properties, Mapping)
            else {}
        )
    if isinstance(schema, Mapping) and "default" in schema:
        default = json.dumps(schema["default"], ensure_ascii=False).lower()
        return f"{name} (default {default})"
    return name


def _model_compatible_description(
    description: str,
    parameters: Mapping[str, Any],
) -> str:
    """Preserve each branch requirement after model-facing schema flattening."""
    branches = parameters.get("oneOf")
    if not isinstance(branches, list) or not branches:
        return description
    branches = [
        branch
        for branch in branches
        if isinstance(branch, Mapping) and branch.get("deprecated") is not True
    ]
    if not branches:
        return description
    first_branch = branches[0]
    first_properties = first_branch.get("properties", {})
    if not isinstance(first_properties, Mapping):
        return description
    discriminator = next(
        (
            name
            for name, schema in first_properties.items()
            if isinstance(schema, Mapping)
            and "const" in schema
            and all(
                isinstance(branch, Mapping)
                and isinstance(branch.get("properties"), Mapping)
                and isinstance(branch["properties"].get(name), Mapping)
                and "const" in branch["properties"][name]
                for branch in branches
            )
        ),
        None,
    )
    if discriminator is None:
        alternatives: list[str] = []
        for branch in branches:
            required = branch.get("required", [])
            if not isinstance(required, list) or not all(
                isinstance(name, str) for name in required
            ):
                return description
            required_text = ", ".join(required) if required else "none"
            alternative = f"required [{required_text}]"
            if alternative not in alternatives:
                alternatives.append(alternative)
        if len(alternatives) < 2:
            return description
        return (
            f"{description} Provide exactly one parameter set: "
            f"{' or '.join(alternatives)}."
        )

    root_properties = parameters.get("properties", {})
    clauses: list[str] = []
    discriminator_values: list[Any] = []
    for branch in branches:
        properties = branch["properties"]
        value = properties[discriminator]["const"]
        if value in discriminator_values:
            return description
        discriminator_values.append(value)
        required = [
            name
            for name in branch.get("required", [])
            if name != discriminator
        ]
        optional = [
            name
            for name in properties
            if name != discriminator and name not in required
        ]

        required_text = ", ".join(required) if required else "none"
        optional_text = (
            ", ".join(
                _model_field_label(name, properties, root_properties)
                for name in optional
            )
            if optional
            else "none"
        )
        clauses.append(
            f"{value} -> required [{required_text}], optional [{optional_text}]"
        )
    return (
        f"{description} Parameters by {discriminator}: {'; '.join(clauses)}. "
        f"Only the fields listed for the selected {discriminator} are allowed."
    )


def _model_compatible_schema(
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Expose union tools without weakening canonical execution validation.

    Claude Code and third-party Anthropic-compatible models can omit MCP tools
    whose input schema has top-level combinators from the model-visible action
    catalog.  The frontend still validates calls against the untouched
    canonical contract; this projection only affects the schema advertised by
    the in-process MCP server. Conditional allOf requirements are documented in
    the tool description and remain mandatory at execution time.
    """
    conjunction = parameters.get("allOf")
    if isinstance(conjunction, list) and conjunction and all(
        isinstance(branch, dict)
        and "if" in branch and "then" in branch
        and set(branch).issubset({"if", "then", "else"})
        for branch in conjunction
    ):
        # Strip only conditional assertions, never field definitions or required
        # fields from a structural allOf that would need a different projection.
        parameters.pop("allOf")
    branches = parameters.pop("oneOf", None)
    if not isinstance(branches, list) or not branches:
        return parameters
    active_branches = [
        branch
        for branch in branches
        if isinstance(branch, dict) and branch.get("deprecated") is not True
    ]
    if not active_branches:
        active_branches = [
            branch for branch in branches if isinstance(branch, dict)
        ]
    required_sets = [
        set(branch.get("required", []))
        for branch in active_branches
    ]
    common_required = (
        set.intersection(*required_sets) if required_sets else set()
    )
    required = list(parameters.get("required", []))
    properties = parameters.get("properties", {})
    if isinstance(properties, dict):
        first_properties = active_branches[0].get("properties", {})
        if isinstance(first_properties, dict):
            discriminator = next(
                (
                    name
                    for name, schema in first_properties.items()
                    if isinstance(schema, dict)
                    and "const" in schema
                    and all(
                        isinstance(branch.get("properties"), dict)
                        and isinstance(branch["properties"].get(name), dict)
                        and "const" in branch["properties"][name]
                        for branch in active_branches
                    )
                ),
                None,
            )
            if discriminator is not None:
                root_discriminator = properties.get(discriminator)
                if (
                    isinstance(root_discriminator, dict)
                    and isinstance(root_discriminator.get("enum"), list)
                ):
                    allowed_values = [
                        branch["properties"][discriminator]["const"]
                        for branch in active_branches
                    ]
                    root_discriminator["enum"] = [
                        value
                        for value in root_discriminator["enum"]
                        if value in allowed_values
                    ]
        required.extend(
            name
            for name in properties
            if name in common_required and name not in required
        )
    if required:
        parameters["required"] = required
    return parameters


def build_deferred_davinci_mcp_server(
    plan: NativeToolPlan,
) -> McpSdkServerConfig:
    server = create_sdk_mcp_server(
        DAVINCI_SERVER_NAME,
        version="2.0.0",
        tools=[
            _create_deferred_frontend_handler(item)
            for item in plan.resident
        ],
    )
    server["alwaysLoad"] = True  # type: ignore[typeddict-unknown-key]
    return server


def build_semantic_grouping_mcp_server(
    planner: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]],
) -> McpSdkServerConfig:
    """Expose a tiny host-side semantic planner, separate from frontend writes."""
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy": {"type": "string", "enum": ["A", "B", "C"]},
        },
        "required": ["strategy"],
    }

    @tool(
        "plan_semantic_grouping",
        "For dashboard option 3 only. Pass only the selected A/B/C strategy after one "
        "complete structure read. Host extracts visible effective cards and their "
        "compact data-configuration profiles, groups by configuration relationship "
        "and actual card type, asks AI once only for ambiguities, validates the final "
        "arguments, and schedules dashboard.set_widget_layout directly. Do not call "
        "the layout tool after this planner succeeds.",
        schema,
    )
    async def plan_semantic_grouping(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await planner(arguments)
        except Exception as exc:  # noqa: BLE001 - MCP failures become bounded tool errors.
            message = str(exc)
            code = (
                "STRUCTURE_READ_REQUIRED"
                if message.startswith("STRUCTURE_READ_REQUIRED:")
                else "SEMANTIC_PLANNING_FAILED"
            )
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "status": "error",
                    "error": {"code": code, "message": message[:500]},
                }, ensure_ascii=False, separators=(",", ":"))}],
                "is_error": True,
            }
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            }],
            "is_error": False,
        }

    server = create_sdk_mcp_server(
        DAVINCI_PLANNER_SERVER_NAME,
        version="1.0.0",
        tools=[plan_semantic_grouping],
    )
    server["alwaysLoad"] = True  # type: ignore[typeddict-unknown-key]
    return server


def native_frontend_system_prompt() -> str:
    """Prioritize answerable business choices before native tool preparation."""
    return f"""Authenticated Davinci page tools:
- Page tools named mcp__{DAVINCI_SERVER_NAME}__* are already resident for the current page.
- Business decisions come before tool preparation. If the requested action is unclear, ask what to do. If a choice is required and the user has already named distinguishable alternatives, ask which one before any tool call, including Skill or a list lookup. Neither a lookup nor an execution reference can supply the user's choice. Otherwise read only missing facts needed to give answerable options, then ask and end the turn waiting for the user. Execution refs, permissions and configuration can be checked after selection.
- For mixed tasks, resolve business choices before preparatory container or directory writes, unless the user explicitly requests independent work first. After a tool result provides sufficient choice evidence, ask and wait; do not infer the user's intent by inspecting unrelated configuration, querying values or trying configurations.
- Subscription configuration can apply independent, unambiguous schedule or content settings while a separate business choice remains open; preserve that choice and do not guess its answer.
- Discovery-only and already-confirmed tasks should deliver or execute directly without inventing another choice.
- Choose tools from their names, descriptions and JSON schemas according to the user's goal.
- Treat Page State and structured Tool Results as the source of truth.
- Reuse returned IDs and opaque references exactly; never invent page state or data.
- Report an operation as complete only after its Tool Result succeeds.
- Dataset capability availability comes from the current page catalog and tool receipts. A Skill snapshot saying auxiliary sources or source filters are not yet integrated may be outdated: when dataset.editor.apply_draft is available, it supports both through sources, relations and sources[].filters. Preserve the Skill's business constraints and user authorization; this does not grant unavailable tools or bypass native validation. Ordinary source filters belong to the editor draft, not the read-only marketplace relationship query.
- For dataset editing, discover sources with dataset.marketplace.search or davinci_data catalog tools. If already on the editor, read dataset.editor.get_context instead of opening it again. EDITOR_ALREADY_OPEN requires get_context, not closing or discarding the existing draft. Read get_context and get_source_fields before apply_draft: it atomically replaces the complete draft, so preserve unrelated sources, fields and filters and reuse the returned draftRevision. Before configuring each auxiliary join, call dataset.editor.get_join_candidates with the intended main fields and filters; choose only backend-authorized common-dimension pairs and never guess join keys.
- Dataset filters: ordinary fieldIds come from fields; isQueryVar=true is allowed only for widget sources and exact entries returned in queryVars. Query variables are filters only, never output fields or join keys. A required date field is not automatically a query variable. Ask for a missing required date range before apply_draft; never invent dates, remove required filters or submit an empty between range. A variable absent from the returned queryVars is an invalid selector, not proof of an ACL denial.
- Dataset completion: requested fieldIds are intentions, not evidence of selected fields. Only a successful apply_draft result whose sources contain the requested fieldIds proves the draft update. Only validation.valid=true for that current draft proves validation; a successful tool envelope alone is insufficient. On committed=false, say the replacement did not take effect and never claim its fields were selected or validated. If state is uncertain, read get_context before reporting progress or retrying. A corrected retry needs fresh evidence of success; never repeat unchanged invalid input.
- dataset.editor.validate and preview inspect the draft without persistence. dataset.editor.save_draft only opens the native confirmation dialog; status opened is never evidence of a saved dataset. Report that user confirmation remains. Never call the inactive legacy dataset.editor.save or invent a save receipt.
"""


def sdk_qualified_name(public_name: str) -> str:
    try:
        contract = DEFAULT_REGISTRY.get(public_name)
    except KeyError as exc:
        raise ValueError(f"Unknown Davinci frontend tool: {public_name}") from exc
    except ValueError:
        try:
            sdk_name = LEGACY_PUBLIC_TO_SDK_TOOL[public_name]
        except KeyError as exc:
            raise ValueError(f"Unknown Davinci frontend tool: {public_name}") from exc
        return f"mcp__{DAVINCI_SERVER_NAME}__{sdk_name}"
    if contract.executor != "frontend" or not contract.public:
        raise ValueError(f"Unknown Davinci frontend tool: {public_name}")
    return f"mcp__{DAVINCI_SERVER_NAME}__{contract.sdk_tool_name}"


def public_name_for_sdk_tool(qualified_name: str) -> str:
    prefix = f"mcp__{DAVINCI_SERVER_NAME}__"
    if not qualified_name.startswith(prefix):
        raise ValueError(f"Unknown Davinci SDK tool: {qualified_name}")
    sdk_name = qualified_name.removeprefix(prefix)
    for contract in DEFAULT_REGISTRY.public_contracts:
        if contract.executor == "frontend" and contract.sdk_tool_name == sdk_name:
            return contract.action
    for public_name, legacy_sdk_name in LEGACY_PUBLIC_TO_SDK_TOOL.items():
        if legacy_sdk_name == sdk_name:
            return public_name
    raise ValueError(f"Unknown Davinci SDK tool: {qualified_name}")


def is_davinci_sdk_tool(qualified_name: str) -> bool:
    try:
        public_name_for_sdk_tool(qualified_name)
    except ValueError:
        return False
    return True


async def _tool_response(
    bridge: RunFrontendToolBridge,
    public_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if public_name == "dashboard.set_widget_layout":
        validation_message = layout_argument_validation_error(arguments)
        if validation_message is not None:
            bridge_error = bridge.reject_call(
                public_name,
                arguments,
                code="INVALID_ARGUMENT",
                message=validation_message,
                stage="argument_validation",
                retryable=False,
                write_dispatched=False,
            )
            payload = _layout_error_payload(
                code="INVALID_ARGUMENT",
                message=validation_message,
                stage="argument_validation",
                retryable=False,
                write_dispatched=False,
                session_id=bridge.thread_id,
                tool_call_id=bridge_error.details.get("toolCallId"),
            )
            _log_layout_failure(payload["diagnostics"])
            return {
                "content": [{"type": "text", "text": json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":"),
                )}],
                "is_error": True,
            }
    try:
        result = await bridge.claim_and_wait(public_name, arguments)
    except FrontendToolBridgeError as exc:
        if public_name == "dashboard.set_widget_layout" and exc.details:
            _log_layout_failure(exc.details)
            bridge.record_layout_failure(exc.details)
        return {
            "content": [{"type": "text", "text": exc.to_tool_json()}],
            "is_error": True,
        }
    diagnostics = None
    if public_name == "dashboard.set_widget_layout":
        diagnostics = layout_failure_diagnostics(
            result.content,
            session_id=bridge.thread_id,
            tool_call_id=result.tool_call_id,
            fallback_code=result.error,
        )
        if diagnostics is not None:
            _log_layout_failure(diagnostics)
            bridge.record_layout_failure(diagnostics)
    response_content = result.content
    if diagnostics is not None:
        try:
            receipt = json.loads(result.content)
        except (TypeError, ValueError):
            receipt = None
        if isinstance(receipt, dict):
            receipt["diagnostics"] = diagnostics
            response_content = json.dumps(
                receipt,
                ensure_ascii=False,
                separators=(",", ":"),
            )
    return {
        "content": [{"type": "text", "text": response_content}],
        "is_error": result.error is not None or diagnostics is not None,
    }


def build_davinci_tools(
    bridge: RunFrontendToolBridge,
) -> list[SdkMcpTool[Any]]:
    contracts = []
    for public_name in bridge.public_tool_names:
        try:
            contract = DEFAULT_REGISTRY.get(public_name)
        except ValueError:
            continue
        if contract.public and contract.executor == "frontend":
            contracts.append(contract)
    tools = create_frontend_tool_handlers(contracts, bridge)
    if NAVIGATE_TOOL.name in bridge.public_tool_names:

        @tool(
            LEGACY_PUBLIC_TO_SDK_TOOL[NAVIGATE_TOOL.name],
            NAVIGATE_TOOL.description,
            NAVIGATE_TOOL.parameters,
        )
        async def navigate_to(arguments: dict[str, Any]) -> dict[str, Any]:
            return await _tool_response(bridge, NAVIGATE_TOOL.name, arguments)

        tools.append(navigate_to)

    return tools


def create_frontend_tool_handler(
    contract: ToolContract,
    bridge: RunFrontendToolBridge,
) -> SdkMcpTool[Any]:
    if not contract.public or contract.executor != "frontend":
        raise ValueError("davinci_ui accepts only public frontend contracts")

    @tool(
        contract.sdk_tool_name,
        contract.description,
        dict(contract.input_schema),
    )
    async def generated_handler(arguments: dict[str, Any]) -> dict[str, Any]:
        return await _tool_response(bridge, contract.action, arguments)

    return generated_handler


def create_frontend_tool_handlers(
    contracts: Sequence[ToolContract],
    bridge: RunFrontendToolBridge,
) -> list[SdkMcpTool[Any]]:
    return [create_frontend_tool_handler(contract, bridge) for contract in contracts]


def create_davinci_ui_mcp_server(
    contracts: Sequence[ToolContract],
    bridge: RunFrontendToolBridge,
) -> McpSdkServerConfig:
    handlers = create_frontend_tool_handlers(contracts, bridge)
    return create_sdk_mcp_server(
        DAVINCI_SERVER_NAME,
        version="1.0.0",
        tools=handlers,
    )


def build_davinci_mcp_server(
    bridge: RunFrontendToolBridge,
) -> McpSdkServerConfig:
    return create_sdk_mcp_server(
        DAVINCI_SERVER_NAME,
        version="1.0.0",
        tools=build_davinci_tools(bridge),
    )


def build_davinci_core_mcp_server(
    store: SnapshotArtifactStore,
    *,
    owner_key: str,
    page_instance_id: str,
    resource_id: str,
) -> McpSdkServerConfig:
    contract = DEFAULT_REGISTRY.get("dashboard.get_widget_data")

    @tool(
        contract.sdk_tool_name,
        contract.description,
        dict(contract.input_schema),
    )
    async def get_widget_data(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            content = read_widget_data(
                store,
                owner_key=owner_key,
                page_instance_id=page_instance_id,
                resource_id=resource_id,
                arguments=arguments,
            )
        except (SnapshotArtifactNotFound, ValueError):
            return {
                "content": [
                    {"type": "text", "text": '{"summary":"Snapshot unavailable"}'}
                ],
                "is_error": True,
            }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        content, ensure_ascii=False, separators=(",", ":")
                    ),
                }
            ],
            "is_error": False,
        }

    return create_sdk_mcp_server(
        DAVINCI_CORE_SERVER_NAME,
        version="1.0.0",
        tools=[get_widget_data],
    )


def frontend_tool_system_prompt(bridge: RunFrontendToolBridge) -> str:
    available = ", ".join(bridge.public_tool_names)
    legacy_actions = {CAPTURE_TOOL.name, NAVIGATE_TOOL.name}
    if any(name not in legacy_actions for name in bridge.public_tool_names):
        return f"""Embedded Davinci frontend contract:
- The active page exposes only these frontend tools: {available}.
- Choose and compose the advertised tools according to the user's intent.
- Treat structured Tool Results as the source of truth for current page state and data.
- Reuse opaque references and entity IDs exactly as returned by tools; never invent them.
- Report an operation as complete only after its Tool Result succeeds.
- Never invent page state or dashboard values that a Tool Result did not return.
"""
    return f"""Embedded Davinci frontend contract:
- The active page exposes only these frontend tools: {available}.
- To interpret the dashboard currently visible to the user, you must call dashboard.capture_current_view before stating dashboard values or conclusions.
- If the active page is not a dashboard and the user asks for a dashboard interpretation, first call navigateTo with destination dashboard. After navigation succeeds, call dashboard.capture_current_view in the same Run.
- Never infer dashboard values when capture fails or is unavailable.
- To switch the Davinci page, call navigateTo with the requested destination.
- Only report navigation as complete after navigateTo returns a successful result.
"""
