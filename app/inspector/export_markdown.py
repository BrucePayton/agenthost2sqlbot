"""Render an export bundle as Markdown for a human or another agent to read."""

import json
import re
from datetime import datetime
from typing import Any

_FOLD_THRESHOLD = 1200

_TITLES = {
    "message.user": "用户消息",
    "assistant.thinking": "模型思考",
    "message.assistant.completed": "Agent 回复",
    "subscription.progress": "订阅配置进度",
    "message.assistant.delta": "Agent 流式片段",
    "context.compacted": "上下文压缩",
    "turn.failed": "Turn 失败",
    "turn.cancelled": "Turn 取消",
    "turn.interrupted": "Turn 中断",
}


def render_markdown(bundle: dict[str, Any]) -> str:
    scope = bundle.get("scope") or {}
    session = bundle.get("session") or {}
    lines: list[str] = []
    heading = "会话导出" if not scope.get("turn_id") else "单 Turn 导出"
    lines.append(f"# Davinci Agent {heading}")
    lines.append("")
    lines.append(bundle.get("analysis_prompt", "").strip())
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(_session_section(session, scope, bundle.get("generated_at")))
    lines.extend(_context_section(bundle))
    if "feedback" in bundle:
        lines.extend(["## 用户评价", ""])
        lines.extend(_fenced(_json(bundle["feedback"]), "json"))
        lines.append("")
    lines.extend(_sources_section(bundle.get("sources") or {}))
    lines.extend(
        _timeline_section(bundle.get("turns") or [], bundle.get("tool_surface") or {})
    )
    return "\n".join(lines).rstrip() + "\n"


def _session_section(
    session: dict[str, Any],
    scope: dict[str, Any],
    generated_at: str | None,
) -> list[str]:
    rows = [
        ("Session ID", session.get("id")),
        ("标题", session.get("title")),
        (
            "用户",
            f"{session.get('user_display_name')} · {session.get('identity_subject')}",
        ),
        (
            "Workspace",
            f"{session.get('workspace_name')} ({session.get('workspace_id')})",
        ),
        ("状态", session.get("status")),
        ("最近错误", session.get("last_error_code") or "—"),
        ("Claude Session", session.get("claude_session_id") or "—"),
        ("Turn / Event", f"{session.get('turn_count')} / {session.get('event_count')}"),
        ("创建 / 更新", f"{session.get('created_at')} → {session.get('updated_at')}"),
        ("导出范围", scope.get("turn_id") or "整个 session"),
        ("导出时间", generated_at),
    ]
    lines = ["## 1. 会话", "", "| 字段 | 值 |", "| --- | --- |"]
    lines.extend(f"| {label} | {_cell(value)} |" for label, value in rows)
    lines.append("")
    return lines


def _context_section(bundle: dict[str, Any]) -> list[str]:
    surface = bundle.get("tool_surface") or {}
    lines = ["## 2. 会话上下文（模型被给了什么）", ""]
    lines.append(f"- 模型：`{surface.get('model') or '默认'}`")
    lines.append(f"- allowed_tools：{_inline_list(surface.get('allowed_tools'))}")
    lines.append(f"- skills：{_inline_list(_skill_names(surface.get('skills')))}")
    lines.append(f"- MCP servers：{_inline_list(surface.get('mcp_servers'))}")
    frontend_tools = surface.get("frontend_tools") or []
    lines.append(
        f"- 前端页面工具（运行时注入，共 {len(frontend_tools)} 个）："
        f"{_inline_list(_tool_names(frontend_tools))}"
    )
    lines.append(f"- 注意：{surface.get('note', '')}")
    lines.append("")
    if frontend_tools:
        lines.append("<details><summary>前端页面工具的完整定义</summary>")
        lines.append("")
        lines.extend(_fenced(_json(frontend_tools), "json"))
        lines.append("")
        lines.append("</details>")
        lines.append("")
    lines.append("<details><summary>workspace_snapshot 与 session 上下文原文</summary>")
    lines.append("")
    lines.extend(_fenced(_json(bundle.get("context")), "json"))
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return lines


def _sources_section(sources: dict[str, Any]) -> list[str]:
    transcript = sources.get("transcript") or {}
    lines = ["## 3. 数据来源", ""]
    lines.append(
        f"- 数据库事件：{sources.get('db_events', 0)} 条（工具 IO 在库里截断于 8000 字符）"
    )
    if transcript.get("available"):
        lines.append(
            f"- 磁盘 transcript：可用，{transcript.get('entries', 0)} 条记录、"
            f"{transcript.get('thinking_blocks', 0)} 个思考块；工具入参出参已用原文覆盖截断版本"
        )
        for path in transcript.get("files") or []:
            lines.append(f"  - `{path}`")
    else:
        lines.append(
            f"- 磁盘 transcript：**不可用** —— {transcript.get('reason') or '未知原因'}。"
            "本次导出没有模型思考，工具 IO 可能是 8000 字符的截断版本。"
        )
    lines.append("")
    return lines


def _timeline_section(
    turns: list[dict[str, Any]],
    surface: dict[str, Any],
) -> list[str]:
    lines = ["## 4. 执行时间线", ""]
    if not turns:
        lines.append("_没有可导出的事件。_")
        lines.append("")
        return lines
    for index, turn in enumerate(turns, start=1):
        lines.append(
            f"### Turn {index} · `{turn.get('id') or '未知'}` · {turn.get('status') or '—'}"
        )
        lines.append("")
        if turn.get("input_text"):
            lines.append(f"> 用户输入：{_cell(turn['input_text'])}")
            lines.append("")
        for item in turn.get("items") or []:
            lines.extend(_item_block(item, surface))
    return lines


def _item_block(item: dict[str, Any], surface: dict[str, Any]) -> list[str]:
    payload = item.get("payload") or {}
    event_type = item.get("event_type") or "event"
    clock = _clock(item.get("at"))
    lines: list[str] = []

    if event_type == "tool.started":
        lines.append(f"#### [{clock}] 调用工具 · `{item.get('tool_name') or 'tool'}`")
        lines.append("")
        lines.extend(
            _body(item.get("full_input"), payload.get("input_preview"), "入参")
        )
    elif event_type == "tool.completed":
        state = "工具失败" if payload.get("is_error") else "工具完成"
        duration = payload.get("duration_ms")
        suffix = f"（{duration} ms）" if duration is not None else ""
        lines.append(
            f"#### [{clock}] {state} · `{item.get('tool_name') or 'tool'}`{suffix}"
        )
        lines.append("")
        lines.extend(
            _body(item.get("full_output"), payload.get("output_preview"), "输出")
        )
    elif event_type == "assistant.thinking":
        lines.append(f"#### [{clock}] 模型思考 _(Turn 归属为推断)_")
        lines.append("")
        lines.extend(_fenced(str(payload.get("text", ""))))
        lines.append("")
    elif event_type in {"message.user", "message.assistant.completed", "subscription.progress"}:
        run_config = _run_config(payload)
        lines.append(f"#### [{clock}] {_TITLES[event_type]}{run_config}")
        lines.append("")
        if payload.get("text"):
            lines.extend(_fenced(str(payload["text"])))
            lines.append("")
        extras = {
            key: value
            for key, value in payload.items()
            if key not in {"text", "model", "effort"} and value not in (None, [], {})
        }
        if extras.get("frontend_tools") == (surface.get("frontend_tools") or []):
            extras["frontend_tools"] = (
                f"[与 §2 列出的 {len(surface.get('frontend_tools') or [])} 个页面工具完全一致，此处省略]"
            )
        if extras:
            lines.extend(_collapsible(_json(extras), "json", "本条消息的其余字段"))
            lines.append("")
    else:
        lines.append(f"#### [{clock}] {_TITLES.get(event_type, event_type)}")
        lines.append("")
        lines.extend(_collapsible(_json(payload), "json", "payload"))
        lines.append("")
    return lines


def _body(full: Any, preview: Any, label: str) -> list[str]:
    if full is not None:
        text = full if isinstance(full, str) else _json(full)
        language = "" if isinstance(full, str) else "json"
        lines = [f"{label}（transcript 原文）：", ""]
        lines.extend(_collapsible(text, language, label))
    elif preview:
        lines = [f"{label}（数据库，可能被截断）：", ""]
        lines.extend(_collapsible(str(preview), "", label))
    else:
        lines = [f"{label}：_空_"]
    lines.append("")
    return lines


def _collapsible(text: str, language: str, label: str) -> list[str]:
    """Fold anything long enough to bury the surrounding trace.

    Frontend tool schemas and page state ride along on every user message and
    run to thousands of lines; folded, they stay in the export without hiding
    the events around them. Both humans and agents read through <details>.
    """
    if len(text) <= _FOLD_THRESHOLD:
        return _fenced(text, language)
    lines = [f"<details><summary>{label}（{len(text)} 字符，已折叠）</summary>", ""]
    lines.extend(_fenced(text, language))
    lines.extend(["", "</details>"])
    return lines


def _run_config(payload: dict[str, Any]) -> str:
    parts = [str(payload[key]) for key in ("model", "effort") if payload.get(key)]
    return f" _({' · '.join(parts)})_" if parts else ""


def _tool_names(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    return [
        tool.get("name", "?") if isinstance(tool, dict) else str(tool) for tool in tools
    ]


def _skill_names(skills: Any) -> list[str]:
    if not isinstance(skills, list):
        return []
    return [
        skill.get("name") or skill.get("id") or str(skill)
        if isinstance(skill, dict)
        else str(skill)
        for skill in skills
    ]


def _inline_list(values: Any) -> str:
    if not values:
        return "_无_"
    return ", ".join(f"`{value}`" for value in values)


def _fenced(text: str, language: str = "") -> list[str]:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", text, fence]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _cell(value: Any) -> str:
    text = "—" if value in (None, "") else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _clock(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%H:%M:%S.%f")[:-3]
    except ValueError:
        return value
