import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from ag_ui.core import CustomEvent, RunAgentInput, ToolMessage, UserMessage
from ag_ui.encoder import EventEncoder
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agui.adapter import AgUiEventMapper
from app.agui.bridge import FrontendToolBridgeError, RunFrontendToolBridge
from app.agui.deferred_tools import DeferredFrontendToolError
from app.agui.validation_errors import native_validation_error
from app.agui.models import (
    HostContext,
    validate_frontend_tools,
    validate_native_context,
    validate_native_frontend_tools,
    validate_native_page_state,
    validate_native_tool_message_content,
)
from app.api.dependencies import AppServices, Identity, get_services
from app.config import CLAUDE_EFFORT_LEVELS
from app.errors import AppError
from app.runtime.contracts import (
    RuntimeContextItem,
    RuntimeFrontendTool,
    RuntimeToolResult,
)

router = APIRouter(prefix="/api")
Services = Annotated[AppServices, Depends(get_services)]
logger = logging.getLogger("uvicorn.error")


def _run_overrides(body: RunAgentInput, settings) -> tuple[str | None, str | None]:
    props = body.forwarded_props if isinstance(body.forwarded_props, dict) else {}
    raw_model = props.get("model")
    model = (
        str(raw_model)
        if isinstance(raw_model, str) and raw_model in settings.claude_selectable_models
        else None
    )
    raw_effort = props.get("effort")
    effort = (
        str(raw_effort)
        if isinstance(raw_effort, str) and raw_effort in CLAUDE_EFFORT_LEVELS
        else None
    )
    return model, effort


def _native_run_metadata(body: RunAgentInput) -> dict[str, Any]:
    """Accept only bounded page-catalog identity telemetry from the runner."""
    props = body.forwarded_props if isinstance(body.forwarded_props, dict) else {}
    metadata: dict[str, Any] = {}
    for source, target in (
        ("profileId", "profile_id"),
        ("catalogDigest", "catalog_digest"),
        ("toolSetId", "tool_set_id"),
    ):
        value = props.get(source)
        if value is None:
            continue
        if not isinstance(value, str) or not value or len(value) > 200:
            raise ValueError(f"{source} is invalid")
        metadata[target] = value
    for source, target in (
        ("toolSetChanges", "tool_set_changes"),
        ("catalogDigestChanges", "catalog_digest_changes"),
    ):
        value = props.get(source, 0)
        if isinstance(value, bool) or value not in (0, 1):
            raise ValueError(f"{source} is invalid")
        metadata[target] = int(value)
    return metadata


@router.post("/ag-ui")
async def run_ag_ui(
    body: RunAgentInput,
    request: Request,
    services: Services,
    identity: Identity,
) -> StreamingResponse:
    _require_inline_runtime(services)
    await services.workspace_access.require_session_owner(identity, body.thread_id)
    _validate_run_id(body.run_id)
    final_message = body.messages[-1] if body.messages else None
    if isinstance(final_message, ToolMessage):
        if _is_native_profile(body):
            return await _resume_native_tool_result(body, request, services)
        return await _continue_tool_result(body, services)
    if _is_native_profile(body):
        return await _start_native_run(body, request, services)
    return await _start_run(body, request, services)


async def _resume_native_tool_result(
    body: RunAgentInput,
    request: Request,
    services: AppServices,
) -> StreamingResponse:
    tool_messages: list[ToolMessage] = []
    for message in reversed(body.messages):
        if not isinstance(message, ToolMessage):
            break
        tool_messages.append(message)
    tool_messages.reverse()
    if not tool_messages:
        raise AppError(
            "invalid_request",
            "A native Tool Result Run must end with a ToolMessage.",
            422,
        )
    stage = "state"
    try:
        page_state = validate_native_page_state(body.state)
        stage = "tools"
        tools = validate_native_frontend_tools(body.tools)
        stage = "forwardedProps"
        model, effort = _run_overrides(body, services.settings)
        runtime_metadata = _native_run_metadata(body)
        stage = "continuation"
        # Recover from durable events after process restart or bounded-cache eviction.
        recovery = await services.turns.repository.frontend_tool_recovery(body.thread_id)
        wanted_ids = {message.tool_call_id for message in tool_messages}
        for state in recovery:
            if state["call"].tool_call_id not in wanted_ids:
                continue
            result = state["tool_result"]
            await services.deferred_frontend_tools.restore(
                state["call"], continuation_run_id=state["continuation_run_id"],
                content=result["content"] if result else None,
                error="error" if result and result.get("is_error") else None,
                mixed_batch=state.get("requires_restatement", False))
        resolved_results = []
        for message in tool_messages:
            call = await services.deferred_frontend_tools.get(
                body.thread_id, message.tool_call_id
            )
            if call is None:
                raise DeferredFrontendToolError(
                    "TOOL_NOT_FOUND",
                    "No deferred frontend ToolCall matches this Tool Result.",
                    404,
                )
            content = validate_native_tool_message_content(
                message.content, message.error
            )
            resolved_results.append((message, call, content))

        origins = {call.origin_run_id for _, call, _ in resolved_results}
        if len(origins) != 1:
            raise DeferredFrontendToolError("TOOL_RESULT_CONFLICT", "一次续接只能回传同一批原始工具结果。")
        expected_ids = {state["call"].tool_call_id for state in recovery if state["call"].origin_run_id in origins}
        if expected_ids and wanted_ids != expected_ids:
            raise DeferredFrontendToolError("TOOL_RESULT_CONFLICT", "请完整回传原始批次的全部工具结果。")
        for message, _call, content in resolved_results:
            await services.deferred_frontend_tools.consume(
                thread_id=body.thread_id, continuation_run_id=body.run_id,
                tool_call_id=message.tool_call_id, content=content, error=message.error,
                validate_only=True)
        turn = await services.turns.start(
            body.thread_id,
            "",
            [],
            body.run_id,
            turn_id=body.run_id,
            runtime_frontend_tools=_runtime_frontend_tools(tools),
            runtime_page_state=page_state,
            runtime_tool_results=tuple(
                RuntimeToolResult(
                    tool_call_id=message.tool_call_id,
                    content=content,
                    is_error=message.error is not None,
                    frontend_round_trip_ms=(max(0, round((time.monotonic() - call.recorded_at_monotonic) * 1000))
                        if call.recorded_at_monotonic is not None else None),
                    origin=call.origin,
                )
                for message, call, content in resolved_results
            ),
            runtime_context_items=_runtime_context_items(body),
            runtime_model=model,
            runtime_effort=effort,
            runtime_metadata=runtime_metadata,
        )
        # Only bind the in-memory cache after the whole continuation is durably accepted.
        # A failed persistence attempt can therefore be retried using the original Run ID.
        for message, call, content in resolved_results:
            consumption = await services.deferred_frontend_tools.consume(
                thread_id=body.thread_id, continuation_run_id=body.run_id,
                tool_call_id=message.tool_call_id, content=content, error=message.error)
            logger.info(
                "agui_native_tool_resumed",
                extra={
                    "thread_id": body.thread_id,
                    "run_id": body.run_id,
                    "origin_run_id": call.origin_run_id,
                    "tool_call_id": call.tool_call_id,
                    "tool_name": call.public_name,
                    "status": json.loads(content)["status"],
                    "error_code": message.error,
                    "duration_ms": (max(0, round((time.monotonic() - call.recorded_at_monotonic) * 1000))
                                    if call.recorded_at_monotonic is not None else None),
                    "replay_status": consumption.status,
                    **_native_page_log_fields(page_state),
                },
            )
    except DeferredFrontendToolError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    except ValueError as exc:
        raise native_validation_error(exc, stage) from exc
    return _stream_native_turn(body, request, services, turn)


async def _start_native_run(
    body: RunAgentInput,
    request: Request,
    services: AppServices,
) -> StreamingResponse:
    user_message = _terminal_user_message(body)
    stage = "state"
    try:
        page_state = validate_native_page_state(body.state)
        stage = "tools"
        tools = validate_native_frontend_tools(body.tools)
        stage = "forwardedProps"
        model, effort = _run_overrides(body, services.settings)
        runtime_metadata = _native_run_metadata(body)
        stage = "turn"
        turn = await services.turns.start(
            body.thread_id,
            user_message.content,
            [],
            body.run_id,
            turn_id=body.run_id,
            runtime_frontend_tools=_runtime_frontend_tools(tools),
            runtime_page_state=page_state,
            runtime_context_items=_runtime_context_items(body),
            runtime_model=model,
            runtime_effort=effort,
            runtime_metadata=runtime_metadata,
        )
        schema_bytes = _native_tool_schema_bytes(tools)
        logger.info(
            "agui_native_run_started tool_count=%d tool_schema_bytes=%d",
            len(tools),
            schema_bytes,
            extra={
                "thread_id": body.thread_id,
                "run_id": body.run_id,
                "status": "started",
                "tool_count": len(tools),
                "tool_schema_bytes": schema_bytes,
                **_native_page_log_fields(page_state),
            },
        )
    except ValueError as exc:
        raise native_validation_error(exc, stage) from exc
    return _stream_native_turn(body, request, services, turn)


def _stream_native_turn(
    body: RunAgentInput,
    request: Request,
    services: AppServices,
    turn,
) -> StreamingResponse:
    mapper = AgUiEventMapper(body.thread_id, turn.id, bridge=None)
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def stream() -> AsyncIterator[str]:
        try:
            async for record in services.event_stream.iter_events(
                turn.id,
                after_sequence=0,
                heartbeat_seconds=services.settings.sse_heartbeat_seconds,
            ):
                if record is None:
                    yield ": heartbeat\n\n"
                    continue
                payload = json.loads(record.payload_json)
                for event in mapper.map(
                    record.event_type, payload, record.created_at
                ):
                    yield encoder.encode(event)
                if mapper.terminal:
                    break
        except (asyncio.CancelledError, GeneratorExit):
            try:
                await services.turns.cancel(turn.id)
            except AppError:
                pass
            raise

    return _streaming_response(stream())


def _runtime_context_items(body: RunAgentInput) -> tuple[RuntimeContextItem, ...]:
    # 在边界就丢掉超限条目：它们会被原样写进 Turn 事件，也会计入 Runner 请求大小。
    return tuple(
        RuntimeContextItem(description=item.description, value=str(item.value))
        for item in validate_native_context(body.context)
    )


def _runtime_frontend_tools(tools) -> tuple[RuntimeFrontendTool, ...]:
    return tuple(
        RuntimeFrontendTool(
            name=tool.name,
            description=tool.description,
            parameters=dict(tool.parameters),
        )
        for tool in tools
    )


def _native_tool_schema_bytes(tools) -> int:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        }
        for tool in tools
    ]
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _native_page_log_fields(page_state: dict[str, Any]) -> dict[str, Any]:
    page = page_state["page"]
    revisions = page_state["revisions"]
    resource = page.get("resource") or {}
    return {
        "page_instance_id": page["instanceId"],
        "resource_type": resource.get("type"),
        "resource_id": resource.get("id"),
        "route_revision": revisions["routeRevision"],
        "resource_revision": revisions.get("resourceRevision"),
        "data_revision": revisions.get("dataRevision"),
    }


def _is_native_profile(body: RunAgentInput) -> bool:
    return (
        isinstance(body.forwarded_props, dict)
        and body.forwarded_props.get("profile") == "davinci-agui-native-v2"
    )


async def _continue_tool_result(
    body: RunAgentInput,
    services: AppServices,
) -> StreamingResponse:
    final_message = body.messages[-1] if body.messages else None
    if not isinstance(final_message, ToolMessage) or len(body.messages) != 1:
        raise AppError(
            "invalid_request",
            "A Tool Result continuation must contain exactly one ToolMessage.",
            422,
        )
    next_host_context = _extract_host_context(body.state)
    try:
        next_tools = validate_frontend_tools(next_host_context, body.tools)
        outcome = await services.frontend_tool_bridges.submit(
            body.thread_id,
            body.run_id,
            final_message,
            next_host_context,
            next_tools,
        )
    except FrontendToolBridgeError as exc:
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    except ValueError as exc:
        raise AppError(
            "invalid_request",
            "AG-UI input validation failed.",
            422,
        ) from exc
    event = CustomEvent(
        name="tool_result.accepted",
        value={
            "toolCallId": outcome.tool_call_id,
            "status": outcome.status,
        },
    )
    encoder = EventEncoder(accept="text/event-stream")

    async def acknowledgement() -> AsyncIterator[str]:
        yield encoder.encode(event)

    return _streaming_response(acknowledgement())


async def _start_run(
    body: RunAgentInput,
    request: Request,
    services: AppServices,
) -> StreamingResponse:
    user_message = _terminal_user_message(body)
    host_context = _extract_host_context(body.state)
    bridge: RunFrontendToolBridge | None = None
    try:
        tools = validate_frontend_tools(host_context, body.tools)
        model, effort = _run_overrides(body, services.settings)
        bridge = services.frontend_tool_bridges.register(
            body.thread_id,
            body.run_id,
            host_context,
            tools,
        )
        turn = await services.turns.start(
            body.thread_id,
            user_message.content,
            [],
            body.run_id,
            turn_id=body.run_id,
            runtime_model=model,
            runtime_effort=effort,
        )
    except FrontendToolBridgeError as exc:
        if bridge is not None:
            await services.frontend_tool_bridges.remove(
                body.thread_id, body.run_id, code="RUN_ERROR"
            )
        raise AppError(exc.code, exc.message, exc.status_code) from exc
    except (AppError, ValueError) as exc:
        if bridge is not None:
            await services.frontend_tool_bridges.remove(
                body.thread_id, body.run_id, code="RUN_ERROR"
            )
        if isinstance(exc, AppError):
            raise
        raise AppError(
            "invalid_request", "AG-UI input validation failed.", 422
        ) from exc
    except Exception:
        if bridge is not None:
            await services.frontend_tool_bridges.remove(
                body.thread_id, body.run_id, code="RUN_ERROR"
            )
        raise

    mapper = AgUiEventMapper(body.thread_id, turn.id, bridge)
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def stream() -> AsyncIterator[str]:
        terminal = False
        try:
            async for record in services.event_stream.iter_events(
                turn.id,
                after_sequence=0,
                heartbeat_seconds=services.settings.sse_heartbeat_seconds,
            ):
                if record is None:
                    yield ": heartbeat\n\n"
                    continue
                payload = json.loads(record.payload_json)
                for event in mapper.map(
                    record.event_type, payload, record.created_at
                ):
                    yield encoder.encode(event)
                if mapper.terminal:
                    terminal = True
                    break
        except (asyncio.CancelledError, GeneratorExit):
            await _cancel_disconnected_run(services, body.thread_id, turn.id)
            raise
        finally:
            if terminal:
                await services.frontend_tool_bridges.remove(
                    body.thread_id,
                    turn.id,
                    code="RUN_ERROR",
                    message="The Agent Run has finished.",
                )

    return _streaming_response(stream())


def _terminal_user_message(body: RunAgentInput) -> UserMessage:
    if not body.messages or not isinstance(body.messages[-1], UserMessage):
        raise AppError(
            "invalid_request",
            "An initial Run must end with a UserMessage.",
            422,
        )
    message = body.messages[-1]
    if not isinstance(message.content, str) or not message.content.strip():
        raise AppError(
            "invalid_request",
            "The terminal UserMessage must contain non-empty text.",
            422,
        )
    return message


def _extract_host_context(state: Any) -> HostContext:
    if not isinstance(state, dict) or not isinstance(state.get("hostContext"), dict):
        raise AppError(
            "invalid_request",
            "state.hostContext is required.",
            422,
        )
    try:
        return HostContext.model_validate(state["hostContext"])
    except Exception as exc:
        raise AppError(
            "invalid_request",
            "state.hostContext is invalid.",
            422,
        ) from exc


def _validate_run_id(run_id: str) -> None:
    try:
        parsed = uuid.UUID(run_id)
    except ValueError as exc:
        raise AppError("invalid_request", "runId must be a UUID.", 422) from exc
    if str(parsed) != run_id.lower():
        raise AppError("invalid_request", "runId must be a canonical UUID.", 422)


def _require_inline_runtime(services: AppServices) -> None:
    if services.settings.app_runtime_mode != "local_inline":
        raise AppError(
            "CAPABILITY_UNAVAILABLE",
            "AG-UI frontend tools require the local_inline runtime.",
            400,
        )


async def _cancel_disconnected_run(
    services: AppServices,
    thread_id: str,
    run_id: str,
) -> None:
    await services.frontend_tool_bridges.remove(
        thread_id,
        run_id,
        code="IFRAME_CLOSED",
    )
    try:
        await services.turns.cancel(run_id)
    except AppError:
        pass


def _streaming_response(content: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        content,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
