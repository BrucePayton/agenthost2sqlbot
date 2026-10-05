"""Full-fidelity session export for offline analysis by another agent.

Neither source is complete on its own. The database keeps the platform-side
skeleton — turn boundaries, frontend tool handoffs, error codes — but truncates
tool IO at 8k (`app.runtime.events.preview`) and drops thinking blocks
entirely. The Claude transcript on disk keeps thinking and untruncated tool IO
but knows nothing about turns, frontend tools or platform errors. This module
merges both and degrades to database-only when the session directory is gone.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.inspector.redaction import redact

TRANSCRIPT_GLOB = "claude-config/projects/*/*.jsonl"
EXPORT_VERSION = 1

ANALYSIS_PROMPT = """\
你拿到的是 Davinci Agent 一次会话（或其中一个 Turn）的完整执行轨迹，用于排查 Agent 行为问题。

阅读顺序：
1. 「会话上下文」——模型这一轮被给了哪些工具、哪些 skill、哪个模型；很多"Agent 不听话"的根因在这里。
2. 「执行时间线」——按 Turn 分组，含模型思考（thinking）、工具调用完整入参、工具返回完整原文。
3. 思考块来自磁盘 transcript，平台事件来自数据库；两者按时间戳合并，Turn 归属对思考块是就近推断的。

请回答：
- 哪一步开始偏离预期？
- 根因在哪一层：系统提示词 / skill 内容 / 工具描述 / 工具返回的数据 / 平台事件（前端工具、超时、错误码）？
- 给出可验证的修复建议，指明要改哪个文件或哪段提示词。
"""

_SKIP_TRANSCRIPT_TYPES = frozenset(
    {"ai-title", "queue-operation", "last-prompt", "attachment"}
)


@dataclass(slots=True)
class Transcript:
    """Thinking blocks and untruncated tool IO recovered from disk."""

    available: bool = False
    reason: str | None = None
    files: list[str] = field(default_factory=list)
    entry_count: int = 0
    tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_outputs: dict[str, Any] = field(default_factory=dict)
    thinking: list[dict[str, Any]] = field(default_factory=list)


def load_transcript(session_path: Path) -> Transcript:
    """Read every Claude transcript under a session directory.

    A resumed session writes more than one file, so all of them are merged.
    Anything unreadable degrades to `available=False` rather than raising:
    an export without thinking is still worth having.
    """
    try:
        paths = sorted(session_path.glob(TRANSCRIPT_GLOB))
    except Exception as exc:  # noqa: BLE001 - degrade, never fail the export
        return Transcript(reason=f"session 目录不可读（{type(exc).__name__}: {exc}）")
    if not paths:
        return Transcript(
            reason="session 目录里没有 Claude transcript（会话目录已清理，或该会话从未真正运行）"
        )

    transcript = Transcript(available=True, files=[str(path) for path in paths])
    for path in paths:
        try:
            # Line by line, and never strict: a transcript is written by tools
            # whose output can carry invalid UTF-8 or grow to hundreds of MB.
            # An unreadable byte must cost one line, not the whole export.
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    entry = _parse_line(line)
                    if entry is None:
                        continue
                    transcript.entry_count += 1
                    _index_entry(transcript, entry)
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the export
            transcript.reason = (
                f"部分 transcript 读取中断（{type(exc).__name__}: {exc}）"
            )
            continue
    transcript.thinking.sort(
        key=lambda item: item["at"] or datetime.min.replace(tzinfo=UTC)
    )
    return transcript


def _parse_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        entry = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict) or entry.get("type") in _SKIP_TRANSCRIPT_TYPES:
        return None
    return entry


def _index_entry(transcript: Transcript, entry: dict[str, Any]) -> None:
    message = entry.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    at = _parse_timestamp(entry.get("timestamp"))
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "thinking":
            transcript.thinking.append(
                {
                    "at": at,
                    "text": str(block.get("thinking", "")),
                    "model": message.get("model"),
                    "uuid": entry.get("uuid"),
                }
            )
        elif block_type == "tool_use":
            tool_use_id = str(block.get("id", ""))
            if tool_use_id:
                transcript.tool_inputs[tool_use_id] = {
                    "name": block.get("name"),
                    "input": redact(block.get("input")),
                }
        elif block_type == "tool_result":
            tool_use_id = str(block.get("tool_use_id", ""))
            if tool_use_id:
                transcript.tool_outputs[tool_use_id] = redact(block.get("content"))


def build_bundle(
    *,
    session: dict[str, Any],
    context: dict[str, Any],
    events: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    transcript: Transcript,
    generated_at: datetime,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Merge database events with transcript thinking into one ordered trace."""
    selected = [
        event for event in events if turn_id is None or event.get("turn_id") == turn_id
    ]
    items = [_db_item(event, transcript) for event in selected]
    items.extend(_thinking_items(transcript, items, turn_id=turn_id))
    items.sort(key=lambda item: (item["at"] or "", item["order"]))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["turn_id"] or "", []).append(item)

    snapshot = context.get("workspace_snapshot") or {}
    return {
        "export_version": EXPORT_VERSION,
        "generated_at": _iso(generated_at),
        "analysis_prompt": ANALYSIS_PROMPT,
        "scope": {"session_id": session.get("id"), "turn_id": turn_id},
        "session": session,
        "context": context,
        "tool_surface": {
            "model": snapshot.get("model"),
            "frontend_tools": _first_frontend_tools(selected),
            "allowed_tools": snapshot.get("allowed_tools", []),
            "skills": snapshot.get("skills", []),
            "mcp_servers": list((snapshot.get("mcp_servers") or {}).keys()),
            "note": (
                "运行时还会在快照之上追加当前页面固定常驻的前端工具"
                "（mcp__davinci_ui__*），并把 Skill 从 SDK allowed_tools 里摘掉；"
                "快照不含这部分。"
            ),
        },
        "sources": {
            "db_events": len(selected),
            "transcript": {
                "available": transcript.available,
                "reason": transcript.reason,
                "files": transcript.files,
                "entries": transcript.entry_count,
                "thinking_blocks": len(transcript.thinking),
            },
        },
        "turns": [
            {
                **_turn_meta(turns, key),
                "items": [_public_item(item) for item in group],
            }
            for key, group in grouped.items()
        ],
    }


def _first_frontend_tools(events: list[dict[str, Any]]) -> list[Any]:
    """Hoist the page tool schemas, which ride identically on every user message."""
    for event in events:
        tools = (event.get("payload") or {}).get("frontend_tools")
        if tools:
            return tools
    return []


def _db_item(event: dict[str, Any], transcript: Transcript) -> dict[str, Any]:
    payload = event.get("payload") or {}
    tool_use_id = str(payload.get("tool_use_id", ""))
    recovered = transcript.tool_inputs.get(tool_use_id)
    item = {
        "at": event.get("created_at"),
        "order": int(event.get("sequence") or 0),
        "turn_id": event.get("turn_id"),
        "source": "db",
        "event_type": event.get("event_type"),
        "role": event.get("role"),
        "payload": payload,
        "tool_name": (recovered or {}).get("name") or payload.get("name"),
        "full_input": None,
        "full_output": None,
    }
    if recovered is not None and event.get("event_type") != "tool.completed":
        item["full_input"] = recovered["input"]
    if (
        event.get("event_type") == "tool.completed"
        and tool_use_id in transcript.tool_outputs
    ):
        item["full_output"] = transcript.tool_outputs[tool_use_id]
    return item


def _thinking_items(
    transcript: Transcript,
    db_items: list[dict[str, Any]],
    *,
    turn_id: str | None,
) -> list[dict[str, Any]]:
    """Attach each thinking block to the turn that was running at its timestamp.

    The transcript carries no turn id, so attribution is nearest-preceding-event
    and is labelled as inferred everywhere it surfaces.
    """
    if not transcript.thinking:
        return []
    anchors = sorted(
        (item for item in db_items if item["at"]),
        key=lambda item: item["at"],
    )
    items: list[dict[str, Any]] = []
    for index, block in enumerate(transcript.thinking):
        at = _iso(block["at"]) if block["at"] else None
        owner = _nearest_turn(anchors, at)
        if turn_id is not None and owner != turn_id:
            continue
        items.append(
            {
                "at": at,
                "order": -1,
                "turn_id": owner,
                "source": "transcript",
                "event_type": "assistant.thinking",
                "role": "assistant",
                "payload": {
                    "text": block["text"],
                    "model": block["model"],
                    "turn_id_inferred": True,
                },
                "tool_name": None,
                "full_input": None,
                "full_output": None,
                "index": index,
            }
        )
    return items


def _nearest_turn(anchors: list[dict[str, Any]], at: str | None) -> str | None:
    if not anchors:
        return None
    if at is None:
        return anchors[0]["turn_id"]
    owner = anchors[0]["turn_id"]
    for anchor in anchors:
        if anchor["at"] > at:
            break
        owner = anchor["turn_id"]
    return owner


def _turn_meta(turns: list[dict[str, Any]], turn_id: str) -> dict[str, Any]:
    for turn in turns:
        if turn.get("id") == turn_id:
            return dict(turn)
    return {"id": turn_id or None, "status": None, "input_text": None}


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"order", "index"}}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat()
