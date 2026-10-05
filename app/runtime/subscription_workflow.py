"""Subscription task checkpoints survive clarification turns and worker restarts."""

import json
import re
from copy import deepcopy
from typing import Any

from app.runtime.contracts import RuntimeRequest, RuntimeToolResult


SUBSCRIPTION_STEPS = {
    "trigger": "执行时间", "datasets": "数据查询", "conditions": "触发条件",
    "pushMode": "数据推送方式", "receivers": "接收人", "content": "消息内容",
    "finalize": "规则名称",
}
OPERATION_STEPS = {
    "set_schedule": "trigger", "set_send_rule": "trigger",
    "bind_dataset_query": "datasets", "upsert_dataset_query": "datasets",
    "import_widget_source": "datasets", "remove_dataset_query": "datasets",
    "set_trigger_conditions": "conditions", "set_push_mode": "pushMode",
    "set_recipients": "receivers", "set_content": "content",
    "patch_content": "content", "set_finalize": "finalize",
}


def _task_id(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 256 and value.strip() else None


def _revision(value: Any) -> int | None:
    return value if type(value) is int and value >= 1 else None


def _native_context(request: RuntimeRequest) -> tuple[str | None, int | None]:
    for item in reversed(request.context_items):
        if item.description != "订阅配置上下文":
            continue
        try:
            context = json.loads(item.value)
        except (ValueError, TypeError):
            continue
        if isinstance(context, dict) and _task_id(context.get("taskId")):
            return _task_id(context["taskId"]), _revision(context.get("revision"))
    return None, None


def _receipt_payload(receipt: RuntimeToolResult) -> dict:
    try:
        payload = json.loads(receipt.content)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_receipt(task: dict, draft: dict) -> bool:
    task_id, receipt_id = _task_id(task.get("native_task_id")), _task_id(draft.get("taskId"))
    if task_id and receipt_id and task_id != receipt_id:
        return False
    current, received = _revision(task.get("revision")), _revision(draft.get("revision"))
    if receipt_id and not task_id:
        return True  # First trusted native identity supersedes legacy unscoped facts.
    return not (current is not None and received is not None and received < current)


def subscription_presentation(arguments: dict) -> dict:
    """Only accept bounded display metadata; it never changes business configuration."""
    value = arguments.get("presentation")
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ("step", "nextStep")
              if isinstance(value.get(key), str) and value[key] in SUBSCRIPTION_STEPS}
    if isinstance(value.get("purpose"), str) and value["purpose"].strip():
        result["purpose"] = value["purpose"].strip()[:400]
    return result


def subscription_operation_details(operations: list) -> list[dict]:
    """Keep small human-readable action facts, not another editable draft."""
    details = []
    for operation in operations if isinstance(operations, list) else []:
        if not isinstance(operation, dict):
            continue
        name = operation.get("operation")
        step = OPERATION_STEPS.get(name)
        if not step:
            continue
        label = SUBSCRIPTION_STEPS[step]
        if name == "set_schedule":
            times = operation.get("times")
            frequency = operation.get("frequency")
            prefix = {"daily": "每个工作日" if operation.get("dailyMode") == "workday" else "每天",
                      "weekly": "每周", "monthly": "每月"}.get(frequency, "")
            if frequency == "weekly" and isinstance(operation.get("weekdays"), list):
                names = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日", 0: "日"}
                days = [names[day] for day in operation["weekdays"] if isinstance(day, int) and day in names]
                prefix += "、".join(days)
            elif frequency == "monthly" and isinstance(operation.get("monthDays"), list):
                days = ["最后一天" if day == "eom" else f"{day}日"
                        for day in operation["monthDays"]
                        if day == "eom" or isinstance(day, int) and 1 <= day <= 31]
                prefix += "、".join(days)
            if frequency == "once" and isinstance(operation.get("executeAt"), str):
                label = f"{operation['executeAt'][:100]}单次执行"
            if isinstance(times, list):
                valid = [time for time in times if isinstance(time, str)
                         and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time)]
                if valid and prefix:
                    label = f"{prefix}{'、'.join(valid)}执行"[:250]
        elif name == "set_send_rule":
            label = {"scheduled_no_dataset": "定时发送通知", "scheduled_dataset": "定时推送数据",
                     "conditional": "满足条件时发送"}.get(operation.get("sendRule"), "发送方式")
        elif name == "import_widget_source":
            label = "载入指标的数据配置"
        elif name == "remove_dataset_query":
            label = "移除指定数据查询"
        elif name in {"set_content", "patch_content"}:
            parts = []
            if "title" in operation:
                parts.append("消息标题")
            if "titleColor" in operation:
                parts.append("标题颜色")
            if "components" in operation:
                parts.append("消息正文")
            label = "、".join(parts) or label
        elif name == "set_push_mode":
            label = {"all": "全部数据生成一张卡片", "single": "全部数据生成一张卡片",
                     "record": "按记录生成卡片", "group": "按分组生成卡片"}.get(
                         operation.get("mode"), label)
        elif name == "set_finalize" and isinstance(operation.get("ruleName"), str):
            label = f"规则名称“{operation['ruleName'][:120]}”"
        details.append({"operation": name, "step": step, "label": label})
    return details


def progress_payload(task: dict, *, run_id: str, identity: str, phase: str,
                     text: str, step: str | None = None) -> dict | None:
    """Dedupe execution identities across streams and worker restarts, not prose."""
    notice_id = f"{run_id}:subscription:{identity}:{phase}"
    emitted = list(task.get("progress_notice_ids", []))
    if notice_id in emitted:
        return None
    task["progress_notice_ids"] = (emitted + [notice_id])[-96:]
    payload = {"noticeId": notice_id, "runId": run_id, "phase": phase, "text": text}
    if step in SUBSCRIPTION_STEPS:
        payload["step"] = step
        task["current_step"] = step
    if _task_id(task.get("native_task_id")):
        payload["taskId"] = task["native_task_id"]
    if _revision(task.get("revision")) is not None:
        payload["revision"] = task["revision"]
    return payload


def subscription_started_notice(task: dict, operation: Any, *, run_id: str,
                                tool_call_id: str, tool_name: str,
                                arguments: dict) -> dict | None:
    """Describe an observed dispatch; no success or resource match is inferred."""
    if operation and operation.execution_result == "denied":
        return None
    if operation and operation.kind == "frontend" and operation.execution_result != "pending":
        return None  # A replayed, already-resolved call does not start work again.
    presentation = subscription_presentation(arguments)
    if operation:
        presentation = presentation or getattr(operation, "subscription_presentation", {})
    details = subscription_operation_details(arguments.get("operations", []))
    if operation:
        details = details or list(getattr(operation, "subscription_details", ()))
    step = presentation.get("step")
    purpose = presentation.get("purpose", "").rstrip("。；; ")
    if tool_name.startswith("mcp__davinci_data__"):
        # The preceding native action supplies its next stage; do not inspect or
        # change the data MCP's implementation or require a navigation model turn.
        step = task.get("next_step") or step or "datasets"
        stage_key = f"{step}:{task.get('revision')}:{run_id}"
        if task.get("discovery_notice") == stage_key:
            return None
        task["discovery_notice"] = stage_key
        query = arguments.get("query")
        target = query[:200] if isinstance(query, str) and query.strip() else "所需数据及字段能力"
        text = f"正在查找并核验{target}。"
    elif not tool_name.startswith("space.message_rule."):
        return None
    elif tool_name.endswith(".search_options"):
        kind = arguments.get("kind")
        inferred = {"recipient_group": "receivers", "member": "receivers",
                    "recipient_member": "receivers", "dashboard": "content",
                    "widget": "content", "alert_widget": "datasets"}
        # A dashboard may supply alert data or message content. Reuse the
        # explicitly announced discovery phase; names alone cannot decide it.
        next_step = task.get("next_step")
        if kind == "dashboard" and next_step in {"datasets", "content"}:
            step = step or next_step
        step = step or inferred.get(kind, "datasets")
        target = purpose or (str(arguments.get("query", ""))[:200]) or {
            "dashboard": "仪表盘", "widget": "仪表盘内容组件",
            "alert_widget": "可用于预警的图表",
        }.get(kind, SUBSCRIPTION_STEPS[step])
        text = f"正在查找并核验：{target}。"
    elif tool_name.endswith(".get_context"):
        if not presentation:
            return None
        text = f"正在确认：{purpose or '当前订阅设置'}。"
    elif tool_name.endswith((".start_draft", ".apply_draft")):
        step = step or (details[0]["step"] if details else "trigger")
        target = purpose or "、".join(dict.fromkeys(item["label"] for item in details))
        text = f"正在配置：{target}。" if target else "正在打开订阅设置，填写已明确的内容。"
    elif tool_name.endswith(".review_draft"):
        step, text = "finalize", "正在核对当前配置是否完整、有效。"
    else:
        return None
    payload = progress_payload(task, run_id=run_id, identity=tool_call_id, phase="started",
                               text=text, step=step)
    # A stale write may still be explained in chat, but must not navigate using
    # the newer form revision. The native controller rejects the old revision.
    expected = _revision(arguments.get("expectedRevision"))
    if payload and expected is not None:
        payload["revision"] = expected
    return payload


def _failure_notice(payload: dict) -> tuple[str, str]:
    error = payload.get("error")
    error = error if isinstance(error, dict) else {}
    details = error.get("details")
    details = details if isinstance(details, dict) else {}
    code, draft_code = error.get("code"), details.get("draftCode")
    if code == "SPACE_CONTEXT_REQUIRED":
        return "waiting", "当前尚未进入目标空间，需要先打开对应空间的订阅设置。"
    if code == "STALE_CONTEXT" or draft_code in {"CONTEXT_STALE", "OPTION_REF_STALE"}:
        return "failed", "页面配置或引用已更新，需要按当前页面重新核对本次修改。"
    if code == "SECTION_BUSY" or draft_code == "SECTION_BUSY":
        return "waiting", "相关配置正在处理中，暂时无法继续这一步。已完成的配置可以继续查看。"
    if code == "INVALID_ARGUMENT":
        return "failed", "这次配置参数未通过校验，需要修正后再继续。"
    if code in {"PERMISSION_DENIED", "DRAFT_NOT_FOUND", "TOOL_NOT_AVAILABLE"}:
        return "failed", "当前页面暂时不允许执行这项配置，需先核对页面状态和可编辑范围。"
    if code in {"OUTPUT_SCHEMA_INVALID", "READBACK_SCHEMA_INVALID"}:
        return "failed", "本次操作的回执未通过校验，暂时无法确认写入结果，需要先读取当前配置核对。"
    if code and code not in {"TOOL_TIMEOUT", "TRANSPORT_ERROR", "TOOL_EXECUTION_FAILED"}:
        return "failed", "当前操作未完成，需要根据返回的错误处理，已填写内容保留。"
    return "failed", "暂时无法确认本次操作的结果，需要先读取当前配置核对，避免重复写入。"


def subscription_recovery_notice(request: RuntimeRequest, ledger: Any) -> str | None:
    """Compatibility wording for callers that have not adopted receipt events."""
    for receipt in reversed(request.tool_results):
        operation = ledger.get(receipt.tool_call_id)
        if not operation or not operation.tool_name.startswith("space.message_rule."):
            continue
        payload = _receipt_payload(receipt)
        if receipt.is_error or operation.execution_result in {"error", "denied"} or payload.get("status") == "error":
            return _failure_notice(payload)[1]
    return None



def _ready_completion(draft: dict) -> dict | None:
    """A structural finish receipt must describe this exact unsaved version."""
    completion = draft.get("completion")
    if (isinstance(completion, dict) and completion.get("status") == "ready"
            and _revision(draft.get("revision")) is not None
            and _revision(completion.get("revision")) == draft["revision"]
            and completion.get("saved") is False
            and completion.get("dataVerified") is False
            and isinstance(completion.get("message"), str)
            and completion["message"].strip()):
        return completion
    return None


def subscription_receipt_notice(task: dict, operation: Any, receipt: RuntimeToolResult,
                                *, run_id: str) -> dict | None:
    """Describe the real native receipt after resolving its ledger entry."""
    if not operation or not operation.tool_name.startswith("space.message_rule."):
        return None
    origin = getattr(operation, "subscription_origin_run", "") or run_id
    payload = _receipt_payload(receipt)
    if receipt.is_error or operation.execution_result in {"error", "denied"} or payload.get("status") == "error":
        phase, text = _failure_notice(payload)
        return progress_payload(task, run_id=origin, identity=receipt.tool_call_id,
                                phase=phase, text=text)
    if operation.execution_result != "success" or payload.get("status") != "success":
        return None
    if not operation.tool_name.endswith((".start_draft", ".apply_draft", ".review_draft")):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    draft = data.get("activeDraft", data)
    if not isinstance(draft, dict) or not _current_receipt(task, draft):
        return None  # An old receipt cannot describe or advance the current form.
    record_subscription_facts(task, data)
    completion = _ready_completion(draft)
    if operation.subscription_finish and completion:
        return progress_payload(task, run_id=origin, identity=receipt.tool_call_id,
            phase="completed", text="当前配置已通过配置检查，尚未保存，实际数据待发送预览验证。")
    if operation.tool_name.endswith(".review_draft"):
        return None
    configuration = draft.get("configuration", {})
    actual_operations = configuration.get("operations", []) if isinstance(configuration, dict) else []
    actual_operations = actual_operations if isinstance(actual_operations, list) else []
    applied = set(operation.subscription_operations)
    details = subscription_operation_details([item for item in actual_operations
        if isinstance(item, dict) and item.get("operation") in applied])
    requested = getattr(operation, "subscription_arguments", {}).get("operations", [])
    content_edits = [item for item in requested if isinstance(item, dict)
                     and item.get("operation") == "set_content"]
    if content_edits:
        # A title default must not claim the whole pre-existing/empty body was
        # configured. Read values from the receipt, but describe changed fields.
        keys = {key for item in content_edits for key in item}
        actual_content = next((item for item in actual_operations
                               if isinstance(item, dict) and item.get("operation") == "set_content"), None)
        if actual_content:
            details = [item for item in details if item["operation"] != "set_content"]
            details += subscription_operation_details([{key: value for key, value in actual_content.items()
                                                        if key in keys}])
    # A successful receipt proves the operation ran, not that the model's
    # proposed business values all survived native normalization.
    labels = [item["label"] for item in details] or [SUBSCRIPTION_STEPS[OPERATION_STEPS[name]]
        for name in operation.subscription_operations if name in OPERATION_STEPS]
    detail = "、".join(dict.fromkeys(labels))
    text = f"已设置：{detail}。" if detail else "已打开订阅设置。"
    next_step = getattr(operation, "subscription_presentation", {}).get("nextStep")
    if next_step in SUBSCRIPTION_STEPS:
        task["next_step"] = next_step
    return progress_payload(task, run_id=origin, identity=receipt.tool_call_id,
                            phase="completed", text=text)


def subscription_receipt_notices(request: RuntimeRequest, ledger: Any) -> list[dict]:
    """Report committed action facts, once per original call, after native receipts."""
    notices = []
    for receipt in request.tool_results:
        notice = subscription_receipt_notice(ledger.subscription_task,
            ledger.get(receipt.tool_call_id), receipt, run_id=request.run_id)
        if notice:
            notices.append(notice)
    return notices


def _restore_subscription_facts(task: dict) -> None:
    """Retire executable legacy plans while retaining their observed facts."""
    legacy = task.pop("executor", None)
    if not isinstance(legacy, dict):
        return
    task["legacy_checkpoint"] = True
    for key in ("confirmed_selections", "request_selections", "choice"):
        if isinstance(legacy.get(key), dict):
            task.setdefault(key, deepcopy(legacy[key]))
    readback = legacy.get("last_readback")
    if isinstance(readback, dict):
        record_subscription_facts(task, readback)
    # Unexecuted operations and guessed remaining requirements are deliberately
    # discarded. Original utterances and genuine SDK receipts stay untouched.


def prepare_subscription_task(request: RuntimeRequest, previous: dict,
                              *, operations: list[Any] | None = None) -> dict:
    """Merge a new instruction into the task; the native form remains authoritative."""
    restored = request.metadata.get("subscription_task")
    task = deepcopy(restored if isinstance(restored, dict) else previous)
    _restore_subscription_facts(task)
    task_id, native_revision = _native_context(request)
    previous_task_id = _task_id(task.get("native_task_id"))
    if task_id and task.get("native_task_id") and task_id != task["native_task_id"]:
        # SDK results belong to the session even after navigation. Preserve
        # the utterance on a tool continuation, never carry the previous form.
        task = {key: task[key] for key in (
            "original_request", "intro_shown", "amendments", "last_response",
            "sdk_pending_results", "legacy_checkpoint")
            if key in task and (request.tool_results or key in {
                "sdk_pending_results", "legacy_checkpoint"})}
    first_instruction = "original_request" not in task
    task.setdefault("original_request", request.text)
    task.setdefault("intro_shown", False)
    task.setdefault("status", "understanding")
    if task_id:
        task["native_task_id"] = task_id
        if native_revision is not None:
            # Presentation follows the live form after manual edits. Business
            # operations must still read context and obey expectedRevision.
            task["revision"] = (max(native_revision, _revision(task.get("revision")) or 0)
                                if previous_task_id == task_id else native_revision)
        elif previous_task_id != task_id:
            task.pop("revision", None)
    if request.text.strip() and not request.tool_results and task.get("last_user_run") != request.run_id:
        amendments = list(task.get("amendments", []))
        if not first_instruction:
            amendments.append(request.text)
        # The existing server checkpoint keeps exact utterances. Prompt size
        # is a projection concern; it must not discard earlier requirements.
        task["amendments"] = amendments
        task["last_user_run"] = request.run_id
        task["status"] = "understanding"
        task.pop("next_step", None)
        task.pop("discovery_notice", None)
    page = request.page_state.get("page") or {}
    scope = {"instance": page.get("instanceId"), "space": (page.get("space") or {}).get("id")}
    if task.get("scope_key") != scope:
        task.pop("data_evidence", None)
        task.pop("candidate_evidence", None)
    task["scope_key"] = scope
    return task


def subscription_completion(request: RuntimeRequest, ledger: Any) -> dict | None:
    """Accept handoff only from a successful finish operation at its exact revision."""
    if request.text.strip() or ledger.subscription_recovery:
        return None
    if any(receipt.is_error or _receipt_payload(receipt).get("status") == "error"
           for receipt in request.tool_results):
        return None
    task = ledger.subscription_task
    task_id = _task_id(task.get("native_task_id"))
    context_id, context_revision = _native_context(request)
    if not task_id or (context_id and context_id != task_id):
        return None
    if any(op.execution_result == "pending" and op.tool_name.startswith("space.message_rule.")
           and op.tool_name.endswith((".start_draft", ".apply_draft", ".save_draft"))
           for op in ledger.operations):
        return None
    for receipt in reversed(request.tool_results):
        operation = ledger.get(receipt.tool_call_id)
        if (receipt.is_error or not operation or operation.execution_result != "success"
                or not operation.subscription_finish):
            continue
        payload = _receipt_payload(receipt)
        data = payload.get("data")
        completion = _ready_completion(data) if isinstance(data, dict) else None
        if (payload.get("status") == "success" and isinstance(completion, dict)
                and _task_id(data.get("taskId")) == task_id
                and _revision(data.get("revision")) is not None
                and _revision(task.get("revision")) == data["revision"]
                and (context_revision is None or context_revision <= data["revision"])
                ):
            return completion
    return None


def record_subscription_facts(task: dict, data: dict, *, allow_task_change: bool = False,
                              arguments: dict | None = None) -> None:
    """Retain versioned native observations, never an executable target state."""
    if isinstance(data.get("results"), list) and isinstance(data.get("kind"), str):
        # Candidate evidence plus the preceding response lets "A" resume the
        # same choice. References still require validation by the live page.
        evidence = list(task.get("candidate_evidence", []))
        evidence.append({
            "kind": data["kind"], "context_version": data.get("contextVersion"),
            "taskId": task.get("native_task_id"),
            "parentRef": (arguments or {}).get("dashboardRef") or (arguments or {}).get("datasetRef"),
            "scope_key": deepcopy(task.get("scope_key")),
            "truncated": bool(data.get("truncated")) or len(data["results"]) > 20,
            "items": [{key: item[key] for key in (
                "ref", "label", "sourceKind", "sourceLabel", "contentKind", "type",
                "dashboardRef", "dashboardLabel", "widgetRef", "widgetLabel", "role",
                "dataType", "isEmployeeAccount", "employeeAccountType",
                "filterCapabilities", "description", "required", "hasDefault", "locked"
            ) if key in item} for item in data["results"][:20] if isinstance(item, dict)],
        })
        task["candidate_evidence"] = evidence[-4:]
    draft = data.get("activeDraft", data)
    if not isinstance(draft, dict):
        return
    receipt_id = _task_id(draft.get("taskId"))
    if allow_task_change and receipt_id and receipt_id != task.get("native_task_id"):
        for key in ("revision", "steps", "next_step", "discovery_notice", "current_step", "native_readback"):
            task.pop(key, None)
        task["native_task_id"] = receipt_id
    if not _current_receipt(task, draft):
        return
    if receipt_id:
        task["native_task_id"] = receipt_id
    if _revision(draft.get("revision")) is not None:
        task["revision"] = draft["revision"]
    if isinstance(draft.get("configurationSteps"), list):
        task["steps"] = draft["configurationSteps"]
    if (receipt_id and _revision(draft.get("revision")) is not None
            and isinstance(draft.get("configuration"), dict)):
        # This is observed evidence, not a writable target. A newer manual
        # revision makes it stale until another actual native receipt arrives.
        task["native_readback"] = {key: deepcopy(draft[key]) for key in (
            "taskId", "revision", "summary", "configuration", "queries", "configurationSteps",
            "sourceBinding", "contentBindings", "completion") if key in draft}
        task["status"] = "configuring"
    completion = draft.get("completion")
    if isinstance(completion, dict) and completion.get("revision") == draft.get("revision"):
        task["status"] = "configuration_ready" if _ready_completion(draft) else "configuring"
