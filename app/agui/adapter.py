import json
from datetime import datetime
from typing import Any

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    Interrupt,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from app.agui.bridge import RunFrontendToolBridge

FAILED_TERMINALS = {
    "turn.failed",
    "turn.outcome_unknown",
    "turn.recovery_required",
}
CANCELLED_TERMINALS = {"turn.cancelled", "turn.interrupted"}
# 面板执行详情所需的原始事件。转发的是 Turn 事件本身，不是 AG-UI 的原生
# THINKING_*／TOOL_CALL_RESULT——后者有强排序约束，且不会进 message 模型，
# 前端照样要从 onEvent 捞；转发原始事件则让实时与 /messages 回放同形。
# 有意排除 message.assistant.delta：正文已走 TextMessage 事件，它占全部
# Turn 事件的七成，重复转发会让 SSE 流量翻倍。
TERMINAL_EVENT_TYPES = FAILED_TERMINALS | CANCELLED_TERMINALS | {"turn.completed"}
TRACE_EVENT_TYPES = {
    "tool.started",
    "tool.completed",
    "message.assistant.thinking",
    "message.assistant.thinking.delta",
    "frontend_tool.deferred",
    "usage.updated",
    "context.compacted",
    "turn.progress",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "turn.cancelled",
    "turn.interrupted",
    "turn.outcome_unknown",
    "turn.recovery_required",
}
SAFE_RUNTIME_ERRORS = {
    "SUBSCRIPTION_NO_PROGRESS",
    "TOOL_CONTINUATION_REQUIRED",
    "TOOL_RESULT_CONFLICT",
    "TOOL_NOT_FOUND",
    "CONTEXT_STALE",
    "PERSISTENCE_OUTCOME_UNKNOWN",
    "claude_auth_failed",
    "claude_rate_limited",
    "claude_timeout",
    "claude_unavailable",
    "mcp_unavailable",
}


class AgUiEventMapper:
    def __init__(
        self,
        thread_id: str,
        run_id: str,
        bridge: RunFrontendToolBridge | None,
    ) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.message_id = f"{run_id}:assistant"
        self.text_started = False
        self.text_ended = False
        self.terminal = False
        self.run_started = False
        self.subscription_notice_count = 0
        self.subscription_notice_ids: set[str] = set()
        self.assistant_segment_count = 0
        self.bridge = bridge

    def map(
        self,
        event_type: str,
        payload: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> list[BaseEvent]:
        # `_map_core` 命中分支即 return，所以 trace 只能在它之外统一插入。
        # 终态要在调用前取——`_map_core` 会把 self.terminal 置为 True。
        was_terminal = self.terminal
        traced = not was_terminal and event_type in TRACE_EVENT_TYPES
        if traced and event_type in TERMINAL_EVENT_TYPES:
            # 客户端校验器在 RUN_FINISHED／RUN_ERROR 之后拒收任何事件，
            # 所以终态的 trace 必须排在核心事件之前，否则每个 turn 收尾
            # 都会抛 AGUIError 打断整条流。
            return [self._trace(event_type, payload, occurred_at)] + self._map_core(
                event_type, payload
            )
        events = self._map_core(event_type, payload)
        if traced:
            events.append(self._trace(event_type, payload, occurred_at))
        return events

    def _trace(
        self,
        event_type: str,
        payload: dict[str, Any],
        occurred_at: datetime | None,
    ) -> CustomEvent:
        return CustomEvent(
            name="workspace.trace",
            value={
                "event_type": event_type,
                "turn_id": self.run_id,
                "at": occurred_at.isoformat() if occurred_at else None,
                "payload": payload,
            },
        )

    def _map_core(self, event_type: str, payload: dict[str, Any]) -> list[BaseEvent]:
        if self.terminal:
            return []
        if event_type == "turn.started":
            if self.run_started:
                return []
            self.run_started = True
            return [RunStartedEvent(threadId=self.thread_id, runId=self.run_id)]
        if event_type == "turn.progress":
            return [
                CustomEvent(
                    name="workspace.progress",
                    value={
                        "phase": str(payload.get("phase", "")),
                        "message": str(payload.get("message", "")),
                    },
                )
            ]
        if event_type == "message.assistant.delta":
            return self._append_text(str(payload.get("text", "")))
        if event_type == "subscription.progress":
            text = str(payload.get("text", "")).strip()
            if not text:
                return []
            self.subscription_notice_count += 1
            notice_id = payload.get("noticeId") or f"{self.run_id}:subscription:{self.subscription_notice_count}"
            if not isinstance(notice_id, str) or notice_id in self.subscription_notice_ids:
                return []
            self.subscription_notice_ids.add(notice_id)
            preceding = self._close_text()
            if self.text_started:
                # Later model prose belongs after this progress message, rather
                # than being appended above it to the earlier understanding.
                self.assistant_segment_count += 1
                self.message_id = f"{self.run_id}:assistant:{self.assistant_segment_count}"
                self.text_started = self.text_ended = False
            return preceding + [
                TextMessageStartEvent(messageId=notice_id, role="assistant"),
                TextMessageContentEvent(messageId=notice_id, delta=text),
                TextMessageEndEvent(messageId=notice_id),
                CustomEvent(name="workspace.subscription_progress", value={**payload, "noticeId": notice_id}),
            ]
        if event_type == "message.assistant.completed":
            if self.text_started:
                return []
            return self._append_text(str(payload.get("text", "")))
        if event_type == "tool.started":
            return self._tool_started(payload)
        if event_type == "frontend_tool.deferred":
            return self._frontend_tool_deferred(payload)
        if event_type == "turn.completed":
            return self._finish(success=True)
        if event_type in CANCELLED_TERMINALS:
            return self._finish(success=False)
        if event_type in FAILED_TERMINALS:
            return self._fail(payload)
        return []

    def _append_text(self, text: str) -> list[BaseEvent]:
        if not text or self.text_ended:
            return []
        events: list[BaseEvent] = []
        if not self.text_started:
            self.text_started = True
            events.append(
                TextMessageStartEvent(messageId=self.message_id, role="assistant")
            )
        events.append(TextMessageContentEvent(messageId=self.message_id, delta=text))
        return events

    def _close_text(self) -> list[BaseEvent]:
        if not self.text_started or self.text_ended:
            return []
        self.text_ended = True
        return [TextMessageEndEvent(messageId=self.message_id)]

    def _tool_started(self, payload: dict[str, Any]) -> list[BaseEvent]:
        tool_call_id = str(payload.get("tool_use_id", ""))
        if not tool_call_id or self.bridge is None:
            return []
        call = self.bridge.lookup_call(tool_call_id)
        if call is None:
            return []
        try:
            arguments = json.loads(call.arguments_json)
        except json.JSONDecodeError:
            arguments = {}
        arguments_json = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return [
            ToolCallStartEvent(
                toolCallId=tool_call_id,
                toolCallName=call.public_name,
                parentMessageId=self.message_id,
            ),
            ToolCallArgsEvent(toolCallId=tool_call_id, delta=arguments_json),
            ToolCallEndEvent(toolCallId=tool_call_id),
        ]

    def _frontend_tool_deferred(
        self, payload: dict[str, Any]
    ) -> list[BaseEvent]:
        tool_call_id = str(payload.get("tool_use_id", ""))
        public_name = str(payload.get("name", ""))
        arguments = payload.get("arguments")
        if not tool_call_id or not public_name or not isinstance(arguments, dict):
            return []
        arguments_json = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._close_text() + [
            ToolCallStartEvent(
                toolCallId=tool_call_id,
                toolCallName=public_name,
                parentMessageId=self.message_id,
            ),
            ToolCallArgsEvent(toolCallId=tool_call_id, delta=arguments_json),
            ToolCallEndEvent(toolCallId=tool_call_id),
        ]

    def _finish(self, *, success: bool) -> list[BaseEvent]:
        events = self._close_text()
        self.terminal = True
        outcome = (
            RunFinishedSuccessOutcome()
            if success
            else RunFinishedInterruptOutcome(
                interrupts=[
                    Interrupt(
                        id=f"{self.run_id}:cancelled",
                        reason="cancelled",
                        message="The Agent Run was cancelled.",
                    )
                ]
            )
        )
        events.append(
            RunFinishedEvent(
                threadId=self.thread_id,
                runId=self.run_id,
                outcome=outcome,
            )
        )
        return events

    def _fail(self, payload: dict[str, Any]) -> list[BaseEvent]:
        events = self._close_text()
        self.terminal = True
        code = str(payload.get("code", ""))
        safe = code in SAFE_RUNTIME_ERRORS
        events.append(
            RunErrorEvent(
                code=code if safe else "RUN_ERROR",
                message=(
                    str(payload.get("message", "Agent 运行失败。"))
                    if safe
                    else "The Agent Run could not be completed."
                ),
            )
        )
        return events
