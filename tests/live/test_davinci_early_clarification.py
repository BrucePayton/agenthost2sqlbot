"""Observable early-question judges and production-schema replay contracts."""

import pytest

from tests.live.davinci_feedback_fixture import early_options, fixture_payload
from tests.live.test_davinci_feedback_regressions import assess_early


def valid_question(case="early_metric"):
    """Build complete fixture-backed options without relying on punctuation."""
    options = early_options(case)
    return "请选择：\n" + "\n".join(
        f"{label}. {o['datasetName']} · {o['fieldName']}：{o['description']}"
        for label, o in zip("AB", options)
    )


@pytest.mark.parametrize(
    "case", ["early_metric", "early_same_dataset", "early_compound"]
)
def test_complete_options_accept(case):
    """Accept definitions and identities with one discovery and optional date."""
    calls = [
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "金额", "limit": 3, "maxMatchedFields": 3},
        }
    ]
    assert not assess_early(case, calls, valid_question(case) + "；需要统计哪段时间？")


def test_same_dataset_heading_preserves_combined_choices():
    """A shared dataset and metric heading still identifies both field choices."""
    from tests.live.test_davinci_feedback_regressions import clarification_evidence

    reply = (
        "渠道结算数据集里有两个结算额口径：\n"
        "A. booked_amount — 已入账金额，包含退货\n"
        "B. accepted_value — 验收入库估值，不代表实际支付\n"
        "请选择 A 或 B。"
    )
    evidence = clarification_evidence("early_same_dataset", reply)
    assert not evidence["violations"]
    assert not evidence["reviewConcerns"]
    assert evidence["optionMapping"]["A"]["fieldRef"].endswith("/booked_amount")
    cross_dataset = clarification_evidence("early_metric", reply)
    assert cross_dataset["violations"] or cross_dataset["reviewConcerns"]


def test_known_object_fixture_keeps_both_user_supplied_candidates():
    """An unnecessary list call must not introduce a contradiction in fake facts."""
    import json

    from app.agui.contracts import CONTRACT_PATH, load_contract_registry
    from tests.live.test_davinci_feedback_regressions import fixture_receipt

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    call = {"id": "objects", "name": "space.list", "arguments": {"scope": "all"}}
    reply = fixture_receipt("early_missing_object", call, registry)
    spaces = json.loads(json.loads(reply["content"])["data"]["summary"])["spaces"]
    assert [s["name"] for s in spaces] == ["经营空间", "经营空间"]
    assert [s["description"] for s in spaces] == [
        "经营中心的小组空间",
        "研发中心的小组空间",
    ]


@pytest.mark.parametrize("reply", ["正在查找？", "请选择 A 或 B？", "好的？"])
def test_progress_and_missing_options_rejected(reply):
    """Punctuation and generic labels cannot establish useful clarification."""
    assert assess_early("early_metric", [], reply)


def test_natural_choice_question_does_not_require_fixed_wording():
    """A complete A-or-B question is answerable without the word 请选择."""
    calls = [
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "结算额", "limit": 3, "maxMatchedFields": 3},
        }
    ]
    reply = valid_question().replace("请选择：", "找到两种口径：")
    reply += "\n请告诉我要用 A 还是 B 来做指标卡。"
    assert not assess_early("early_metric", calls, reply)


def test_independent_completion_can_use_unambiguous_object_references():
    """The two acknowledged objects can be called 分组和看板 in the final reply."""
    calls = [
        {"name": "workspace.dashboard_group.create", "arguments": {"name": "周报"}},
        {
            "name": "workspace.dashboard.create_and_open",
            "arguments": {"name": "经营看板", "groupName": "周报"},
        },
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "结算额", "limit": 3, "maxMatchedFields": 3},
        },
    ]
    reply = "分组和看板已完成。\n" + valid_question("early_independent_first")
    assert not assess_early("early_independent_first", calls, reply)
    pending = reply.replace("已完成", "尚未完成")
    assert "independent_completion_not_reported" in assess_early(
        "early_independent_first", calls, pending
    )


def test_preparation_and_contradictory_labels_rejected():
    """Excess search, resolver and numbered labels are observable failures."""
    search = {
        "name": "catalog.search_datasets",
        "arguments": {"query": "金额", "limit": 3, "maxMatchedFields": 3},
    }
    for extra in [
        search,
        {"name": "analytics.resolve_data_requirements", "arguments": {}},
        {"name": "space.member.list_by_spaces", "arguments": {}},
    ]:
        assert assess_early("early_metric", [search, extra], valid_question())
    assert assess_early("early_metric", [search], valid_question() + "请选择 1 或 2。")


@pytest.mark.parametrize(
    "case", ["early_metric", "early_same_dataset", "early_compound"]
)
def test_generalized_fixture_schemas(case):
    """Generated datasets and field refs satisfy the exported production contracts."""
    payload = fixture_payload(case, "catalog.search_datasets", {"query": "金额"})
    assert payload["candidates"]
    for option in early_options(case):
        schema = fixture_payload(
            case,
            "catalog.get_dataset_schema",
            {"datasetRef": option["datasetRef"], "fieldRefs": [option["fieldRef"]]},
        )
        assert [f["fieldRef"] for f in schema["fields"]] == [option["fieldRef"]]


def test_conflict_and_binding_judges():
    """Conflict forbids dependent work; chosen references cannot be replaced."""
    assert not assess_early(
        "early_conflict", [], "你选了 A，但描述对应 B，请确认选择哪个口径？"
    )
    assert assess_early("early_conflict", [], "请稍等？")
    option = early_options("early_selected")[0]
    good = [
        {
            "name": "catalog.get_dataset_schema",
            "arguments": {
                "datasetRef": option["datasetRef"],
                "fieldRefs": [option["fieldRef"]],
            },
        }
    ]
    assert not assess_early("early_selected", good, option["description"])
    assert assess_early("early_selected", good, "请选择 A 或 B？")


@pytest.mark.parametrize(
    "case,reply",
    [
        ("early_no_result", "本次检索未找到匹配结果，请补充业务范围。"),
        ("early_permission", "当前账号没有搜索权限，请联系授权负责人。"),
        ("early_upstream", "目录服务暂时不可用，无法确认有哪些数据入口。"),
    ],
)
def test_actual_search_limits(case, reply):
    """Errors establish a search limit and never establish dataset nonexistence."""
    calls = [
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "结算额", "limit": 3, "maxMatchedFields": 3},
        }
    ]
    assert not assess_early(case, calls, reply)
    assert assess_early(case, calls, reply + "数据集不存在，请提供 datasetRef。")
    assert assess_early(case, calls, reply + "请选择 A. 渠道结算 或 B. 仓储核算。")


def test_preparation_receipts_satisfy_production_contracts():
    """Wrongly ordered container writes get valid ACKs, so behavior is judged."""
    import json

    from app.agui.contracts import CONTRACT_PATH, load_contract_registry
    from tests.live.test_davinci_feedback_regressions import fixture_receipt

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    calls = [
        {
            "id": "one",
            "name": "workspace.dashboard_group.create",
            "arguments": {"name": "周报"},
        },
        {
            "id": "two",
            "name": "workspace.dashboard.create_and_open",
            "arguments": {"name": "经营看板", "groupName": "周报"},
        },
    ]
    for call in calls:
        receipt = fixture_receipt("early_compound", call, registry)
        assert json.loads(receipt["content"])["data"]["created"] is True
    assert assess_early("early_compound", calls, valid_question("early_compound"))


def test_style_only_receipt_and_judge():
    """Style capability reads remain independent of catalog metric ambiguity."""
    from app.agui.contracts import CONTRACT_PATH, load_contract_registry
    from tests.live.test_davinci_feedback_regressions import fixture_receipt

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    call = {
        "id": "style",
        "name": "dashboard.get_widget_edit_capabilities",
        "arguments": {"widgetIds": ["daily"], "sections": ["appearance"]},
    }
    fixture_receipt("early_style", call, registry)
    assert not assess_early("early_style", [call], "该组件可以编辑背景颜色。")
    assert assess_early(
        "early_style",
        [call, {"name": "catalog.search_datasets", "arguments": {}}],
        "背景颜色",
    )


def test_missing_search_definitions_are_completed_by_production_service():
    """Replay uses a real service export, with one bounded internal schema read."""
    from tests.live.davinci_feedback_fixture import CONTRACTS

    case = "early_definition_missing"
    probe = CONTRACTS["definitionSupplement"]
    assert probe["schemaReads"] == ["warehouseTopic:741"]
    assert probe["accessChecks"] == 2
    assert all(
        f["description"] is None
        for c in probe["upstreamSearch"]["candidates"]
        for f in c["matchedFields"]
    )
    search = fixture_payload(case, "catalog.search_datasets", {"query": "结算额"})
    assert search == probe["result"]
    assert {
        f["fieldRef"]: f["description"]
        for c in search["candidates"]
        for f in c["matchedFields"]
    } == {o["fieldRef"]: o["description"] for o in early_options(case)}
    calls = [
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "结算额", "limit": 3, "maxMatchedFields": 3},
        }
    ]
    assert not assess_early(case, calls, valid_question(case))
    options = early_options(case)
    supplement = {
        "name": "catalog.get_dataset_schema",
        "arguments": {
            "datasetRef": options[0]["datasetRef"],
            "fieldRefs": [o["fieldRef"] for o in options],
        },
    }
    evidence = fixture_payload(case, supplement["name"], supplement["arguments"])
    assert all(f["description"] for f in evidence["fields"])
    assert "unnecessary_definition_read" in assess_early(
        case, calls + [supplement], valid_question(case)
    )
    assert assess_early(case, calls + [supplement, supplement], valid_question(case))


def test_explicit_independent_part_first_exception():
    """Explicitly requested container creation precedes search and metric choice."""
    writes = [
        {"name": "workspace.dashboard_group.create", "arguments": {"name": "周报"}},
        {
            "name": "workspace.dashboard.create_and_open",
            "arguments": {"name": "经营看板", "groupName": "周报"},
        },
    ]
    search = {
        "name": "catalog.search_datasets",
        "arguments": {"query": "结算额", "limit": 3, "maxMatchedFields": 3},
    }
    reply = "已创建周报分组和经营看板。" + valid_question("early_independent_first")
    assert not assess_early("early_independent_first", writes + [search], reply)
    assert assess_early("early_independent_first", [search] + writes, reply)
    assert assess_early(
        "early_compound", writes + [search], valid_question("early_compound")
    )


def test_option_closing_references_and_reverse_order():
    """Closing A/B references are not rows and dataset order is model-owned."""
    search = [
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "结算额", "limit": 3, "maxMatchedFields": 3},
        }
    ]
    assert not assess_early(
        "early_metric", search, valid_question() + "\n回复 A 或 B 即可。"
    )
    options = early_options("early_metric")
    reversed_reply = "请选择：\n" + "\n".join(
        f"{label}. {o['datasetName']} · {o['fieldName']}：{o['description']}"
        for label, o in zip("AB", reversed(options))
    )
    assert not assess_early("early_metric", search, reversed_reply)


def test_conflict_does_not_require_literal_option_labels():
    """A business-language conflict question can identify the discrepancy."""
    assert not assess_early(
        "early_conflict", [], "你的选项与口径描述不一致，请确认要用哪种定义？"
    )


def test_table_options_and_reversed_continuation_mapping():
    """Table rows preserve model-owned labels and selected field references."""
    from tests.live.test_davinci_feedback_regressions import (
        clarification_evidence,
        continuation_request,
    )

    options = early_options("early_metric")
    reply = (
        "请选择：\n| 选项 | 数据集 | 指标 | 定义 |\n| --- | --- | --- | --- |\n"
        + "\n".join(
            f"| {label} | {o['datasetName']} | {o['fieldName']} | {o['description']} |"
            for label, o in zip("AB", reversed(options))
        )
        + "\n回复 A 或 B。"
    )
    evidence = clarification_evidence("early_metric", reply)
    assert not evidence["violations"] and not evidence["reviewConcerns"]
    prompt, selected = continuation_request("early_selected", evidence["optionMapping"])
    assert "选择 A" in prompt and selected == options[1]
    calls = [
        {
            "name": "catalog.get_dataset_schema",
            "arguments": {
                "datasetRef": selected["datasetRef"],
                "fieldRefs": [selected["fieldRef"]],
            },
        }
    ]
    assert not assess_early("early_selected", calls, selected["description"], selected)
    conflict, _ = continuation_request("early_conflict", evidence["optionMapping"])
    assert options[0]["description"] in conflict


def test_equivalent_and_unproven_semantics_are_separate():
    """Known paraphrases pass; uncertain semantics stay pending rather than failing."""
    from tests.live.test_davinci_feedback_regressions import clarification_evidence

    reply = (
        "请选择：\nA. 渠道结算 · 结算额：已记账金额，包括退货。\n"
        "B. 仓储核算 · 结算额：验收估价，并非实际付款。\n回复 A 或 B。"
    )
    evidence = clarification_evidence("early_metric", reply)
    assert not evidence["violations"] and not evidence["reviewConcerns"]
    uncertain = reply.replace("已记账金额，包括退货", "按账簿汇总并保留逆向交易")
    evidence = clarification_evidence("early_metric", uncertain)
    assert not evidence["violations"]
    assert "definition_semantics_need_review:A" in evidence["reviewConcerns"]
    missing = "请选择：\nA. 渠道结算 · 结算额\nB. 仓储核算 · 结算额"
    assert (
        "option_definition_missing"
        in clarification_evidence("early_metric", missing)["violations"]
    )


def test_bound_definition_requires_actual_schema_evidence():
    """Refs alone do not provide definitions, even if the model guesses correctly."""
    option = early_options("early_bound")[0]
    assert assess_early("early_bound", [], option["description"])
    calls = [
        {
            "name": "catalog.get_dataset_schema",
            "arguments": {
                "datasetRef": option["datasetRef"],
                "fieldRefs": [option["fieldRef"]],
            },
        }
    ]
    assert not assess_early("early_bound", calls, option["description"])


@pytest.mark.parametrize(
    "arguments",
    [{"query": "金额"}, {"query": "金额", "limit": 50, "maxMatchedFields": 50}],
)
def test_initial_choice_search_is_small(arguments):
    """Default or enlarged searches cannot masquerade as a bounded first read."""
    calls = [{"name": "catalog.search_datasets", "arguments": arguments}]
    assert "initial_search_not_bounded" in assess_early(
        "early_metric", calls, valid_question()
    )


def test_complete_definitions_do_not_allow_extra_schema_reads():
    """Two reads are a ceiling; already sufficient search evidence must stop reads."""
    calls = [
        {
            "name": "catalog.search_datasets",
            "arguments": {"query": "金额", "limit": 3, "maxMatchedFields": 3},
        },
        {
            "name": "catalog.get_dataset_schema",
            "arguments": {"datasetRef": "warehouseTopic:741", "limit": 200},
        },
    ]
    assert "unnecessary_definition_read" in assess_early(
        "early_metric", calls, valid_question()
    )


@pytest.mark.parametrize(
    "original,replacement",
    [
        ("已入账金额", "并非已入账金额"),
        ("验收入库估值", "不是验收入库估值"),
    ],
)
def test_negated_definitions_are_not_automatically_proven(original, replacement):
    """Negating a matching business concept requires review instead of success."""
    from tests.live.test_davinci_feedback_regressions import clarification_evidence

    reply = valid_question().replace(original, replacement)
    evidence = clarification_evidence("early_metric", reply)
    assert evidence["reviewConcerns"]


def test_unremoved_returns_keep_the_inclusion_meaning():
    """Negation of removal still means returns are included in booked amount."""
    from tests.live.test_davinci_feedback_regressions import definition_proven

    assert definition_proven(early_options("early_metric")[0], "已入账金额，未扣除退货")
