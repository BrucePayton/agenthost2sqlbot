import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from ag_ui.core import Tool
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from pydantic.alias_generators import to_camel

from app.agui.catalog import DEFAULT_REGISTRY, to_ag_ui_tool

logger = logging.getLogger(__name__)

MAX_TOOL_RESULT_BYTES = 64 * 1024
MAX_NATIVE_TOOLS = 64
MAX_NATIVE_SCHEMA_BYTES = 32 * 1024
MAX_NATIVE_TOOL_DESCRIPTION_CHARS = 8000
NATIVE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

# 页面提供的 AG-UI RunAgentInput.context 是不可信输入：只接受受限的名字和长度。
# 与同组 validate_native_* 不同，这里超限只丢弃并记日志、不抛 422——预注入上下文是
# 锦上添花，不能让一个坏条目把整个 Run 打回；也不能让它写进 Turn 事件或撑爆 Runner 请求。
CONTEXT_ITEM_NAME = re.compile(r"^[a-z_]{1,40}$")
MAX_CONTEXT_ITEMS = 5
MAX_CONTEXT_VALUE_CHARS = 4000
# 值里的尖括号一律换成全角：注入块用 <davinci_context> 包裹，组件名之类的页面数据
# 不能自带一个 `</davinci_context>` 去伪造标签边界。逐字符替换，长度不变，重复执行等价。
CONTEXT_VALUE_ESCAPES = str.maketrans({"<": "＜", ">": "＞"})

CAPTURE_TOOL = Tool(
    name="dashboard.capture_current_view",
    description="Capture the dashboard exactly as the user currently sees it.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
)
NAVIGATE_TOOL = Tool(
    name="navigateTo",
    description="Navigate the Davinci host to an allowed application view.",
    parameters={
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "enum": ["dashboard", "datasets"],
            },
            "resourceId": {"type": "string"},
        },
        "required": ["destination"],
        "additionalProperties": False,
    },
)

_TOOL_CATALOG = {
    CAPTURE_TOOL.name: CAPTURE_TOOL,
    NAVIGATE_TOOL.name: NAVIGATE_TOOL,
}


class StrictCamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class HostContext(StrictCamelModel):
    page_type: Literal["dashboard", "dataset"] | None = None
    resource_id: str | None = None
    context_version: int = Field(ge=0)
    supported_capabilities: list[Literal["dashboard.capture_current_view"]] = Field(
        default_factory=list
    )
    supported_commands: list[Literal["navigateTo"]] = Field(default_factory=list)
    protocol_version: Literal["1.0"] | None = None
    page_instance_id: str | None = None
    route: dict[str, Any] | None = None
    view_mode: dict[str, Any] | None = None
    permissions: dict[str, Any] | None = None
    page_state: dict[str, Any] | None = None
    supported_actions: list[str] = Field(default_factory=list)

    @property
    def is_v1(self) -> bool:
        return self.protocol_version == "1.0"

    @model_validator(mode="after")
    def validate_page_contract(self) -> "HostContext":
        if self.is_v1:
            if not self.page_instance_id or not isinstance(self.route, dict):
                raise ValueError("V1 page binding is incomplete")
            if self.route.get("view") not in {
                "dashboard",
                "dataset-marketplace",
                "dataset-editor",
                "other",
            }:
                raise ValueError("V1 route view is invalid")
            if len(self.supported_actions) != len(set(self.supported_actions)):
                raise ValueError("V1 supportedActions contains duplicates")
            for action in self.supported_actions:
                contract = DEFAULT_REGISTRY.get(action)
                if not contract.public:
                    raise ValueError("V1 supportedActions contains an internal action")
            return self
        if self.page_type == "dashboard":
            if self.resource_id != "1024":
                raise ValueError("dashboard resourceId must be 1024")
            if self.supported_capabilities != ["dashboard.capture_current_view"]:
                raise ValueError("dashboard capability declaration is invalid")
        else:
            if self.resource_id is not None:
                raise ValueError("dataset resourceId must be null")
            if self.supported_capabilities:
                raise ValueError("dataset cannot declare dashboard capabilities")
        if self.supported_commands != ["navigateTo"]:
            raise ValueError("navigateTo must be the only supported command")
        return self


class DashboardPage(StrictCamelModel):
    page_type: Literal["dashboard"]
    dashboard_id: Literal["1024"]
    title: str = Field(min_length=1, max_length=200)
    context_version: int = Field(ge=0)
    captured_at: datetime


class DashboardFilter(StrictCamelModel):
    field: str = Field(min_length=1, max_length=100)
    operator: Literal["eq", "relative"]
    value: str = Field(min_length=1, max_length=200)


class DashboardMetrics(StrictCamelModel):
    item_count: int = Field(ge=0)
    weekly_change_pct: float
    bid_amount: int = Field(ge=0)


class DashboardWidget(StrictCamelModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    values: list[Any] | dict[str, Any]


class DashboardSnapshot(StrictCamelModel):
    schema_version: Literal["mock-dashboard-snapshot-v1"]
    page: DashboardPage
    filters: list[DashboardFilter]
    metrics: DashboardMetrics
    widgets: list[DashboardWidget]


class UiAck(StrictCamelModel):
    schema_version: Literal["davinci-ui-ack-v1"]
    status: Literal["executed"]
    destination: Literal["dashboard", "datasets"]
    path: Literal["/dashboard/1024", "/datasets"]
    context_version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_destination_path(self) -> "UiAck":
        expected = (
            "/dashboard/1024" if self.destination == "dashboard" else "/datasets"
        )
        if self.path != expected:
            raise ValueError("navigation destination and path do not match")
        return self


FrontendToolErrorCode = Literal[
    "ORIGIN_REJECTED",
    "CONTEXT_STALE",
    "CAPABILITY_UNAVAILABLE",
    "TARGET_NOT_FOUND",
    "TOOL_TIMEOUT",
    "SESSION_MISMATCH",
    "TOOL_RESULT_CONFLICT",
    "IFRAME_CLOSED",
    "RUN_ERROR",
    "INVALID_ARGUMENT",
    "SEMANTIC_PLANNER_REQUIRED",
    "SEMANTIC_PLANNER_STRATEGY_MISMATCH",
    "LAYOUT_WRITE_ALREADY_DISPATCHED",
]


class FrontendToolErrorPayload(StrictCamelModel):
    schema_version: Literal["davinci-tool-error-v1"]
    ok: Literal[False]
    code: FrontendToolErrorCode
    message: str = Field(min_length=1, max_length=500)
    context_version: int | None = Field(default=None, ge=0)


def validate_frontend_tools(
    host_context: HostContext, tools: list[Tool]
) -> tuple[Tool, ...]:
    if host_context.is_v1:
        expected = tuple(
            to_ag_ui_tool(DEFAULT_REGISTRY.get(action))
            for action in host_context.supported_actions
            if DEFAULT_REGISTRY.get(action).executor == "frontend"
        )
        if not tools:
            return expected
        if [item.model_dump(by_alias=True) for item in tools] != [
            item.model_dump(by_alias=True) for item in expected
        ]:
            raise ValueError("CAPABILITY_UNAVAILABLE: frontend tools do not match page")
        return tuple(tools)
    expected_names = list(_TOOL_CATALOG)
    supplied_names = [tool.name for tool in tools]
    if len(supplied_names) != len(set(supplied_names)):
        raise ValueError("CAPABILITY_UNAVAILABLE: duplicate frontend tools")
    for tool in tools:
        expected = _TOOL_CATALOG.get(tool.name)
        if expected is None:
            raise ValueError(f"CAPABILITY_UNAVAILABLE: unknown tool {tool.name}")
        if tool.model_dump(by_alias=True) != expected.model_dump(by_alias=True):
            raise ValueError(f"invalid frontend tool definition for {tool.name}")
    if supplied_names != expected_names:
        raise ValueError("CAPABILITY_UNAVAILABLE: frontend tools do not match page")
    return tuple(tools)


class NativePageResource(StrictCamelModel):
    type: Literal["dashboard", "dataset"]
    id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, max_length=500)


class NativeSpaceContext(StrictCamelModel):
    """Trusted current-user scope for one concrete collaborative space."""

    id: str = Field(min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    role: Literal["owner", "admin", "member"]


class NativePage(StrictCamelModel):
    instance_id: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "workspace",
        "dashboard",
        "dataset-marketplace",
        "dataset-editor",
        "collaborative-space",
        "other",
    ]
    route: str = Field(min_length=1, max_length=2000)
    resource: NativePageResource | None = None
    space: NativeSpaceContext | None = None
    # Davinci publishes this semantic hint on subscription pages and overlays.
    # It is part of pageStateSchema and does not grant additional permissions.
    workflow: Literal["subscription"] | None = None
    view_mode: Literal["self", "delegated"] | None = None

    @model_validator(mode="after")
    def validate_space_scope(self) -> "NativePage":
        """Keep trusted space and delegated-view data on their valid page kinds."""
        if self.space is not None and self.kind not in {
            "dashboard",
            "collaborative-space",
        }:
            raise ValueError("space is not valid for this page kind")
        if self.view_mode is not None and not (
            self.kind == "dashboard"
            or (self.kind == "collaborative-space" and self.space is not None)
        ):
            raise ValueError(
                "viewMode is only valid for Dashboard or trusted "
                "collaborative-space pages"
            )
        return self


class NativeDashboardCapabilities(StrictCamelModel):
    """Fine-grained trusted Dashboard capability ceiling for the current page."""

    can_read_data: StrictBool
    can_use_runtime_controls: StrictBool
    can_inspect_widget_config: StrictBool
    can_open_share_panel: StrictBool
    can_open_message_rule_panel: StrictBool
    can_persist_dashboard: StrictBool
    can_publish_dashboard: StrictBool


class NativeWorkspaceCapabilities(StrictCamelModel):
    """Trusted workspace-level capability ceiling for the current actor."""

    can_create_space: StrictBool
    can_create_dashboard_group: StrictBool


class NativeSpaceCapabilities(StrictCamelModel):
    """Trusted current-space management ceiling for the current actor."""

    can_manage: StrictBool


class NativeResourceCapabilities(StrictCamelModel):
    """Trusted persistence ceiling for the current page resource."""

    can_persist: StrictBool


class NativePermissions(StrictCamelModel):
    can_read: bool
    can_operate: bool
    can_persist: bool
    workspace_capabilities: NativeWorkspaceCapabilities | None = None
    space_capabilities: NativeSpaceCapabilities | None = None
    resource_capabilities: NativeResourceCapabilities | None = None
    dashboard_capabilities: NativeDashboardCapabilities | None = None


class NativeFilter(StrictCamelModel):
    filter_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=500)
    value: Any


class NativeUiState(StrictCamelModel):
    busy: bool
    selected_widget_id: str | None = Field(default=None, max_length=200)
    opened_panel: str | None = Field(default=None, max_length=200)
    active_filters: list[NativeFilter] = Field(max_length=100)


class NativeRevisions(StrictCamelModel):
    route_revision: int = Field(ge=0)
    resource_revision: int | None = Field(default=None, ge=0)
    data_revision: int | None = Field(default=None, ge=0)


class NativeDataStatus(StrictCamelModel):
    loading_widget_ids: list[str] = Field(max_length=500)
    error_widget_ids: list[str] = Field(max_length=500)
    last_refresh_at: datetime | None = None


class NativePageState(StrictCamelModel):
    schema_version: Literal["davinci-page-state-v1"]
    page: NativePage
    permissions: NativePermissions
    ui: NativeUiState
    revisions: NativeRevisions
    data_status: NativeDataStatus

    @model_validator(mode="after")
    def validate_dashboard_capability_scope(self) -> "NativePageState":
        """Reject Dashboard capability ceilings published on unrelated pages."""
        if (
            self.permissions.dashboard_capabilities is not None
            and self.page.kind != "dashboard"
        ):
            raise ValueError(
                "dashboardCapabilities is only valid for Dashboard pages"
            )
        return self


def validate_native_page_state(value: Any) -> dict[str, Any]:
    return NativePageState.model_validate(value).model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )


def validate_native_frontend_tools(tools: list[Tool]) -> tuple[Tool, ...]:
    if len(tools) > MAX_NATIVE_TOOLS:
        raise ValueError("CAPABILITY_UNAVAILABLE: too many frontend tools")
    names: set[str] = set()
    validated: list[Tool] = []
    for tool in tools:
        if not NATIVE_TOOL_NAME.fullmatch(tool.name) or tool.name in names:
            raise ValueError("CAPABILITY_UNAVAILABLE: invalid or duplicate tool name")
        if (
            not tool.description
            or len(tool.description) > MAX_NATIVE_TOOL_DESCRIPTION_CHARS
        ):
            raise ValueError("CAPABILITY_UNAVAILABLE: invalid tool description")
        if len(json.dumps(tool.parameters).encode("utf-8")) > MAX_NATIVE_SCHEMA_BYTES:
            raise ValueError("CAPABILITY_UNAVAILABLE: tool schema is too large")
        if tool.parameters.get("type") != "object":
            raise ValueError("CAPABILITY_UNAVAILABLE: tool input must be an object")
        names.add(tool.name)
        validated.append(tool)
    return tuple(validated)


class ContextItemLike(Protocol):
    """AG-UI `Context` 与 `RuntimeContextItem` 的公共结构。"""

    description: str
    value: Any


@dataclass(frozen=True, slots=True)
class EscapedContextItem:
    """转义后的上下文条目，与 `ContextItemLike` 同构。"""

    description: str
    value: str


def validate_native_context(
    items: Sequence[ContextItemLike] | None,
) -> tuple[ContextItemLike, ...]:
    """筛出并转义可注入的上下文条目；这条规则由本函数独占，路由与用户消息共用。"""
    accepted: list[ContextItemLike] = []
    for item in items or ():
        description = str(item.description)
        if len(accepted) >= MAX_CONTEXT_ITEMS:
            logger.info(
                "agui_context_item_dropped",
                extra={"description": description[:60], "reason": "too_many"},
            )
            break
        value = str(item.value)
        if (
            not CONTEXT_ITEM_NAME.fullmatch(description)
            or len(value) > MAX_CONTEXT_VALUE_CHARS
        ):
            logger.info(
                "agui_context_item_dropped",
                extra={"description": description[:60], "reason": "invalid"},
            )
            continue
        accepted.append(
            EscapedContextItem(
                description=description,
                value=value.translate(CONTEXT_VALUE_ESCAPES),
            )
        )
    return tuple(accepted)


def validate_native_tool_message_content(content: str, error: str | None) -> str:
    if len(content.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
        raise ValueError("Tool Result exceeds the 64 KiB limit")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Tool Result content must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Tool Result envelope must be an object")  # noqa: TRY004
    status = payload.get("status")
    if status not in {"success", "partial", "error"}:
        raise ValueError("Tool Result envelope status is invalid")
    if not isinstance(payload.get("issues"), list):
        raise ValueError(  # noqa: TRY004
            "Tool Result envelope issues must be an array"
        )
    payload_error = payload.get("error")
    if error is not None:
        if status != "error" or not isinstance(payload_error, dict):
            raise ValueError("Tool error marker requires an error envelope")
        if payload_error.get("code") != error:
            raise ValueError("Tool error marker does not match envelope code")
    elif status == "error":
        raise ValueError("Error envelope requires a ToolMessage error marker")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_tool_message_content(
    tool_name: str,
    content: str,
    error: str | None,
) -> str:
    if len(content.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
        raise ValueError("Tool Result exceeds the 64 KiB limit")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Tool Result content must be valid JSON") from exc

    if error is not None:
        if payload.get("schemaVersion") != "davinci-tool-error-v1":
            raise ValueError("tool error marker requires a structured error payload")
        parsed_error = FrontendToolErrorPayload.model_validate(payload)
        if parsed_error.code != error:
            raise ValueError("tool error marker does not match payload code")
        return parsed_error.model_dump_json(by_alias=True)

    if tool_name == CAPTURE_TOOL.name and payload.get("schemaVersion") == (
        "mock-dashboard-snapshot-v1"
    ):
        return DashboardSnapshot.model_validate(payload).model_dump_json(by_alias=True)
    if tool_name == NAVIGATE_TOOL.name:
        return UiAck.model_validate(payload).model_dump_json(by_alias=True)

    try:
        contract = DEFAULT_REGISTRY.get(tool_name)
    except ValueError:
        contract = None
    if contract is not None and contract.public and contract.executor == "frontend":
        try:
            validate_json_schema(payload, dict(contract.output_schema))
        except JsonSchemaValidationError as exc:
            raise ValueError("Tool Result failed canonical output validation") from exc
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    if tool_name == CAPTURE_TOOL.name:
        parsed: BaseModel = DashboardSnapshot.model_validate(payload)
    elif tool_name == NAVIGATE_TOOL.name:
        parsed = UiAck.model_validate(payload)
    else:
        raise ValueError(f"CAPABILITY_UNAVAILABLE: unknown tool {tool_name}")
    return parsed.model_dump_json(by_alias=True)
