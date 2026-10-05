import json

import pytest
from jsonschema import Draft202012Validator

from app.runtime import semantic_grouping
from app.runtime.contracts import RuntimeToolResult
from app.runtime.semantic_grouping import (
    compile_semantic_layout,
    extract_dashboard_structure,
    grouping_prompt,
    run_semantic_grouping_query,
)


def structure_receipt(widgets, *, revision=8, has_more=False):
    data = {
        "summary": "test dashboard",
        "resourceRevision": revision,
        "totalCount": len(widgets),
        "rootCount": sum(widget["parentId"] is None for widget in widgets),
        "childCount": sum(widget["parentId"] is not None for widget in widgets),
        "returnedCount": len(widgets),
        "hasMore": has_more,
        "widgets": widgets,
    }
    return RuntimeToolResult("structure-call", json.dumps({
        "status": "success", "data": data, "issues": [],
    }))


def widget(
    widget_id,
    title,
    card_type,
    *,
    parent_id=None,
    editable=True,
    order=0,
    semantic_profile=None,
):
    result = {
        "widgetId": widget_id,
        "title": title,
        "type": card_type,
        "parentId": parent_id,
        "coordinateSpace": "container" if parent_id else "root",
        "layoutEditable": editable,
        "layout": {"x": 0, "y": order, "width": 12, "height": 6, "order": order},
    }
    if semantic_profile is not None:
        result["semanticProfile"] = semantic_profile
    return result


def profile(
    dataset,
    *,
    dimensions=(),
    metrics=(),
    filters=(),
    grouping=(),
    time=(),
    drill_paths=(),
):
    def fields(values):
        return [
            value if isinstance(value, dict) else {"ref": value}
            for value in values
        ]

    def metric_fields(values):
        return [
            value if isinstance(value, dict) else {"ref": value, "agg": "sum"}
            for value in values
        ]

    return {
        "chartType": "test",
        "datasets": [dataset],
        "dimensions": fields(dimensions),
        "metrics": metric_fields(metrics),
        "filters": fields(filters),
        "grouping": fields(grouping),
        "time": fields(time),
        "drillPaths": [list(path) for path in drill_paths],
    }


def topic_profile(topic, *, metrics=()):
    return profile(
        "test-dataset",
        dimensions=(topic,),
        metrics=metrics,
        grouping=(topic,),
    )


def test_extracts_released_flat_children_and_ignores_internal_cards_and_tabs():
    receipt = structure_receipt([
        widget("flat", "经营分析", "flat", editable=False, order=0),
        widget(
            "visible", "经营总览：成交订单量", "metric",
            parent_id="flat", editable=False,
        ),
        widget("internal-text", "", "text", parent_id="flat", editable=False),
        widget("hidden", "框架占位", "placeholder", parent_id="flat", editable=False),
        widget("framework", "内部框架", "framework", parent_id="flat", editable=False),
        widget("tabs", "分析标签", "tab", editable=False, order=1),
        widget(
            "inactive-tab-card", "隐藏页折线图", "chart",
            parent_id="tabs", editable=False,
        ),
        widget("orphan", "实现节点", "chart", parent_id="missing", editable=False),
        widget("root", "明细下钻：订单明细", "table", order=2),
    ])

    snapshot = extract_dashboard_structure(receipt.content)

    assert [card.widget_id for card in snapshot.cards] == ["visible", "root"]
    assert snapshot.flat_container_ids == ("flat",)
    assert snapshot.tab_container_ids == ("tabs",)
    assert snapshot.preserved_tab_member_ids == ("inactive-tab-card",)
    assert snapshot.original_root_ids == ("flat", "tabs", "root")
    assert snapshot.reserved_titles == ("分析标签", "隐藏页折线图")
    assert snapshot.resource_revision == 8


@pytest.mark.parametrize("mutation, message", [
    (lambda payload: payload["data"].update(hasMore=True), "complete structure"),
    (lambda payload: payload["data"]["widgets"].append(
        dict(payload["data"]["widgets"][0])), "duplicate widget IDs"),
])
def test_rejects_incomplete_or_duplicate_structure(mutation, message):
    receipt = structure_receipt([widget("metric", "成交订单量", "metric")])
    payload = json.loads(receipt.content)
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        extract_dashboard_structure(json.dumps(payload))


@pytest.mark.asyncio
async def test_hybrid_planner_sends_only_ambiguous_cards_to_ai_once():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget(
            "m1", "经营总览：近30天成交订单量", "metric", order=0,
            semantic_profile=topic_profile("orders", metrics=("order_count",)),
        ),
        widget(
            "t1", "趋势判断：每日成交订单量走势", "chart", order=1,
            semantic_profile=topic_profile("orders", metrics=("order_count",)),
        ),
        widget("mystery", "客户健康度", "chart", order=2),
    ]).content)
    calls = []

    async def decide(payload):
        calls.append(payload)
        return {"decisions": [{
            "widgetId": "mystery", "topicKey": "customer",
            "topicTitle": "客户分析", "stage": "breakdown",
        }]}

    async def decide_titles(_payload):
        return {"groupTitles": [{
            "groupId": "group-0", "title": "经营总览：成交订单量",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", ai_decider=decide, title_decider=decide_titles
    )

    assert len(calls) == 1
    assert calls[0]["cards"] == [{
        "widgetId": "mystery", "title": "客户健康度", "chartType": "chart",
        "semanticProfile": {},
    }]
    assert len(calls[0]["anchors"]) == 1
    assert calls[0]["anchors"][0]["topicKey"].startswith("profile:")
    assert calls[0]["anchors"][0]["stages"] == ["overview", "trend"]
    assert calls[0]["anchors"][0]["cardTypes"] == ["metric", "chart"]
    assert calls[0]["anchors"][0]["semanticProfiles"] == [
        topic_profile("orders", metrics=("order_count",)),
        topic_profile("orders", metrics=("order_count",)),
    ]
    assert result["layoutArguments"]["preset"]["orderedWidgetIds"] == ["m1", "t1", "mystery"]
    assert result["diagnostics"]["aiCallCount"] == 1
    assert result["diagnostics"]["deterministicCardCount"] == 2


@pytest.mark.asyncio
async def test_compiler_builds_host_layout_arguments_and_flat_regroup_list():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("flat-a", "旧分组A", "flat", order=0),
        widget("m1", "经营总览：成交订单量", "metric", parent_id="flat-a", order=0,
               semantic_profile=topic_profile("overview")),
        widget("m2", "经营总览：成交金额", "metric", parent_id="flat-a", order=1,
               semantic_profile=topic_profile("overview")),
        widget("t1", "趋势判断：成交订单量走势", "chart", parent_id="flat-a", order=2,
               semantic_profile=topic_profile("trend")),
        widget("t2", "趋势判断：成交用户数走势", "chart", parent_id="flat-a", order=3,
               semantic_profile=topic_profile("trend")),
        widget("flat-b", "旧分组B", "flat", order=1),
        widget("d1", "明细下钻：订单明细", "table", parent_id="flat-b", order=0,
               semantic_profile=topic_profile("detail")),
        widget("d2", "明细下钻：用户明细", "table", parent_id="flat-b", order=1,
               semantic_profile=topic_profile("detail")),
    ], revision=11).content)

    result = await compile_semantic_layout(snapshot, "B")

    assert result["layoutArguments"] == {
        "preset": {
            "mode": "reorder", "sizing": "content",
            "orderedWidgetIds": ["m1", "m2", "t1", "t2", "d1", "d2"],
            "groups": [
                {"title": "经营总览：成交表现", "widgetIds": ["m1", "m2"]},
                {"title": "趋势判断：成交趋势", "widgetIds": ["t1", "t2"]},
                {"title": "明细下钻：订单与用户明细", "widgetIds": ["d1", "d2"]},
            ],
            "groupingConfirmed": True,
            "regroup": {"containerWidgetIds": ["flat-a", "flat-b"], "confirmed": True},
        },
        "expectedResourceRevision": 11,
    }
    assert result["diagnostics"]["aiCallCount"] == 0


@pytest.mark.asyncio
async def test_compiler_requests_all_group_titles_in_one_batch_after_compilation():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "经营总览：成交订单量", "metric", order=0,
               semantic_profile=topic_profile("overview")),
        widget("m2", "经营总览：成交金额", "metric", order=1,
               semantic_profile=topic_profile("overview")),
        widget("t1", "趋势判断：成交订单量走势", "chart", order=2,
               semantic_profile=topic_profile("trend")),
        widget("t2", "趋势判断：成交金额走势", "chart", order=3,
               semantic_profile=topic_profile("trend")),
    ]).content)
    calls = []

    async def decide_titles(payload):
        calls.append(payload)
        return {"groupTitles": [
            {"groupId": "group-0", "title": "经营总览：成交表现"},
            {"groupId": "group-1", "title": "趋势判断：成交趋势"},
        ]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=decide_titles
    )

    assert calls == [{
        "phase": "group_titles",
        "strategy": "C",
        "reservedTitles": [],
        "groups": [{
            "groupId": "group-0",
            "stages": ["overview"],
            "stageTitles": ["经营总览"],
            "memberContents": ["成交订单量", "成交金额"],
            "memberCards": [{
                "content": "成交订单量", "chartType": "metric",
                "semanticProfile": topic_profile("overview"),
            }, {
                "content": "成交金额", "chartType": "metric",
                "semanticProfile": topic_profile("overview"),
            }],
            "semanticFrame": {
                "businessObjectCandidates": ["成交"],
                "analysisIntentCandidates": ["表现"],
                "memberCoverage": [
                    {"memberIndex": 0, "object": "成交", "intent": "表现"},
                    {"memberIndex": 1, "object": "成交", "intent": "表现"},
                ],
            },
            "fallbackTitle": "经营总览：成交表现",
        }, {
            "groupId": "group-1",
            "stages": ["trend"],
            "stageTitles": ["趋势判断"],
            "memberContents": ["成交订单量走势", "成交金额走势"],
            "memberCards": [{
                "content": "成交订单量走势", "chartType": "chart",
                "semanticProfile": topic_profile("trend"),
            }, {
                "content": "成交金额走势", "chartType": "chart",
                "semanticProfile": topic_profile("trend"),
            }],
            "semanticFrame": {
                "businessObjectCandidates": ["成交"],
                "analysisIntentCandidates": ["趋势"],
                "memberCoverage": [
                    {"memberIndex": 0, "object": "成交", "intent": "趋势"},
                    {"memberIndex": 1, "object": "成交", "intent": "趋势"},
                ],
            },
            "fallbackTitle": "趋势判断：成交趋势",
        }],
    }]
    assert result["layoutArguments"]["preset"]["groups"] == [
        {"title": "经营总览：成交表现", "widgetIds": ["m1", "m2"]},
        {"title": "趋势判断：成交趋势", "widgetIds": ["t1", "t2"]},
    ]
    assert result["diagnostics"]["aiCallCount"] == 0
    assert result["diagnostics"]["titleAiCallCount"] == 1
    assert result["diagnostics"]["titleAiFallback"] is False
    assert result["diagnostics"]["titleAiPlanningMs"] >= 0


@pytest.mark.asyncio
async def test_empty_titles_use_visible_card_type_as_title_grounding_material():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "", "metric", order=0, semantic_profile=topic_profile("summary")),
        widget("m2", "", "metric", order=1, semantic_profile=topic_profile("summary")),
    ]).content)
    calls = []

    async def decide_titles(payload):
        calls.append(payload)
        return {"groupTitles": [{
            "groupId": "group-0", "title": "经营总览：指标表现",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=decide_titles
    )

    assert calls[0]["groups"][0]["memberContents"] == ["指标", "指标"]
    assert result["layoutArguments"]["preset"]["groups"] == [{
        "title": "经营总览：指标表现", "widgetIds": ["m1", "m2"],
    }]
    assert result["diagnostics"]["titleAiCallCount"] == 1
    assert result["diagnostics"]["titleAiFallback"] is False


@pytest.mark.asyncio
async def test_non_empty_titles_reject_type_suffix_as_ungrounded():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "结构拆解：同标题", "metric", order=0,
               semantic_profile=topic_profile("same")),
        widget("m2", "结构拆解：同标题", "metric", order=1,
               semantic_profile=topic_profile("same")),
    ]).content)
    calls = 0

    async def append_type_suffix(_payload):
        nonlocal calls
        calls += 1
        return {"groupTitles": [{
            "groupId": "group-0", "title": "结构拆解：同标题指标",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=append_type_suffix
    )

    assert calls == 1
    assert result["layoutArguments"]["preset"]["groups"] == [{
        "title": "结构拆解：同标题表现", "widgetIds": ["m1", "m2"],
    }]
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
async def test_group_title_cannot_copy_only_one_member_when_group_has_distinct_content():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("city-count", "结构拆解：各城市群提交订单量", "chart", order=0,
               semantic_profile=topic_profile("city")),
        widget("city-amount", "结构拆解：各城市群成交订单金额", "chart", order=1,
               semantic_profile=topic_profile("city")),
    ]).content)

    async def copy_first_member(_payload):
        return {"groupTitles": [{
            "groupId": "group-0", "title": "结构拆解：各城市群提交订单量",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=copy_first_member
    )
    title = result["layoutArguments"]["preset"]["groups"][0]["title"]

    assert title != "结构拆解：各城市群提交订单量"
    assert "各城市群" in title
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
async def test_group_title_cannot_hide_distinct_metrics_behind_only_common_dimension():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("city-count", "结构拆解：各城市群提交订单量", "chart", order=0,
               semantic_profile=topic_profile("city")),
        widget("city-amount", "结构拆解：各城市群成交订单金额", "chart", order=1,
               semantic_profile=topic_profile("city")),
    ]).content)

    async def return_only_common_dimension(_payload):
        return {"groupTitles": [{
            "groupId": "group-0", "title": "结构拆解：各城市群",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=return_only_common_dimension
    )
    title = result["layoutArguments"]["preset"]["groups"][0]["title"]

    assert title != "结构拆解：各城市群"
    assert "各城市群" in title
    assert title == "结构拆解：各城市群成交表现"
    assert not any(
        placeholder in title
        for placeholder in ("多指标", "关联分析", "综合分析", "数据分析")
    )
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "members, expected",
    [
        (
            [
                ("city-count", "结构拆解：城市成交订单量TOP排行", "leaderboard"),
                ("city-amount", "结构拆解：城市成交订单金额TOP排行", "leaderboard"),
                ("city-rate", "结构拆解：城市成交率排行", "leaderboard"),
            ],
            "结构拆解：城市成交综合排行",
        ),
        (
            [
                ("method-amount", "结构拆解：各交易方式成交订单金额占比", "pie"),
                ("method-count", "结构拆解：各交易方式成交订单量占比", "pie"),
            ],
            "结构拆解：各交易方式成交结构",
        ),
        (
            [
                ("model-count", "结构拆解：质检型号成交订单量", "chart"),
                ("model-amount", "结构拆解：质检型号成交订单金额", "chart"),
            ],
            "结构拆解：质检型号成交表现",
        ),
    ],
)
async def test_group_title_summarizes_every_member_as_business_object_and_intent(
    members, expected
):
    snapshot = extract_dashboard_structure(structure_receipt([
        widget(
            widget_id,
            title,
            card_type,
            order=index,
            semantic_profile=topic_profile("shared-topic", metrics=(widget_id,)),
        )
        for index, (widget_id, title, card_type) in enumerate(members)
    ]).content)

    result = await compile_semantic_layout(snapshot, "C")

    assert result["layoutArguments"]["preset"]["groups"] == [{
        "title": expected,
        "widgetIds": [member[0] for member in members],
    }]


@pytest.mark.asyncio
async def test_title_diagnostics_explain_forbidden_model_output_and_fallback():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("city-count", "结构拆解：城市成交订单量TOP排行", "leaderboard", order=0,
               semantic_profile=topic_profile("city", metrics=("count",))),
        widget("city-amount", "结构拆解：城市成交订单金额TOP排行", "leaderboard", order=1,
               semantic_profile=topic_profile("city", metrics=("amount",))),
    ]).content)

    async def forbidden_title(_payload):
        return {"groupTitles": [{
            "groupId": "group-0", "title": "结构拆解：城市成交多指标",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=forbidden_title
    )

    assert result["diagnostics"]["titleGroups"] == [{
        "groupId": "group-0",
        "memberIds": ["city-count", "city-amount"],
        "businessObjectCandidates": ["城市成交"],
        "analysisIntentCandidates": ["综合排行"],
        "acceptedTitleSource": "deterministic_fallback",
        "validationRejectionReason": "forbidden_term",
    }]


@pytest.mark.asyncio
async def test_mixed_title_batch_grounds_empty_titles_by_type_and_others_by_content():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "", "metric", order=0, semantic_profile=topic_profile("summary")),
        widget("m2", "", "metric", order=1, semantic_profile=topic_profile("summary")),
        widget("t1", "趋势判断：成交订单量走势", "chart", order=2,
               semantic_profile=topic_profile("trend")),
        widget("t2", "趋势判断：成交金额走势", "chart", order=3,
               semantic_profile=topic_profile("trend")),
    ]).content)
    calls = []

    async def decide_titles(payload):
        calls.append(payload)
        return {"groupTitles": [
            {"groupId": "group-0", "title": "经营总览：指标表现"},
            {"groupId": "group-1", "title": "趋势判断：成交趋势"},
        ]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=decide_titles
    )

    assert len(calls) == 1
    assert [group["memberContents"] for group in calls[0]["groups"]] == [
        ["指标", "指标"],
        ["成交订单量走势", "成交金额走势"],
    ]
    assert result["layoutArguments"]["preset"]["groups"] == [
        {"title": "经营总览：指标表现", "widgetIds": ["m1", "m2"]},
        {"title": "趋势判断：成交趋势", "widgetIds": ["t1", "t2"]},
    ]
    assert result["diagnostics"]["titleAiCallCount"] == 1
    assert result["diagnostics"]["titleAiFallback"] is False


@pytest.mark.asyncio
async def test_existing_ai_decider_is_reused_for_the_title_phase():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "经营总览：成交订单量", "metric", order=0,
               semantic_profile=topic_profile("overview")),
        widget("m2", "经营总览：成交金额", "metric", order=1,
               semantic_profile=topic_profile("overview")),
    ]).content)
    phases = []

    async def decide(payload):
        phases.append(payload["phase"])
        return {"groupTitles": [{
            "groupId": "group-0", "title": "经营总览：成交表现",
        }]}

    result = await compile_semantic_layout(snapshot, "C", ai_decider=decide)

    assert phases == ["group_titles"]
    assert result["layoutArguments"]["preset"]["groups"][0]["title"] == (
        "经营总览：成交表现"
    )
    assert result["diagnostics"]["aiCallCount"] == 0
    assert result["diagnostics"]["titleAiCallCount"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "ungrounded",
        "too_long",
        "spaced_too_long",
        "malformed",
        "timeout",
        "error",
    ],
)
async def test_title_failure_falls_back_for_the_whole_batch_without_retry(failure):
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "经营总览：成交订单量", "metric", order=0,
               semantic_profile=topic_profile("overview")),
        widget("m2", "经营总览：成交金额", "metric", order=1,
               semantic_profile=topic_profile("overview")),
        widget("t1", "趋势判断：成交订单量走势", "chart", order=2,
               semantic_profile=topic_profile("trend")),
        widget("t2", "趋势判断：成交金额走势", "chart", order=3,
               semantic_profile=topic_profile("trend")),
    ]).content)
    calls = 0

    async def decide_titles(_payload):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise TimeoutError("title generation timed out")
        if failure == "error":
            raise RuntimeError("title generation unavailable")
        invalid_title = {
            "ungrounded": "经营总览：利润增长",
            "too_long": "经营总览：成交订单量成交金额分析汇总",
            "spaced_too_long": "经营总览：成  交  订  单  量",
            "malformed": "经营总览-成交订单",
        }[failure]
        return {"groupTitles": [
            {"groupId": "group-0", "title": invalid_title},
            {"groupId": "group-1", "title": "趋势判断：成交金额走势"},
        ]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=decide_titles
    )

    assert calls == 1
    assert result["layoutArguments"]["preset"]["groups"] == [
        {"title": "经营总览：成交表现", "widgetIds": ["m1", "m2"]},
        {"title": "趋势判断：成交趋势", "widgetIds": ["t1", "t2"]},
    ]
    assert result["diagnostics"]["titleAiCallCount"] == 1
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.parametrize(
    "card_type, expected",
    [
        ("metric", "指标"),
        ("table", "明细"),
        ("pivot", "透视表"),
        ("scatter", "散点图"),
        ("funnel", "漏斗图"),
        ("radar", "雷达图"),
        ("calendar", "日历图"),
        ("gantt", "甘特图"),
    ],
)
def test_empty_title_uses_specific_visible_card_type(card_type, expected):
    card = semantic_grouping.DashboardCard("card", "", card_type, 0)

    assert semantic_grouping._visible_card_content(card) == expected


def test_visible_card_type_titles_cover_host_widget_type_codes():
    expected = {
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
    }

    assert {
        card_type: semantic_grouping.VISIBLE_CARD_TYPE_TITLES[card_type]
        for card_type in expected
    } == expected


def test_compile_order_never_merges_distinct_topics_from_identical_titles():
    card_specs = [
        ("m1", "metric", "metric-topic"),
        ("m2", "metric", "metric-topic"),
        ("d1", "table", "detail-topic"),
        ("d2", "table", "detail-topic"),
    ]
    cards = tuple(
        semantic_grouping.DashboardCard(widget_id, "同标题", card_type, index)
        for index, (widget_id, card_type, _topic) in enumerate(card_specs)
    )
    decisions = {
        widget_id: semantic_grouping.SemanticDecision(
            widget_id, topic, "经营分析", "breakdown"
        )
        for widget_id, _card_type, topic in card_specs
    }

    ordered, groups = semantic_grouping._compile_order(cards, decisions, "C")

    assert ordered == ["m1", "m2", "d1", "d2"]
    assert [group["widgetIds"] for group in groups] == [
        ["m1", "m2"], ["d1", "d2"],
    ]


def test_reserved_title_exhaustion_reuses_clear_grounded_title_without_failure():
    cards = (
        semantic_grouping.DashboardCard("m1", "A", "metric", 0),
        semantic_grouping.DashboardCard("m2", "A", "metric", 1),
    )
    decisions = {
        card.widget_id: semantic_grouping.SemanticDecision(
            card.widget_id, "topic-a", "经营分析", "breakdown"
        )
        for card in cards
    }
    items = list(decisions.values())
    card_by_id = {card.widget_id: card for card in cards}
    candidates = semantic_grouping._grounded_title_candidates(items, card_by_id)

    ordered, groups = semantic_grouping._compile_order(
        cards, decisions, "C", tuple(candidates)
    )

    assert ordered == ["m1", "m2"]
    assert groups == [{"title": candidates[0], "widgetIds": ["m1", "m2"]}]
    assert "指标" not in groups[0]["title"]
    assert "分组" not in groups[0]["title"]
    assert len(groups[0]["title"]) <= 16


def test_high_cardinality_topics_are_not_merged_by_title_or_split_by_capacity():
    card_types = ("metric", "table", "scatter")
    cards = tuple(
        semantic_grouping.DashboardCard(
            f"card-{index}", "A", card_types[index % len(card_types)], index
        )
        for index in range(200)
    )
    decisions = {
        card.widget_id: semantic_grouping.SemanticDecision(
            card.widget_id, f"topic-{index // 2}", "经营分析", "breakdown"
        )
        for index, card in enumerate(cards)
    }

    ordered, groups = semantic_grouping._compile_order(cards, decisions, "C")

    expected_ids = [card.widget_id for card in cards]
    assert ordered == expected_ids
    assert [group["widgetIds"] for group in groups] == [
        [f"card-{index}", f"card-{index + 1}"]
        for index in range(0, 200, 2)
    ]
    assert [
        widget_id for group in groups for widget_id in group["widgetIds"]
    ] == expected_ids


@pytest.mark.asyncio
async def test_group_titles_are_unique_from_preserved_tab_titles():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "经营总览：成交订单量", "metric", order=0,
               semantic_profile=topic_profile("overview")),
        widget("m2", "经营总览：成交金额", "metric", order=1,
               semantic_profile=topic_profile("overview")),
        widget("tabs", "经营总览：成交订单量", "tabLayout", order=2),
        widget("tab-child", "标签页内容", "chart", parent_id="tabs"),
    ]).content)
    calls = 0

    async def collide_with_tab(payload):
        nonlocal calls
        calls += 1
        assert payload["reservedTitles"] == [
            "经营总览：成交订单量",
            "标签页内容",
        ]
        return {"groupTitles": [{
            "groupId": "group-0", "title": "经营总览：成交订单量",
        }]}

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=collide_with_tab
    )

    assert snapshot.reserved_titles == ("经营总览：成交订单量", "标签页内容")
    assert calls == 1
    assert result["layoutArguments"]["preset"]["groups"] == [{
        "title": "经营总览：成交表现", "widgetIds": ["m1", "m2"],
    }]
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
async def test_duplicate_generated_titles_fall_back_for_the_whole_batch():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("r1", "区域成交订单量", "metric", order=0,
               semantic_profile=topic_profile("region")),
        widget("r2", "城市成交金额", "metric", order=1,
               semantic_profile=topic_profile("region")),
        widget("s1", "渠道成交订单量", "metric", order=2,
               semantic_profile=topic_profile("source")),
        widget("s2", "来源成交金额", "metric", order=3,
               semantic_profile=topic_profile("source")),
    ]).content)

    async def duplicate_titles(_payload):
        return {"groupTitles": [
            {"groupId": "group-0", "title": "经营总览：成交订单量"},
            {"groupId": "group-1", "title": "经营总览：成交订单量"},
        ]}

    result = await compile_semantic_layout(
        snapshot, "B", title_decider=duplicate_titles
    )

    assert result["layoutArguments"]["preset"]["groups"] == [
        {"title": "经营总览：区域成交与城市成交表现", "widgetIds": ["r1", "r2"]},
        {"title": "经营总览：渠道成交与来源成交表现", "widgetIds": ["s1", "s2"]},
    ]
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
async def test_fallback_titles_disambiguate_duplicate_generic_topics():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("a1", "客户：健康度", "chart", order=0),
        widget("a2", "客户活跃度", "chart", order=1),
        widget("b1", "服务体验", "chart", order=2),
        widget("b2", "服务满意度", "chart", order=3),
    ]).content)

    async def decide_ambiguities(_payload):
        return {"decisions": [
            {"widgetId": "a1", "topicKey": "customer-a",
             "topicTitle": "经营分析", "stage": "breakdown"},
            {"widgetId": "a2", "topicKey": "customer-a",
             "topicTitle": "经营分析", "stage": "breakdown"},
            {"widgetId": "b1", "topicKey": "service-b",
             "topicTitle": "经营分析", "stage": "breakdown"},
            {"widgetId": "b2", "topicKey": "service-b",
             "topicTitle": "经营分析", "stage": "breakdown"},
        ]}

    async def fail_titles(_payload):
        raise RuntimeError("title generation unavailable")

    result = await compile_semantic_layout(
        snapshot,
        "C",
        ai_decider=decide_ambiguities,
        title_decider=fail_titles,
    )

    groups = result["layoutArguments"]["preset"]["groups"]
    assert groups == [
        {"title": "结构拆解：客户表现", "widgetIds": ["a1", "a2"]},
        {"title": "结构拆解：服务表现", "widgetIds": ["b1", "b2"]},
    ]
    assert len({group["title"] for group in groups}) == len(groups)
    assert all("：" in group["title"] and len(group["title"]) <= 16 for group in groups)


@pytest.mark.asyncio
async def test_fallback_never_drops_confirmed_groups_when_titles_conflict():
    card_specs = [
        ("a1", "同名指标", "topic-a"),
        ("a2", "同名指标", "topic-a"),
        ("b1", "另一指标", "topic-b"),
        ("b2", "另一指标", "topic-b"),
        ("c1", "同名指标", "topic-c"),
        ("c2", "另一指标", "topic-c"),
        ("d1", "同名指标", "topic-d"),
        ("d2", "同名指标", "topic-d"),
        ("e1", "", "topic-e"),
        ("e2", "", "topic-e"),
    ]
    snapshot = extract_dashboard_structure(structure_receipt([
        widget(widget_id, title, "chart", order=index)
        for index, (widget_id, title, _topic) in enumerate(card_specs)
    ]).content)
    ambiguity_calls = 0
    title_calls = 0

    async def decide_ambiguities(_payload):
        nonlocal ambiguity_calls
        ambiguity_calls += 1
        return {"decisions": [
            {
                "widgetId": widget_id,
                "topicKey": topic,
                "topicTitle": "经营分析",
                "stage": "breakdown",
            }
            for widget_id, _title, topic in card_specs
        ]}

    async def fail_titles(_payload):
        nonlocal title_calls
        title_calls += 1
        raise RuntimeError("title generation unavailable")

    result = await compile_semantic_layout(
        snapshot,
        "C",
        ai_decider=decide_ambiguities,
        title_decider=fail_titles,
    )

    groups = result["layoutArguments"]["preset"]["groups"]
    assert [group["widgetIds"] for group in groups] == [
        ["a1", "a2"],
        ["b1", "b2"],
        ["c1", "c2"],
        ["d1", "d2"],
        ["e1", "e2"],
    ]
    assert result["layoutArguments"]["preset"]["orderedWidgetIds"] == [
        "a1", "a2", "b1", "b2", "c1", "c2", "d1", "d2", "e1", "e2",
    ]
    assert sorted(
        widget_id for group in groups for widget_id in group["widgetIds"]
    ) == sorted(widget_id for widget_id, _title, _topic in card_specs)
    assert len({group["title"] for group in groups}) == len(groups)
    assert all(not group["title"].endswith(tuple("0123456789")) for group in groups)
    assert all("分组" not in group["title"] for group in groups)
    assert ambiguity_calls == 1
    assert title_calls == 1
    assert result["diagnostics"]["titleAiCallCount"] == 1
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, required_key",
    [
        ({"strategy": "C", "cards": [], "anchors": []}, "decisions"),
        ({"phase": "group_titles", "strategy": "C", "groups": []}, "groupTitles"),
    ],
)
async def test_query_uses_a_strict_schema_for_each_phase(
    monkeypatch, arguments, required_key
):
    captured_schemas = []

    async def no_messages():
        if False:
            yield None

    def capture_query(*, prompt, options):
        captured_schemas.append(options.output_format["schema"])
        return no_messages()

    monkeypatch.setattr(semantic_grouping, "query", capture_query)
    options = semantic_grouping.ClaudeAgentOptions(
        output_format={"type": "json_schema", "schema": {}}
    )

    with pytest.raises(RuntimeError, match="did not return a result"):
        await run_semantic_grouping_query(arguments, options=options)

    schema = captured_schemas[0]
    assert schema["required"] == [required_key]
    assert schema["properties"][required_key]["minItems"] == 1
    assert not Draft202012Validator(schema).is_valid({})
    assert not Draft202012Validator(schema).is_valid({required_key: []})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    [
        "top_level_extra",
        "decision_extra",
        "missing_required",
        "wrong_type",
        "topic_key_too_long",
        "topic_title_too_long",
    ],
)
async def test_ambiguity_runtime_rejects_schema_invalid_output_without_retry(
    invalid_case,
):
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("x1", "客户健康度", "chart", order=0),
        widget("x2", "服务体验", "chart", order=1),
    ]).content)
    calls = 0

    async def invalid(_payload):
        nonlocal calls
        calls += 1
        output = {
            "decisions": [
                {
                    "widgetId": "x1",
                    "topicKey": "customer",
                    "topicTitle": "客户分析",
                    "stage": "breakdown",
                },
                {
                    "widgetId": "x2",
                    "topicKey": "customer",
                    "topicTitle": "客户分析",
                    "stage": "breakdown",
                },
            ]
        }
        if invalid_case == "top_level_extra":
            output["extra"] = True
        elif invalid_case == "decision_extra":
            output["decisions"][0]["extra"] = True
        elif invalid_case == "missing_required":
            del output["decisions"][0]["topicTitle"]
        elif invalid_case == "wrong_type":
            output["decisions"][0]["topicKey"] = 7
        elif invalid_case == "topic_key_too_long":
            output["decisions"][0]["topicKey"] = "k" * 81
        else:
            output["decisions"][0]["topicTitle"] = "主" * 51
        return output

    result = await compile_semantic_layout(snapshot, "C", ai_decider=invalid)

    assert calls == 1
    assert result["layoutArguments"]["preset"]["orderedWidgetIds"] == ["x1", "x2"]
    assert "groups" not in result["layoutArguments"]["preset"]
    assert result["diagnostics"]["aiFallback"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_case",
    [
        "top_level_extra",
        "title_extra",
        "missing_required",
        "wrong_type",
        "group_id_too_long",
    ],
)
async def test_title_runtime_rejects_schema_invalid_output_without_retry(invalid_case):
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "经营总览：成交订单量", "metric", order=0,
               semantic_profile=topic_profile("overview")),
        widget("m2", "经营总览：成交金额", "metric", order=1,
               semantic_profile=topic_profile("overview")),
    ]).content)
    calls = 0

    async def invalid(_payload):
        nonlocal calls
        calls += 1
        output = {
            "groupTitles": [{
                "groupId": "group-0",
                "title": "经营总览：成交订单",
            }]
        }
        if invalid_case == "top_level_extra":
            output["extra"] = True
        elif invalid_case == "title_extra":
            output["groupTitles"][0]["extra"] = True
        elif invalid_case == "missing_required":
            del output["groupTitles"][0]["title"]
        elif invalid_case == "wrong_type":
            output["groupTitles"][0]["title"] = 7
        else:
            output["groupTitles"][0]["groupId"] = "g" * 101
        return output

    result = await compile_semantic_layout(
        snapshot, "C", title_decider=invalid
    )

    assert calls == 1
    assert result["layoutArguments"]["preset"]["groups"] == [{
        "title": "经营总览：成交表现", "widgetIds": ["m1", "m2"],
    }]
    assert result["diagnostics"]["titleAiFallback"] is True


@pytest.mark.asyncio
async def test_strategy_c_does_not_split_a_related_group_by_card_count():
    titles = [
        ("经营总览", "metric", 5),
        ("效率监控", "chart", 2),
        ("趋势判断", "chart", 2),
        ("结构拆解", "chart", 4),
        ("风险归因", "leaderboard", 3),
        ("明细下钻", "table", 2),
    ]
    cards = []
    index = 0
    for section, card_type, count in titles:
        for offset in range(count):
            widget_id = f"w{index}"
            cards.append(widget(
                widget_id,
                f"{section}：卡片{offset + 1}",
                card_type,
                order=index,
                semantic_profile=topic_profile("shared-topic"),
            ))
            index += 1

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )
    preset = result["layoutArguments"]["preset"]

    assert len(preset["groups"]) == 1
    assert sorted(preset["groups"][0]["widgetIds"]) == sorted(
        f"w{card_index}" for card_index in range(len(cards))
    )


@pytest.mark.asyncio
async def test_same_titles_with_unrelated_semantic_profiles_are_grouped_separately():
    cards = [
        widget(
            "store-rank", "相同标题", "leaderboard", order=0,
            semantic_profile=profile(
                "orders", dimensions=("entity_type",), metrics=("order_count",),
                grouping=("entity_type",),
            ),
        ),
        widget(
            "store-chart", "相同标题", "chart", order=1,
            semantic_profile=profile(
                "orders", dimensions=("entity_type",), metrics=("unit_price",),
                grouping=("entity_type",),
            ),
        ),
        widget(
            "warehouse-rank", "相同标题", "leaderboard", order=2,
            semantic_profile=profile(
                "inventory", dimensions=("entity_type",), metrics=("stock_count",),
                grouping=("entity_type",),
            ),
        ),
        widget(
            "warehouse-chart", "相同标题", "chart", order=3,
            semantic_profile=profile(
                "inventory", dimensions=("entity_type",), metrics=("turnover_days",),
                grouping=("entity_type",),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert [
        group["widgetIds"]
        for group in result["layoutArguments"]["preset"]["groups"]
    ] == [
        ["store-rank", "store-chart"],
        ["warehouse-rank", "warehouse-chart"],
    ]


@pytest.mark.asyncio
async def test_different_titles_with_related_profiles_are_grouped_together():
    cards = [
        widget(
            "trend", "每日经营走势", "chart", order=0,
            semantic_profile=profile(
                "orders", dimensions=("shop_tier",), metrics=("order_count",),
                filters=("status",), grouping=("shop_tier",), time=("order_day",),
            ),
        ),
        widget(
            "ranking", "重点对象排名", "leaderboard", order=1,
            semantic_profile=profile(
                "orders", dimensions=("shop_tier",), metrics=("order_amount",),
                filters=("status",), grouping=("shop_tier",), time=("order_day",),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "trend", "ranking",
    ]


@pytest.mark.asyncio
async def test_unknown_business_dimension_groups_by_profile_without_a_whitelist():
    cards = [
        widget(
            "unknown-rank", "甲", "leaderboard", order=0,
            semantic_profile=profile(
                "fulfillment", dimensions=("fulfillment_wave",),
                metrics=("completed_count",), grouping=("fulfillment_wave",),
                drill_paths=(("fulfillment_wave", "station_code"),),
            ),
        ),
        widget(
            "unknown-chart", "乙", "chart", order=1,
            semantic_profile=profile(
                "fulfillment", dimensions=("fulfillment_wave",),
                metrics=("duration",), grouping=("fulfillment_wave",),
                drill_paths=(("fulfillment_wave", "station_code"),),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "unknown-rank", "unknown-chart",
    ]


@pytest.mark.asyncio
async def test_field_aliases_do_not_hide_shared_refs():
    cards = [
        widget(
            "left", "左侧标题", "leaderboard", order=0,
            semantic_profile=profile(
                "view:orders",
                dimensions=({"ref": "city_code", "name": "城市"},),
                metrics=({"ref": "amount", "name": "成交额", "agg": "sum"},),
                grouping=({"ref": "city_code", "name": "所在城市"},),
            ),
        ),
        widget(
            "right", "右侧标题", "chart", order=1,
            semantic_profile=profile(
                "view:orders",
                dimensions=({"ref": "city_code", "name": "区域城市"},),
                metrics=({"ref": "amount", "name": "金额", "agg": "sum"},),
                grouping=({"ref": "city_code", "name": "城市编码"},),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "left", "right",
    ]


@pytest.mark.asyncio
async def test_metric_relation_uses_ref_and_aggregation():
    cards = [
        widget(
            "sum", "相同标题", "leaderboard", order=0,
            semantic_profile=profile(
                "view:orders",
                metrics=({"ref": "amount", "name": "总金额", "agg": "sum"},),
            ),
        ),
        widget(
            "avg", "相同标题", "leaderboard", order=1,
            semantic_profile=profile(
                "view:orders",
                metrics=({"ref": "amount", "name": "平均金额", "agg": "avg"},),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert "groups" not in result["layoutArguments"]["preset"]
    assert result["diagnostics"]["ambiguousCardCount"] == 2


@pytest.mark.asyncio
async def test_same_stage_reorders_members_by_profile_relation_and_type_compatibility():
    cards = [
        widget(
            "count-chart", "结构拆解：订单分布", "chart", order=0,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_count",),
                grouping=("city_code",),
            ),
        ),
        widget(
            "amount-chart", "结构拆解：金额分布", "chart", order=1,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_amount",),
                grouping=("city_code",),
            ),
        ),
        widget(
            "count-rank", "结构拆解：订单排行", "leaderboard", order=2,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_count",),
                grouping=("city_code",),
            ),
        ),
        widget(
            "amount-rank", "结构拆解：金额排行", "leaderboard", order=3,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_amount",),
                grouping=("city_code",),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "count-chart", "count-rank", "amount-chart", "amount-rank",
    ]


@pytest.mark.asyncio
async def test_same_stage_does_not_force_leaderboards_to_a_fixed_side():
    cards = [
        widget(
            "count-rank", "结构拆解：订单排行", "leaderboard", order=0,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_count",),
                grouping=("city_code",),
            ),
        ),
        widget(
            "amount-chart", "结构拆解：金额分布", "chart", order=1,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_amount",),
                grouping=("city_code",),
            ),
        ),
        widget(
            "count-chart", "结构拆解：订单分布", "chart", order=2,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_count",),
                grouping=("city_code",),
            ),
        ),
        widget(
            "amount-rank", "结构拆解：金额排行", "leaderboard", order=3,
            semantic_profile=profile(
                "orders", dimensions=("city_code",), metrics=("order_amount",),
                grouping=("city_code",),
            ),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "count-rank", "count-chart", "amount-rank", "amount-chart",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["B", "C"])
async def test_flexible_member_order_preserves_large_stage_direction(strategy):
    cards = [
        widget(
            "breakdown-rank", "结构拆解：金额排行", "leaderboard", order=0,
            semantic_profile=topic_profile("orders", metrics=("order_amount",)),
        ),
        widget(
            "trend-chart", "趋势判断：订单走势", "chart", order=1,
            semantic_profile=topic_profile("orders", metrics=("order_count",)),
        ),
        widget(
            "breakdown-chart", "结构拆解：金额分布", "chart", order=2,
            semantic_profile=topic_profile("orders", metrics=("order_amount",)),
        ),
        widget(
            "trend-rank", "趋势判断：订单排行", "leaderboard", order=3,
            semantic_profile=topic_profile("orders", metrics=("order_count",)),
        ),
    ]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), strategy
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "trend-chart", "trend-rank", "breakdown-rank", "breakdown-chart",
    ]


@pytest.mark.asyncio
async def test_same_stage_relationship_hub_can_lead_a_rank_heavy_group():
    cards = [
        widget(
            f"rank-{index}", f"结构拆解：排行{index}", "leaderboard", order=index,
            semantic_profile=profile(
                "quality", dimensions=("category",),
                metrics=(f"metric-{index}",), grouping=("category",),
            ),
        )
        for index in range(3)
    ] + [widget(
        "chart", "结构拆解：件单价", "chart", order=3,
        semantic_profile=profile(
            "quality", dimensions=("category",), metrics=("unit-price",),
            grouping=("category",),
        ),
    )]

    result = await compile_semantic_layout(
        extract_dashboard_structure(structure_receipt(cards).content), "C"
    )

    assert result["layoutArguments"]["preset"]["groups"][0]["widgetIds"] == [
        "chart", "rank-0", "rank-1", "rank-2",
    ]


def test_small_coherent_topic_can_share_one_group_across_stages():
    cards = (
        semantic_grouping.DashboardCard("m1", "成交订单量", "metric", 0),
        semantic_grouping.DashboardCard("m2", "成交金额", "metric", 1),
        semantic_grouping.DashboardCard("t1", "成交订单量趋势", "chart", 2),
        semantic_grouping.DashboardCard("t2", "成交金额趋势", "chart", 3),
    )
    decisions = {
        "m1": semantic_grouping.SemanticDecision("m1", "business", "经营分析", "overview"),
        "m2": semantic_grouping.SemanticDecision("m2", "business", "经营分析", "overview"),
        "t1": semantic_grouping.SemanticDecision("t1", "business", "经营分析", "trend"),
        "t2": semantic_grouping.SemanticDecision("t2", "business", "经营分析", "trend"),
    }

    ordered, groups = semantic_grouping._compile_order(cards, decisions, "C")

    assert ordered == ["m1", "m2", "t1", "t2"]
    assert [group["widgetIds"] for group in groups] == [["m1", "m2", "t1", "t2"]]


@pytest.mark.asyncio
async def test_compiler_keeps_tab_root_in_effective_order_without_planning_members():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("m1", "经营总览：成交订单量", "metric", order=0),
        widget("t1", "趋势判断：成交订单量走势", "chart", order=1),
        widget("tabs", "分析标签", "tabLayout", order=2),
        widget("tab-child", "标签页内部卡片", "chart", parent_id="tabs"),
    ], revision=12).content)

    result = await compile_semantic_layout(snapshot, "C")

    assert result["layoutArguments"]["preset"]["orderedWidgetIds"] == [
        "m1", "t1", "tabs",
    ]


@pytest.mark.asyncio
async def test_invalid_ai_result_degrades_without_retry_and_preserves_uncertain_order():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("x1", "客户健康度", "chart", order=0),
        widget("x2", "服务体验", "chart", order=1),
    ]).content)
    calls = 0

    async def invalid(_payload):
        nonlocal calls
        calls += 1
        return {"decisions": [{"widgetId": "not-a-card", "topicKey": "x",
                                "topicTitle": "错误", "stage": "detail"}]}

    result = await compile_semantic_layout(snapshot, "C", ai_decider=invalid)

    assert calls == 1
    assert result["layoutArguments"]["preset"]["orderedWidgetIds"] == ["x1", "x2"]
    assert "groups" not in result["layoutArguments"]["preset"]
    assert result["diagnostics"]["aiFallback"] is True


@pytest.mark.asyncio
async def test_failed_ambiguity_planning_keeps_existing_flat_when_no_safe_group_exists():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("flat", "原平铺", "flat", order=0),
        widget("x1", "客户健康度", "chart", parent_id="flat", order=0),
        widget("x2", "服务体验", "chart", parent_id="flat", order=1),
        widget("root", "单独说明", "text", order=1),
    ]).content)

    async def invalid(_payload):
        raise RuntimeError("planner unavailable")

    result = await compile_semantic_layout(snapshot, "C", ai_decider=invalid)
    preset = result["layoutArguments"]["preset"]

    assert preset["orderedWidgetIds"] == ["flat", "root"]
    assert "groups" not in preset
    assert "regroup" not in preset
    assert "groupingConfirmed" not in preset
    assert result["diagnostics"]["aiFallback"] is True


@pytest.mark.asyncio
async def test_strategy_a_places_all_metrics_before_non_metrics():
    snapshot = extract_dashboard_structure(structure_receipt([
        widget("phenomenon", "现象判断：成交订单量异常", "chart", order=0),
        widget("metric", "成交订单量", "metric", order=1),
        widget("detail", "订单明细", "table", order=2),
    ]).content)

    result = await compile_semantic_layout(snapshot, "A")

    assert result["layoutArguments"]["preset"]["orderedWidgetIds"] == [
        "metric", "phenomenon", "detail"
    ]


def test_ai_prompt_contains_only_ambiguities_and_compact_anchors():
    prompt = grouping_prompt({
        "strategy": "C",
        "cards": [{"widgetId": "x", "title": "客户健康度", "chartType": "chart"}],
        "anchors": [{"topicKey": "business", "topicTitle": "经营分析",
                     "stages": ["overview"], "representativeTitles": ["成交订单量"]}],
    })

    assert "Return exactly one ambiguity decision set" in prompt
    assert "Do not reorder deterministic cards" in prompt
    assert "客户健康度" in prompt
    assert len(prompt) < 2_000
