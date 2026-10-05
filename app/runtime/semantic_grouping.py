"""Host-owned hybrid semantic planning for dashboard options 3B and 3C."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from jsonschema import Draft202012Validator

STAGES = ("overview", "phenomenon", "trend", "breakdown", "cause", "detail")
STAGE_ORDER = {
    "A": {
        "overview": 0,
        "phenomenon": 1,
        "trend": 1,
        "breakdown": 1,
        "cause": 1,
        "detail": 1,
    },
    "B": {
        "overview": 0,
        "phenomenon": 0,
        "trend": 1,
        "breakdown": 2,
        "cause": 3,
        "detail": 4,
    },
    "C": {
        "phenomenon": 0,
        "overview": 0,
        "trend": 1,
        "breakdown": 2,
        "cause": 3,
        "detail": 4,
    },
}
FLAT_TYPES = frozenset(
    {"flat", "flatlayout", "flat_layout", "flat-container", "19001"}
)
TAB_TYPES = frozenset(
    {"tab", "tabs", "tablayout", "tab_layout", "tab-container", "19002"}
)
INTERNAL_TYPES = frozenset({"placeholder", "framework", "internal", "hidden"})
TEXT_TYPES = frozenset({"text", "richtext", "rich_text"})
TITLE_DECISION_TIMEOUT_SECONDS = 15.0
MAX_GROUP_TITLE_LENGTH = 20
FORBIDDEN_GROUP_TITLE_TERMS = (
    "多指标",
    "关联分析",
    "综合分析",
    "数据分析",
)
VISIBLE_CARD_TYPE_TITLES = {
    "1001": "表格",
    "2001": "指标",
    "3001": "柱状图",
    "3002": "堆积柱状图",
    "3003": "百分比柱状图",
    "4001": "趋势图",
    "4002": "平滑趋势图",
    "4003": "阶梯趋势图",
    "5001": "饼图",
    "5002": "环形图",
    "6001": "条形图",
    "6002": "堆积条形图",
    "6003": "百分比条形图",
    "7001": "面积图",
    "7002": "堆积面积图",
    "7003": "百分比面积图",
    "8001": "散点图",
    "9001": "雷达图",
    "10001": "漏斗图",
    "11001": "排行",
    "12001": "进度条",
    "12002": "半环进度",
    "12003": "圆环进度",
    "12004": "组合图",
    "13001": "透视表",
    "14001": "内嵌网页",
    "16001": "日历图",
    "17001": "甘特图",
    "18001": "说明",
    "20001": "消息概览",
    "20002": "消息指标卡",
    "20003": "消息列表",
    "20004": "消息指标",
    "21001": "任务概览",
    "21002": "任务指标",
    "21003": "任务列表",
    "metric": "指标",
    "metriccard": "指标",
    "metric_card": "指标",
    "chart": "趋势图",
    "line": "趋势图",
    "linechart": "趋势图",
    "line_chart": "趋势图",
    "area": "面积图",
    "areachart": "面积图",
    "area_chart": "面积图",
    "scatter": "散点图",
    "scatterchart": "散点图",
    "scatter_chart": "散点图",
    "funnel": "漏斗图",
    "funnelchart": "漏斗图",
    "funnel_chart": "漏斗图",
    "radar": "雷达图",
    "radarchart": "雷达图",
    "radar_chart": "雷达图",
    "leaderboard": "排行",
    "ranking": "排行",
    "rank": "排行",
    "table": "明细",
    "detail": "明细",
    "pivot": "透视表",
    "pivottable": "透视表",
    "pivot_table": "透视表",
    "pie": "饼图",
    "donut": "环形图",
    "doughnut": "环形图",
    "bar": "柱状图",
    "column": "柱状图",
    "progress": "进度图",
    "progressbar": "进度图",
    "progress_bar": "进度图",
    "gauge": "进度图",
    "combo": "组合图",
    "combination": "组合图",
    "iframe": "内嵌网页",
    "webpage": "内嵌网页",
    "calendar": "日历图",
    "gantt": "甘特图",
    "text": "说明",
    "richtext": "说明",
    "rich_text": "说明",
}
STAGE_TITLES = {
    "overview": "经营总览",
    "phenomenon": "现象判断",
    "trend": "趋势判断",
    "breakdown": "结构拆解",
    "cause": "风险归因",
    "detail": "明细下钻",
}


AMBIGUITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "widgetId": {"type": "string", "minLength": 1, "maxLength": 100},
                    "topicKey": {"type": "string", "minLength": 1, "maxLength": 80},
                    "topicTitle": {"type": "string", "minLength": 1, "maxLength": 50},
                    "stage": {"type": "string", "enum": list(STAGES)},
                },
                "required": ["widgetId", "topicKey", "topicTitle", "stage"],
            },
        },
    },
    "required": ["decisions"],
}

TITLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "groupTitles": {
            "type": "array",
            "minItems": 1,
            "maxItems": 200,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "groupId": {"type": "string", "minLength": 1, "maxLength": 100},
                    "title": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": MAX_GROUP_TITLE_LENGTH,
                    },
                },
                "required": ["groupId", "title"],
            },
        },
    },
    "required": ["groupTitles"],
}

_AMBIGUITY_VALIDATOR = Draft202012Validator(AMBIGUITY_SCHEMA)
_TITLE_VALIDATOR = Draft202012Validator(TITLE_SCHEMA)

# Backward import compatibility for the caller's ambiguity-planning options.
PLAN_SCHEMA = AMBIGUITY_SCHEMA


@dataclass(frozen=True, slots=True)
class DashboardCard:
    widget_id: str
    title: str
    chart_type: str
    input_index: int
    semantic_profile: Mapping[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "widgetId": self.widget_id,
            "title": self.title,
            "chartType": self.chart_type,
            "semanticProfile": dict(self.semantic_profile or {}),
        }


@dataclass(frozen=True, slots=True)
class DashboardStructure:
    resource_revision: int
    cards: tuple[DashboardCard, ...]
    original_root_ids: tuple[str, ...]
    flat_container_ids: tuple[str, ...]
    tab_container_ids: tuple[str, ...]
    preserved_tab_member_ids: tuple[str, ...]
    reserved_titles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SemanticDecision:
    widget_id: str
    topic_key: str
    topic_title: str
    stage: str


def _normalized_type(widget: Mapping[str, Any]) -> str:
    return str(widget.get("type") or "").strip().lower().replace(" ", "")


def _visual_key(
    widget: Mapping[str, Any], fallback: int
) -> tuple[float, float, float, int]:
    layout = widget.get("layout") if isinstance(widget.get("layout"), Mapping) else {}
    return (
        float(layout.get("y", 0)),
        float(layout.get("x", 0)),
        float(layout.get("order", fallback)),
        fallback,
    )


def extract_dashboard_structure(content: str) -> DashboardStructure:
    """Extract movable visible cards from one complete, successful structure receipt."""
    try:
        envelope = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("dashboard structure receipt is not valid JSON") from exc
    if not isinstance(envelope, Mapping) or envelope.get("status") != "success":
        raise ValueError("dashboard structure receipt was not successful")
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise TypeError("dashboard structure receipt has no data")
    widgets = data.get("widgets")
    if not isinstance(widgets, list) or data.get("hasMore") is not False:
        raise ValueError("complete structure receipt is required")
    revision = data.get("resourceRevision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("structure receipt has no valid resource revision")

    indexed: list[tuple[int, Mapping[str, Any]]] = []
    ids: list[str] = []
    for index, raw in enumerate(widgets):
        if not isinstance(raw, Mapping):
            raise TypeError("structure receipt contains an invalid widget")
        widget_id = str(raw.get("widgetId") or "")
        if not widget_id:
            raise ValueError("structure receipt contains a widget without an ID")
        ids.append(widget_id)
        indexed.append((index, raw))
    if len(set(ids)) != len(ids):
        raise ValueError("structure receipt contains duplicate widget IDs")
    if data.get("returnedCount") != len(widgets) or data.get("totalCount") != len(
        widgets
    ):
        raise ValueError("complete structure receipt is required")

    by_id = {str(widget["widgetId"]): widget for _, widget in indexed}
    roots = [
        (index, widget) for index, widget in indexed if widget.get("parentId") is None
    ]
    roots.sort(key=lambda item: _visual_key(item[1], item[0]))
    flat_ids = tuple(
        str(widget["widgetId"])
        for _, widget in roots
        if _normalized_type(widget) in FLAT_TYPES
    )
    tab_ids = tuple(
        str(widget["widgetId"])
        for _, widget in roots
        if _normalized_type(widget) in TAB_TYPES
    )
    tab_set = set(tab_ids)
    tab_members = tuple(
        str(widget["widgetId"])
        for _, widget in indexed
        if str(widget.get("parentId") or "") in tab_set
    )

    children: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, widget in indexed:
        parent_id = str(widget.get("parentId") or "")
        if parent_id:
            children[parent_id].append((index, widget))
    for members in children.values():
        members.sort(key=lambda item: _visual_key(item[1], item[0]))

    ordered_candidates: list[tuple[Mapping[str, Any], bool]] = []
    for _, root in roots:
        root_id = str(root["widgetId"])
        root_type = _normalized_type(root)
        if root_type in FLAT_TYPES:
            ordered_candidates.extend(
                (widget, True) for _, widget in children.get(root_id, [])
            )
        elif root_type not in TAB_TYPES:
            ordered_candidates.append((root, False))

    cards: list[DashboardCard] = []
    for widget, is_flat_child in ordered_candidates:
        widget_id = str(widget["widgetId"])
        parent_id = str(widget.get("parentId") or "")
        card_type = _normalized_type(widget)
        title = str(widget.get("title") or "").strip()
        if parent_id and parent_id not in by_id:
            continue
        if parent_id in tab_set or card_type in FLAT_TYPES | TAB_TYPES | INTERNAL_TYPES:
            continue
        if not is_flat_child and widget.get("layoutEditable") is not True:
            continue
        if card_type in TEXT_TYPES and not title:
            continue
        semantic_profile = widget.get("semanticProfile")
        if not isinstance(semantic_profile, Mapping):
            semantic_profile = None
        cards.append(
            DashboardCard(
                widget_id,
                title,
                card_type,
                len(cards),
                semantic_profile,
            )
        )
    if not cards:
        raise ValueError("complete structure receipt contains no visible movable cards")
    card_ids = {card.widget_id for card in cards}
    flat_set = set(flat_ids)
    reserved_titles: list[str] = []
    for _, widget in indexed:
        widget_id = str(widget["widgetId"])
        widget_type = _normalized_type(widget)
        parent_id = str(widget.get("parentId") or "")
        title = str(widget.get("title") or "").strip()
        if (
            not title
            or widget_id in card_ids | flat_set
            or widget_type in INTERNAL_TYPES
            or (parent_id and parent_id not in by_id)
        ):
            continue
        ancestor_id = parent_id
        retained = not parent_id
        visited: set[str] = set()
        while ancestor_id and ancestor_id not in visited:
            visited.add(ancestor_id)
            ancestor = by_id.get(ancestor_id)
            if ancestor is None:
                break
            ancestor_type = _normalized_type(ancestor)
            if ancestor_type in FLAT_TYPES:
                retained = False
                break
            if ancestor_type in TAB_TYPES:
                retained = True
                break
            ancestor_id = str(ancestor.get("parentId") or "")
        if retained and title not in reserved_titles:
            reserved_titles.append(title)
    original_root_ids = tuple(
        str(widget["widgetId"])
        for _, widget in roots
        if _normalized_type(widget) not in INTERNAL_TYPES
        and (
            _normalized_type(widget) in FLAT_TYPES | TAB_TYPES
            or widget.get("layoutEditable") is True
        )
        and not (
            _normalized_type(widget) in TEXT_TYPES
            and not str(widget.get("title") or "").strip()
        )
    )
    return DashboardStructure(
        revision,
        tuple(cards),
        original_root_ids,
        flat_ids,
        tab_ids,
        tab_members,
        tuple(reserved_titles),
    )


EXPLICIT_STAGES = {
    "经营总览": "overview",
    "核心指标": "overview",
    "现象判断": "phenomenon",
    "趋势判断": "trend",
    "结构拆解": "breakdown",
    "风险归因": "cause",
    "原因分析": "cause",
    "明细下钻": "detail",
    "效率监控": "overview",
}
SEMANTIC_PROFILE_FIELDS = (
    "datasets",
    "dimensions",
    "metrics",
    "filters",
    "grouping",
    "time",
    "drillPaths",
)
METRIC_TYPES = frozenset(
    {"metric", "metriccard", "metric_card", "2001", "20002", "20004", "21002"}
)
LEADERBOARD_TYPES = frozenset({"leaderboard", "ranking", "rank", "11001"})
DETAIL_TYPES = frozenset(
    {"table", "detail", "pivot", "pivottable", "pivot_table", "1001", "13001"}
)
NON_DATA_TYPES = TEXT_TYPES | frozenset(
    {"iframe", "webpage", "calendar", "gantt", "14001", "16001", "17001", "18001"}
)


def _stage_for(card: DashboardCard) -> str | None:
    prefix = card.title.replace(":", "：").split("：", 1)[0]
    if prefix in EXPLICIT_STAGES:
        return EXPLICIT_STAGES[prefix]
    title = card.title.lower()
    if card.chart_type == "metric":
        return "overview"
    if card.chart_type == "table" or any(word in title for word in ("明细", "清单")):
        return "detail"
    if any(word in title for word in ("原因", "归因", "驱动因素", "风险因子")):
        return "cause"
    if any(word in title for word in ("趋势", "走势", "变化")):
        return "trend"
    if card.chart_type == "leaderboard" or any(
        word in title for word in ("占比", "分布", "排行", "top", "各")
    ):
        return "breakdown"
    profile = _profile_parts(card)
    if profile["time"]:
        return "trend"
    if profile["dimensions"] | profile["grouping"] | profile["drillPaths"]:
        return "breakdown"
    return None


def _normalized_ref(value: Any) -> str:
    return str(value or "").strip().casefold()


def _scalar_profile_values(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    return frozenset(ref for item in values if (ref := _normalized_ref(item)))


def _field_profile_refs(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        value = [value]
    refs: set[str] = set()
    for item in value:
        ref = _normalized_ref(item.get("ref")) if isinstance(item, Mapping) else _normalized_ref(item)
        if ref:
            refs.add(ref)
    return frozenset(refs)


def _metric_profile_refs(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        value = [value]
    metrics: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            ref = _normalized_ref(item.get("ref"))
            aggregation = _normalized_ref(item.get("agg"))
        else:
            ref = _normalized_ref(item)
            aggregation = ""
        if ref:
            metrics.add(f"{ref}\x1f{aggregation}")
    return frozenset(metrics)


def _drill_path_refs(value: Any) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    refs: set[str] = set()
    for path in value:
        members = path if isinstance(path, (list, tuple, set, frozenset)) else [path]
        refs.update(ref for member in members if (ref := _normalized_ref(member)))
    return frozenset(refs)


def _profile_parts(card: DashboardCard) -> dict[str, frozenset[str]]:
    profile = card.semantic_profile if isinstance(card.semantic_profile, Mapping) else {}
    return {
        "datasets": _scalar_profile_values(profile.get("datasets")),
        "dimensions": _field_profile_refs(profile.get("dimensions")),
        "metrics": _metric_profile_refs(profile.get("metrics")),
        "filters": _field_profile_refs(profile.get("filters")),
        "grouping": _field_profile_refs(profile.get("grouping")),
        "time": _field_profile_refs(profile.get("time")),
        "drillPaths": _drill_path_refs(profile.get("drillPaths")),
    }


def _card_family(card: DashboardCard) -> str:
    if card.chart_type in METRIC_TYPES:
        return "metric"
    if card.chart_type in LEADERBOARD_TYPES:
        return "leaderboard"
    if card.chart_type in DETAIL_TYPES:
        return "detail"
    if card.chart_type in NON_DATA_TYPES:
        return "non-data"
    return "visual"


def _relation_evidence(left: DashboardCard, right: DashboardCard) -> frozenset[str]:
    left_parts = _profile_parts(left)
    right_parts = _profile_parts(right)
    if not any(left_parts.values()) or not any(right_parts.values()):
        return frozenset()
    if _card_family(left) == "non-data" or _card_family(right) == "non-data":
        return frozenset()

    left_datasets = left_parts["datasets"]
    right_datasets = right_parts["datasets"]
    if left_datasets and right_datasets and left_datasets.isdisjoint(right_datasets):
        return frozenset()

    left_axes = (
        left_parts["dimensions"] | left_parts["grouping"] | left_parts["drillPaths"]
    )
    right_axes = (
        right_parts["dimensions"] | right_parts["grouping"] | right_parts["drillPaths"]
    )
    shared_axes = left_axes & right_axes
    if left_axes and right_axes and not shared_axes:
        return frozenset()

    evidence: set[str] = set()
    if left_datasets & right_datasets:
        evidence.add("dataset")
    if shared_axes:
        evidence.add("axis")
    if left_parts["metrics"] & right_parts["metrics"]:
        evidence.add("metric")
    if left_parts["filters"] & right_parts["filters"]:
        evidence.add("filter")
    if left_parts["time"] & right_parts["time"]:
        evidence.add("time")

    if "axis" in evidence or "metric" in evidence:
        return frozenset(evidence)
    if (
        "dataset" in evidence
        and _card_family(left) == _card_family(right) == "metric"
        and ("filter" in evidence or "time" in evidence or not left_axes | right_axes)
    ):
        return frozenset(evidence)
    return frozenset()


def _profile_topic_key(cards: list[DashboardCard]) -> str:
    fingerprints = [
        {
            field: sorted(_profile_parts(card)[field])
            for field in SEMANTIC_PROFILE_FIELDS
        }
        for card in cards
    ]
    digest = hashlib.sha256(
        json.dumps(fingerprints, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return f"profile:{digest}"


def _related_profile_groups(cards: tuple[DashboardCard, ...]) -> list[list[DashboardCard]]:
    groups: list[list[DashboardCard]] = []
    for card in cards:
        candidates: list[tuple[int, int]] = []
        for index, group in enumerate(groups):
            evidence = [_relation_evidence(card, member) for member in group]
            if all(evidence):
                candidates.append((sum(len(item) for item in evidence), index))
        if candidates:
            _, target = max(candidates, key=lambda item: (item[0], -item[1]))
            groups[target].append(card)
        else:
            groups.append([card])
    return groups


def deterministic_decisions(
    cards: tuple[DashboardCard, ...],
    strategy: str,
) -> tuple[dict[str, SemanticDecision], list[DashboardCard]]:
    if strategy not in {"A", "B", "C"}:
        raise ValueError("semantic grouping strategy must be A, B, or C")
    decisions: dict[str, SemanticDecision] = {}
    ambiguous: list[DashboardCard] = []
    for group in _related_profile_groups(cards):
        if len(group) < 2:
            ambiguous.extend(group)
            continue
        stages = {
            card.widget_id: (
                "overview"
                if strategy == "A" and _card_family(card) == "metric"
                else (_stage_for(card) or ("detail" if strategy == "A" else None))
            )
            for card in group
        }
        if any(stage is None for stage in stages.values()):
            ambiguous.extend(group)
            continue
        topic_key = _profile_topic_key(group)
        for card in group:
            stage = stages[card.widget_id]
            assert stage is not None
            decisions[card.widget_id] = SemanticDecision(
                card.widget_id,
                topic_key,
                "关联分析",
                stage,
            )
    return decisions, ambiguous


def _anchors(
    decisions: Mapping[str, SemanticDecision], cards: tuple[DashboardCard, ...]
) -> list[dict[str, Any]]:
    card_by_id = {card.widget_id: card for card in cards}
    grouped: dict[str, list[SemanticDecision]] = defaultdict(list)
    for decision in decisions.values():
        grouped[decision.topic_key].append(decision)
    anchors = []
    for topic_key, items in grouped.items():
        member_cards = [card_by_id[item.widget_id] for item in items]
        anchors.append(
            {
                "topicKey": topic_key,
                "topicTitle": items[0].topic_title,
                "stages": list(dict.fromkeys(item.stage for item in items)),
                "representativeTitles": [
                    card.title for card in member_cards[:2]
                ],
                "cardTypes": list(dict.fromkeys(card.chart_type for card in member_cards)),
                "semanticProfiles": [
                    dict(card.semantic_profile or {}) for card in member_cards[:2]
                ],
            }
        )
    return anchors


def grouping_prompt(arguments: Mapping[str, Any]) -> str:
    """Build the single AI call for only rule-ambiguous cards."""
    return (
        "Return exactly one ambiguity decision set as JSON. Do not reorder deterministic "
        "cards or repeat them in decisions. Assign every ambiguous card exactly once to a "
        "business topic and stage. Decide membership from semanticProfile data relationships "
        "and chartType display compatibility; titles may inform stage and final wording only. "
        "Use a supplied anchor when its configuration is clearly related or create a concise "
        "new topic otherwise. Keep unsupported causal claims out of strategy C. Do not "
        "discuss alternatives or explain card by card.\nstrategy="
        + str(arguments["strategy"])
        + "\nanchors="
        + json.dumps(
            arguments.get("anchors", []), ensure_ascii=False, separators=(",", ":")
        )
        + "\nambiguousCards="
        + json.dumps(
            arguments.get("cards", []), ensure_ascii=False, separators=(",", ":")
        )
    )


def title_prompt(arguments: Mapping[str, Any]) -> str:
    """Build one grounded batch request for all compiled group titles."""
    return (
        "Return exactly one group title set as JSON with groupTitles. Cover every "
        "groupId exactly once and keep every title unique. Use the form 阶段：内容摘要, "
        "using exactly one supplied stageTitle before the Chinese colon. Keep the full "
        "title at most 20 characters. Synthesize all distinct memberContents: retain a supplied "
        "semanticFrame businessObjectCandidate and one grounded analysisIntentCandidate. Never "
        "copy only one member title when the group has different members. Use every memberCards "
        "entry, including its chartType and semanticProfile, to understand the complete data "
        "meaning; do not expose internal field names in the title. Never use 多指标, "
        "关联分析, 综合分析, or 数据分析. Never invent facts, dimensions, "
        "conclusions, or causes. "
        "Do not reuse any reservedTitle. Return no prose.\nstrategy="
        + str(arguments["strategy"])
        + "\nreservedTitles="
        + json.dumps(
            arguments.get("reservedTitles", []),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\ngroups="
        + json.dumps(
            arguments.get("groups", []), ensure_ascii=False, separators=(",", ":")
        )
    )


def _validate_ai_decisions(
    ambiguous: list[DashboardCard],
    output: Mapping[str, Any],
) -> dict[str, SemanticDecision]:
    _AMBIGUITY_VALIDATOR.validate(output)
    expected = {card.widget_id for card in ambiguous}
    raw_decisions = output["decisions"]
    result: dict[str, SemanticDecision] = {}
    for raw in raw_decisions:
        widget_id = raw["widgetId"]
        topic_key = raw["topicKey"].strip()
        topic_title = raw["topicTitle"].strip()
        stage = raw["stage"]
        if (
            widget_id not in expected
            or widget_id in result
            or not topic_key
            or not topic_title
            or stage not in STAGES
        ):
            raise ValueError("AI ambiguity result does not match ambiguous cards")
        result[widget_id] = SemanticDecision(widget_id, topic_key, topic_title, stage)
    if set(result) != expected:
        raise ValueError(
            "AI ambiguity result must cover every ambiguous card exactly once"
        )
    return result


def _title_requests(
    groups: list[dict[str, Any]],
    decisions: Mapping[str, SemanticDecision],
    cards: tuple[DashboardCard, ...],
) -> list[dict[str, Any]]:
    card_by_id = {card.widget_id: card for card in cards}
    requests: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        member_ids = group["widgetIds"]
        member_cards = [card_by_id[widget_id] for widget_id in member_ids]
        semantic_frame = _title_semantic_frame(member_cards)
        stages = list(
            dict.fromkeys(decisions[widget_id].stage for widget_id in member_ids)
        )
        requests.append(
            {
                "groupId": f"group-{index}",
                "stages": stages,
                "stageTitles": _stage_titles_for_group(
                    [decisions[widget_id] for widget_id in member_ids], card_by_id
                ),
                "memberContents": [
                    _visible_card_content(card_by_id[widget_id])
                    for widget_id in member_ids
                ],
                "memberCards": [
                    {
                        "content": _visible_card_content(card_by_id[widget_id]),
                        "chartType": card_by_id[widget_id].chart_type,
                        "semanticProfile": dict(
                            card_by_id[widget_id].semantic_profile or {}
                        ),
                    }
                    for widget_id in member_ids
                ],
                "semanticFrame": semantic_frame,
                "fallbackTitle": group["title"],
            }
        )
    return requests


def _normalized_title_text(value: str) -> str:
    return _compact_title_text(value).casefold()


def _compact_title_text(value: str) -> str:
    return re.sub(r"[\s:：，,。；;（）()\-—_]+", "", value)


def _member_title_content(title: str) -> str:
    normalized = title.replace(":", "：")
    prefix, separator, content = normalized.partition("：")
    return content if separator and prefix in EXPLICIT_STAGES else normalized


def _visible_card_content(card: DashboardCard) -> str:
    """Use visible card type metadata only when title content is missing."""
    content = _compact_title_text(_member_title_content(card.title))
    if content:
        return content
    return _visible_card_type_title(card)


def _visible_card_type_title(card: DashboardCard) -> str:
    """Return the grounded display name for one real card type."""
    return VISIBLE_CARD_TYPE_TITLES.get(card.chart_type, card.chart_type or "卡片")


def _grounded_summary(
    summary: str,
    member_contents: list[str],
    semantic_frame: Mapping[str, Any],
) -> bool:
    contents = [_normalized_title_text(content) for content in member_contents]
    distinct_contents = list(dict.fromkeys(content for content in contents if content))
    normalized_summary = _normalized_title_text(summary)
    if not normalized_summary or any(
        _normalized_title_text(term) in normalized_summary
        for term in FORBIDDEN_GROUP_TITLE_TERMS
    ):
        return False
    if len(distinct_contents) > 1 and normalized_summary in distinct_contents:
        return False
    object_candidates = [
        _normalized_title_text(value)
        for value in semantic_frame.get("businessObjectCandidates", [])
        if _normalized_title_text(value)
    ]
    intent_candidates = [
        _normalized_title_text(value)
        for value in semantic_frame.get("analysisIntentCandidates", [])
        if _normalized_title_text(value)
    ]
    return any(candidate in normalized_summary for candidate in object_candidates) and any(
        candidate in normalized_summary for candidate in intent_candidates
    )


def _common_member_prefix(contents: list[str]) -> str:
    distinct = list(dict.fromkeys(_compact_title_text(content) for content in contents))
    if len(distinct) < 2:
        return ""
    prefix = distinct[0]
    for content in distinct[1:]:
        while prefix and not content.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    return prefix.rstrip("与和及、")


_DISPLAY_SUFFIXES = (
    "TOP排行榜",
    "TOP排行",
    "排行榜",
    "排行",
    "排名",
    "走势",
    "趋势",
    "变化",
    "占比",
    "分布",
    "明细",
    "对比",
    "比较",
)
_MEASURE_SUFFIXES = (
    "平均提交到成交时长",
    "订单金额",
    "订单量",
    "用户数",
    "客户数",
    "客单价",
    "件单价",
    "金额",
    "数量",
    "单量",
    "件数",
    "人数",
    "时长",
    "周期",
    "次数",
    "比率",
    "比例",
    "率",
)


def _semantic_object_stem(content: str) -> str:
    value = _compact_title_text(content)
    changed = True
    while value and changed:
        changed = False
        for suffix in _DISPLAY_SUFFIXES + _MEASURE_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)].rstrip("的")
                changed = True
                break
    return value or _compact_title_text(content)


def _title_semantic_frame(cards: list[DashboardCard]) -> dict[str, Any]:
    contents = [_visible_card_content(card) for card in cards]
    stems = list(dict.fromkeys(_semantic_object_stem(content) for content in contents))
    common = _common_member_prefix(stems)
    object_candidates: list[str] = []
    if common and len(common) >= 2:
        if len(stems) > 1 and common.startswith("各"):
            expanded = min(
                (stem for stem in stems if stem.startswith(common)),
                key=lambda value: (len(value), value),
            )
            object_candidates.append(expanded)
        object_candidates.append(common)
    elif len(stems) == 1:
        object_candidates.append(stems[0])
    elif stems:
        combined = "与".join(stems[:2])
        object_candidates.append(combined)

    normalized_contents = [_normalized_title_text(content) for content in contents]
    families = {_card_family(card) for card in cards}
    has_ranking = "leaderboard" in families or any(
        any(marker.casefold() in content for marker in ("排行", "排名", "top"))
        for content in normalized_contents
    )
    has_structure = any(
        any(marker in content for marker in ("占比", "分布", "结构"))
        for content in normalized_contents
    )
    has_trend = any(
        any(marker in content for marker in ("趋势", "走势", "变化"))
        for content in normalized_contents
    )
    if has_ranking:
        distinct_meanings = {
            _semantic_object_stem(content) + "\x1f" + content for content in contents
        }
        intents = ["综合排行" if len(distinct_meanings) > 1 else "排行"]
    elif has_structure:
        intents = ["结构"]
    elif has_trend or any(_stage_for(card) == "trend" for card in cards):
        intents = ["趋势"]
    elif families == {"detail"}:
        intents = ["明细"]
    else:
        intents = ["表现"]

    if not object_candidates or not intents:
        raise ValueError("unable to derive a grounded group title frame")
    primary_object = object_candidates[0]
    primary_intent = intents[0]
    return {
        "businessObjectCandidates": list(dict.fromkeys(object_candidates))[:4],
        "analysisIntentCandidates": intents[:4],
        "memberCoverage": [
            {
                "memberIndex": index,
                "object": primary_object,
                "intent": primary_intent,
            }
            for index in range(len(cards))
        ],
    }


def _validate_ai_titles(
    requests: list[dict[str, Any]],
    output: Mapping[str, Any],
    reserved_titles: tuple[str, ...] = (),
) -> dict[str, str]:
    _TITLE_VALIDATOR.validate(output)
    expected = {request["groupId"]: request for request in requests}
    raw_titles = output["groupTitles"]
    result: dict[str, str] = {}
    normalized_titles = {
        _normalized_title_text(title) for title in reserved_titles if title.strip()
    }
    for raw in raw_titles:
        group_id = raw["groupId"]
        title = raw["title"].strip()
        if group_id not in expected or group_id in result:
            raise ValueError("AI title result does not match compiled groups")
        if len(title) > MAX_GROUP_TITLE_LENGTH or title.count("：") != 1:
            raise ValueError("AI title result contains a malformed title")
        stage_title, summary = title.split("：", 1)
        request = expected[group_id]
        if any(term in summary for term in FORBIDDEN_GROUP_TITLE_TERMS):
            raise ValueError("AI title result contains a forbidden placeholder")
        if stage_title not in request["stageTitles"] or not _grounded_summary(
            summary, request["memberContents"], request["semanticFrame"]
        ):
            raise ValueError("AI title result is not grounded in its group")
        normalized_title = _normalized_title_text(title)
        if normalized_title in normalized_titles:
            raise ValueError("AI title result contains duplicate titles")
        normalized_titles.add(normalized_title)
        result[group_id] = title
    if set(result) != set(expected):
        raise ValueError("AI title result must cover every compiled group exactly once")
    return result


def _title_rejection_reason(error: Exception) -> str:
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    message = str(error).casefold()
    if "forbidden placeholder" in message:
        return "forbidden_term"
    if "duplicate" in message:
        return "duplicate"
    if "grounded" in message:
        return "incomplete_coverage"
    if "malformed" in message or "schema" in type(error).__name__.casefold():
        return "malformed_output"
    return "model_error"


def _stage_titles_for_group(
    items: list[SemanticDecision],
    card_by_id: Mapping[str, DashboardCard],
) -> list[str]:
    titles: list[str] = []
    for item in items:
        prefix = card_by_id[item.widget_id].title.replace(":", "：").split("：", 1)[0]
        if EXPLICIT_STAGES.get(prefix) == item.stage and prefix not in titles:
            titles.append(prefix)
    for stage in dict.fromkeys(item.stage for item in items):
        canonical = STAGE_TITLES[stage]
        if canonical not in titles:
            titles.append(canonical)
    return titles


def _grounded_title_candidates(
    items: list[SemanticDecision],
    card_by_id: Mapping[str, DashboardCard],
) -> list[str]:
    """Build deterministic titles from stage and user-visible content only."""
    stage_titles = _stage_titles_for_group(items, card_by_id)
    cards = [card_by_id[item.widget_id] for item in items]
    frame = _title_semantic_frame(cards)

    candidates: list[str] = []
    for stage_title in stage_titles:
        available = MAX_GROUP_TITLE_LENGTH - len(stage_title) - 1
        for business_object in frame["businessObjectCandidates"]:
            for intent in frame["analysisIntentCandidates"]:
                summary = f"{business_object}{intent}"
                if len(summary) <= available and _grounded_summary(
                    summary,
                    [_visible_card_content(card) for card in cards],
                    frame,
                ):
                    candidates.append(f"{stage_title}：{summary}")
    return list(dict.fromkeys(candidates))


def _fallback_group_title(
    items: list[SemanticDecision],
    card_by_id: Mapping[str, DashboardCard],
    reserved_titles: set[str],
    assigned_titles: set[str],
) -> str:
    candidates = _grounded_title_candidates(items, card_by_id)
    if not candidates:
        first = items[0]
        stage_title = STAGE_TITLES[first.stage]
        available = MAX_GROUP_TITLE_LENGTH - len(stage_title) - 1
        visible_content = _visible_card_content(card_by_id[first.widget_id])
        candidates = [f"{stage_title}：{visible_content[:available]}"]

    for candidate in candidates:
        normalized = _normalized_title_text(candidate)
        if normalized not in reserved_titles and normalized not in assigned_titles:
            assigned_titles.add(normalized)
            return candidate

    # Reserved-title avoidance is best-effort when distinct groups produce the
    # same summary. Add a compact visible sequence so every group remains
    # addressable by the frontend without leaking internal identifiers.
    for candidate in candidates:
        normalized = _normalized_title_text(candidate)
        if normalized not in assigned_titles:
            assigned_titles.add(normalized)
            return candidate

    base = candidates[0]
    for sequence in range(2, 10000):
        suffix = f"（{sequence}）"
        candidate = f"{base[: MAX_GROUP_TITLE_LENGTH - len(suffix)]}{suffix}"
        normalized = _normalized_title_text(candidate)
        if normalized not in reserved_titles and normalized not in assigned_titles:
            assigned_titles.add(normalized)
            return candidate
    raise ValueError("unable to build a unique visible group title")


def _order_group_members(
    items: list[SemanticDecision],
    card_by_id: Mapping[str, DashboardCard],
    positions: Mapping[str, int],
    stage_order: Mapping[str, int],
) -> list[SemanticDecision]:
    ordered: list[SemanticDecision] = []
    stages = sorted({item.stage for item in items}, key=stage_order.__getitem__)
    for stage in stages:
        remaining = sorted(
            (item for item in items if item.stage == stage),
            key=lambda item: positions[item.widget_id],
        )
        if not remaining:
            continue
        stage_items = tuple(remaining)

        def seed_affinity(
            item: SemanticDecision,
            candidates: tuple[SemanticDecision, ...] = stage_items,
        ) -> tuple[int, int, int]:
            candidate = card_by_id[item.widget_id]
            relations = [
                (
                    card_by_id[other.widget_id],
                    _relation_evidence(candidate, card_by_id[other.widget_id]),
                )
                for other in candidates
                if other.widget_id != item.widget_id
            ]
            complementary = sum(
                bool(relation)
                and _card_family(candidate) != _card_family(other)
                for other, relation in relations
            )
            return (
                sum(len(relation) for _, relation in relations),
                complementary,
                -positions[item.widget_id],
            )

        seed = max(remaining, key=seed_affinity)
        remaining.remove(seed)
        ordered.append(seed)
        while remaining:
            previous = card_by_id[ordered[-1].widget_id]

            def affinity(
                item: SemanticDecision,
                previous_card: DashboardCard = previous,
            ) -> tuple[int, int, int]:
                candidate = card_by_id[item.widget_id]
                evidence = _relation_evidence(previous_card, candidate)
                complementary = int(
                    bool(evidence)
                    and _card_family(previous_card) != _card_family(candidate)
                )
                return (
                    len(evidence),
                    complementary,
                    -positions[item.widget_id],
                )

            next_item = max(remaining, key=affinity)
            remaining.remove(next_item)
            ordered.append(next_item)
    return ordered


def _compile_order(
    cards: tuple[DashboardCard, ...],
    decisions: Mapping[str, SemanticDecision],
    strategy: str,
    reserved_titles: tuple[str, ...] = (),
) -> tuple[list[str], list[dict[str, Any]]]:
    positions = {card.widget_id: card.input_index for card in cards}
    card_by_id = {card.widget_id: card for card in cards}
    grouped: dict[str, list[SemanticDecision]] = defaultdict(list)
    for decision in decisions.values():
        grouped[decision.topic_key].append(decision)
    topics = sorted(
        grouped.values(),
        key=lambda items: (
            min(
                STAGE_ORDER.get(strategy, STAGE_ORDER["B"])[item.stage]
                for item in items
            ),
            min(positions[item.widget_id] for item in items),
        ),
    )
    ordered: list[str] = []
    groups: list[dict[str, Any]] = []
    reserved_group_titles = {
        _normalized_title_text(title) for title in reserved_titles if title.strip()
    }
    assigned_group_titles: set[str] = set()
    stage_order = STAGE_ORDER.get(strategy, STAGE_ORDER["B"])
    for items in topics:
        items = _order_group_members(
            items,
            card_by_id,
            positions,
            stage_order,
        )
        members = [item.widget_id for item in items]
        ordered.extend(members)
        if len(members) >= 2:
            fallback_title = _fallback_group_title(
                items,
                card_by_id,
                reserved_group_titles,
                assigned_group_titles,
            )
            groups.append({"title": fallback_title, "widgetIds": members})
    grouped_ids = set(ordered)
    ordered.extend(
        card.widget_id for card in cards if card.widget_id not in grouped_ids
    )
    if len(ordered) != len(cards) or len(set(ordered)) != len(cards):
        raise ValueError(
            "compiled semantic plan must cover every visible card exactly once"
        )
    return ordered, groups


async def compile_semantic_layout(
    snapshot: DashboardStructure,
    strategy: str,
    *,
    ai_decider: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
    | None = None,
    title_decider: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
    | None = None,
) -> dict[str, Any]:
    """Compile a Host layout with at most one ambiguity and one title request."""
    started = perf_counter()
    deterministic_started = perf_counter()
    decisions, ambiguous = deterministic_decisions(snapshot.cards, strategy)
    deterministic_ms = round((perf_counter() - deterministic_started) * 1000, 3)
    ai_calls = 0
    ai_fallback = False
    ai_ms = 0.0
    if ambiguous and ai_decider is not None:
        payload = {
            "strategy": strategy,
            "cards": [card.public_dict() for card in ambiguous],
            "anchors": _anchors(decisions, snapshot.cards),
        }
        ai_started = perf_counter()
        ai_calls = 1
        try:
            output = await ai_decider(payload)
            decisions.update(_validate_ai_decisions(ambiguous, output))
        except Exception:  # noqa: BLE001 - invalid/failed AI must deterministically degrade.
            ai_fallback = True
        ai_ms = round((perf_counter() - ai_started) * 1000, 3)

    if strategy == "A":
        for card in snapshot.cards:
            if card.widget_id in decisions:
                continue
            decisions[card.widget_id] = SemanticDecision(
                card.widget_id,
                f"unresolved:{card.widget_id}",
                "待判断",
                "overview" if _card_family(card) == "metric" else (_stage_for(card) or "detail"),
            )

    assembly_started = perf_counter()
    assembly_seconds = 0.0
    ordered, groups = _compile_order(
        snapshot.cards, decisions, strategy, snapshot.reserved_titles
    )
    title_ai_calls = 0
    title_ai_fallback = False
    title_ai_ms = 0.0
    title_requests = _title_requests(groups, decisions, snapshot.cards) if groups else []
    title_groups_diagnostics = [
        {
            "groupId": request["groupId"],
            "memberIds": list(groups[index]["widgetIds"][:50]),
            "businessObjectCandidates": list(
                request["semanticFrame"]["businessObjectCandidates"][:4]
            ),
            "analysisIntentCandidates": list(
                request["semanticFrame"]["analysisIntentCandidates"][:4]
            ),
            "acceptedTitleSource": "rule",
            "validationRejectionReason": None,
        }
        for index, request in enumerate(title_requests[:50])
    ]
    effective_title_decider = title_decider or ai_decider
    if groups and effective_title_decider is not None:
        payload = {
            "phase": "group_titles",
            "strategy": strategy,
            "reservedTitles": list(snapshot.reserved_titles),
            "groups": title_requests,
        }
        assembly_seconds += perf_counter() - assembly_started
        title_ai_started = perf_counter()
        title_ai_calls = 1
        try:
            output = await asyncio.wait_for(
                effective_title_decider(payload),
                timeout=TITLE_DECISION_TIMEOUT_SECONDS,
            )
            generated_titles = _validate_ai_titles(
                title_requests, output, snapshot.reserved_titles
            )
            for index, group in enumerate(groups):
                group["title"] = generated_titles[f"group-{index}"]
                if index < len(title_groups_diagnostics):
                    title_groups_diagnostics[index]["acceptedTitleSource"] = "model"
        except Exception as exc:  # noqa: BLE001 - title failures use the compiled fallback.
            title_ai_fallback = True
            rejection_reason = _title_rejection_reason(exc)
            for diagnostic in title_groups_diagnostics:
                diagnostic["acceptedTitleSource"] = "deterministic_fallback"
                diagnostic["validationRejectionReason"] = rejection_reason
        title_ai_ms = round((perf_counter() - title_ai_started) * 1000, 3)
        assembly_started = perf_counter()

    preset: dict[str, Any] = {"mode": "reorder", "sizing": "content"}
    if groups:
        preset.update(
            orderedWidgetIds=ordered + list(snapshot.tab_container_ids),
            groups=groups,
            groupingConfirmed=True,
        )
        if snapshot.flat_container_ids:
            preset["regroup"] = {
                "containerWidgetIds": list(snapshot.flat_container_ids),
                "confirmed": True,
            }
    else:
        preset["orderedWidgetIds"] = (
            ordered + list(snapshot.tab_container_ids)
            if strategy == "A" and not snapshot.flat_container_ids
            else list(snapshot.original_root_ids)
        )
    layout_arguments = {
        "preset": preset,
        "expectedResourceRevision": snapshot.resource_revision,
    }
    assembly_seconds += perf_counter() - assembly_started
    assembly_ms = round(assembly_seconds * 1000, 3)
    return {
        "status": "success",
        "layoutArguments": layout_arguments,
        "summary": f"已编译 {len(snapshot.cards)} 张可见卡片，生成 {len(groups)} 个业务分组。",
        "diagnostics": {
            "visibleCardCount": len(snapshot.cards),
            "deterministicCardCount": len(snapshot.cards) - len(ambiguous),
            "ambiguousCardCount": len(ambiguous),
            "aiCallCount": ai_calls,
            "aiFallback": ai_fallback,
            "deterministicPlanningMs": deterministic_ms,
            "aiPlanningMs": ai_ms,
            "titleAiCallCount": title_ai_calls,
            "titleAiFallback": title_ai_fallback,
            "titleAiPlanningMs": title_ai_ms,
            "titleGroups": title_groups_diagnostics,
            "parameterAssemblyMs": assembly_ms,
            "totalPlanningMs": round((perf_counter() - started) * 1000, 3),
        },
    }


async def run_semantic_grouping_query(
    arguments: Mapping[str, Any],
    *,
    options: ClaudeAgentOptions,
) -> dict[str, Any]:
    """Run one isolated structured-output request for the requested semantic phase."""
    result: ResultMessage | None = None
    prompt = (
        title_prompt(arguments)
        if arguments.get("phase") == "group_titles"
        else grouping_prompt(arguments)
    )
    schema = (
        TITLE_SCHEMA
        if arguments.get("phase") == "group_titles"
        else AMBIGUITY_SCHEMA
    )
    phase_options = replace(
        options,
        output_format={"type": "json_schema", "schema": schema},
    )
    messages: AsyncIterator[Any] = query(prompt=prompt, options=phase_options)
    async for message in messages:
        if isinstance(message, ResultMessage):
            result = message
    if result is None or result.is_error:
        raise RuntimeError("Semantic grouping planner did not return a result")
    output = result.structured_output
    if not isinstance(output, Mapping):
        if not result.result:
            raise RuntimeError("Semantic grouping planner returned no JSON")
        output = json.loads(result.result)
    if not isinstance(output, Mapping):
        raise TypeError("Semantic grouping planner returned invalid JSON")
    return dict(output)
