from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_digest(content: str, error: str | None) -> str:
    """Match the exact content and the error flag delivered to the model SDK."""
    return _digest(_canonical_json({"content": content, "is_error": bool(error)}))


MAX_KEPT_RESULT_CHARS = 4000


def _mark_discarded_structure_page(
    source: dict[str, Any], compact: dict[str, Any]
) -> None:
    """Describe discarded structure rows without exposing a skip-ahead cursor."""
    widgets = source.get("widgets")
    if not isinstance(widgets, list):
        return
    compact["returnedCount"] = 0
    compact.pop("nextCursor", None)
    if widgets or source.get("returnedCount", 0):
        compact["hasMore"] = True
    compact["readbackAction"] = "dashboard.get_structure"
    compact["readbackRequired"] = True
    compact["restartRead"] = True
    compact["reason"] = "stored recovery omitted structure widgets; restart from page one"


def _truncate_result(content: str) -> str:
    """Keep a replayable recovery receipt without altering the SDK result."""
    if len(content) <= MAX_KEPT_RESULT_CHARS:
        return content
    try:
        result = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return content[: MAX_KEPT_RESULT_CHARS - 6] + "…[截断]"
    if not isinstance(result, dict):
        return _canonical_json(
            {"status": "unknown", "truncated": True, "originalType": type(result).__name__}
        )

    compact: dict[str, Any] = {
        "status": result.get("status", "unknown"),
        "truncated": True,
    }
    for key in (
        "summary",
        "resourceRevision",
        "totalCount",
        "rootCount",
        "childCount",
        "returnedCount",
        "hasMore",
        "nextCursor",
    ):
        if key in result:
            value = result[key]
            compact[key] = value[:1000] if key == "summary" and isinstance(value, str) else value
    _mark_discarded_structure_page(result, compact)
    data = result.get("data")
    if isinstance(data, dict):
        kept_data = {
            key: data[key]
            for key in (
                "resourceId",
                "resourceRevision",
                "persisted",
                "state",
                "layoutChangeCount",
                "returnedLayoutChangeCount",
                "layoutChangesTruncated",
                "issueCount",
                "returnedIssueCount",
                "issuesTruncated",
                "readbackAction",
                "summary",
                "totalCount",
                "rootCount",
                "childCount",
                "returnedCount",
                "hasMore",
                "nextCursor",
            )
            if key in data
        }
        if "layoutChangeCount" in kept_data:
            kept_data["returnedLayoutChangeCount"] = 0
            kept_data["layoutChangesTruncated"] = True
            kept_data["readbackAction"] = "dashboard.get_structure"
        _mark_discarded_structure_page(data, kept_data)
        if kept_data:
            compact["data"] = kept_data
    raw_issues = result.get("issues")
    if isinstance(raw_issues, list):
        kept_issues = []
        for issue in raw_issues[:10]:
            if not isinstance(issue, dict):
                continue
            kept_issue = {
                key: (
                    issue[key][:1000]
                    if key == "message" and isinstance(issue[key], str)
                    else issue[key]
                )
                for key in ("code", "message", "retryable", "targetRef")
                if key in issue
            }
            widget_ids = issue.get("widgetIds")
            if isinstance(widget_ids, list):
                kept_issue["widgetIdCount"] = issue.get(
                    "widgetIdCount", len(widget_ids)
                )
                kept_issue["returnedWidgetIdCount"] = 0
                widget_id_count = kept_issue["widgetIdCount"]
                kept_issue["widgetIdsTruncated"] = bool(
                    issue.get("widgetIdsTruncated")
                    or widget_id_count
                    or issue.get("returnedWidgetIdCount", len(widget_ids))
                    != widget_id_count
                )
                if widget_id_count:
                    compact["readbackAction"] = "dashboard.get_structure"
            kept_issues.append(kept_issue)
        compact["issues"] = kept_issues
        issue_count = result.get("issueCount", len(raw_issues))
        compact["issueCount"] = issue_count
        compact["returnedIssueCount"] = len(kept_issues)
        compact["issuesTruncated"] = bool(
            result.get("issuesTruncated")
            or len(kept_issues) < issue_count
            or any(issue.get("widgetIdsTruncated") for issue in kept_issues)
        )
    for key in (
        "error",
        "observed",
        "readbackAction",
    ):
        if key in result:
            compact[key] = result[key]
    serialized = _canonical_json(compact)
    while len(serialized) > MAX_KEPT_RESULT_CHARS and compact.get("issues"):
        compact["issues"].pop()
        compact["returnedIssueCount"] = len(compact["issues"])
        compact["issuesTruncated"] = True
        serialized = _canonical_json(compact)
    if len(serialized) <= MAX_KEPT_RESULT_CHARS:
        return serialized
    # Error details may themselves be unexpectedly large. Preserve the stable
    # error identity and persistence fields rather than slicing serialized JSON.
    error = compact.get("error")
    if isinstance(error, dict):
        compact["error"] = {
            key: (
                error[key][:1000]
                if key == "message" and isinstance(error[key], str)
                else error[key]
            )
            for key in ("code", "message", "retryable", "layer")
            if key in error
        }
    serialized = _canonical_json(compact)
    if len(serialized) <= MAX_KEPT_RESULT_CHARS:
        return serialized
    fallback = {"status": compact["status"], "truncated": True}
    compact_data = compact.get("data")
    if isinstance(compact_data, dict):
        fallback_data = {
            key: compact_data[key]
            for key in ("resourceId", "resourceRevision", "persisted", "state")
            if key in compact_data
        }
        if fallback_data:
            fallback["data"] = fallback_data
    if "resourceRevision" in compact:
        fallback["resourceRevision"] = compact["resourceRevision"]
    if "readbackAction" in compact:
        fallback["readbackAction"] = compact["readbackAction"]
    compact_error = compact.get("error")
    if isinstance(compact_error, dict):
        fallback["error"] = {
            key: compact_error[key]
            for key in ("code", "message", "retryable", "layer")
            if key in compact_error
        }
    return _canonical_json(fallback)


def deferred_argument_hash(arguments: Any) -> str:
    """The hash a recorded call carries, for looking one up by its arguments."""
    return _digest(_canonical_json(json.loads(_canonical_json(arguments))))


class DeferredFrontendToolError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class DeferredFrontendToolCall:
    thread_id: str
    origin_run_id: str
    tool_call_id: str
    public_name: str
    arguments: dict[str, Any]
    argument_hash: str
    origin: Literal["model", "program"] = "model"
    recorded_at_monotonic: float | None = field(
        default_factory=time.monotonic,
        compare=False,
        repr=False,
    )

    @classmethod
    def create(
        cls,
        *,
        thread_id: str,
        origin_run_id: str,
        tool_call_id: str,
        public_name: str,
        arguments: dict[str, Any],
        origin: Literal["model", "program"] = "model",
    ) -> DeferredFrontendToolCall:
        copied_arguments = json.loads(_canonical_json(arguments))
        return cls(
            thread_id=thread_id,
            origin_run_id=origin_run_id,
            tool_call_id=tool_call_id,
            public_name=public_name,
            arguments=copied_arguments,
            argument_hash=_digest(_canonical_json(copied_arguments)),
            origin=origin,
        )


@dataclass(frozen=True, slots=True)
class DeferredToolConsumption:
    call: DeferredFrontendToolCall
    status: Literal["accepted", "replayed"]


@dataclass(slots=True)
class _StoredCall:
    call: DeferredFrontendToolCall
    continuation_run_id: str | None = None
    result_hash: str | None = None
    # True when the reply that suspended this call went on to have another
    # ToolCall denied: the transcript closes the suspended call with a
    # placeholder, so the real result needs to be restated to be seen.
    mixed_batch: bool = False
    result_content: str | None = None


class DeferredFrontendToolStore:
    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], _StoredCall] = OrderedDict()
        self._lock = asyncio.Lock()
        self._load_history: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = None

    def bind_history_loader(self, loader: Callable[[str], Awaitable[list[dict[str, Any]]]]) -> None:
        """Use durable call identity on cache miss, including SDK replay after eviction."""
        self._load_history = loader

    async def record(
        self, call: DeferredFrontendToolCall
    ) -> DeferredFrontendToolCall:
        key = (call.thread_id, call.tool_call_id)
        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                if existing.call != call:
                    raise DeferredFrontendToolError(
                        "TOOL_RESULT_CONFLICT",
                        "The deferred ToolCall conflicts with the recorded call.",
                    )
                self._entries.move_to_end(key)
                return existing.call
            self._entries[key] = _StoredCall(call=call)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return call

    async def restore(
        self, call: DeferredFrontendToolCall, *, continuation_run_id: str | None = None,
        content: str | None = None, error: str | None = None,
        mixed_batch: bool = False,
    ) -> None:
        """Restore trusted persisted state; an uncommitted memory binding is not authoritative."""
        key = (call.thread_id, call.tool_call_id)
        async with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.call != call:
                raise DeferredFrontendToolError("TOOL_RESULT_CONFLICT", "历史调用与当前记录冲突。")
            self._entries[key] = _StoredCall(
                call=existing.call if existing else call,
                continuation_run_id=continuation_run_id,
                result_hash=_result_digest(content, error) if continuation_run_id and content is not None else None,
                mixed_batch=mixed_batch or bool(existing and existing.mixed_batch),
                result_content=_truncate_result(content) if content is not None and not error else None,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    async def mark_mixed_batch(
        self, thread_id: str, tool_call_ids: Iterable[str]
    ) -> None:
        async with self._lock:
            for tool_call_id in tool_call_ids:
                stored = self._entries.get((thread_id, tool_call_id))
                if stored is not None:
                    stored.mixed_batch = True

    async def mixed_batch_ids(
        self, thread_id: str, tool_call_ids: Iterable[str]
    ) -> set[str]:
        async with self._lock:
            return {
                tool_call_id
                for tool_call_id in tool_call_ids
                if (stored := self._entries.get((thread_id, tool_call_id)))
                is not None
                and stored.mixed_batch
            }

    async def latest_result_content(
        self, thread_id: str, public_name: str, argument_hash: str
    ) -> str | None:
        """The most recent answer this Thread got for this exact call."""
        async with self._lock:
            for (entry_thread, _), stored in reversed(self._entries.items()):
                if (
                    entry_thread == thread_id
                    and stored.call.public_name == public_name
                    and stored.call.argument_hash == argument_hash
                    and stored.result_content is not None
                ):
                    return stored.result_content
        return None

    async def get(
        self, thread_id: str, tool_call_id: str
    ) -> DeferredFrontendToolCall | None:
        async with self._lock:
            stored = self._entries.get((thread_id, tool_call_id))
            if stored is not None:
                return stored.call
        if self._load_history is not None:
            for state in await self._load_history(thread_id):
                if state["call"].tool_call_id == tool_call_id:
                    result = state["tool_result"]
                    await self.restore(state["call"], continuation_run_id=state["continuation_run_id"],
                                       content=result["content"] if result else None,
                                       error="error" if result and result.get("is_error") else None,
                                       mixed_batch=state.get("requires_restatement", False))
                    return state["call"]
        return None

    async def pending_layout_call(self, thread_id: str, tool_call_id: str) -> bool:
        """Authorize private computation only for a recent, unanswered layout call."""
        await self.get(thread_id, tool_call_id)
        async with self._lock:
            stored = self._entries.get((thread_id, tool_call_id))
            return bool(stored and stored.result_hash is None
                        and stored.continuation_run_id is None
                        and stored.call.public_name == "dashboard.set_widget_layout"
                        and stored.call.recorded_at_monotonic is not None
                        and 0 <= time.monotonic()-stored.call.recorded_at_monotonic < 120)

    async def consume(
        self,
        *,
        thread_id: str,
        continuation_run_id: str,
        tool_call_id: str,
        content: str,
        error: str | None,
        validate_only: bool = False,
    ) -> DeferredToolConsumption:
        """Check a result before persistence, or bind it after durable acceptance."""
        result_hash = _result_digest(content, error)
        key = (thread_id, tool_call_id)
        async with self._lock:
            stored = self._entries.get(key)
            if stored is None:
                if any(
                    candidate_tool_call_id == tool_call_id
                    for _, candidate_tool_call_id in self._entries
                ):
                    raise DeferredFrontendToolError(
                        "SESSION_MISMATCH",
                        "The Tool Result belongs to a different Thread.",
                    )
                raise DeferredFrontendToolError(
                    "TOOL_NOT_FOUND",
                    "No deferred frontend ToolCall matches this Tool Result.",
                    404,
                )
            if stored.continuation_run_id is None:
                if not validate_only:
                    stored.continuation_run_id = continuation_run_id
                    stored.result_hash = result_hash
                    if error is None:
                        stored.result_content = _truncate_result(content)
                    self._entries.move_to_end(key)
                return DeferredToolConsumption(stored.call, "accepted")
            if (
                stored.continuation_run_id == continuation_run_id
                and stored.result_hash == result_hash
            ):
                self._entries.move_to_end(key)
                return DeferredToolConsumption(stored.call, "replayed")
            raise DeferredFrontendToolError(
                "TOOL_RESULT_CONFLICT",
                "The Tool Result conflicts with the consumed continuation.",
            )
