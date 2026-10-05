import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ag_ui.core import Tool, ToolMessage
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.agui.catalog import DEFAULT_REGISTRY
from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from app.agui.models import (
    MAX_TOOL_RESULT_BYTES,
    DashboardSnapshot,
    FrontendToolErrorPayload,
    HostContext,
    UiAck,
    validate_tool_message_content,
)

_LAYOUT_TOOL_NAME = "dashboard.set_widget_layout"
_V2_LAYOUT_OUTPUT_VALIDATOR = Draft202012Validator(dict(
    load_contract_registry(
        CONTRACT_PATH.with_name("davinci-agent-v2.json")
    ).get(_LAYOUT_TOOL_NAME).output_schema
))


def _bridge_error_details(
    *,
    stage: str,
    code: str,
    session_id: str | None,
    tool_call_id: str | None,
    retryable: bool = False,
    write_dispatched: bool | None = None,
    layout_run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "code": code,
        "retryable": retryable,
        "writeDispatched": write_dispatched,
        "sessionId": session_id,
        "toolCallId": tool_call_id,
        "layoutRunId": layout_run_id,
    }


class FrontendToolBridgeError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 409,
        *,
        context_version: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context_version = context_version
        self.details = dict(details or {})

    def to_tool_json(self) -> str:
        payload = json.loads(FrontendToolErrorPayload(
            schemaVersion="davinci-tool-error-v1",
            ok=False,
            code=self.code,
            message=self.message,
            contextVersion=self.context_version,
        ).model_dump_json(by_alias=True))
        if self.details:
            payload["diagnostics"] = self.details
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ToolSubmission:
    content: str
    error: str | None
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class SubmissionOutcome:
    status: str
    tool_call_id: str


@dataclass(slots=True)
class PendingToolCall:
    tool_call_id: str
    public_name: str
    arguments_json: str
    future: asyncio.Future[ToolSubmission]
    expires_at: float
    claimed: bool = False
    submitted_fingerprint: str | None = None
    terminal_error: FrontendToolBridgeError | None = None

    @property
    def pending(self) -> bool:
        return not self.future.done() and self.terminal_error is None


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_submission_content(
    public_name: str,
    content: str,
    error: str | None,
) -> str:
    """Accept the native V2 layout envelope on the legacy bridge boundary."""
    if public_name != _LAYOUT_TOOL_NAME:
        return validate_tool_message_content(public_name, content, error)
    if len(content.encode("utf-8")) > MAX_TOOL_RESULT_BYTES:
        raise ValueError("Tool Result exceeds the 64 KiB limit")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return validate_tool_message_content(public_name, content, error)
    if not isinstance(payload, dict) or payload.get("status") not in {
        "success", "partial", "error",
    }:
        return validate_tool_message_content(public_name, content, error)
    validation_error = next(_V2_LAYOUT_OUTPUT_VALIDATOR.iter_errors(payload), None)
    if validation_error is not None:
        raise ValueError("Tool Result failed canonical output validation.") from (
            validation_error
        )
    payload_error = payload.get("error")
    if error is not None:
        if (
            payload.get("status") != "error"
            or not isinstance(payload_error, dict)
            or payload_error.get("code") != error
        ):
            raise ValueError("tool error marker does not match envelope code")
    elif payload.get("status") == "error":
        raise ValueError("Error envelope requires a ToolMessage error marker")
    return canonical_json(payload)


class RunFrontendToolBridge:
    def __init__(
        self,
        thread_id: str,
        run_id: str,
        host_context: HostContext,
        tools: tuple[Tool, ...],
        *,
        tool_timeout_seconds: float,
        registration_grace_seconds: float,
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.host_context = host_context
        self.tools = tools
        self.public_tool_names = tuple(tool.name for tool in tools)
        self.tool_timeout_seconds = tool_timeout_seconds
        self.registration_grace_seconds = registration_grace_seconds
        self._calls: dict[str, PendingToolCall] = {}
        self._call_order: list[str] = []
        self._call_registered = asyncio.Event()
        self._closed_error: FrontendToolBridgeError | None = None
        self._terminal_layout_failure: dict[str, Any] | None = None

    @property
    def pending_count(self) -> int:
        return sum(call.pending for call in self._calls.values())

    @property
    def terminal_layout_failure(self) -> dict[str, Any] | None:
        return (
            dict(self._terminal_layout_failure)
            if self._terminal_layout_failure is not None
            else None
        )

    def record_layout_failure(self, diagnostics: dict[str, Any]) -> None:
        if diagnostics.get("retryable") is False:
            self._terminal_layout_failure = dict(diagnostics)

    def reject_call(
        self,
        public_name: str,
        arguments: dict[str, Any],
        *,
        code: str,
        message: str,
        stage: str,
        retryable: bool,
        write_dispatched: bool | None,
    ) -> FrontendToolBridgeError:
        call = self._matching_call(public_name, arguments)
        error = FrontendToolBridgeError(
            code,
            message,
            422,
            context_version=self.host_context.context_version,
            details=_bridge_error_details(
                stage=stage,
                code=code,
                retryable=retryable,
                write_dispatched=write_dispatched,
                session_id=self.thread_id,
                tool_call_id=call.tool_call_id if call is not None else None,
            ),
        )
        if call is not None:
            self._terminate_call(call, error)
        if public_name == _LAYOUT_TOOL_NAME:
            self.record_layout_failure(error.details)
        return error

    def _matching_call(
        self,
        public_name: str,
        arguments: dict[str, Any],
    ) -> PendingToolCall | None:
        arguments_json = canonical_json(arguments)
        for tool_call_id in self._call_order:
            call = self._calls[tool_call_id]
            if (
                call.public_name == public_name
                and call.arguments_json == arguments_json
                and not call.claimed
                and (call.terminal_error is not None or not call.future.done())
            ):
                return call
        return None

    def _error_for_call(
        self,
        call: PendingToolCall,
        *,
        code: str,
        message: str,
        status_code: int,
        stage: str,
        retryable: bool = False,
        write_dispatched: bool | None = None,
    ) -> FrontendToolBridgeError:
        error = FrontendToolBridgeError(
            code,
            message,
            status_code,
            context_version=self.host_context.context_version,
            details=_bridge_error_details(
                stage=stage,
                code=code,
                retryable=retryable,
                write_dispatched=write_dispatched,
                session_id=self.thread_id,
                tool_call_id=call.tool_call_id,
            ),
        )
        if call.public_name == _LAYOUT_TOOL_NAME:
            self.record_layout_failure(error.details)
        return error

    def _enrich_call_error(
        self,
        call: PendingToolCall,
        error: FrontendToolBridgeError,
        *,
        stage: str,
    ) -> FrontendToolBridgeError:
        if error.details:
            return error
        return self._error_for_call(
            call,
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            stage=stage,
        )

    def begin_call(
        self,
        tool_call_id: str,
        public_name: str,
        arguments: dict[str, Any],
    ) -> None:
        if self._closed_error is not None:
            raise FrontendToolBridgeError(
                self._closed_error.code,
                self._closed_error.message,
                self._closed_error.status_code,
                context_version=self._closed_error.context_version,
                details=_bridge_error_details(
                    stage="call_registration",
                    code=self._closed_error.code,
                    session_id=self.thread_id,
                    tool_call_id=tool_call_id,
                    write_dispatched=False,
                ),
            )
        if public_name not in self.public_tool_names:
            raise FrontendToolBridgeError(
                "CAPABILITY_UNAVAILABLE",
                f"Frontend tool {public_name!r} is unavailable on this page.",
                400,
                context_version=self.host_context.context_version,
                details=_bridge_error_details(
                    stage="call_registration",
                    code="CAPABILITY_UNAVAILABLE",
                    session_id=self.thread_id,
                    tool_call_id=tool_call_id,
                    write_dispatched=False,
                ),
            )
        arguments_json = canonical_json(arguments)
        existing = self._calls.get(tool_call_id)
        if existing is not None:
            if (
                existing.public_name == public_name
                and existing.arguments_json == arguments_json
            ):
                return
            raise FrontendToolBridgeError(
                "TOOL_RESULT_CONFLICT",
                "The Tool Call ID was reused with different input.",
                409,
                details=_bridge_error_details(
                    stage="call_registration",
                    code="TOOL_RESULT_CONFLICT",
                    session_id=self.thread_id,
                    tool_call_id=tool_call_id,
                    write_dispatched=False,
                ),
            )
        loop = asyncio.get_running_loop()
        self._calls[tool_call_id] = PendingToolCall(
            tool_call_id=tool_call_id,
            public_name=public_name,
            arguments_json=arguments_json,
            future=loop.create_future(),
            expires_at=loop.time() + self.tool_timeout_seconds,
        )
        self._call_order.append(tool_call_id)
        self._call_registered.set()

    def lookup_call(self, tool_call_id: str) -> PendingToolCall | None:
        return self._calls.get(tool_call_id)

    async def claim_and_wait(
        self,
        public_name: str,
        arguments: dict[str, Any],
    ) -> ToolSubmission:
        call = self._matching_call(public_name, arguments)
        if call is None:
            raise FrontendToolBridgeError(
                "SESSION_MISMATCH",
                "No matching frontend Tool Call is active for this Run.",
                409,
                context_version=self.host_context.context_version,
                details=_bridge_error_details(
                    stage="frontend_wait",
                    code="SESSION_MISMATCH",
                    session_id=self.thread_id,
                    tool_call_id=None,
                    write_dispatched=False,
                ),
            )
        if call.terminal_error is not None:
            raise call.terminal_error
        if call.future.done():
            raise FrontendToolBridgeError(
                "SESSION_MISMATCH",
                "No matching frontend Tool Call is active for this Run.",
                409,
                context_version=self.host_context.context_version,
                details=_bridge_error_details(
                    stage="frontend_wait",
                    code="SESSION_MISMATCH",
                    session_id=self.thread_id,
                    tool_call_id=call.tool_call_id,
                    write_dispatched=False,
                ),
            )
        call.claimed = True
        remaining = call.expires_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise self._expire(call)
        try:
            return await asyncio.wait_for(
                asyncio.shield(call.future),
                timeout=remaining,
            )
        except TimeoutError as exc:
            raise self._expire(call) from exc

    async def submit(self, message: ToolMessage) -> SubmissionOutcome:
        return await self.submit_with_context(
            message,
            self.host_context,
            self.tools,
        )

    async def submit_with_context(
        self,
        message: ToolMessage,
        next_host_context: HostContext,
        next_tools: list[Tool] | tuple[Tool, ...],
    ) -> SubmissionOutcome:
        call = await self._wait_for_call(message.tool_call_id)
        if call.terminal_error is not None:
            raise call.terminal_error
        try:
            content = _validate_submission_content(
                call.public_name,
                message.content,
                message.error,
            )
        except (ValueError, ValidationError) as exc:
            error = self._error_for_call(
                call,
                code="RUN_ERROR",
                message="Frontend Tool Result failed schema validation.",
                status_code=422,
                stage="result_validation",
            )
            self._terminate_call(call, error)
            raise error from exc
        fingerprint = hashlib.sha256(
            f"{message.error or ''}\0{content}".encode()
        ).hexdigest()
        if call.submitted_fingerprint is not None:
            if call.submitted_fingerprint == fingerprint:
                try:
                    self._require_current_context(next_host_context, next_tools)
                except FrontendToolBridgeError as exc:
                    raise self._enrich_call_error(
                        call, exc, stage="result_binding"
                    ) from exc
                return SubmissionOutcome("replayed", call.tool_call_id)
            raise self._error_for_call(
                call,
                code="TOOL_RESULT_CONFLICT",
                message="A different result was already submitted for this Tool Call.",
                status_code=409,
                stage="result_submission",
            )
        try:
            resolved_host_context = self._validate_result_binding(
                call,
                content,
                message.error,
                next_host_context,
                next_tools,
            )
        except FrontendToolBridgeError as exc:
            error = self._enrich_call_error(call, exc, stage="result_binding")
            self._terminate_call(call, error)
            raise error from exc
        if call.future.cancelled():
            raise self._expire(call)
        call.submitted_fingerprint = fingerprint
        self.host_context = resolved_host_context
        self.tools = tuple(next_tools)
        call.future.set_result(ToolSubmission(
            content=content,
            error=message.error,
            tool_call_id=call.tool_call_id,
        ))
        return SubmissionOutcome("accepted", call.tool_call_id)

    async def fail_all(self, code: str, message: str) -> None:
        self._closed_error = FrontendToolBridgeError(
            code,
            message,
            _status_for_code(code),
            context_version=self.host_context.context_version,
            details=_bridge_error_details(
                stage="run_termination",
                code=code,
                session_id=self.thread_id,
                tool_call_id=None,
            ),
        )
        for call in self._calls.values():
            if call.future.done():
                continue
            error = self._error_for_call(
                call,
                code=code,
                message=message,
                status_code=_status_for_code(code),
                stage="run_termination",
            )
            call.terminal_error = error
            if call.claimed:
                call.future.set_exception(error)
            else:
                call.future.cancel()

    async def _wait_for_call(self, tool_call_id: str) -> PendingToolCall:
        deadline = asyncio.get_running_loop().time() + self.registration_grace_seconds
        while True:
            call = self._calls.get(tool_call_id)
            if call is not None:
                return call
            self._call_registered.clear()
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(
                    self._call_registered.wait(),
                    timeout=remaining,
                )
            except TimeoutError:
                break
        raise FrontendToolBridgeError(
            "SESSION_MISMATCH",
            "Tool Result does not match an active Tool Call.",
            409,
            context_version=self.host_context.context_version,
            details=_bridge_error_details(
                stage="result_submission",
                code="SESSION_MISMATCH",
                session_id=self.thread_id,
                tool_call_id=tool_call_id,
                write_dispatched=False,
            ),
        )

    def _validate_result_binding(
        self,
        call: PendingToolCall,
        content: str,
        error: str | None,
        next_host_context: HostContext,
        next_tools: list[Tool] | tuple[Tool, ...],
    ) -> HostContext:
        if self.host_context.is_v1:
            return self._validate_v1_result_binding(
                call, content, error, next_host_context, next_tools
            )
        if error is not None:
            self._require_current_context(next_host_context, next_tools)
            return self.host_context
        if call.public_name == "dashboard.capture_current_view":
            snapshot = DashboardSnapshot.model_validate_json(content)
            if snapshot.page.context_version != self.host_context.context_version:
                raise FrontendToolBridgeError(
                    "CONTEXT_STALE",
                    "Dashboard context changed before capture completed.",
                    409,
                    context_version=self.host_context.context_version,
                )
            self._require_current_context(next_host_context, next_tools)
            return self.host_context
        ack = UiAck.model_validate_json(content)
        arguments = json.loads(call.arguments_json)
        if ack.destination != arguments.get("destination"):
            raise FrontendToolBridgeError(
                "TARGET_NOT_FOUND",
                "Navigation ACK does not match the requested destination.",
                409,
                context_version=self.host_context.context_version,
            )
        if ack.context_version != self.host_context.context_version + 1:
            raise FrontendToolBridgeError(
                "CONTEXT_STALE",
                "Navigation ACK has an unexpected context version.",
                409,
                context_version=self.host_context.context_version,
            )
        if tuple(next_tools) != self.tools:
            raise FrontendToolBridgeError(
                "CAPABILITY_UNAVAILABLE",
                "Frontend Tool catalog changed during the active Run.",
                409,
                context_version=self.host_context.context_version,
            )
        expected_page_type = (
            "dashboard" if ack.destination == "dashboard" else "dataset"
        )
        expected_resource_id = "1024" if ack.destination == "dashboard" else None
        if (
            next_host_context.page_type != expected_page_type
            or next_host_context.resource_id != expected_resource_id
        ):
            raise FrontendToolBridgeError(
                "TARGET_NOT_FOUND",
                "Navigation HostContext does not match the requested destination.",
                409,
                context_version=self.host_context.context_version,
            )
        if next_host_context.context_version != ack.context_version:
            raise FrontendToolBridgeError(
                "CONTEXT_STALE",
                "Navigation HostContext does not match the navigation ACK.",
                409,
                context_version=self.host_context.context_version,
            )
        return next_host_context

    def _validate_v1_result_binding(
        self,
        call: PendingToolCall,
        content: str,
        error: str | None,
        next_host_context: HostContext,
        next_tools: list[Tool] | tuple[Tool, ...],
    ) -> HostContext:
        if error is not None:
            self._require_current_context(next_host_context, next_tools)
            return self.host_context
        contract = DEFAULT_REGISTRY.get(call.public_name)
        payload = json.loads(content)
        if contract.context_effect == "none":
            if (
                next_host_context == self.host_context
                and tuple(next_tools) == self.tools
            ):
                return self.host_context
            observed_version = payload.get("contextVersion")
            if (
                next_host_context.page_instance_id == self.host_context.page_instance_id
                and next_host_context.context_version == observed_version
                and next_host_context.context_version
                > self.host_context.context_version
                and tuple(next_tools) == self.tools
            ):
                return next_host_context
            raise FrontendToolBridgeError(
                "CONTEXT_STALE",
                "Frontend Tool Result does not match the observed PageContext.",
                409,
                context_version=self.host_context.context_version,
            )
        ack_version = payload.get("contextVersion")
        if (
            next_host_context.page_instance_id != self.host_context.page_instance_id
            or not isinstance(ack_version, int)
            or ack_version <= self.host_context.context_version
            or next_host_context.context_version < ack_version
        ):
            raise FrontendToolBridgeError(
                "CONTEXT_STALE",
                "Frontend Tool Result does not match the advanced PageContext.",
                409,
                context_version=self.host_context.context_version,
            )
        if tuple(next_tools) != self.tools:
            raise FrontendToolBridgeError(
                "CAPABILITY_UNAVAILABLE",
                "Frontend Tool catalog changed during the active Run.",
                409,
                context_version=self.host_context.context_version,
            )
        return next_host_context

    def _require_current_context(
        self,
        next_host_context: HostContext,
        next_tools: list[Tool] | tuple[Tool, ...],
    ) -> None:
        if next_host_context != self.host_context or tuple(next_tools) != self.tools:
            raise FrontendToolBridgeError(
                "CONTEXT_STALE",
                "Frontend Tool Result changed HostContext unexpectedly.",
                409,
                context_version=self.host_context.context_version,
            )

    def _expire(self, call: PendingToolCall) -> FrontendToolBridgeError:
        if call.terminal_error is not None:
            return call.terminal_error
        error = self._error_for_call(
            call,
            code="TOOL_TIMEOUT",
            message=(
                "Frontend Tool Result was not received within "
                f"{self.tool_timeout_seconds:g} seconds."
            ),
            status_code=504,
            stage="frontend_wait",
        )
        call.terminal_error = error
        if not call.future.done():
            call.future.cancel()
        return error

    @staticmethod
    def _terminate_call(
        call: PendingToolCall,
        error: FrontendToolBridgeError,
    ) -> None:
        call.terminal_error = error
        if call.future.done():
            return
        if call.claimed:
            call.future.set_exception(error)
        else:
            call.future.cancel()


class FrontendToolBridgeRegistry:
    def __init__(
        self,
        *,
        tool_timeout_seconds: float | None = None,
        registration_grace_seconds: float = 1.0,
    ) -> None:
        self.tool_timeout_seconds = (
            tool_timeout_seconds
            if tool_timeout_seconds is not None
            else max(
                contract.timeout_ms
                for contract in DEFAULT_REGISTRY.public_contracts
                if contract.executor == "frontend"
            )
            / 1000
        )
        self.registration_grace_seconds = registration_grace_seconds
        self._bridges: dict[tuple[str, str], RunFrontendToolBridge] = {}
        self._active_by_thread: dict[str, str] = {}

    @property
    def pending_count(self) -> int:
        return sum(bridge.pending_count for bridge in self._bridges.values())

    @property
    def active_run_count(self) -> int:
        return len(self._bridges)

    def register(
        self,
        thread_id: str,
        run_id: str,
        host_context: HostContext,
        tools: list[Tool] | tuple[Tool, ...],
    ) -> RunFrontendToolBridge:
        existing_run = self._active_by_thread.get(thread_id)
        if existing_run is not None:
            existing = self._bridges[(thread_id, existing_run)]
            if (
                existing_run == run_id
                and existing.host_context == host_context
                and existing.tools == tuple(tools)
            ):
                return existing
            raise FrontendToolBridgeError(
                "SESSION_MISMATCH",
                "This Session already has a different active Run.",
                409,
                context_version=existing.host_context.context_version,
                details=_bridge_error_details(
                    stage="session_registration",
                    code="SESSION_MISMATCH",
                    session_id=thread_id,
                    tool_call_id=None,
                    write_dispatched=False,
                ),
            )
        bridge = RunFrontendToolBridge(
            thread_id,
            run_id,
            host_context,
            tuple(tools),
            tool_timeout_seconds=self.tool_timeout_seconds,
            registration_grace_seconds=self.registration_grace_seconds,
        )
        self._bridges[(thread_id, run_id)] = bridge
        self._active_by_thread[thread_id] = run_id
        return bridge

    def get(self, thread_id: str, run_id: str) -> RunFrontendToolBridge | None:
        return self._bridges.get((thread_id, run_id))

    def active_for_thread(self, thread_id: str) -> RunFrontendToolBridge | None:
        run_id = self._active_by_thread.get(thread_id)
        if run_id is None:
            return None
        return self._bridges.get((thread_id, run_id))

    async def submit(
        self,
        thread_id: str,
        run_id: str,
        message: ToolMessage,
        next_host_context: HostContext | None = None,
        next_tools: list[Tool] | tuple[Tool, ...] | None = None,
    ) -> SubmissionOutcome:
        bridge = self.get(thread_id, run_id)
        if bridge is None:
            raise FrontendToolBridgeError(
                "SESSION_MISMATCH",
                "Tool Result does not belong to an active Session and Run.",
                409,
                details=_bridge_error_details(
                    stage="result_submission",
                    code="SESSION_MISMATCH",
                    session_id=thread_id,
                    tool_call_id=message.tool_call_id,
                    write_dispatched=False,
                ),
            )
        return await bridge.submit_with_context(
            message,
            next_host_context or bridge.host_context,
            next_tools or bridge.tools,
        )

    async def remove(
        self,
        thread_id: str,
        run_id: str,
        *,
        code: str,
        message: str | None = None,
    ) -> None:
        bridge = self._bridges.pop((thread_id, run_id), None)
        if bridge is None:
            return
        if self._active_by_thread.get(thread_id) == run_id:
            self._active_by_thread.pop(thread_id, None)
        await bridge.fail_all(message=message or _default_message(code), code=code)

    async def shutdown(self) -> None:
        bridges = tuple(self._bridges.values())
        self._bridges.clear()
        self._active_by_thread.clear()
        for bridge in bridges:
            await bridge.fail_all("RUN_ERROR", _default_message("RUN_ERROR"))


def _default_message(code: str) -> str:
    return {
        "IFRAME_CLOSED": "The embedded Agent was closed before the Tool completed.",
        "RUN_ERROR": "The Agent Run ended before the frontend Tool completed.",
        "TOOL_TIMEOUT": "Frontend Tool Result was not received before the timeout.",
    }.get(code, "The frontend Tool Call could not be completed.")


def _status_for_code(code: str) -> int:
    return {
        "TOOL_TIMEOUT": 504,
        "CAPABILITY_UNAVAILABLE": 400,
        "TARGET_NOT_FOUND": 404,
        "RUN_ERROR": 500,
    }.get(code, 409)
