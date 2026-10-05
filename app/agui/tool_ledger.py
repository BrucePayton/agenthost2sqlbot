from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

READBACK_TOOLS = frozenset(
    {
        "dashboard.get_widget_config",
        "dashboard.get_widget_data",
        "dashboard.get_publish_readiness",
        "dashboard.get_structure",
    }
)
# 只有改查询语义的写需要数据级读回。布局、样式、重命名、删除和发布的
# 回执已带 before/after 或 affectedWidgetIds，再查数据不会增加验证证据。
READBACK_REQUIRED_WRITE_TOOLS = frozenset(
    {
        "dashboard.apply_widget_spec",
        "dashboard.set_widget_dataset",
        "dashboard.apply_global_filter_edits",
    }
)
READBACK_REQUIRED_EDIT_CAPABILITY_PREFIXES = ("metric.comparison.",)


def query_semantics_write_targets(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> tuple[tuple[str, ...], bool] | None:
    """改查询语义的写所涉及的 (目标 widgetIds, 是否 dashboard 级)；否则 None。

    空读回门、写预算和读回义务都由这一个判定驱动，避免三处各自漂移。

    dryRun 豁免只认 dashboard.apply_widget_spec——它是契约里唯一定义 dryRun
    的工具。平台不校验前端工具的输入，若对所有工具都豁免，被门禁拦住的模型
    只要在别的写工具上附一个 dryRun 键就能抬起三道门。
    """
    if tool_name == "dashboard.apply_widget_spec":
        if arguments.get("dryRun"):
            return None
        widget_id = str(arguments.get("widgetId") or "")
        # create 路径入参没有 id，回执回来后再补绑目标。
        return ((widget_id,) if widget_id else (), False)
    if tool_name == "dashboard.set_widget_dataset":
        widget_id = str(arguments.get("widgetId") or "")
        return ((widget_id,) if widget_id else (), False)
    if tool_name == "dashboard.apply_global_filter_edits":
        # 全局筛选没有 widget 级的事实来源：回执的 affectedWidgetIds 只列
        # 「因筛选被删而联动失效」的组件，改筛选时通常是空的。
        return ((), True)
    if tool_name != "dashboard.apply_widget_edits":
        return None
    operations = arguments.get("operations")
    if not isinstance(operations, list):
        return None
    touched: list[str] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        edits = operation.get("edits")
        if not isinstance(edits, list):
            continue
        for edit in edits:
            if not isinstance(edit, Mapping):
                continue
            capability_id = str(edit.get("capabilityId") or "")
            if capability_id.startswith(READBACK_REQUIRED_EDIT_CAPABILITY_PREFIXES):
                widget_id = str(operation.get("widgetId") or "")
                if widget_id and widget_id not in touched:
                    touched.append(widget_id)
                break
    if not touched:
        return None
    return tuple(touched), False
# Readback tools can truncate rows and return partial while still executing.
FRONTEND_OK_STATUSES = frozenset({"success", "partial"})
# 发布目标在同一仪表盘的修复流程内最多延续这么多个用户轮次（超时未发布即清空）。
PUBLISH_OBJECTIVE_MAX_TURNS = 5


def arguments_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# 能作为「这次写确实落到了这个组件上」证据的读回状态。missing/error/loading
# 不算——否则读一个根本不存在的 id 就能平凡地满足验证。unavailable 要算：
# 消息组件没有查询路径，对它做数据级验证本就不可能。
VERIFYING_WIDGET_STATES = frozenset({"ready", "empty", "unavailable"})


def readback_widget_verifications(content: str) -> dict[str, str]:
    """读回结果里每个组件的状态；state 缺席时按 rows 推断。"""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    widgets = data.get("widgets") if isinstance(data, Mapping) else None
    if not isinstance(widgets, list):
        return {}
    states: dict[str, str] = {}
    for widget in widgets:
        if not isinstance(widget, Mapping):
            continue
        widget_id = str(widget.get("widgetId") or "")
        if not widget_id:
            continue
        state = str(widget.get("state") or "")
        if not state:
            rows = widget.get("rows")
            state = "empty" if isinstance(rows, list) and not rows else "ready"
        states[widget_id] = state
    return states


def parse_receipt_widget_id(content: str) -> str:
    """写回执里的最终 widgetId；create 路径靠它把义务绑到新建的组件上。"""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, Mapping):
        return ""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return ""
    return str(data.get("widgetId") or "")


def parse_receipt_probe_states(content: str) -> dict[str, str]:
    """写回执自带的探针结果；rowCount 只有在探针真的跑过时才算读回证据。

    探针跑不起来时前端返回 rowCount:0 + unavailableReason（并把回执降为 partial），
    那不是"查到 0 行"，不能据此解除读回义务。
    """
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    if payload.get("status") not in ("success", "partial"):
        return {}
    data = payload.get("data")
    if not isinstance(data, Mapping) or data.get("persisted") is not True:
        return {}
    widget_id = str(data.get("widgetId") or "")
    probe = data.get("probe")
    if not widget_id or not isinstance(probe, Mapping):
        return {}
    if probe.get("unavailableReason"):
        return {}
    row_count = probe.get("rowCount")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        return {}
    return {widget_id: "empty" if row_count == 0 else "ready"}


def readback_widget_states(content: str) -> dict[str, bool]:
    """Maps each read-back Widget id to whether it came back with no rows.

    An empty read-back is an answer about the data, not a defect in the Widget,
    so the caller uses it to stop the model from rewriting the same Widget in
    search of a query that returns something.
    """
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    data = payload.get("data")
    widgets = data.get("widgets") if isinstance(data, Mapping) else None
    if not isinstance(widgets, list):
        return {}
    states: dict[str, bool] = {}
    for widget in widgets:
        if not isinstance(widget, Mapping):
            continue
        widget_id = str(widget.get("widgetId") or "")
        if not widget_id:
            continue
        rows = widget.get("rows")
        states[widget_id] = widget.get("state") == "empty" or (
            isinstance(rows, list) and not rows
        )
    return states


def parse_frontend_result(content: str) -> tuple[bool, int | None, bool]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False, None, False
    if not isinstance(payload, Mapping):
        return False, None, False
    status = payload.get("status")
    success = status in FRONTEND_OK_STATUSES
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    observed = (
        payload.get("observed")
        if isinstance(payload.get("observed"), Mapping)
        else {}
    )
    revision = data.get("resourceRevision", observed.get("resourceRevision"))
    revision = (
        revision
        if isinstance(revision, int) and not isinstance(revision, bool)
        else None
    )
    persisted = bool(data.get("persisted")) if status == "success" else False
    return success, revision, persisted


def write_requires_readback(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> bool:
    return query_semantics_write_targets(tool_name, arguments) is not None


@dataclass(slots=True)
class ToolOperation:
    tool_use_id: str
    tool_name: str
    arguments_hash: str
    kind: Literal["frontend", "mcp", "other"]
    revision_before: int | None
    execution_result: Literal["pending", "success", "error", "denied"] = "pending"
    revision_after: int | None = None
    write_receipt: bool = False
    readback_receipt: bool = False
    readback_required: bool = False
    dry_run: bool = False
    # 谓词结果在构造时固化：record_call 只拿得到 ToolOperation，看不到原始参数。
    target_widget_ids: tuple[str, ...] = ()
    dashboard_scope: bool = False
    is_query_write: bool = False
    verified: bool = False
    # 空间页没有 resourceRevision，同参读取跨越一次写之后不再是"结果不会不同"。
    write_epoch_before: int = 0
    context_key: str | None = None
    candidate_search_scope: str | None = None
    candidate_count: int | None = None
    subscription_operations: tuple[str, ...] = ()
    subscription_finish: bool = False
    subscription_presentation: dict = field(default_factory=dict)
    subscription_details: list[dict] = field(default_factory=list)
    subscription_origin_run: str = ""
    # Read evidence belongs to the page/editor that dispatched it, even if the
    # user navigates while the tool is running. This is not an execution plan.
    subscription_scope_key: dict = field(default_factory=dict)
    # Program continuations are audited frontend calls, never SDK tool-use blocks.
    origin: Literal["model", "program"] = "model"
    subscription_arguments: dict = field(default_factory=dict)
    # Keep an in-flight identity across new input without charging the new turn.
    carried_from_previous_turn: bool = False


@dataclass(slots=True)
class ThreadLedger:
    user_turn_text: str = ""
    operations: list[ToolOperation] = field(default_factory=list)
    last_tool_names: tuple[str, ...] = ()
    pending_tools_delta: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
    stop_blocked_once: bool = False
    publish_objective: str | None = None
    publish_objective_turns: int = 0
    last_context_hashes: dict[str, str] = field(default_factory=dict)
    empty_readback_widget_ids: set[str] = field(default_factory=set)
    write_epoch: int = 0
    # A technical readback failure is not evidence that another dataset is needed.
    subscription_recovery: str | None = None
    # Task continuity is independent of the per-user-turn tool budget.
    subscription_task: dict = field(default_factory=dict)

    def start_user_turn(self, text: str, *, resource_id: str | None = None) -> None:
        self.user_turn_text = text
        self.operations = [op for op in self.operations if op.execution_result == "pending"
                           and (op.origin == "program" or op.tool_name.startswith("space.message_rule."))]
        for op in self.operations:
            op.carried_from_previous_turn = True
        self.stop_blocked_once = False
        # A new instruction is the user's chance to redirect, so an earlier
        # empty read-back no longer blocks anything.
        self.empty_readback_widget_ids = set()
        self.write_epoch = 0
        self.subscription_recovery = None
        self._advance_publish_objective(text, resource_id)

    def record_readback_states(self, states: Mapping[str, bool]) -> None:
        for widget_id, is_empty in states.items():
            if is_empty:
                self.empty_readback_widget_ids.add(widget_id)
            else:
                self.empty_readback_widget_ids.discard(widget_id)

    def count_calls(self, tool_names: Iterable[str]) -> int:
        wanted = set(tool_names)
        return sum(
            1
            for op in self.operations
            if op.tool_name in wanted
            and not op.carried_from_previous_turn
            and op.execution_result != "denied"
            and not op.dry_run
        )

    def count_catalog_searches(self) -> int:
        return sum(
            1
            for op in self.operations
            if op.kind == "mcp"
            and not op.carried_from_previous_turn
            and op.tool_name.startswith("mcp__davinci_data__catalog_")
            and op.execution_result != "denied"
        )

    def count_probe_calls(self) -> int:
        return sum(
            1
            for op in self.operations
            if op.dry_run and op.execution_result != "denied"
            and not op.carried_from_previous_turn
        )

    def _advance_publish_objective(self, text: str, resource_id: str | None) -> None:
        from app.runtime.publish_intent import (  # 避免循环导入
            user_negates_publish,
            user_requests_publish,
        )

        target = resource_id or ""
        if user_negates_publish(text):
            self.clear_publish_objective()
            return
        if user_requests_publish(text):
            self.publish_objective = target
            self.publish_objective_turns = 0
            return
        if self.publish_objective is None:
            return
        if self.publish_objective != target:
            self.clear_publish_objective()
            return
        self.publish_objective_turns += 1
        if self.publish_objective_turns >= PUBLISH_OBJECTIVE_MAX_TURNS:
            self.clear_publish_objective()

    def clear_publish_objective(self) -> None:
        self.publish_objective = None
        self.publish_objective_turns = 0

    def record_call(self, op: ToolOperation) -> None:
        # A dry run persists nothing, so it owes no read-back; every other
        # query-semantics write does, whether or not the caller said so.
        if op.is_query_write and not op.dry_run:
            op.readback_required = True
        op.write_epoch_before = self.write_epoch
        self.operations.append(op)

    def count_query_writes(self) -> int:
        """本轮已执行的查询语义写次数（写预算用）。按调用计，不按组件计。"""
        return sum(
            1
            for op in self.operations
            if op.is_query_write
            and not op.carried_from_previous_turn
            and op.execution_result != "denied"
            and not op.dry_run
        )

    def identical_calls(
        self,
        tool_name: str,
        arguments_hash_value: str,
        revision_before: int | None,
        context_key: str | None = None,
    ) -> int:
        """Count successful calls only within the same resource and write epoch."""
        return sum(
            1
            for op in self.operations
            if op.tool_name == tool_name
            and not op.carried_from_previous_turn
            and op.arguments_hash == arguments_hash_value
            and op.revision_before == revision_before
            and op.context_key == context_key
            and op.write_epoch_before == self.write_epoch
            and op.execution_result == "success"
        )

    def get(self, tool_use_id: str) -> ToolOperation | None:
        for op in reversed(self.operations):
            if op.tool_use_id == tool_use_id:
                return op
        return None

    def resolve(
        self,
        tool_use_id: str,
        *,
        success: bool,
        revision_after: int | None,
        write_receipt: bool,
    ) -> None:
        op = self.get(tool_use_id)
        if op is None:
            return
        op.execution_result = "success" if success else "error"
        op.revision_after = revision_after
        op.write_receipt = write_receipt
        if success and write_receipt:
            self.write_epoch += 1

    def mark_readback(self, tool_use_id: str) -> None:
        op = self.get(tool_use_id)
        if op is not None:
            op.readback_receipt = True

    def record_candidate_result(self, tool_use_id: str, content: str) -> None:
        """Count actual successful candidate results, not inferred tool intent."""
        op = self.get(tool_use_id)
        if op is None or not op.candidate_search_scope or op.execution_result != "success":
            return
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            op.candidate_count = len(data["results"])

    def record_subscription_result(self, tool_use_id: str, content: str) -> None:
        """Retain recovery state across searches; clear it only after a valid draft read/write."""
        op = self.get(tool_use_id)
        if op is None or not op.tool_name.startswith("space.message_rule."):
            return
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            return
        if not isinstance(payload, dict):
            return
        error = payload.get("error") or {}
        if not isinstance(error, dict):
            error = {}
        invalid_output = error.get("code") in {"OUTPUT_SCHEMA_INVALID", "READBACK_SCHEMA_INVALID"} or (
            error.get("code") == "EXECUTION_FAILED"
            and "output failed its declared schema" in str(error.get("message", "")).lower()
        )
        if op.execution_result == "error" and (invalid_output or self.subscription_recovery):
            if op.tool_name.endswith(".get_context"):
                self.subscription_recovery = "blocked"
            elif invalid_output:
                self.subscription_recovery = "read_required"
            return
        if op.execution_result != "success" or op.tool_name.endswith(".search_options"):
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        if op.tool_name.endswith('.get_context') and 'scope' in data and 'activeDraft' not in data:
            self.subscription_recovery = None
            return
        draft = data.get("activeDraft", data)
        config = draft.get("configuration") if isinstance(draft, dict) else None
        if not isinstance(config, dict):
            return
        gaps = config.get("unresolved", [])
        if not isinstance(gaps, list):
            return
        self.subscription_recovery = "partial" if any(
            isinstance(gap, dict) and str(gap.get("message", "")).startswith("READBACK_SCHEMA_INVALID:")
            for gap in gaps
        ) else None

    def subscription_recovery_denial(self, name: str, arguments: Mapping[str, Any]) -> str | None:
        """Block source substitution and duplicate creation while technical recovery is pending."""
        recovery = self.subscription_recovery
        if not recovery:
            return None
        if name == "space.message_rule.get_context" and recovery == "blocked":
            return "SUBSCRIPTION_RECOVERY_BLOCKED: 当前草稿回读仍失败；停止重复调用并说明技术阻塞。不能声称草稿损坏或要求换数据源。"
        if name == "space.message_rule.start_draft":
            return "SUBSCRIPTION_RECOVERY_REQUIRED: 上次调用可能已创建草稿，先读取当前草稿；不要重复新建、清空或改用其他数据源。"
        source_search = name == "space.message_rule.search_options" and arguments.get("kind") in {"dataset", "dashboard", "alert_widget", "template"}
        catalog_search = name.startswith("mcp__davinci_data__") and "search_datasets" in name
        source_write = name == "space.message_rule.apply_draft" and any(
            isinstance(op, Mapping) and (op.get("operation") == "bind_dataset_query" or (
                op.get("operation") == "upsert_dataset_query" and (not op.get("queryRef") or op.get("datasetRef"))
            )) for op in arguments.get("operations", [])
        )
        if source_search or catalog_search or source_write:
            return "SUBSCRIPTION_SOURCE_PRESERVED: 当前问题是配置回读失败，不是找不到数据。保留原组件、数据源及时间口径；先恢复草稿，不能搜索或绑定替代数据源。"
        return None

    def repeated_failed_subscription_call(self, name: str, args_hash: str, context_key: str | None) -> bool:
        """Stop identical failed writes; a successful context read or new user turn permits recovery."""
        if not context_key or name.endswith((".get_context", ".search_options", ".review_draft")):
            return False
        failed = 0
        for op in reversed(self.operations):
            if op.carried_from_previous_turn:
                continue
            if op.context_key != context_key:
                break
            if op.execution_result == "success" and op.tool_name.startswith("space.message_rule."):
                return False
            if op.tool_name == name and op.arguments_hash == args_hash and op.execution_result == "error":
                failed += 1
                if failed >= 2:
                    return True
        return False

    def candidate_search_exhausted(self, scope: str, context_key: str | None) -> bool:
        """Stop keyword churn after three empty reads for the same scope/role.

        A successful discovery, a write, a new page, or a new user turn resets
        this boundary. It never interrupts a write or treats failures as empty.
        """
        empty = 0
        for op in reversed(self.operations):
            if op.carried_from_previous_turn:
                continue
            if op.candidate_search_scope != scope or op.context_key != context_key:
                continue
            if op.write_epoch_before != self.write_epoch:
                break
            if op.candidate_count is None:
                continue
            if op.candidate_count > 0:
                return False
            empty += 1
            if empty >= 3:
                return True
        return False

    def bind_write_receipt(self, tool_use_id: str, widget_id: str) -> None:
        """用写回执里的 widgetId 补上目标。

        create 路径的入参里没有任何 id，最终 id 只在回执里出现；不补绑的话
        这条义务永远清不掉。
        """
        op = self.get(tool_use_id)
        if op is None or not widget_id:
            return
        if not op.target_widget_ids:
            op.target_widget_ids = (widget_id,)

    def apply_readback_verification(
        self,
        widget_states: Mapping[str, str],
        observed_revision: int | None,
    ) -> None:
        """用一次 get_widget_data 的结果去核销写义务。"""
        verified_ids = {
            widget_id
            for widget_id, state in widget_states.items()
            if state in VERIFYING_WIDGET_STATES
        }
        for op in self.operations:
            if not (op.write_receipt and op.readback_required) or op.verified:
                continue
            if op.dashboard_scope:
                op.verified = True
                continue
            if not op.target_widget_ids:
                # create 回执还没补绑目标：先留着，别自动清成假验证。
                continue
            if not set(op.target_widget_ids) <= verified_ids:
                continue
            if (
                op.tool_name == "dashboard.apply_widget_spec"
                and op.revision_after is not None
                and observed_revision is not None
                and observed_revision < op.revision_after
            ):
                # 读到的是写之前的版本，不算验证。
                continue
            op.verified = True

    def has_unverified_write(self) -> bool:
        return any(
            op.write_receipt and op.readback_required and not op.verified
            for op in self.operations
        )

    def diff_tool_names(
        self, current: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        previous = set(self.last_tool_names)
        now = set(current)
        return tuple(sorted(now - previous)), tuple(sorted(previous - now))


class ToolLedgerStore:
    def __init__(self) -> None:
        self._ledgers: dict[str, ThreadLedger] = {}

    def get(self, thread_id: str) -> ThreadLedger:
        ledger = self._ledgers.get(thread_id)
        if ledger is None:
            ledger = ThreadLedger()
            self._ledgers[thread_id] = ledger
        return ledger

    def drop(self, thread_id: str) -> None:
        self._ledgers.pop(thread_id, None)
