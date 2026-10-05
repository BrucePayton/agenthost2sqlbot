#!/usr/bin/env python3
"""Tool-surface report: contract inventory, profile visibility, real usage.

Usage: .venv/bin/python scripts/tool_surface_report.py --db /Users/a110356/work/data/davinci-local-18001/app.db --since 2026-09-01
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 与 FE FrontendCapabilityCatalog.belongsToProfile 保持一致（数据集计划 A1 增两个 Profile）。
PROFILES = ("workspace", "dashboard", "space", "space-dashboard", "dataset-marketplace", "dataset-editor")


def belongs_to_profile(bundle: str, profile: str) -> bool:
    if bundle in ("core-navigation", "space-message-rule"):
        return True
    if profile in ("space", "space-dashboard") and bundle == "space-core":
        return True
    if profile in ("dashboard", "space-dashboard") and bundle.startswith("dashboard-"):
        return True
    if profile in ("dataset-marketplace", "dataset-editor") and bundle == "dataset-marketplace":
        return True
    return profile == "dataset-editor" and bundle == "dataset-editor"


def profile_tools(rows: list[dict]) -> dict[str, list[str]]:
    return {
        profile: [
            row["action"]
            for row in rows
            if row.get("public") is True
            and row.get("executor") == "frontend"
            and belongs_to_profile(str(row.get("bundle") or ""), profile)
        ]
        for profile in PROFILES
    }


def contract_rows() -> list[dict]:
    from app.agui.claude_tools import _model_compatible_description, _model_compatible_schema
    from app.agui.contracts import CONTRACT_PATH, load_contract_registry

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    raw = registry.raw_contract
    templates = raw["schemaTemplates"]
    rows = []
    for tool in raw["tools"]:
        schema = templates[tool["inputTemplate"]]
        projected = _model_compatible_schema(deepcopy(schema))
        rows.append(
            {
                "action": tool["action"],
                "bundle": tool.get("bundle"),
                "executor": tool.get("executor"),
                "public": tool.get("public"),
                "risk": tool.get("risk"),
                "desc_len": len(_model_compatible_description(tool["description"], schema)),
                "schema_bytes": len(
                    json.dumps(projected, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                ),
            }
        )
    return rows


def _normalize_name(name: str) -> str:
    if name.startswith("mcp__davinci_ui__"):
        return "davinci_ui:" + name.removeprefix("mcp__davinci_ui__").replace("__", ".")
    if name.startswith("mcp__davinci_data__"):
        return "davinci_data:" + name.removeprefix("mcp__davinci_data__")
    return name


def _open_readonly(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"telemetry database not found: {db_path}")
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def per_tool_usage(db_path: str, since: str) -> list[dict]:
    with _open_readonly(db_path) as conn:
        rows = conn.execute(
            "select event_type, payload_json from turn_events where created_at >= ? "
            "and event_type in ('tool.started','tool.completed','frontend_tool.deferred') order by created_at",
            (since,),
        ).fetchall()
    started: dict[str, str] = {}
    deferred: dict[str, str] = {}
    for event_type, payload_json in rows:
        payload = json.loads(payload_json)
        if event_type == "tool.started":
            started[payload["tool_use_id"]] = payload["name"]
        elif event_type == "frontend_tool.deferred":
            deferred[payload["tool_use_id"]] = "davinci_ui:" + payload["name"]
    usage: dict[str, dict] = {}
    for event_type, payload_json in rows:
        if event_type != "tool.completed":
            continue
        payload = json.loads(payload_json)
        tool_use_id = payload["tool_use_id"]
        name = _normalize_name(deferred.get(tool_use_id) or started.get(tool_use_id) or str(payload.get("name")))
        entry = usage.setdefault(name, {"tool": name, "calls": 0, "errors": 0, "unknown": 0, "statuses": {}, "codes": {}})
        entry["calls"] += 1
        failed = bool(payload.get("is_error"))
        # 新数据：producer 写入的结构化字段优先（Step 3b）。
        status = payload.get("result_status")
        code = payload.get("error_code")
        if status is None:
            preview = payload.get("output_preview") or ""
            if preview.endswith("...[truncated]"):
                entry["unknown"] += 1          # 被截断，无法判定
            else:
                try:
                    output = json.loads(preview)
                except (TypeError, ValueError):
                    output = None
                if isinstance(output, dict) and output.get("status"):
                    status = output.get("status")
                    error = output.get("error")
                    if status == "error" and isinstance(error, dict):
                        code = error.get("code")
                else:
                    entry["unknown"] += 1      # 非 JSON 或无 status，不当成功
        if status:
            entry["statuses"][status] = entry["statuses"].get(status, 0) + 1
            if status == "error":
                failed = True
                if code:
                    entry["codes"][code] = entry["codes"].get(code, 0) + 1
        if failed:
            entry["errors"] += 1
    return sorted(usage.values(), key=lambda item: -item["calls"])


def repeat_rate(db_path: str, since: str) -> tuple[int, int]:
    """(identical deferred calls repeated within one user turn, total deferred calls)."""
    with _open_readonly(db_path) as conn:
        turns = conn.execute(
            "select id, session_id, input_text from turns where created_at >= ? order by created_at", (since,)
        ).fetchall()
        deferred_rows = conn.execute(
            "select turn_id, payload_json from turn_events where event_type='frontend_tool.deferred' and created_at >= ?",
            (since,),
        ).fetchall()
    groups: dict[str, list[list[str]]] = collections.defaultdict(list)
    for turn_id, session_id, input_text in turns:
        if input_text != "" or not groups[session_id]:
            groups[session_id].append([])
        groups[session_id][-1].append(turn_id)
    deferred_by_turn: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for turn_id, payload_json in deferred_rows:
        payload = json.loads(payload_json)
        deferred_by_turn[turn_id].append((payload["name"], json.dumps(payload["arguments"], sort_keys=True)))
    repeats = total = 0
    for user_turns in groups.values():
        for host_turns in user_turns:
            seen: collections.Counter = collections.Counter()
            for turn_id in host_turns:
                for key in deferred_by_turn.get(turn_id, []):
                    seen[key] += 1
                    total += 1
            repeats += sum(count - 1 for count in seen.values() if count > 1)
    return repeats, total


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "| " + " | ".join(headers) + " |\n|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return f"{head}\n{body}" if rows else head


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--since", default="2026-01-01")
    args = parser.parse_args()
    rows = contract_rows()
    public = [row for row in rows if row["public"] is True]
    print(f"## 1. 契约：{len(rows)} 个，公开 {len(public)}\n")
    print(_table(["action", "bundle", "risk", "desc", "schema B"],
                 [[r["action"], r["bundle"], r["risk"], r["desc_len"], r["schema_bytes"]] for r in public]))
    print("\n## 2. Profile 可见性\n")
    profiles = profile_tools(rows)
    print(_table(["profile", "tools", "model-visible bytes"],
                 [[p, len(t), sum(r["schema_bytes"] + r["desc_len"] for r in public if r["action"] in t)]
                  for p, t in profiles.items()]))
    usage = per_tool_usage(args.db, args.since)
    used = {row["tool"].removeprefix("davinci_ui:") for row in usage}
    never = [row["action"] for row in public if row["action"] not in used]
    print(f"\n## 3. 用量（since {args.since}）\n")
    print(_table(["tool", "calls", "errors", "unknown", "statuses", "codes"],
                 [[r["tool"], r["calls"], r["errors"], r["unknown"], r["statuses"], r["codes"]] for r in usage]))
    repeats, total = repeat_rate(args.db, args.since)
    print(f"\n同轮重复调用：{repeats}/{total}")
    print(f"从未调用（{len(never)}/{len(public)}）：" + ", ".join(never))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
