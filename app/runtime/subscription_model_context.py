"""Project observed subscription facts without maintaining an executable plan."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

from app.errors import AppError
from app.runtime.subscription_discovery import subscription_field_evidence


REQUEST_HISTORY_PROMPT_BUDGET = 12000


def _request_history_projection(task: dict, history_path: Path | None) -> dict:
    """Bound repeated prompt text while keeping every utterance in the checkpoint."""
    original = task.get("original_request", "")
    amendments = task.get("amendments", [])
    history = {"original_request": original, "amendments": amendments}
    if len(json.dumps(history, ensure_ascii=False)) <= REQUEST_HISTORY_PROMPT_BUDGET:
        return deepcopy(history)
    if history_path is None:
        raise ValueError("A session-local history path is required for an abbreviated projection")
    _write_request_history(history_path, task, history)
    # The initial request and newest changes stay immediately visible. Older
    # entries are omitted only from this display, with explicit source ranges.
    visible_original = original[:2000]
    while len(json.dumps(visible_original, ensure_ascii=False)) > 3000:
        visible_original = visible_original[:len(visible_original) // 2]
    visible_amendments: list[str] = []
    partial: list[dict] = []
    for index in range(len(amendments) - 1, -1, -1):
        candidate = [amendments[index], *visible_amendments]
        size = len(json.dumps({"original_request": visible_original, "amendments": candidate},
                              ensure_ascii=False))
        if size > REQUEST_HISTORY_PROMPT_BUDGET:
            if not visible_amendments:
                # Limit encoded length as well: escaped newlines/control
                # characters can otherwise exceed the character budget.
                snippet = amendments[index][:REQUEST_HISTORY_PROMPT_BUDGET - 3000]
                while len(json.dumps({"original_request": visible_original, "amendments": [snippet]},
                                     ensure_ascii=False)) > REQUEST_HISTORY_PROMPT_BUDGET:
                    snippet = snippet[:len(snippet) // 2]
                visible_amendments = [snippet]
                partial = [{"number": index + 1, "includedCharacters": len(snippet),
                            "totalCharacters": len(amendments[index])}]
            break
        visible_amendments = candidate
    omitted_count = len(amendments) - len(visible_amendments)
    return {"original_request": visible_original, "amendments": visible_amendments,
            "request_history": {"complete": False, "path": str(history_path),
                "originalRequestCharacters": len(original),
                "originalRequestIncludedCharacters": len(visible_original),
                "amendmentsTotal": len(amendments),
                "visibleAmendmentsStart": omitted_count + 1,
                "omittedAmendments": {"from": 1, "to": omitted_count} if omitted_count else None,
                "partialAmendments": partial,
                "notice": "这里只显示部分原文；完成需求核对前用 Read 按需分页读取该文件。"
                          "entries 按原请求、补答顺序排列；每条 textParts 直接拼接即完整原文，不是摘要。"}}


def _write_request_history(path: Path, task: dict, history: dict) -> None:
    """Materialize a private, rebuildable Read artifact from the existing checkpoint."""
    # The caller supplies a fixed filename beneath this session's workspace;
    # never derive a path from user text or native resource identifiers.
    if not path.is_absolute() or path.parent.is_symlink() or path.is_symlink():
        raise AppError("subscription_history_unavailable", "无法提供订阅要求原文，未继续配置。", 500)
    entries = []
    for index, text in enumerate([history["original_request"], *history["amendments"]]):
        entries.append({"kind": "original_request" if index == 0 else "amendment", "number": index,
            # Short JSON lines let the existing Read tool page even a very long
            # single-line message without its per-line truncation losing text.
            "textParts": [text[offset:offset + 256] for offset in range(0, len(text), 256)] or [""]})
    content = json.dumps({"nativeTaskId": task.get("native_task_id"), "entries": entries},
                         ensure_ascii=False, indent=2)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".subscription-history-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    except OSError as exc:
        raise AppError("subscription_history_unavailable", "无法提供订阅要求原文，未继续配置。", 500) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _current_readback(task: dict) -> dict:
    """Only a receipt for the current editor revision can describe its state."""
    readback = task.get("native_readback")
    if (not isinstance(readback, dict) or not task.get("native_task_id")
            or readback.get("taskId") != task.get("native_task_id")
            or type(task.get("revision")) is not int or task["revision"] < 1
            or type(readback.get("revision")) is not int
            or readback["revision"] != task.get("revision")):
        return {}
    return readback


def compact_subscription_task(task: dict, *, history_path: Path | None = None) -> dict:
    """Preserve user corrections and exact evidence; never suggest replaying edits."""
    if not isinstance(task, dict):
        return {}
    result = {key: deepcopy(task[key]) for key in (
        "last_response", "status", "native_task_id",
        "revision", "current_step", "candidate_evidence", "confirmed_selections",
        "request_selections", "choice") if key in task}
    result.update(_request_history_projection(task, history_path))
    readback = _current_readback(task)
    if readback:
        result["current_configuration"] = {key: deepcopy(readback[key]) for key in (
            "taskId", "revision", "summary", "configuration", "configurationSteps", "completion") if key in readback}
    facts = subscription_resource_facts(task)
    if facts:
        result["resource_facts"] = facts
    evidence = subscription_field_evidence(task)
    if evidence:
        result["field_evidence"] = evidence
    result["notice"] = (
        "这是用户要求和真实工具回执的只读证据，不是待重放操作。"
        "只修改本次要求涉及的配置；原生 ready 仅证明该版本结构合法，"
        "仍需对照用户要求核对。来源和字段引用继续由原生工具验证。")
    return result


def subscription_resource_facts(task: dict) -> list[dict]:
    """Keep native source and component semantics bound to the exact task/version."""
    draft = _current_readback(task)
    if not draft:
        return []
    scope = {"taskId": draft["taskId"], "revision": draft["revision"]}
    allowed = {"kind", "type", "contentKind", "widgetRef", "widgetLabel", "dashboardRef",
               "dashboardLabel", "componentRef", "sourceKind", "sourceLabel", "ref", "label"}
    result = []
    source = draft.get("sourceBinding")
    if isinstance(source, dict) and source:
        result.append({**{key: deepcopy(value) for key, value in source.items() if key in allowed},
                       **scope, "purpose": "monitoring", "status": "bound"})
    for binding in draft.get("contentBindings", []):
        if isinstance(binding, dict):
            result.append({**{key: deepcopy(value) for key, value in binding.items() if key in allowed},
                           **scope, "purpose": "content", "status": "bound"})
    return result
