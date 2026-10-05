#!/usr/bin/env python3
"""Audit one Davinci session's persisted tool trace."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

READBACK_TOOLS = frozenset(
    {
        "dashboard.get_widget_config",
        "dashboard.get_widget_data",
        "dashboard.get_publish_readiness",
        "dashboard.get_structure",
    }
)
FORBIDDEN_TOOLS = frozenset(
    {"Agent", "Bash", "Edit", "ListMcpResourcesTool", "Write"}
)
REPEAT_LIMIT = 3
# 同一个工具在一个调用组里被叫到这个次数就算刷工具，入参不同也算。
# REPEAT_LIMIT 只抓逐字节相同的入参，换个参数重试同一个工具它是看不见的。
SAME_TOOL_LIMIT = 4
# 用户请求的累计处理预算，不包含两次请求之间的人工检查或等待时间。
DURATION_BUDGET_SECONDS = 15 * 60.0
# 模型调用次数相对工具调用次数的倍数上限。
MODEL_INVOCATION_RATIO = 2.0
# 单个 host turn 的模型往返上限：一轮里反复自言自语就是空转。
TURN_MODEL_INVOCATION_LIMIT = 6
# 这些错误码说明工具契约本身坏了（后端没落库、查询没下发、参数被拒），
# 不是模型用错工具，必须单独计数而不是混在 business_tool_errors 里。
CONTRACT_ERROR_CODES = (
    "PERSISTENCE_OUTCOME_UNKNOWN",
    "WAIT_TIMEOUT",
    "QUERY_NOT_DISPATCHED",
    "INVALID_ARGUMENT",
    "CATALOG_SEARCH_EXHAUSTED",
)
PRE_TOOL_DENIAL_PREFIXES = (
    "FRONTEND_TOOL_SERIALIZED:",
    "PAGE_TOOL_IN_FLIGHT:",
    "PUBLISH_REQUIRES_USER_REQUEST:",
    "REPEATED_CALL_BLOCKED:",
)
FRONTEND_SDK_PREFIXES = (
    "mcp__davinci_ui__",
    "mcp__davinci_ui_more__",
)
# output_preview 会在 8000 字处被截断，json.loads 直接废掉，所以用正则捞错误码。
# issues[].code 里的码（WAIT_TIMEOUT 之类）不带 is_error，只能这样抓。
_JSON_CODE_PATTERN = re.compile(r'"code"\s*:\s*"([A-Z][A-Z0-9_]{2,})"')
# error.code=EXECUTION_FAILED 时真正的原因写在 error.message 里（如 INVALID_ARGUMENT）。
_JSON_MESSAGE_CODE_PATTERN = re.compile(r'"message"\s*:\s*"([A-Z][A-Z0-9_]{2,})"')
# 护栏类错误是纯文本的 "CODE: 说明" 形式，不是 JSON。
_PREFIX_CODE_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]{2,}):")


@dataclass(slots=True)
class AuditReport:
    orphan_tool_use_ids: list[str] = field(default_factory=list)
    repeated_calls: list[tuple[str, str, int]] = field(default_factory=list)
    repeated_tools: list[tuple[str, int]] = field(default_factory=list)
    writes_without_readback: list[str] = field(default_factory=list)
    forbidden_tool_calls: list[str] = field(default_factory=list)
    denied_frontend_calls: list[str] = field(default_factory=list)
    business_tool_errors: list[tuple[str, str]] = field(default_factory=list)
    contract_error_codes: dict[str, int] = field(default_factory=dict)
    turns: int = 0
    frontend_calls: int = 0
    mcp_calls: int = 0
    tool_calls: int = 0
    model_invocations: int = 0
    duration_seconds: float = 0.0
    active_duration_seconds: float = 0.0
    user_requests: int = 0
    tool_duration_ms: float = 0.0
    model_api_duration_ms: float = 0.0
    subscription_draft_starts: int = 0
    subscription_reviews: int = 0
    subscription_data_checks: int = 0
    subscription_rule_list_reads: int = 0
    first_editable_draft_seconds: float | None = None
    # (turn_id, 墙钟秒数, 模型往返次数, 工具调用次数)
    worst_turn: tuple[str, float, int, int] | None = None
    stalled_turns: list[tuple[str, int]] = field(default_factory=list)
    failed_turns: list[str] = field(default_factory=list)
    duration_budget_seconds: float = DURATION_BUDGET_SECONDS
    model_invocation_ratio: float = MODEL_INVOCATION_RATIO
    turn_model_invocation_limit: int = TURN_MODEL_INVOCATION_LIMIT

    @property
    def over_duration_budget(self) -> bool:
        duration = self.active_duration_seconds if self.user_requests else self.duration_seconds
        return duration > self.duration_budget_seconds

    @property
    def over_model_budget(self) -> bool:
        # tool_calls 为 0 时不判：没工具可比，纯问答会话不该因此挂掉。
        return self.tool_calls > 0 and (
            self.model_invocations > self.model_invocation_ratio * self.tool_calls
        )

    @property
    def passed(self) -> bool:
        return not (
            self.orphan_tool_use_ids
            or self.repeated_calls
            or self.repeated_tools
            or self.writes_without_readback
            or self.forbidden_tool_calls
            or self.denied_frontend_calls
            or self.business_tool_errors
            or self.contract_error_codes
            or self.failed_turns
            or self.stalled_turns
            or self.over_duration_budget
            or self.over_model_budget
        )


def _public_name(sdk_name: str) -> str:
    for prefix in FRONTEND_SDK_PREFIXES:
        if sdk_name.startswith(prefix):
            return sdk_name.removeprefix(prefix).replace("__", ".")
    return sdk_name


def _canonical_input(value: Any) -> str:
    if not isinstance(value, str):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value.strip()
    return json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _result_payload(content: Any) -> Mapping[str, Any]:
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _error_codes(preview: str) -> list[str]:
    """从工具输出预览里按出现顺序抽出所有错误码（去重）。

    预览可能被截断成非法 JSON，所以这里一律走正则，不做 json.loads。
    """

    if not preview:
        return []
    text = preview.strip()
    codes: list[str] = []
    prefix = _PREFIX_CODE_PATTERN.match(text)
    if prefix:
        codes.append(prefix.group(1))
    codes.extend(_JSON_CODE_PATTERN.findall(text))
    codes.extend(_JSON_MESSAGE_CODE_PATTERN.findall(text))
    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def _parse_timestamp(value: Any) -> datetime | None:
    """解析 turn_events.created_at；库里是无时区的，payload 里的是带时区的。"""

    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _tool_results(payload: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    results = payload.get("tool_results")
    if not isinstance(results, Sequence) or isinstance(
        results, (str, bytes, bytearray)
    ):
        return ()
    return tuple(item for item in results if isinstance(item, Mapping))


def analyze_events(
    events: list[dict[str, Any]],
    *,
    duration_budget_seconds: float = DURATION_BUDGET_SECONDS,
    model_invocation_ratio: float = MODEL_INVOCATION_RATIO,
    turn_model_invocation_limit: int = TURN_MODEL_INVOCATION_LIMIT,
) -> AuditReport:
    """Analyze events supplied in chronological order."""

    report = AuditReport(
        duration_budget_seconds=duration_budget_seconds,
        model_invocation_ratio=model_invocation_ratio,
        turn_model_invocation_limit=turn_model_invocation_limit,
    )
    started: dict[str, tuple[str, str]] = {}
    resolved: set[str] = set()
    call_groups: list[list[tuple[str, str]]] = [[]]
    writes: dict[str, bool] = {}
    pending_writes: list[str] = []
    turn_ids: set[str] = set()
    contract_codes: Counter[str] = Counter()
    turn_bounds: dict[str, tuple[datetime, datetime]] = {}
    turn_model_invocations: dict[str, int] = {}
    turn_tool_calls: Counter[str] = Counter()
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    request_bounds: list[list[datetime]] = []
    measured_tools: set[str] = set()

    for event in events:
        turn_id = str(event.get("turn_id", ""))
        if turn_id:
            turn_ids.add(turn_id)
        moment = _parse_timestamp(event.get("created_at"))
        if moment is not None:
            first_seen = moment if first_seen is None else min(first_seen, moment)
            last_seen = moment if last_seen is None else max(last_seen, moment)
            if turn_id:
                bounds = turn_bounds.get(turn_id)
                turn_bounds[turn_id] = (
                    moment if bounds is None else min(bounds[0], moment),
                    moment if bounds is None else max(bounds[1], moment),
                )
        event_type = str(event.get("event_type", ""))
        raw_payload = event.get("payload")
        payload = raw_payload if isinstance(raw_payload, Mapping) else {}
        if event_type == "message.user" and payload.get("text") and not _tool_results(payload):
            report.user_requests += 1
            if moment is not None:
                request_bounds.append([moment, moment])
        elif moment is not None and request_bounds:
            request_bounds[-1][1] = max(request_bounds[-1][1], moment)

        if event_type == "message.user":
            if payload.get("text") and any(call_groups[-1]):
                call_groups.append([])
            for result in _tool_results(payload):
                tool_use_id = str(result.get("tool_call_id", ""))
                if not tool_use_id:
                    continue
                resolved.add(tool_use_id)
                result_payload = _result_payload(result.get("content"))
                status = result_payload.get("status")
                success = (
                    result.get("is_error") is not True
                    and status in {"success", "partial"}
                )
                data = result_payload.get("data")
                data = data if isinstance(data, Mapping) else {}
                public_name = started.get(tool_use_id, ("", ""))[0]
                if (status == "success" and success and data.get("persisted") is True
                        and public_name != "space.message_rule.save_draft"):
                    writes[tool_use_id] = False
                    pending_writes.append(tool_use_id)
                if success and public_name in READBACK_TOOLS:
                    for write_id in pending_writes:
                        writes[write_id] = True
                    pending_writes.clear()
            continue

        if event_type == "tool.started":
            tool_use_id = str(payload.get("tool_use_id", ""))
            sdk_name = str(payload.get("name", ""))
            public_name = _public_name(sdk_name)
            input_preview = _canonical_input(payload.get("input_preview", ""))
            arguments = _result_payload(input_preview)
            if public_name == "space.message_rule.start_draft":
                report.subscription_draft_starts += 1
            elif public_name == "space.message_rule.review_draft":
                report.subscription_reviews += 1
                report.subscription_data_checks += int(arguments.get("includeDataCheck") is True)
            elif public_name == "space.message_rule.get_context":
                report.subscription_rule_list_reads += int(bool(
                    arguments.get("includeRules") or arguments.get("query")
                ))
            if tool_use_id:
                started[tool_use_id] = (public_name, input_preview)
            report.tool_calls += 1
            if turn_id:
                turn_tool_calls[turn_id] += 1
            if (
                sdk_name in FORBIDDEN_TOOLS
                and sdk_name not in report.forbidden_tool_calls
            ):
                report.forbidden_tool_calls.append(sdk_name)
            if sdk_name.startswith(FRONTEND_SDK_PREFIXES):
                report.frontend_calls += 1
                call_groups[-1].append((public_name, input_preview))
            elif sdk_name.startswith("mcp__"):
                report.mcp_calls += 1
            continue

        if event_type in {"frontend_tool.deferred", "tool.completed"}:
            tool_use_id = str(payload.get("tool_use_id", ""))
            if tool_use_id:
                resolved.add(tool_use_id)
            if event_type == "tool.completed":
                # tool.completed 覆盖全部工具（含 deferred 前端工具的最终回执），
                # 所以错误码只从这里取，避免和 message.user 里的 tool_results 重复计数。
                output_preview = str(payload.get("output_preview", ""))
                public_name = started.get(tool_use_id, ("", ""))[0]
                duration = payload.get("duration_ms")
                if (tool_use_id not in measured_tools and isinstance(duration, (int, float))
                        and not isinstance(duration, bool) and duration >= 0):
                    report.tool_duration_ms += duration
                    measured_tools.add(tool_use_id)
                if (public_name == "space.message_rule.start_draft"
                        and report.first_editable_draft_seconds is None
                        and request_bounds and moment is not None):
                    result = _result_payload(output_preview)
                    if (result.get("status") == "success"
                            and (result.get("data") or {}).get("status") == "active"):
                        report.first_editable_draft_seconds = (
                            moment - request_bounds[-1][0]
                        ).total_seconds()
                codes = _error_codes(output_preview)
                if payload.get("is_error") is True:
                    if output_preview.startswith(PRE_TOOL_DENIAL_PREFIXES):
                        # PreToolUse 拒绝是护栏生效，另立一档，不算业务报错。
                        report.denied_frontend_calls.append(public_name or tool_use_id)
                    else:
                        report.business_tool_errors.append(
                            (public_name or tool_use_id, codes[0] if codes else "")
                        )
                # WAIT_TIMEOUT 这类码挂在 issues[] 上、is_error 是 false，
                # 所以契约错误码要在成功回执里也扫一遍。
                for code in codes:
                    if code in CONTRACT_ERROR_CODES:
                        contract_codes[code] += 1
            continue

        if event_type == "usage.updated":
            api_duration = payload.get("sdk_duration_api_ms")
            if isinstance(api_duration, (int, float)) and api_duration >= 0:
                report.model_api_duration_ms += api_duration
            # 一个 host turn 里模型实际往返了几次，用来抓"只思考不干活"的空转轮。
            model_turns = payload.get("model_api_turns")
            if isinstance(model_turns, int) and turn_id:
                turn_model_invocations[turn_id] = model_turns
            continue

        if event_type == "turn.failed":
            code = payload.get("code") or "unknown"
            report.failed_turns.append(f"{turn_id}: {code}")

    report.turns = len(turn_ids)
    report.contract_error_codes = dict(sorted(contract_codes.items()))
    report.model_invocations = sum(turn_model_invocations.values())
    if first_seen is not None and last_seen is not None:
        report.duration_seconds = (last_seen - first_seen).total_seconds()
    report.active_duration_seconds = sum(
        max(0.0, (end - start).total_seconds()) for start, end in request_bounds
    )
    for turn, invocations in sorted(turn_model_invocations.items()):
        if invocations > turn_model_invocation_limit:
            report.stalled_turns.append((turn, invocations))
    if turn_bounds:
        # 最差的一轮按墙钟排，同分再看模型往返次数。
        worst = max(
            turn_bounds,
            key=lambda turn: (
                (turn_bounds[turn][1] - turn_bounds[turn][0]).total_seconds(),
                turn_model_invocations.get(turn, 0),
            ),
        )
        start, end = turn_bounds[worst]
        report.worst_turn = (
            worst,
            (end - start).total_seconds(),
            turn_model_invocations.get(worst, 0),
            turn_tool_calls.get(worst, 0),
        )
    report.orphan_tool_use_ids = [
        tool_use_id for tool_use_id in started if tool_use_id not in resolved
    ]
    tool_repeats: Counter[str] = Counter()
    for calls in call_groups:
        counts = Counter(calls)
        for (name, input_preview), count in counts.items():
            if count >= REPEAT_LIMIT:
                report.repeated_calls.append((name, input_preview, count))
        # 只看工具名不看入参：换个参数把同一个工具刷四遍，上面那条抓不到。
        by_tool = Counter(name for name, _ in calls)
        for name, count in by_tool.items():
            if count >= SAME_TOOL_LIMIT:
                tool_repeats[name] = max(tool_repeats[name], count)
    report.repeated_tools = sorted(tool_repeats.items())
    report.writes_without_readback = [
        tool_use_id for tool_use_id, verified in writes.items() if not verified
    ]
    return report


def load_events(db_path: str | Path, session_id: str) -> list[dict[str, Any]]:
    path = Path(db_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"database does not exist: {path}")
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "select e.turn_id, e.sequence, e.event_type, e.payload_json, "
            "e.created_at, t.input_text "
            "from turn_events e join turns t on t.id = e.turn_id "
            "where e.session_id = ? "
            "order by e.created_at, e.sequence",
            (session_id,),
        ).fetchall()
    return [
        {
            "turn_id": row[0],
            "sequence": row[1],
            "event_type": row[2],
            "payload": json.loads(row[3]),
            "created_at": row[4],
            "input_text": row[5],
        }
        for row in rows
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a persisted Davinci Agent session tool trace."
    )
    parser.add_argument("db", help="Path to the Agent Host SQLite database")
    parser.add_argument("session_id")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--duration-budget",
        type=float,
        default=DURATION_BUDGET_SECONDS,
        metavar="SECONDS",
        help=(
            "Wall-clock budget for the whole session "
            f"(default: {DURATION_BUDGET_SECONDS:.0f})"
        ),
    )
    parser.add_argument(
        "--model-ratio",
        type=float,
        default=MODEL_INVOCATION_RATIO,
        metavar="RATIO",
        help=(
            "Fail when model invocations exceed RATIO * tool calls "
            f"(default: {MODEL_INVOCATION_RATIO})"
        ),
    )
    parser.add_argument(
        "--turn-model-limit",
        type=int,
        default=TURN_MODEL_INVOCATION_LIMIT,
        metavar="N",
        help=(
            "Fail when a single turn makes more than N model round-trips "
            f"(default: {TURN_MODEL_INVOCATION_LIMIT})"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = analyze_events(
            load_events(args.db, args.session_id),
            duration_budget_seconds=args.duration_budget,
            model_invocation_ratio=args.model_ratio,
            turn_model_invocation_limit=args.turn_model_limit,
        )
    except (FileNotFoundError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(
            json.dumps(
                asdict(report)
                | {
                    "passed": report.passed,
                    "over_duration_budget": report.over_duration_budget,
                    "over_model_budget": report.over_model_budget,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"session {args.session_id}: turns={report.turns} "
            f"tool_calls={report.tool_calls} "
            f"frontend_calls={report.frontend_calls} mcp_calls={report.mcp_calls}"
        )
        print(f"orphan tool_use: {report.orphan_tool_use_ids}")
        print(f"repeated calls (>= {REPEAT_LIMIT}): {report.repeated_calls}")
        print(f"repeated tools (>= {SAME_TOOL_LIMIT}): {report.repeated_tools}")
        print(f"writes without readback: {report.writes_without_readback}")
        print(f"forbidden tool calls: {report.forbidden_tool_calls}")
        print(f"denied frontend calls: {report.denied_frontend_calls}")
        print(f"business tool errors: {report.business_tool_errors}")
        print(f"contract error codes: {report.contract_error_codes}")
        print(
            f"active duration: {report.active_duration_seconds:.0f}s "
            f"(session wall time {report.duration_seconds:.0f}s, "
            f"user requests {report.user_requests}) / "
            f"budget {report.duration_budget_seconds:.0f}s "
            f"{'OVER' if report.over_duration_budget else 'ok'}"
        )
        print(
            f"model invocations: {report.model_invocations} vs "
            f"tool_calls {report.tool_calls} "
            f"(limit {report.model_invocation_ratio}x) "
            f"{'OVER' if report.over_model_budget else 'ok'}"
        )
        if report.worst_turn is not None:
            turn, seconds, invocations, tool_calls = report.worst_turn
            print(
                f"worst turn: {turn} {seconds:.0f}s "
                f"model_invocations={invocations} tool_calls={tool_calls}"
            )
        print(
            f"stalled turns (> {report.turn_model_invocation_limit} model "
            f"round-trips): {report.stalled_turns}"
        )
        print(f"failed turns: {report.failed_turns}")
        print(f"overall: {'PASS' if report.passed else 'FAIL'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
