"""Replay feedback with a real model, fake page receipts and isolated catalog MCP.

Opt in: RUN_LIVE_DAVINCI_SKILL_ROUTING=1 pytest <this-file> -k live.
Only model traffic leaves this harness; all business actions are simulated.
Results contain visible replies/tool arguments and timing, never model reasoning.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from jsonschema import ValidationError, validate

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.davinci_feedback_fixture import early_options
from tests.live.test_davinci_prompt_comparison import business_receipt
from tests.live.test_davinci_round_two_flows import page
from tests.live.test_davinci_skill_routing import _native_body, _write_live_workspace
from tests.live.test_subscription_agui_qwen import _frontend_calls

HARNESS_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
RUNTIME_SOURCES_SHA256 = {
    name: hashlib.sha256(
        (Path(__file__).resolve().parents[2] / name).read_bytes()
    ).hexdigest()
    for name in ("app/runtime/claude.py", "app/agui/claude_tools.py")
}
DATASET_REF = "warehouseTopic:662"
FIELD_REF = "warehouseTopic:662/net_sales"
CASES = (
    "delete_new",
    "delete_added",
    "delete_cold",
    "known_definition",
    "combination",
    "global_discovery",
    "open_only",
    "continue_target",
    "navigation_query_failure",
)


EARLY_CASES = (
    "early_missing_action",
    "early_missing_object",
    "early_metric",
    "early_same_dataset",
    "early_definition_missing",
    "early_independent_first",
    "early_compound",
    "early_selected",
    "early_conflict",
    "early_bound",
    "early_style",
    "early_no_result",
    "early_permission",
    "early_upstream",
)


def option_blocks(text: str) -> list[tuple[str, str]]:
    """Extract labelled list blocks or table rows, excluding closing references."""
    normalized = text.replace("**", "").replace("`", "")
    blocks = []
    current = None
    for line in normalized.splitlines():
        # A table label must be a complete cell; headers/separators do not match.
        table = re.match(r"^\s*\|\s*([AB12])\s*\|(.+)", line)
        row = re.match(
            r"^\s*(?:[-*+]\s*|#{1,6}\s*)?(?:选项\s*)?([AB12])(?:[.．、:：)）]|\s+)\s*(.*)",
            line,
        )
        match = table or row
        if match:
            blocks.append((match.group(1), match.group(2).strip()))
            current = len(blocks) - 1
        elif current is not None:
            if re.search(r"请选择|请确认|回复|你想|您想|需要统计|时间范围", line):
                current = None
            elif line.strip() and not line.lstrip().startswith("|"):
                label, content = blocks[current]
                blocks[current] = (label, content + " " + line.strip())
    return blocks


def definition_proven(option: dict, text: str) -> bool:
    """Recognize bounded equivalent clauses; unknown paraphrases require review."""
    concept = (
        r"入账|记账|账面"
        if option["fieldRef"].endswith("/booked_amount")
        else r"验收|估值|估价"
    )
    # A matching noun inside a negated clause is not positive definition evidence.
    if re.search(
        r"(?:并非|不是|未|没有|不属于|不代表).{0,6}(?:" + concept + r")", text
    ):
        return False
    if option["fieldRef"].endswith("/booked_amount"):
        recorded = re.search(r"入账|记账|账面|记入账", text)
        includes = re.search(
            r"(?:包含|包括|计入|含|未扣除|不扣除|未剔除|不剔除).{0,6}(?:退货|退回)",
            text,
        )
        excludes = re.search(
            r"(?:不包含|不包括|不含|不计入|扣除|剔除).{0,6}(?:退货|退回)", text
        )
        # Negated exclusion means inclusion, not an actual contradictory clause.
        if excludes and re.search(r"未扣除|不扣除|未剔除|不剔除", text):
            excludes = None
        return bool(recorded and includes and not excludes)
    return bool(
        re.search(r"验收", text)
        and re.search(r"估值|估价|估算价值", text)
        and re.search(
            r"(?:不代表|不是|并非|非).{0,6}(?:实际支付|实际付款|实付|付款金额)", text
        )
    )


def clarification_evidence(case: str, text: str) -> dict:
    """Separate deterministic defects from semantic review and preserve label refs."""
    options = early_options(case)
    blocks = option_blocks(text)
    violations, concerns, mapping = [], [], {}
    # Shared headings identify same-dataset choices without repeating each name.
    common_identity = {
        key: len({option[key] for option in options}) == 1 and options[0][key] in text
        for key in ("datasetName", "fieldName")
    }
    if len(blocks) != 2 or {label for label, _ in blocks} != {"A", "B"}:
        violations.append("unstable_option_labels")
    for label, content in blocks:
        candidates = [
            o
            for o in options
            if (o["datasetName"] in content or common_identity["datasetName"])
            and (o["fieldName"] in content or common_identity["fieldName"])
        ]
        if not candidates:
            violations.append("option_identity_missing")
            continue
        proven = [o for o in candidates if definition_proven(o, content)]
        identified = (
            proven
            if len(proven) == 1
            else [o for o in candidates if o["fieldRef"] in content]
        )
        if not identified and len(candidates) == 1:
            identified = candidates
        residue = content
        for option in options:
            for key in ("datasetName", "fieldName", "fieldRef", "datasetRef"):
                residue = residue.replace(option[key], "")
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", residue):
            violations.append("option_definition_missing")
        elif not proven:
            concerns.append("definition_semantics_need_review:" + label)
        if len(identified) == 1:
            mapping[label] = identified[0]
        else:
            concerns.append("option_identity_mapping_needs_review:" + label)
    if len(mapping) == 2 and len({o["fieldRef"] for o in mapping.values()}) != 2:
        violations.append("duplicate_option_identity")
    if re.search(
        r"(?:请选择|选|回复).{0,12}[12一二].{0,8}(?:或|/|、).{0,4}[12一二]", text
    ):
        violations.append("number_letter_choice_contradiction")
    return {
        "violations": sorted(set(violations)),
        "reviewConcerns": sorted(set(concerns)),
        "optionMapping": mapping,
    }


def continuation_request(case: str, mapping: dict) -> tuple[str, dict | None]:
    """Follow the model's A label, constructing a real conflict with its other option."""
    selected = mapping.get("A")
    other = mapping.get("B")
    if selected is None or other is None:
        return "", None
    if case == "early_conflict":
        return "选择 A，也就是" + other[
            "description"
        ] + "的那个；先不要修改。", selected
    return "选择 A，只解释所选口径，不建图、不查询数值。", selected


def assess_early(
    case: str, calls: list[dict], final: str, selected_option: dict | None = None
) -> list[str]:
    """Judge sufficient business choices, bounded evidence and retained bindings."""
    names = [c["name"] for c in calls]
    failures = []
    asking = bool(
        re.search(
            r"请选择|请确认|回复\s*[AB12]|哪(?:个|一|种)|希望.*(?:做|操作)|想.*(?:做|操作)|需要.*(?:做|操作)"
            r"|请(?:告诉|告知)我[^。！？\n]{0,40}[AB]\s*还是\s*[AB]",
            final,
        )
    )
    if not final:
        failures.append("no_final_response")
    if case in {"early_missing_action", "early_missing_object", "early_conflict"}:
        if names:
            failures.append("preparation_before_user_choice")
        if not asking:
            failures.append("missing_actionable_question")
        if case == "early_missing_object" and not all(
            x in final for x in ["经营中心", "研发中心"]
        ):
            failures.append("known_object_choices_missing")
        if case == "early_conflict" and not re.search(
            r"冲突|矛盾|不一致|不匹配|但.{0,30}(?:对应|描述)|选.{0,15}与.{0,15}不同",
            final,
        ):
            failures.append("conflict_not_identified")
        return failures
    if case == "early_style":
        if names != ["dashboard.get_widget_edit_capabilities"] or "颜色" not in final:
            failures.append("style_only_routing_changed")
        if calls and (
            calls[0]["arguments"].get("widgetIds") != ["daily"]
            or calls[0]["arguments"].get("includeDatasetOptions")
        ):
            failures.append("style_read_lost_target_or_requested_datasets")
        return failures
    if case in {"early_selected", "early_bound"}:
        option = selected_option or early_options(case)[0]
        if case == "early_bound" and names != ["catalog.get_dataset_schema"]:
            failures.append("bound_definition_evidence_not_read_once")
        if any(n != "catalog.get_dataset_schema" for n in names) or len(names) > 1:
            failures.append("chosen_binding_rescanned")
        for call in calls:
            if call["arguments"].get("datasetRef") != option["datasetRef"] or call[
                "arguments"
            ].get("fieldRefs") != [option["fieldRef"]]:
                failures.append("chosen_binding_lost")
        if asking:
            failures.append("selected_definition_reconfirmed")

        return failures
    allowed = {"catalog.search_datasets", "catalog.get_dataset_schema"}
    if case in {
        "early_metric",
        "early_same_dataset",
        "early_definition_missing",
        "early_compound",
        "early_independent_first",
    }:
        for call in calls:
            if call["name"] == "catalog.search_datasets" and any(
                call["arguments"].get(key, 5) != 3
                for key in ("limit", "maxMatchedFields")
            ):
                failures.append("initial_search_not_bounded")
        if "catalog.get_dataset_schema" in names:
            failures.append("unnecessary_definition_read")
    if case == "early_independent_first":
        independent = [
            "workspace.dashboard_group.create",
            "workspace.dashboard.create_and_open",
        ]
        if names[:2] != independent or any(
            names.count(name) != 1 for name in independent
        ):
            failures.append("explicit_independent_part_not_completed_first")
        named_completion = all(
            word in final for word in ("周报", "经营看板")
        ) and re.search(r"已.*(?:创建|新建|建好)", final)
        grouped_completion = re.search(
            r"分组(?:和|与|及|、)看板已(?:经)?(?:创建完成|新建完成|建好|完成)", final
        )
        if not (named_completion or grouped_completion):
            failures.append("independent_completion_not_reported")
        allowed.update(independent)
    if names.count("catalog.search_datasets") != 1:
        failures.append("search_not_exactly_once")
    if names.count("catalog.get_dataset_schema") > 1 or any(
        n not in allowed for n in names
    ):
        failures.append("excess_preparation_before_question")
    if case in {"early_no_result", "early_permission", "early_upstream"}:
        terms = {
            "early_no_result": r"未找到|未搜到|未检索到|没有找到|无匹配",
            "early_permission": r"权限|无权|未授权",
            "early_upstream": r"服务.*(?:不可用|异常|失败)|上游|暂时.*(?:不可用|失败)",
        }
        if not re.search(terms[case], final):
            failures.append("actual_limit_missing")
        if re.search(
            r"数据集不存在|没有这个数据集|(?:请|需要|提供).{0,20}(?:datasetRef|fieldRef|数据集ID|数据集 ID)",
            final,
        ):
            failures.append("invented_absence_or_internal_id_demand")
        if any(
            option["datasetName"] in final or option["fieldRef"] in final
            for option in early_options(case)
        ) or re.search(r"(?:A[.、：:]|B[.、：:])", final):
            failures.append("invented_choices_after_search_limit")
        return failures
    if not asking:
        failures.append("missing_actionable_question")
    failures.extend(clarification_evidence(case, final)["violations"])
    return failures


def workspace_template_hash(workspace: Path) -> str:
    """Hash relative names and exact prompt/Skill bytes for replay attribution."""
    digest = hashlib.sha256()
    for path in sorted(
        [workspace / "CLAUDE.md", *(workspace / ".claude/skills").rglob("*")]
    ):
        if path.is_file():
            digest.update(path.relative_to(workspace).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def assess_feedback(case: str, calls: list[dict], final: str) -> list[str]:
    """Judge observable business completion and forbidden extra work."""
    if case.startswith("early_"):
        return assess_early(case, calls, final)
    names = [call["name"] for call in calls]
    failures = []
    if not final:
        failures.append("no_final_response")
    if case.startswith("delete_"):
        if names.count("space.delete") != 1:
            failures.append("delete_capability_not_used_once")
        if "审批" not in final or any(
            re.search(r"已删除|删除成功", clause)
            and not re.search(r"未|尚|不能|不应|不可|没有", clause)
            for clause in re.split(r"[。；，;,.]", final)
        ):
            failures.append("approval_not_reported_truthfully")
    elif case == "known_definition":
        if sorted(names) != sorted(
            ["dashboard.get_widget_config", "catalog.get_dataset_schema"]
        ):
            failures.append("definition_not_exactly_two_business_reads")
        schemas = [
            c["arguments"] for c in calls if c["name"] == "catalog.get_dataset_schema"
        ]
        if (
            not schemas
            or schemas[0].get("datasetRef") != DATASET_REF
            or schemas[0].get("fieldRefs") != [FIELD_REF]
        ):
            failures.append("definition_refs_not_exact")
        if "退款" not in final or "取消" not in final:
            failures.append("definition_evidence_missing")
    elif case == "combination":
        if any(
            n.startswith(("dashboard.apply", "dashboard.get_widget_data"))
            for n in names
        ):
            failures.append("query_or_write_before_combination_clarified")
        if not any(
            n in {"analytics.resolve_data_requirements", "catalog.search_datasets"}
            for n in names
        ):
            failures.append("combination_candidates_not_read")
        if not all(
            word in final for word in ("回收订单", "门店回收", "成交额", "退款", "验收")
        ):
            failures.append("combination_candidate_definitions_missing")
        if not re.search(r"[？?]|请.*(?:选择|确认)|哪个|哪一", final):
            failures.append("combination_clarification_missing")
    elif case == "global_discovery":
        if (
            "catalog.search_datasets" not in names
            or "catalog.list_dataset_usages" not in names
        ):
            failures.append("global_discovery_incomplete")
        if re.search(r"(?:请|需要|先|哪个|哪一).{0,15}(?:日期|时间范围|时间段)", final):
            failures.append("discovery_incorrectly_requires_date")
        if re.search(
            r"(?:请|需要|先|必须).{0,12}(?:选|明确).{0,10}(?:数据集|表)", final
        ):
            failures.append("discovery_forces_dataset_selection")
        if re.search(
            r"(?:没有|无|不支持|缺少).{0,12}关联报表.{0,8}(?:工具|能力)", final
        ):
            failures.append("discovery_denies_available_usage_tool")
        if not re.search(r"关键词|关键字|本次搜索|本次检索|搜索范围", final):
            failures.append("discovery_keyword_scope_missing")
        if not re.search(
            r"候选|关联.*(?:不代表|不证明|不能证明)|未.*核实.*(?:字段|指标)"
            r"|(?:进一步|后续|还需|仍需|尚需).{0,50}确认.{0,6}指标是否实际被组件使用",
            final,
        ):
            failures.append("dataset_association_mistaken_for_field_binding")
        if not re.search(
            r"(?:未|不|无法|不能|尚未).{0,16}(?:覆盖|全部|所有|完整)|覆盖.{0,8}(?:有限|不完整|未建立)",
            final,
        ):
            failures.append("discovery_resource_coverage_limit_missing")
        for clause in re.split(r"[。；;\n]", final):
            if re.search(
                r"所有.*(?:报表|看板).*(?:都含|都包含|均含)|已确认.*(?:报表|看板).*(?:包含|含有)",
                clause,
            ) and not re.search(r"未|不|无法|不能|尚未", clause):
                failures.append("discovery_overclaims_metric_coverage")
        if any(n.startswith(("dashboard.", "ui.")) for n in names):
            failures.append("discovery_navigated_or_queried_values")
    elif case == "open_only":
        if names != ["ui.open_dashboard"] or calls[0]["arguments"] != {
            "dashboardId": "333"
        }:
            failures.append("open_did_not_stop_after_ack")
    elif case == "continue_target":
        if (
            "dashboard.get_widget_config" not in names
            or "catalog.get_dataset_schema" not in names
        ):
            failures.append("explicit_continue_lost_target")
        if any(
            c["arguments"].get("widgetId") != "daily"
            for c in calls
            if c["name"] == "dashboard.get_widget_config"
            and "widgetId" in c["arguments"]
        ):
            failures.append("continue_wrong_widget")
    elif case == "navigation_query_failure":
        if (
            names.count("ui.open_dashboard") != 1
            or "dashboard.get_widget_data" not in names
        ):
            failures.append("navigation_or_query_not_attempted")
        if not re.search(
            r"已打开|打开了|已进入|已.*切换|已到达", final
        ) or not re.search(
            r"查询失败|查询.*超时|数据.*失败|未能.*数据|无法.*(?:数据|读取|数值)", final
        ):
            failures.append("navigation_and_query_outcomes_conflated")
    return failures


def fixture_receipt(case: str, call: dict, registry) -> dict:
    """Return schema-checked fake ACK/config/error, with zero business network."""
    name, args = call["name"], call["arguments"]
    validate(args, dict(registry.get(name).input_schema))
    if name == "space.create":
        raw = {
            "status": "success",
            "summary": "测试空间已创建",
            "targetRef": "space:1:9",
            "contextVersion": 1,
        }
    elif name.startswith("space."):
        scenario = "ambiguous_object" if case == "early_missing_object" else "delete"
        return business_receipt(scenario, call, registry)
    elif name.startswith("workspace.dashboard"):
        raw = {
            "status": "success",
            "data": {
                "created": True,
                "name": args["name"],
                "iconAssigned": True,
                "menuRefreshed": True,
            },
            "issues": [],
        }
        if name == "workspace.dashboard.create_and_open":
            raw["data"]["opened"] = True
    elif name == "ui.open_dashboard":
        assert args == {"dashboardId": "333"}
        raw = {"status": "opened", "contextVersion": 2, "summary": "已打开回收经营看板"}
    elif name == "workspace.list_dashboards":
        raw = {"summary": "回收经营：dashboardId=333", "contextVersion": 1}
    elif name == "dashboard.get_structure":
        raw = {
            "summary": "当前看板无组件"
            if case == "combination"
            else "回收经营；净成交额组件 widgetId=daily"
        }
    elif name == "dashboard.get_widget_config":
        assert args.get("widgetId") == "daily" or args.get("widgetIds") == ["daily"]
        one = {
            "summary": "净成交额",
            "effectiveSpec": {"title": "净成交额"},
            "refs": {
                "datasetRef": DATASET_REF,
                "fieldRefs": [
                    {"role": "metric", "fieldId": "net_sales", "fieldRef": FIELD_REF}
                ],
            },
        }
        raw = (
            {"summary": "净成交额", "widgets": [{"widgetId": "daily", **one}]}
            if "widgetIds" in args
            else one
        )
    elif name == "dashboard.get_widget_edit_capabilities":
        raw = {
            "status": "success",
            "data": {
                "resourceId": "88",
                "widgets": [
                    {
                        "widgetId": "daily",
                        "widgetType": "metric",
                        "canWrite": True,
                        "capabilities": [
                            {
                                "id": "appearance.backgroundColor",
                                "section": "appearance",
                                "label": "背景颜色",
                                "description": "组件背景颜色",
                                "currentValue": "#ffffff",
                                "writable": True,
                                "valueSpec": {"valueType": "color", "nullable": True},
                            }
                        ],
                    }
                ],
                "commonCapabilities": [],
            },
            "issues": [],
        }
    elif name == "dashboard.apply_widget_spec":
        raw = {
            "status": "success",
            "observed": {"observedAt": "2026-09-07T00:00:00Z"},
            "issues": [],
        }
    elif name == "dashboard.get_widget_data":
        envelope = {
            "status": "error",
            "error": {
                "code": "QUERY_FAILED",
                "message": "看板已打开，但数据源查询超时",
                "retryable": False,
            },
            "issues": [],
        }
        return {
            "id": str(uuid.uuid4()),
            "role": "tool",
            "toolCallId": call["id"],
            "content": json.dumps(envelope, ensure_ascii=False),
            "error": "QUERY_FAILED",
        }
    else:
        raise AssertionError(f"Unexpected fixture action: {name}")
    validate(raw, dict(registry.get(name).output_schema))
    return {
        "id": str(uuid.uuid4()),
        "role": "tool",
        "toolCallId": call["id"],
        "content": json.dumps(
            raw
            if name.startswith("workspace.dashboard")
            or name
            in {"dashboard.get_widget_edit_capabilities", "dashboard.apply_widget_spec"}
            else {"status": "success", "data": raw, "observed": {}, "issues": []},
            ensure_ascii=False,
        ),
    }


def metric_counts(database: Path) -> dict:
    """Export only public aggregate usage and parameter-error counts."""
    with sqlite3.connect(database) as db:
        events = [
            (kind, json.loads(payload))
            for kind, payload in db.execute(
                "select event_type,payload_json from turn_events"
            )
        ]
    return {
        "allToolEvents": [
            {
                "event": k,
                **{
                    key: p[key]
                    for key in (
                        "tool_use_id",
                        "name",
                        "input_preview",
                        "started_at",
                        "completed_at",
                        "duration_ms",
                        "is_error",
                        "error_code",
                    )
                    if key in p
                },
                **(
                    {"outputPreviewChars": len(str(p.get("output_preview", "")))}
                    if k == "tool.completed"
                    else {}
                ),
            }
            for k, p in events
            if k in {"tool.started", "tool.completed", "tool.failed"}
        ],
        "modelApiTurns": sum(
            p.get("model_api_turns", 0) for k, p in events if k == "usage.updated"
        ),
        "parameterErrors": sum(
            "INVALID_ARGUMENT" in json.dumps(p) or "PARAMETER_ERROR" in json.dumps(p)
            for k, p in events
            if k in {"tool.completed", "tool.failed"}
        ),
    }


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SKILL_ROUTING") != "1",
    reason="Explicit real-model opt-in required",
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample", range(int(os.environ.get("DAVINCI_FEEDBACK_SAMPLES", "1")))
)
@pytest.mark.parametrize("case", CASES + EARLY_CASES)
async def test_live_feedback(
    case: str, sample: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use real persistent SDK sessions and current repository prompts/Skills."""
    from app.config import Settings

    root = tmp_path / "workspaces"
    _write_live_workspace(root)
    source_override = os.environ.get("DAVINCI_FEEDBACK_WORKSPACE_SOURCE")
    if source_override:
        source = Path(source_override).resolve()
        shutil.copy2(source / "CLAUDE.md", root / "actual/CLAUDE.md")
        shutil.rmtree(root / "actual/.claude/skills")
        shutil.copytree(source / ".claude/skills", root / "actual/.claude/skills")
    template_hash = workspace_template_hash(root / "actual")
    trace = tmp_path / "catalog.jsonl"
    fake = Path(__file__).with_name("davinci_feedback_fixture.py")
    contract_snapshot = tmp_path / "feedback-contracts.json"
    contract_snapshot.write_bytes(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures/davinci_feedback_contracts.json"
        ).read_bytes()
    )
    manifest = root / "actual/workspace.yaml"
    manifest.write_text(
        manifest.read_text().replace(
            "mcp_servers: {}",
            "  - mcp__davinci_data__*\nmcp_servers:\n  davinci_data:\n    type: stdio\n    command: "
            + sys.executable
            + "\n    entrypoint_env: FEEDBACK_FIXTURE_SCRIPT\n    args:\n      - "
            + str(trace)
            + "\n      - "
            + case
            + "\n      - "
            + str(contract_snapshot),
        )
    )
    monkeypatch.setenv("FEEDBACK_FIXTURE_SCRIPT", str(fake))
    monkeypatch.setenv("WORKSPACES_ROOT", str(root))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    from app.main import create_app

    settings = Settings(
        workspaces_root=root,
        app_data_dir=tmp_path / "data",
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner"},
        identity_mode="mock",
        turn_timeout_seconds=180,
        claude_model="deepseek-v4-pro-0813",
        claude_selectable_models="deepseek-v4-pro-0813",
        claude_default_effort="low",
        claude_thinking_budget_tokens=None,
    )
    app = create_app(settings=settings)
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    names = (
        [c.action for c in registry.public_contracts if c.bundle == "space-core"]
        if case.startswith("delete_")
        else [
            "ui.open_dashboard",
            "workspace.list_dashboards",
            "dashboard.get_structure",
            "dashboard.get_widget_config",
            "dashboard.get_widget_data",
        ]
    )

    if case == "combination":
        names.append("dashboard.apply_widget_spec")
    if case.startswith("early_"):
        names = list(
            dict.fromkeys(
                names
                + [
                    "dashboard.apply_widget_spec",
                    "dashboard.get_widget_edit_capabilities",
                ]
                + [
                    c.action
                    for c in registry.public_contracts
                    if c.bundle == "space-core"
                    or c.action.startswith("workspace.dashboard")
                ]
            )
        )

    def catalog(deletion: bool = True) -> list[dict]:
        """Expose the current page catalog, including capability transitions."""
        return [
            {
                "name": n,
                "description": registry.get(n).description,
                "parameters": dict(registry.get(n).input_schema),
            }
            for n in names
            if deletion or n != "space.delete"
        ]

    prompts = {
        "delete_new": "删除经营空间，按页面确认提交。",
        "delete_added": "现在请删除经营空间，按页面确认提交。",
        "delete_cold": "现在请删除经营空间，按页面确认提交。",
        "known_definition": "当前净成交额组件（widgetId=daily）的指标口径是什么？只查定义。",
        "combination": "当前看板没有组件。帮我加一个成交额指标卡。",
        "global_discovery": "全局找所有和奢侈品回收成交额相关的数据集、指标和关联报表，只找数据入口，不查询数值。",
        "open_only": "打开 333",
        "continue_target": "继续刚才净成交额组件的口径排查。",
        "navigation_query_failure": "打开 333 并解读净成交额组件（widgetId=daily）的数据。",
    }
    prompts.update(
        {
            "early_missing_action": "帮我处理一下当前经营空间。",
            "early_missing_object": "把经营空间改名为经营分析。已知有两个同名空间：经营中心的经营空间和研发中心的经营空间。",
            "early_metric": "帮我加一个结算额指标卡。",
            "early_same_dataset": "帮我加一个结算额指标卡。",
            "early_compound": "帮我新建周报分组、经营看板和结算额指标卡。",
            "early_definition_missing": "帮我加一个结算额指标卡。",
            "early_independent_first": "先完成周报分组和空的经营看板创建，再处理结算额指标卡。",
            "early_selected": "选择 A，只解释所选口径，不建图、不查询数值。",
            "early_conflict": "选择 A，也就是验收入库估值，不代表实际支付的那个；先不要修改。",
            "early_bound": "已确认结算额绑定 datasetRef=warehouseTopic:741，fieldRef=warehouseTopic:741/booked_amount。只查该字段定义，不查询数值。",
            "early_style": "当前已绑定净成交额组件（widgetId=daily），这一步只查看可编辑的样式项，不改口径，也不修改配置。",
            "early_no_result": "找一下结算额指标的数据入口，只查定义。",
            "early_permission": "找一下结算额指标的数据入口，只查定义。",
            "early_upstream": "找一下结算额指标的数据入口，只查定义。",
        }
    )
    calls_seen, phases, failures = [], [], []
    phase_measurements = []
    review_concerns = []
    selected_option = None
    option_mapping = {}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client,
    ):
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        # Compare the actual SDK input, not only source files: bootstrap may
        # reject a broken Skill while still allowing the session to be created.
        session_workspace = (
            settings.app_data_dir / "sessions" / session["id"] / "workspace"
        )
        materialized_hash = workspace_template_hash(session_workspace)
        assert materialized_hash == template_hash, (
            "Session CLAUDE/Skill snapshot differs from the replay template"
        )
        body = _native_body(
            session["id"],
            str(uuid.uuid4()),
            prompts[case],
            page(
                "delete"
                if case.startswith("delete_") or case == "early_missing_action"
                else "interpret"
            ),
            (),
        )
        body["tools"] = catalog()
        if case == "early_missing_action":
            body["state"]["page"]["space"] = {
                "id": "83",
                "name": "经营空间",
                "role": "owner",
            }

        async def run_phase(prompt: str) -> tuple[list[dict], str]:
            """Resume one SDK session through fake receipts until the final reply."""
            body["runId"] = str(uuid.uuid4())
            body["messages"] = [
                {"id": str(uuid.uuid4()), "role": "user", "content": prompt}
            ]
            usage_before = metric_counts(tmp_path / "data/app.db")["modelApiTurns"]
            phase_start = time.monotonic()
            seen = []
            final = ""
            for _ in range(8):
                response = await client.post(
                    "/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body
                )
                response.raise_for_status()
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ")
                ]
                errors = [e for e in events if e.get("type") == "RUN_ERROR"]
                if errors:
                    failures.extend(
                        "runtime_error:" + str(e.get("code", "unknown")) for e in errors
                    )
                    break
                calls, assistant = _frontend_calls(response.text)
                if not calls:
                    final = assistant
                    break
                seen.extend(
                    {
                        "name": c["name"],
                        "arguments": c["arguments"],
                        "atMonotonic": time.monotonic(),
                    }
                    for c in calls
                )
                body["runId"] = str(uuid.uuid4())
                try:
                    body["messages"] = [
                        fixture_receipt(case, c, registry) for c in calls
                    ]
                    for entry, receipt in zip(seen[-len(calls) :], body["messages"]):
                        entry.update(
                            result=json.loads(receipt["content"]),
                            resultChars=len(receipt["content"]),
                        )
                except (AssertionError, KeyError, ValidationError) as error:
                    failures.append("fixture_contract_error:" + type(error).__name__)
                    break
                if any(c["name"] == "ui.open_dashboard" for c in calls):
                    body["state"]["page"]["resource"] = {
                        "type": "dashboard",
                        "id": "333",
                        "name": "回收经营",
                    }
                    body["state"]["revisions"]["routeRevision"] += 1
            phase_measurements.append(
                {
                    "userInput": prompt,
                    "elapsedSeconds": round(time.monotonic() - phase_start, 3),
                    "startedMonotonic": phase_start,
                    "modelApiTurns": metric_counts(tmp_path / "data/app.db")[
                        "modelApiTurns"
                    ]
                    - usage_before,
                }
            )
            return seen, final

        if case in {"delete_added", "delete_cold"}:
            body["tools"] = catalog(False)
            before, reply = await run_phase("删除经营空间。")
            phases.append(
                {"phase": "capability_absent", "calls": before, "final": reply}
            )
            if any(c["name"] == "space.delete" for c in before):
                failures.append("absent_capability_called")
            if case == "delete_cold":
                app.state.services.runtime.tool_ledger.drop(session["id"])
                assert not app.state.services.runtime.tool_ledger.get(
                    session["id"]
                ).last_tool_names
            body["tools"] = catalog()
        elif case in {"open_only", "continue_target"}:
            before, reply = await run_phase(
                "帮我找回收经营看板，后续想排查净成交额组件（widgetId=daily）的口径；这一步只找看板，先不要排查。"
            )
            phases.append(
                {"phase": "find_before_followup", "calls": before, "final": reply}
            )
        if case in {"early_selected", "early_conflict"}:
            before, reply = await run_phase("帮我加一个结算额指标卡。")
            before_mcp = (
                [json.loads(line) for line in trace.read_text().splitlines()]
                if trace.exists()
                else []
            )
            evidence = clarification_evidence("early_metric", reply)
            option_mapping = evidence["optionMapping"]
            review_concerns.extend(
                "initial_choice:" + c for c in evidence["reviewConcerns"]
            )
            followup, selected_option = continuation_request(case, option_mapping)
            phase_failures = assess_early("early_metric", before + before_mcp, reply)
            if followup:
                prompts[case] = followup
            else:
                review_concerns.append("continuation_mapping_unresolved")
            failures.extend("initial_choice:" + f for f in phase_failures)
            phases.append(
                {
                    "phase": "choose_before_followup",
                    "calls": before + before_mcp,
                    "final": reply,
                    "violations": phase_failures,
                    "reviewConcerns": evidence["reviewConcerns"],
                    "optionMapping": option_mapping,
                    **phase_measurements[-1],
                }
            )
        if trace.exists():
            trace.unlink()
        if case in {"early_selected", "early_conflict"} and selected_option is None:
            calls_seen, final = [], ""
        else:
            calls_seen, final = await run_phase(prompts[case])
    mcp_calls = (
        [json.loads(line) for line in trace.read_text().splitlines()]
        if trace.exists()
        else []
    )
    calls_seen.extend(mcp_calls)
    calls_seen.sort(key=lambda c: c.get("atMonotonic", 0))
    if any(call.get("parameterError") for call in mcp_calls):
        failures.append("fixture_parameter_errors")
    usage = metric_counts(tmp_path / "data/app.db")
    if any(
        event.get("name") == "Skill" and event.get("is_error") is True
        for event in usage["allToolEvents"]
    ):
        failures.append("skill_load_failed")
    if case in {"early_selected", "early_conflict"} and selected_option is None:
        violations = failures
    elif case.startswith("early_"):
        violations = failures + assess_early(case, calls_seen, final, selected_option)
    else:
        violations = failures + assess_feedback(case, calls_seen, final)
    if case in {
        "early_metric",
        "early_same_dataset",
        "early_compound",
        "early_definition_missing",
        "early_independent_first",
    }:
        evidence = clarification_evidence(case, final)
        review_concerns.extend(evidence["reviewConcerns"])
        option_mapping = evidence["optionMapping"]
    if (
        case in {"early_selected", "early_bound"}
        and final
        and not definition_proven(selected_option or early_options(case)[0], final)
    ):
        review_concerns.append("selected_definition_semantics_need_review")
    output = {
        "case": case,
        "sample": sample,
        "workspaceTemplateSha256": template_hash,
        "materializedWorkspaceSha256": materialized_hash,
        "harnessSha256": HARNESS_SHA256,
        "runtimeSourcesSha256": RUNTIME_SOURCES_SHA256,
        "fixtureImplementationSha256": hashlib.sha256(fake.read_bytes()).hexdigest(),
        "model": "deepseek-v4-pro-0813",
        "fixtureContractSha256": hashlib.sha256(
            contract_snapshot.read_bytes()
        ).hexdigest(),
        "effort": "low",
        "durationSeconds": phase_measurements[-1]["elapsedSeconds"],
        "firstSufficientClarificationSeconds": phase_measurements[-1]["elapsedSeconds"]
        if case in EARLY_CASES
        and not violations
        and not review_concerns
        and case
        not in {
            "early_selected",
            "early_bound",
            "early_style",
            "early_no_result",
            "early_permission",
            "early_upstream",
        }
        else None,
        "timingMethod": "User submission to complete final reply; conservative upper bound, ASGI response buffered; frontend continuations retain timer",
        "backendMetadataWork": "unmeasured: fixture has no backend metadata work; use Data MCP tests",
        "phaseMeasurements": phase_measurements,
        **usage,
        "fixtureParameterErrors": sum(
            bool(call.get("parameterError")) for call in mcp_calls
        ),
        "calls": calls_seen,
        "phases": phases,
        "final": final,
        "violations": violations,
        "reviewConcerns": sorted(set(review_concerns)),
        "optionMapping": option_mapping,
        "selectedOption": selected_option,
        "reviewStatus": "pending" if review_concerns else "not_required",
        "passed": None if review_concerns and not violations else not violations,
    }
    (tmp_path / "feedback-result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )
    assert not violations, output
    if review_concerns:
        pytest.skip("Semantic review pending; preserved feedback-result.json")


@pytest.mark.parametrize("case", CASES)
def test_judge_rejects_missing_work(case: str) -> None:
    """A vague reply must not pass any completion/clarification scenario."""
    assert assess_feedback(case, [], "好的")


def test_judge_definition_requires_exact_refs() -> None:
    """Two reads with guessed references still fail the definition contract."""
    calls = [
        {"name": "dashboard.get_widget_config", "arguments": {"widgetId": "daily"}},
        {
            "name": "catalog.get_dataset_schema",
            "arguments": {"datasetRef": "guessed", "fieldRefs": [FIELD_REF]},
        },
    ]
    assert "definition_refs_not_exact" in assess_feedback(
        "known_definition", calls, "支付减去退款，排除取消订单"
    )


def test_judge_pending_delete_is_not_deleted() -> None:
    """A fake approval ACK never establishes an actual deletion."""
    calls = [{"name": "space.delete", "arguments": {"spaceRef": "space:1:1"}}]
    assert not assess_feedback("delete_new", calls, "审批已提交，等待审批结果。")
    assert assess_feedback("delete_new", calls, "审批已通过，已删除。")


@pytest.mark.parametrize(
    "case,calls,final",
    [
        (
            "combination",
            [
                {
                    "name": "analytics.resolve_data_requirements",
                    "arguments": {"rawQuery": "成交额"},
                }
            ],
            "请选择回收订单的成交额（包含已退款订单），还是门店回收的成交额（验收入库估价）？",
        ),
        (
            "global_discovery",
            [
                {"name": "catalog.search_datasets", "arguments": {"query": "回收"}},
                {
                    "name": "catalog.list_dataset_usages",
                    "arguments": {"datasetRef": DATASET_REF},
                },
            ],
            "按本次关键词搜索找到回收订单数据集及回收经营候选报表。数据集关联不证明字段绑定，尚未覆盖全部报表类型。",
        ),
        (
            "open_only",
            [{"name": "ui.open_dashboard", "arguments": {"dashboardId": "333"}}],
            "已打开回收经营看板。",
        ),
        (
            "navigation_query_failure",
            [
                {"name": "ui.open_dashboard", "arguments": {"dashboardId": "333"}},
                {
                    "name": "dashboard.get_widget_data",
                    "arguments": {"widgetIds": ["daily"]},
                },
            ],
            "已打开回收经营看板；数据查询失败，尚不能解读数值。",
        ),
    ],
)
def test_judge_accepts_supported_outcomes(
    case: str, calls: list[dict], final: str
) -> None:
    """Accept evidence-backed success, clarification and partial failure reports."""
    assert not assess_feedback(case, calls, final)


def test_judge_open_rejects_restarting_old_analysis() -> None:
    """A navigation ACK ends the new open-only objective despite older work."""
    calls = [
        {"name": "ui.open_dashboard", "arguments": {"dashboardId": "333"}},
        {"name": "dashboard.get_widget_config", "arguments": {"widgetId": "daily"}},
    ]
    assert "open_did_not_stop_after_ack" in assess_feedback(
        "open_only", calls, "已打开。"
    )


def test_judge_accepts_negated_deletion_claim() -> None:
    """Do not mistake an explicit pending-state caveat for a success claim."""
    calls = [{"name": "space.delete", "arguments": {"spaceRef": "space:1:1"}}]
    assert not assess_feedback(
        "delete_cold", calls, "已提交删除审批，尚不能视为已删除。"
    )


def test_judge_accepts_query_timeout_separate_from_navigation() -> None:
    """Timeout wording still clearly distinguishes navigation from data failure."""
    calls = [
        {"name": "ui.open_dashboard", "arguments": {"dashboardId": "333"}},
        {"name": "dashboard.get_widget_data", "arguments": {"widgetIds": ["daily"]}},
    ]
    assert not assess_feedback(
        "navigation_query_failure",
        calls,
        "已打开回收经营看板，但数据查询超时，暂无法读取实际数值。",
    )


@pytest.mark.parametrize(
    "final",
    [
        "请先选择一个数据集，才能告诉你哪里有回收额。",
        "已确认所有 Watcher 报表都含净成交额，包括回收经营。",
        "没有关联报表查询工具，无法查询关联报表。",
    ],
)
def test_discovery_rejects_false_scope_or_capability_claims(final: str) -> None:
    """Successful catalog receipts do not justify narrowing or false capability claims."""
    calls = [
        {"name": "catalog.search_datasets", "arguments": {"query": "回收额"}},
        {
            "name": "catalog.list_dataset_usages",
            "arguments": {"datasetRef": DATASET_REF},
        },
    ]
    assert assess_feedback("global_discovery", calls, final)


def test_fixture_refs_use_production_field_identity() -> None:
    """Field identity is datasetRef/fieldId, not an invented colon namespace."""
    assert DATASET_REF == "warehouseTopic:662"
    assert FIELD_REF == DATASET_REF + "/net_sales"


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "catalog.get_dataset_schema",
            {
                "datasetRef": "warehouseTopic:662",
                "fieldRefs": ["field:feedback:orders:net_sales"],
            },
        ),
        (
            "catalog.get_dataset_schema",
            {
                "datasetRef": "warehouseTopic:662",
                "fieldRefs": ["warehouseTopic:663/net_sales"],
            },
        ),
        (
            "catalog.list_dataset_usages",
            {"datasetRef": "warehouseTopic:662", "cursor": "invented"},
        ),
    ],
)
def test_fixture_rejects_nonproduction_arguments(name: str, args: dict) -> None:
    """Reject malformed/cross-dataset refs and unsupported usage pagination."""
    from tests.live.davinci_feedback_fixture import fixture_payload

    with pytest.raises((ValidationError, ValueError)):
        fixture_payload("default", name, args)


def test_fixture_uses_real_receipt_models() -> None:
    """Checked-in samples retain production candidates, metadata and coverage."""
    from tests.live.davinci_feedback_fixture import CONTRACTS, fixture_payload

    schema = fixture_payload(
        "default",
        "catalog.get_dataset_schema",
        {"datasetRef": DATASET_REF, "fieldRefs": [FIELD_REF]},
    )
    assert schema["status"] == "ok"
    assert schema["dataset"]["datasetRef"] == DATASET_REF
    assert schema["fields"][0]["description"]
    assert "definition" not in schema["fields"][0]
    search = fixture_payload("default", "catalog.search_datasets", {"query": "回收"})
    assert "candidates" in search and "datasets" not in search
    assert search["coverage"]["source"] == "davinci_data_market"
    usages = fixture_payload(
        "default", "catalog.list_dataset_usages", {"datasetRef": DATASET_REF}
    )
    assert usages["coverageComplete"] is False
    assert usages["usages"][0]["evidence"] == "dataset_association"
    assert usages["usages"][0]["resourceType"] == "CUSTOM"
    assert set(CONTRACTS["tools"]) == {
        "catalog.get_dataset_schema",
        "catalog.search_datasets",
        "catalog.list_dataset_usages",
        "analytics.resolve_data_requirements",
    }
    assert CONTRACTS["provenance"]["validatedWithProductionPydantic"] is True


@pytest.mark.asyncio
async def test_fixture_stdio_roundtrip(tmp_path: Path) -> None:
    """Exercise the real stdio fixture transport without calling a model or API."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from tests.live.davinci_feedback_fixture import CONTRACTS

    script = Path(__file__).with_name("davinci_feedback_fixture.py")
    trace = tmp_path / "fixture.jsonl"
    params = StdioServerParameters(
        command=sys.executable, args=[str(script), str(trace), "default"]
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        catalog = await session.list_tools()
        assert {tool.name for tool in catalog.tools} == set(CONTRACTS["tools"])
        response = await session.call_tool(
            "catalog.get_dataset_schema",
            {"datasetRef": DATASET_REF, "fieldRefs": [FIELD_REF]},
        )
        assert not response.isError
        assert response.structuredContent["fields"][0]["fieldRef"] == FIELD_REF
        invalid = await session.call_tool(
            "catalog.list_dataset_usages",
            {"datasetRef": DATASET_REF, "cursor": "unsupported"},
        )
        assert invalid.isError
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert rows[-1]["parameterError"] is True


def test_discovery_accepts_explicit_pending_field_usage_verification() -> None:
    """A specific future field-use check distinguishes association from binding."""
    calls = [
        {"name": "catalog.search_datasets", "arguments": {"query": "回收额"}},
        {
            "name": "catalog.list_dataset_usages",
            "arguments": {"datasetRef": DATASET_REF},
        },
    ]
    final = (
        "关键词检索在授权范围内完成。关联报表是回收经营，关联报表覆盖不完整，"
        "仅覆盖 CUSTOM/SPACE 类型。进一步打开回收经营报表确认该指标是否实际被组件使用。"
    )
    assert not assess_feedback("global_discovery", calls, final)
