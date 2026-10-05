"""Protect the native editor's bounded draft and truthful save contracts."""

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.agui.catalog import to_ag_ui_tool
from app.agui.claude_tools import native_frontend_system_prompt, plan_native_tools
from app.agui.contracts import load_contract_registry
from app.agui.models import validate_native_frontend_tools
from app.runtime.contracts import RuntimeFrontendTool

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = load_contract_registry(ROOT / "contracts" / "davinci-agent-v2.json")
EDITOR_ACTIONS = {
    "dataset.editor.get_context",
    "dataset.editor.get_source_fields",
    "dataset.editor.get_join_candidates",
    "dataset.editor.apply_draft",
    "dataset.editor.validate",
    "dataset.editor.preview",
    "dataset.editor.save_draft",
    "dataset.editor.close",
}


def draft_input() -> dict:
    """Return one valid field-backed replacement with an ordinary filter."""
    return {
        "expectedDraftRevision": "draft-8",
        "mainDatasetRef": "warehouseTable:orders",
        "sources": [
            {
                "datasetRef": "warehouseTable:orders",
                "fieldIds": ["order_id", "region"],
                "filters": [{"fieldId": "region", "operator": "in", "value": ["east"]}],
            }
        ],
        "relations": [],
        "metadata": {"name": "订单", "description": "", "businessSystem": "回收"},
    }


def test_editor_catalog_stays_frontend_only_with_legacy_save_inactive() -> None:
    """All draft actions execute against the page, with no Host save facade."""
    contracts = [REGISTRY.get(action) for action in sorted(EDITOR_ACTIONS)]
    assert {
        item.action
        for item in REGISTRY.public_contracts
        if item.bundle == "dataset-editor"
    } == EDITOR_ACTIONS
    assert all(item.executor == "frontend" for item in contracts)
    assert all(item.version == "2.0" for item in contracts)
    for action in (
        "dataset.editor.save",
        "dataset.editor.export_draft",
        "dataset.editor.apply_draft_impact",
        "dataset.editor.apply_close",
    ):
        assert not REGISTRY.get(action).public

    tools = [to_ag_ui_tool(item) for item in contracts]
    assert validate_native_frontend_tools(tools) == tuple(tools)
    plan = plan_native_tools(
        [
            RuntimeFrontendTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
            )
            for tool in tools
        ]
    )
    assert {item.name for item in plan.resident} == EDITOR_ACTIONS
    for contract in contracts:
        Draft202012Validator.check_schema(dict(contract.input_schema))
        Draft202012Validator.check_schema(dict(contract.output_schema))


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.pop("expectedDraftRevision"),
        lambda value: value["sources"][0]["filters"][0].pop("operator"),
        lambda value: value.update(sources=[]),
        lambda value: value["sources"][0].update(fieldIds=["field"] * 101),
        lambda value: value["sources"][0].update(sql="select * from private"),
        lambda value: value["sources"][0]["filters"][0].update(value={"sql": "x"}),
        lambda value: value.update(
            relations=[
                {
                    "auxDatasetRef": "warehouseTable:other",
                    "joinType": "right",
                    "conditions": [{"mainFieldId": "id", "auxFieldId": "id"}],
                }
            ]
        ),
    ],
)
def test_atomic_draft_rejects_unbounded_or_native_internal_input(change) -> None:
    """A replacement must be revision-bound and limited to public typed data."""
    validator = Draft202012Validator(
        REGISTRY.get("dataset.editor.apply_draft").input_schema
    )
    payload = draft_input()
    validator.validate(payload)
    change(payload)
    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_context_can_round_trip_all_selected_fields_without_destructive_loss() -> None:
    """The read and replacement contracts agree on complete source/filter data."""
    payload = draft_input()
    context = {
        "draftRevision": payload["expectedDraftRevision"],
        "mainDatasetRef": payload["mainDatasetRef"],
        "metadata": payload["metadata"],
        "sources": [{**source, "name": "订单表"} for source in payload["sources"]],
        "relations": [],
        "validation": {"valid": True, "issues": []},
        "dirty": True,
        "summary": "1 个来源，2 个字段",
    }
    for action in ("get_context", "validate", "apply_draft"):
        Draft202012Validator(
            REGISTRY.get(f"dataset.editor.{action}").output_schema
        ).validate(context)
    replacement = deepcopy(context)
    for source in replacement["sources"]:
        source.pop("name")
    replacement = {
        key: replacement[key]
        for key in ("mainDatasetRef", "metadata", "sources", "relations")
    }
    replacement["expectedDraftRevision"] = context["draftRevision"]
    assert replacement == payload


def test_source_fields_preserves_required_rules_without_enum_sql() -> None:
    """A field page carries join and query-variable rules, never private SQL."""
    contract = REGISTRY.get("dataset.editor.get_source_fields")
    payload = {
        "datasetRef": "widget:orders",
        "name": "订单",
        "fields": [
            {
                "fieldId": "order_id",
                "name": "订单编号",
                "fieldType": "dimension",
                "dataType": "varchar",
                "requiredCondition": True,
                "dimensionIdList": [1, 2],
            }
        ],
        "queryVars": [
            {
                "varName": "region",
                "dataType": "varchar",
                "required": True,
                "locked": True,
                "hasDefault": True,
                "defaultValue": "east",
            }
        ],
        "total": 2,
        "offset": 0,
        "limit": 1,
        "nextOffset": 1,
        "summary": "字段 1/2",
    }
    validator = Draft202012Validator(contract.output_schema)
    validator.validate(payload)
    payload["fields"][0]["enumValueSql"] = "private query"
    with pytest.raises(ValidationError):
        validator.validate(payload)
    for arguments in (
        {"datasetRef": "widget:orders", "offset": -1},
        {"datasetRef": "widget:orders", "limit": 101},
    ):
        with pytest.raises(ValidationError):
            Draft202012Validator(contract.input_schema).validate(arguments)


def test_context_preserves_incomplete_native_drafts() -> None:
    """An empty editor and unfinished joins remain readable for later repair."""
    validator = Draft202012Validator(
        REGISTRY.get("dataset.editor.get_context").output_schema
    )
    context = {
        "draftRevision": "draft-0",
        "metadata": {"name": "", "description": "", "businessSystem": ""},
        "mainDatasetRef": None,
        "sources": [],
        "relations": [],
        "validation": {"valid": False, "issues": ["请选择主表"]},
        "dirty": False,
        "summary": "空白编辑器",
    }
    validator.validate(context)
    context["sources"] = [
        {
            "datasetRef": "warehouseTable:main",
            "name": "主表",
            "fieldIds": [],
            "filters": [{"fieldId": "", "value": []}],
        }
    ]
    validator.validate(context)
    context["relations"] = [
        {
            "auxDatasetRef": "warehouseTable:aux",
            "joinType": "left",
            "conditions": [],
        }
    ]
    validator.validate(context)
    replacement = draft_input()
    replacement["relations"] = context["relations"]
    with pytest.raises(ValidationError):
        Draft202012Validator(
            REGISTRY.get("dataset.editor.apply_draft").input_schema
        ).validate(replacement)


def test_native_save_receipt_cannot_claim_persistence() -> None:
    """Opening native confirmation must never validate as a saved receipt."""
    contract = REGISTRY.get("dataset.editor.save_draft")
    assert contract.risk == "persistent"
    assert contract.context_effect == "mutates"
    validator = Draft202012Validator(contract.output_schema)
    validator.validate({"status": "opened", "summary": "请在原生窗口确认保存"})
    for result in (
        {"status": "saved", "summary": "已保存"},
        {"status": "opened", "summary": "已保存", "saved": True},
    ):
        with pytest.raises(ValidationError):
            validator.validate(result)
    prompt = native_frontend_system_prompt()
    assert "atomically replaces the complete draft" in prompt
    assert "status opened is never evidence of a saved dataset" in prompt


def test_native_editor_preparation_gets_a_two_minute_contract_deadline() -> None:
    """Serial native metadata and relation calculations need the full budget."""
    for action in (
        "apply_draft",
        "validate",
        "preview",
        "save_draft",
        "get_join_candidates",
    ):
        assert REGISTRY.get(f"dataset.editor.{action}").timeout_ms == 120_000


def test_join_candidates_preserves_backend_pairs_with_bounded_strict_schemas() -> None:
    """Only field-backed common-dimension results can guide a later draft join."""
    contract = REGISTRY.get("dataset.editor.get_join_candidates")
    assert contract.risk == "read"
    assert contract.context_effect == "none"
    assert contract.executor == "frontend"
    request = {
        "mainDatasetRef": "warehouseTable:main",
        "auxDatasetRef": "warehouseTopic:aux",
        "mainFieldIds": ["customer_id"],
        "mainFilters": [],
        "auxFilters": [],
    }
    validator = Draft202012Validator(contract.input_schema)
    validator.validate(request)
    for changed in ({"mainFieldIds": []}, {"mainFieldIds": ["id"] * 101}):
        with pytest.raises(ValidationError):
            validator.validate({**request, **changed})
    pair = {
        "mainField": {"fieldId": "customer_id", "fieldName": "客户编号"},
        "auxField": {"fieldId": "id", "fieldName": "客户编号"},
    }
    result = {
        "mainDatasetRef": request["mainDatasetRef"],
        "auxDatasetRef": request["auxDatasetRef"],
        "uniquePairs": [pair],
        "ambiguousGroups": [
            {
                "groupKey": "region",
                "groupName": "区域",
                "mainFields": [{"fieldId": "region", "fieldName": "区域"}],
                "auxFields": [{"fieldId": "customer_region", "fieldName": "客户区域"}],
            }
        ],
        "truncated": False,
        "summary": "1 个确定配对和 1 个待选分组",
    }
    output = Draft202012Validator(contract.output_schema)
    output.validate(result)
    with pytest.raises(ValidationError):
        output.validate({**result, "uniquePairs": [pair] * 101})
    with pytest.raises(ValidationError):
        output.validate({**result, "sql": "private query"})
