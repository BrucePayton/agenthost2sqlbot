from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema import validate as validate_json_schema
from jsonschema.exceptions import ValidationError

PUBLIC_ACTIONS = {
    "page.get_context",
    "workspace.list_dashboards",
    "ui.open_dashboard",
    "ui.open_dataset_marketplace",
    "dashboard.get_structure",
    "dashboard.get_filters",
    "dashboard.get_widget_config",
    "dashboard.capture_current_view",
    "dashboard.get_widget_data",
    "dashboard.get_errors",
    "dashboard.focus_widget",
    "dashboard.open_widget_config",
    "dashboard.close_widget_config",
    "dashboard.open_filter_panel",
    "dashboard.set_filter_values",
    "dashboard.clear_filter_values",
    "dashboard.refresh_all",
    "dashboard.refresh_widget",
    "dashboard.enter_fullscreen",
    "dashboard.exit_fullscreen",
    "ui.open_share_panel",
    "dashboard.open_data_push",
    "dashboard.add_widget",
    "dashboard.copy_widget",
    "dashboard.rename_widget",
    "dashboard.set_widget_layout",
    "dataset.marketplace.get_context",
    "dataset.marketplace.search",
    "dataset.marketplace.set_filters",
    "dataset.marketplace.reset_filters",
    "dataset.marketplace.refresh",
    "dataset.marketplace.set_selection",
    "ui.open_related_dashboard",
    "dataset.editor.open",
}

HOST_ACTIONS = {
    "dashboard.get_widget_data",
}

INTERNAL_ACTIONS = {
    "dataset.editor.export_draft",
    "dataset.editor.apply_draft_impact",
    "dataset.editor.apply_close",
    "resource.reload",
    "dashboard.set_widget_chart_type",
    "dashboard.set_widget_fields",
    "dashboard.set_widget_query_filters",
    "dashboard.set_widget_sort",
    "dashboard.set_widget_style",
    "dataset.editor.add_sources",
    "dataset.editor.close",
    "dataset.editor.get_context",
    "dataset.editor.get_join_candidates",
    "dataset.editor.open_source_config",
    "dataset.editor.preview",
    "dataset.editor.remove_source",
    "dataset.editor.save",
    "dataset.editor.search_sources",
    "dataset.editor.set_main_source",
    "dataset.editor.set_metadata",
    "dataset.editor.set_relation_config",
    "dataset.editor.set_table_config",
    "dataset.editor.validate",
    "dataset.get_detail",
    "dataset.get_fields",
    "dataset.get_related_dashboards",
}

BANNED_INPUT_FIELDS = {
    "obId",
    "actor",
    "userId",
    "token",
    "cookie",
    "idempotencyKey",
    "commandId",
}

MESSAGE_RULE_ACTIONS = {
    "space.message_rule.get_context",
    "space.message_rule.search_options",
    "space.message_rule.start_draft",
    "space.message_rule.apply_draft",
    "space.message_rule.review_draft",
    "space.message_rule.save_draft",
}

SUBSCRIPTION_BANNED_INPUT_FIELDS = {
    "spaceId",
    "route",
    "identity",
    "recipientId",
    "employeeId",
    "oaId",
    "obId",
    "draft",
    "draftRef",
}


def _assert_strict_objects(schema: Mapping[str, object]) -> None:
    if schema.get("type") == "object":
        assert schema.get("additionalProperties") is False
        properties = schema.get("properties", {})
        assert isinstance(properties, Mapping)
        for child in properties.values():
            if isinstance(child, Mapping):
                _assert_strict_objects(child)
    items = schema.get("items")
    if isinstance(items, Mapping):
        _assert_strict_objects(items)


def _walk_schema_nodes(schema: object):
    """Yield every nested JSON-schema node, including oneOf branches."""
    if isinstance(schema, Mapping):
        yield schema
        for child in schema.values():
            yield from _walk_schema_nodes(child)
    elif isinstance(schema, list):
        for child in schema:
            yield from _walk_schema_nodes(child)


def test_registry_has_exact_v01_actions_and_executor_map() -> None:
    from app.agui.contracts import load_contract_registry

    registry = load_contract_registry()

    assert set(registry.public_actions) == PUBLIC_ACTIONS
    assert set(registry.host_actions) == HOST_ACTIONS
    assert set(registry.internal_actions) == INTERNAL_ACTIONS
    assert len(registry.public_actions) == 34


def test_dashboard_listing_is_a_frontend_tool_with_optional_search() -> None:
    from app.agui.contracts import load_contract_registry

    contract = load_contract_registry().get("workspace.list_dashboards")

    assert contract.executor == "frontend"
    assert contract.input_schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 200},
        },
        "additionalProperties": False,
    }


def test_tool_timeout_is_loaded_from_canonical_contract_metadata() -> None:
    from app.agui.contracts import load_contract_registry

    registry = load_contract_registry()

    assert registry.get("page.get_context").timeout_ms == 15_000
    assert registry.get("dashboard.capture_current_view").timeout_ms == 75_000


def test_widget_data_contract_returns_structured_bounded_rows() -> None:
    from app.agui.contracts import load_contract_registry

    schema = load_contract_registry().get("dashboard.get_widget_data").output_schema

    assert schema["properties"]["fields"]["maxItems"] == 50
    assert schema["properties"]["rows"]["maxItems"] == 100
    assert schema["required"] == [
        "summary",
        "widgetId",
        "title",
        "fields",
        "rows",
        "offset",
        "returnedRows",
    ]


@pytest.mark.parametrize("timeout_ms", [True, 999, 120_001])
def test_contract_rejects_invalid_tool_timeout_metadata(
    tmp_path, timeout_ms
) -> None:
    from app.agui.contracts import ContractValidationError, load_contract_registry

    registry = load_contract_registry()
    payload = registry.raw_contract.copy()
    payload["tools"] = [dict(tool) for tool in payload["tools"]]
    payload["tools"][0]["timeoutMs"] = timeout_ms
    invalid_timeout = tmp_path / "invalid-timeout.json"
    invalid_timeout.write_text(
        registry.dumps_contract(payload), encoding="utf-8"
    )

    with pytest.raises(ContractValidationError, match="invalid timeoutMs"):
        load_contract_registry(invalid_timeout)


def test_contracts_use_strict_bounded_schemas_without_identity_controls() -> None:
    from app.agui.contracts import load_contract_registry

    registry = load_contract_registry()

    for contract in registry.contracts_by_action.values():
        assert contract.version == "1.0"
        assert contract.context_effect in {"none", "mutates"}
        assert contract.input_schema_id.endswith(contract.action)
        assert contract.output_schema_id.endswith(contract.action)
        _assert_strict_objects(contract.input_schema)
        _assert_strict_objects(contract.output_schema)
        input_properties = contract.input_schema.get("properties", {})
        assert BANNED_INPUT_FIELDS.isdisjoint(input_properties)
        for schema in (contract.input_schema, contract.output_schema):
            if schema.get("type") == "array":
                assert "maxItems" in schema


def test_contract_rejects_duplicate_actions_and_sdk_names(tmp_path) -> None:
    from app.agui.contracts import ContractValidationError, load_contract_registry

    registry = load_contract_registry()
    payload = registry.raw_contract.copy()
    payload["tools"] = [*payload["tools"], payload["tools"][0]]
    duplicate_action = tmp_path / "duplicate-action.json"
    duplicate_action.write_text(registry.dumps_contract(payload), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="duplicate action"):
        load_contract_registry(duplicate_action)

    payload = registry.raw_contract.copy()
    payload["tools"] = [dict(tool) for tool in payload["tools"]]
    payload["tools"][1]["sdkToolName"] = payload["tools"][0]["sdkToolName"]
    duplicate_sdk = tmp_path / "duplicate-sdk.json"
    duplicate_sdk.write_text(registry.dumps_contract(payload), encoding="utf-8")

    with pytest.raises(ContractValidationError, match="duplicate sdkToolName"):
        load_contract_registry(duplicate_sdk)


def test_error_registry_is_canonical_and_has_typed_context_details() -> None:
    from app.agui.contracts import load_contract_registry

    errors = load_contract_registry().error_schemas

    assert "CONTEXT_STALE" not in errors
    assert errors["STALE_CONTEXT"] == {
        "type": "object",
        "properties": {
            "expectedContextVersion": {"type": "integer", "minimum": 0},
            "actualContextVersion": {"type": "integer", "minimum": 0},
        },
        "required": ["expectedContextVersion", "actualContextVersion"],
        "additionalProperties": False,
    }
    assert errors["TOOL_NOT_READY"]["required"] == [
        "action",
        "expectedRoute",
        "currentRoute",
    ]
    _assert_strict_objects(errors["TOOL_NOT_READY"])


def test_strict_schema_validation_reaches_composed_object_branches() -> None:
    from app.agui.contracts import ContractValidationError, _validate_strict_schema

    with pytest.raises(ContractValidationError, match="must forbid"):
        _validate_strict_schema(
            {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"operation": {"const": "invite"}},
                    }
                ]
            },
            label="test.input",
        )

    with pytest.raises(ContractValidationError, match="must forbid"):
        _validate_strict_schema(
            {
                "if": {"required": ["enabled"]},
                "then": {
                    "properties": {
                        "nested": {
                            "type": "object",
                            "properties": {},
                        }
                    }
                },
            },
            label="test.input",
        )


def test_generated_javascript_matches_canonical_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    contract_path = root / "contracts" / "davinci-agent-v1.json"
    generated_path = root / "web" / "shared" / "generated" / "davinci-contracts.js"
    digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()

    generated = generated_path.read_text(encoding="utf-8")
    assert f"export const CONTRACT_DIGEST = '{digest}'" in generated
    assert "export const PUBLIC_TOOL_CONTRACTS = Object.freeze(" in generated
    assert "export const ERROR_SCHEMAS = Object.freeze(" in generated

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate-davinci-contracts.py"),
            "--check",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_served_embed_bundle_uses_current_contract_digests() -> None:
    """Catch stale committed browser bundles that silently reject bridge HELLO."""
    root = Path(__file__).resolve().parents[1]
    expected = {
        hashlib.sha256(
            (root / "contracts" / f"davinci-agent-{profile}.json").read_bytes()
        ).hexdigest()
        for profile in ("v1", "v2")
    }
    bundle = (root / "app" / "web" / "static" / "embed.js").read_text()
    actual = set(re.findall(
        r"(?:var|const)\s+CONTRACT_DIGEST\w*\s*=\s*['\"]([0-9a-f]{64})['\"]",
        bundle,
    ))
    assert actual == expected, (
        "The served embed.js has stale bridge contracts. "
        "Run npm run build:agui and commit the generated browser bundles."
    )


def test_canonical_contract_is_deterministic_json() -> None:
    root = Path(__file__).resolve().parents[1]
    contract_path = root / "contracts" / "davinci-agent-v1.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))

    assert payload["contractVersion"] == "1.0"
    assert payload["protocolVersion"] == "1.0"
    assert len(payload["tools"]) == 60


def test_v2_contract_uses_direct_frontend_widget_data_without_changing_v1() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v1 = load_contract_registry()
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    assert v1.contract_version == "1.0"
    assert v1.get("dashboard.get_widget_data").executor == "host"
    assert v2.contract_version == "2.0"
    assert v2.protocol_version == "agui-native-v2"
    widget_data = v2.get("dashboard.get_widget_data")
    assert widget_data.executor == "frontend"
    assert set(widget_data.input_schema["properties"]) == {
        "widgetIds",
        "maxRows",
        "includeMetadata",
        "waitUntilSettled",
        "timeoutMs",
    }
    assert widget_data.input_schema["properties"]["timeoutMs"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 60000,
    }
    assert widget_data.timeout_ms == 65000
    widget_state = widget_data.output_schema["properties"]["data"]["properties"][
        "widgets"
    ]["items"]["properties"]["state"]
    assert widget_state["enum"] == [
        "ready",
        "empty",
        "loading",
        "error",
        "unavailable",
        "missing",
    ]
    assert widget_data.output_schema["required"] == [
        "status",
        "issues",
        "pagination",
    ]

    refresh = v2.get("dashboard.refresh_all")
    assert refresh.executor == "frontend"
    assert set(refresh.input_schema["properties"]) == {
        "waitUntilSettled",
        "timeoutMs",
    }
    assert "required" not in refresh.input_schema
    assert refresh.timeout_ms == 65000
    refresh_data = refresh.output_schema["properties"]["data"]["properties"]
    assert set(refresh_data) == {
        "resourceId",
        "readyWidgetIds",
        "emptyWidgetIds",
        "loadingWidgetIds",
        "errorWidgetIds",
        "unavailableWidgetIds",
        "missingWidgetIds",
    }

    refresh_widget = v2.get("dashboard.refresh_widget")
    assert refresh_widget.executor == "frontend"
    assert refresh_widget.version == "2.0"
    assert set(refresh_widget.input_schema["properties"]) == {
        "widgetId",
        "waitUntilSettled",
        "timeoutMs",
    }
    assert refresh_widget.input_schema["required"] == ["widgetId"]
    assert refresh_widget.output_schema == refresh.output_schema
    assert refresh_widget.timeout_ms == 65000


def test_v2_contract_adds_exact_collaborative_space_tool_metadata() -> None:
    """Keep P0 collaboration tools bounded to the reviewed public contract."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v1_path = root / "contracts" / "davinci-agent-v1.json"
    v1 = load_contract_registry(v1_path)
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    assert hashlib.sha256(v1_path.read_bytes()).hexdigest() == (
        "239675ff938d4542e83933e0435ca03a3ef3014d51c7f4a507360a92051e6e8a"
    )
    assert {
        "ui.open_space_page",
        "ui.open_personal_workspace",
        "space.message_rule.get_context",
        "dashboard.open_data_alert_config",
    }.isdisjoint(v1.contracts_by_action)
    v2_payload = json.loads(
        (root / "contracts" / "davinci-agent-v2.json").read_text(encoding="utf-8")
    )
    assert v2_payload["schemaTemplates"]["widgetEditCapabilitiesEnvelope"][
        "properties"
    ]["data"]["properties"]["creationOptions"]["items"]["properties"][
        "category"
    ]["enum"] == ["table", "chart", "other", "layout", "message", "task"]
    assert (
        len(v2.contracts_by_action),
        len(v2.public_actions),
        len(
            [contract for contract in v2.public_contracts if contract.executor == "frontend"]
        ),
        len(v2.host_actions),
        len(v2.internal_actions),
    ) == (103, 68, 68, 0, 35)
    assert len(
        [
            contract
            for contract in v2.contracts_by_action.values()
            if contract.executor == "frontend"
        ]
    ) == 99
    public_marketplace_actions = {
        "dataset.marketplace.get_context",
        "dataset.marketplace.search",
        "dataset.marketplace.get_detail",
        "dataset.marketplace.get_join_relations",
        "ui.open_related_dashboard",
        "dataset.editor.open",
    }
    internal_marketplace_actions = {
        "dataset.marketplace.set_filters",
        "dataset.marketplace.reset_filters",
        "dataset.marketplace.refresh",
        "dataset.marketplace.set_selection",
        "dataset.get_fields",
        "dataset.get_detail",
        "dataset.get_related_dashboards",
    }
    assert len(public_marketplace_actions) == 6
    assert len(internal_marketplace_actions) == 7
    assert public_marketplace_actions.issubset(v2.public_actions)
    assert internal_marketplace_actions.isdisjoint(v2.public_actions)
    assert internal_marketplace_actions.issubset(v2.internal_actions)
    search = v2.get("dataset.marketplace.search")
    assert search.risk == "read"
    assert search.context_effect == "none"
    assert "required" not in search.input_schema
    assert set(search.input_schema["properties"]) == {
        "query",
        "scope",
        "datasetTypes",
        "bizSystems",
        "page",
        "pageSize",
    }
    search_result_dataset_type = search.output_schema["properties"]["results"][
        "items"
    ]["properties"]["datasetType"]
    assert "enum" not in search_result_dataset_type
    assert "selfBuiltDataset" in search_result_dataset_type["description"]
    get_detail = v2.get("dataset.marketplace.get_detail")
    assert get_detail.executor == "frontend"
    assert get_detail.risk == "read"
    assert get_detail.context_effect == "none"
    assert set(get_detail.output_schema["required"]) == {
        "datasetRef",
        "name",
        "datasetType",
        "canEdit",
    }
    detail_dataset_type = get_detail.output_schema["properties"]["datasetType"]
    assert "enum" not in detail_dataset_type
    assert "selfBuiltDataset" in detail_dataset_type["description"]
    editor_open = v2.get("dataset.editor.open")
    assert "datasetRef" in editor_open.input_schema["oneOf"][0]["properties"]
    assert editor_open.input_schema["oneOf"][0]["required"] == ["mode"]
    assert editor_open.input_schema["oneOf"][1]["required"] == [
        "mode",
        "datasetRef",
    ]
    assert "selfBuiltDataset" in editor_open.description
    related_dashboard_dataset_type = v1.get(
        "ui.open_related_dashboard"
    ).input_schema["properties"]["datasetType"]
    assert "enum" not in related_dashboard_dataset_type
    d1_demoted_actions = {
        "page.get_context",
        "dashboard.set_widget_dataset",
        "dashboard.open_data_push",
    }
    assert d1_demoted_actions.isdisjoint(v2.public_actions)
    assert d1_demoted_actions.issubset(v2.internal_actions)

    expected = {
        "ui.open_space_page": {
            "version": "2.0",
            "executor": "frontend",
            "risk": "temporary",
            "context_effect": "mutates",
            "bundle": "core-navigation",
            "sdk_tool_name": "ui__open_space_page",
            "description": "Open the collaborative-space home page. This only navigates; it does not create, update, delete, transfer or manage space members.",
            "timeout_ms": 15_000,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["executed", "opened", "refreshed", "focused", "closed"],
                    },
                    "contextVersion": {"type": "integer", "minimum": 0},
                    "summary": {"type": "string", "maxLength": 1000},
                },
                "required": ["status", "contextVersion"],
                "additionalProperties": False,
            },
        },
        "ui.open_personal_workspace": {
            "version": "2.0",
            "executor": "frontend",
            "risk": "temporary",
            "context_effect": "mutates",
            "bundle": "core-navigation",
            "sdk_tool_name": "ui__open_personal_workspace",
            "description": "Open the current user's personal workspace home. This leaves collaborative-space scope and accepts no route or resource arguments.",
            "timeout_ms": 15_000,
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["executed", "opened", "refreshed", "focused", "closed"],
                    },
                    "contextVersion": {"type": "integer", "minimum": 0},
                    "summary": {"type": "string", "maxLength": 1000},
                },
                "required": ["status", "contextVersion"],
                "additionalProperties": False,
            },
        },
        "dashboard.open_data_alert_config": {
            "version": "2.0",
            "executor": "frontend",
            "risk": "temporary",
            "context_effect": "mutates",
            "bundle": "dashboard-runtime",
            "sdk_tool_name": "dashboard__open_data_alert_config",
            "description": "Open a data-alert draft for one eligible widget (ids listed in the dashboard_structure context item) using the same eligibility and template workflow as the alert bell. This does not persist a rule.",
            "timeout_ms": 30_000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "widgetId": {"type": "string", "minLength": 1, "maxLength": 100}
                },
                "required": ["widgetId"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["executed", "opened", "refreshed", "focused", "closed"],
                    },
                    "contextVersion": {"type": "integer", "minimum": 0},
                    "summary": {"type": "string", "maxLength": 1000},
                },
                "required": ["status", "contextVersion"],
                "additionalProperties": False,
            },
        },
    }

    for action, metadata in expected.items():
        contract = v2.get(action)
        assert {
            "version": contract.version,
            "executor": contract.executor,
            "risk": contract.risk,
            "context_effect": contract.context_effect,
            "bundle": contract.bundle,
            "sdk_tool_name": contract.sdk_tool_name,
            "description": contract.description,
            "timeout_ms": contract.timeout_ms,
            "input_schema": dict(contract.input_schema),
            "output_schema": dict(contract.output_schema),
        } == metadata
        assert {
            "spaceId",
            "url",
            "identity",
            "recipient",
            "route",
            "draft",
            "draftRef",
        }.isdisjoint(contract.input_schema["properties"])


def test_v2_contract_defines_strict_subscription_draft_suite() -> None:
    """Keep the complete subscription suite cold, opaque and create-only."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    assert MESSAGE_RULE_ACTIONS.issubset(registry.public_actions)
    assert {
        registry.get(action).executor for action in MESSAGE_RULE_ACTIONS
    } == {"frontend"}
    assert {
        registry.get(action).bundle for action in MESSAGE_RULE_ACTIONS
    } == {"space-message-rule"}

    expected_metadata = {
        "space.message_rule.get_context": ("read", "none", 15_000),
        "space.message_rule.search_options": ("read", "none", 15_000),
        "space.message_rule.start_draft": ("draft", "mutates", 60_000),
        "space.message_rule.apply_draft": ("draft", "mutates", 60_000),
        "space.message_rule.review_draft": ("read", "none", 15_000),
        "space.message_rule.save_draft": ("persistent", "mutates", 120_000),
    }
    for action, metadata in expected_metadata.items():
        contract = registry.get(action)
        assert (contract.risk, contract.context_effect, contract.timeout_ms) == metadata

        for schema in (contract.input_schema, contract.output_schema):
            for node in _walk_schema_nodes(schema):
                if node.get("type") == "object":
                    assert node.get("additionalProperties") is False

        input_field_names = {
            name
            for node in _walk_schema_nodes(contract.input_schema)
            for name in (
                node.get("properties", {}).keys()
                if isinstance(node.get("properties"), Mapping)
                else ()
            )
        }
        assert SUBSCRIPTION_BANNED_INPUT_FIELDS.isdisjoint(input_field_names)
        assert "url" not in contract.input_schema.get("properties", {})
        for node in _walk_schema_nodes(contract.input_schema):
            properties = node.get("properties", {})
            if "url" in properties:
                # Content links are data, never a navigation/identity override.
                assert "actionType" in properties or properties.get("type", {}).get("const") == "link"
                assert re.match(properties["url"]["pattern"], "https://example.com/detail")
                assert not re.match(properties["url"]["pattern"], "javascript:alert(1)")

    search = registry.get("space.message_rule.search_options").input_schema
    assert [
        branch["properties"]["kind"]["const"] for branch in search["oneOf"]
    ] == [
        "template",
        "dataset",
        "field",
        "enum_value",
        "recipient_member",
        "recipient_group",
        "dashboard",
        "widget",
        "tag",
        "alert_widget",
    ]

    start = registry.get("space.message_rule.start_draft").input_schema
    assert [
        branch["properties"]["mode"]["const"] for branch in start["oneOf"]
    ] == ["blank", "template", "widget"]

    apply_schema = registry.get("space.message_rule.apply_draft").input_schema
    operations = apply_schema["properties"]["operations"]
    assert (operations["minItems"], operations["maxItems"]) == (1, 32)
    assert [
        branch["properties"]["operation"]["const"]
        for branch in operations["items"]["oneOf"]
    ] == [
        "set_schedule",
        "set_send_rule",
        "upsert_dataset_query",
        "remove_dataset_query",
        "set_trigger_conditions",
        "set_push_mode",
        "set_recipients",
        "set_content",
        "set_finalize",
        "bind_dataset_query",
        "patch_content",
        "import_widget_source",
    ]

    save = registry.get("space.message_rule.save_draft").input_schema
    assert save["properties"]["desiredStatus"] == {
        "type": "string",
        "enum": ["disabled", "running"],
    }
    assert save["required"] == ["expectedRevision", "desiredStatus"]


def test_subscription_acceptance_operations_and_receipts_match_contract() -> None:
    """Validate representative domain requests, not just tool-name routing."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts/davinci-agent-v2.json")
    fixture = json.loads((root / "workspaces/davinci-dashboard/.claude/skills/"
                         "configure-subscription-rule/evals/cases.json").read_text())
    apply = registry.get("space.message_rule.apply_draft").input_schema
    for case in fixture["cases"]:
        if "navigation" in case:
            validate_json_schema(case["navigation"], dict(registry.get("space.open").input_schema))
        for index, batch in enumerate(case.get("batches", [])):
            validate_json_schema({"expectedRevision": index + 1, "operations": batch}, dict(apply))
        if "save" in case:
            validate_json_schema(case["save"], registry.get(
                "space.message_rule.save_draft").input_schema.copy())
    context = registry.get("space.message_rule.get_context")
    for request in ({}, {"includeRules": True}, {"query": "日报"}):
        validate_json_schema(request, dict(context.input_schema))
    validate_json_schema({"summary": "当前个人草稿", "contextVersion": 1,
                          "rulesIncluded": False, "scope": {
                              "kind": "personal", "recipientPolicy": "self-only",
                              "sourceLocation": "space", "canConfigure": True,
                              "canSave": True}}, dict(context.output_schema))
    save = registry.get("space.message_rule.save_draft")
    receipt = {"status": "success", "persisted": True, "ruleRef": "rule:123",
               "finalStatus": "disabled", "ownershipScope": "personal",
               "summary": "已保存停用规则", "contextVersion": 2}
    validate_json_schema(receipt, dict(save.output_schema))
    for wrong in ({**receipt, "finalStatus": "enabled"},
                  {key: value for key, value in receipt.items() if key != "ruleRef"}):
        with pytest.raises(ValidationError):
            validate_json_schema(wrong, dict(save.output_schema))


def test_subscription_skill_native_examples_match_documented_calls_and_real_schema() -> None:
    """Examples are executable native inputs, never a second configure language."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    skill = root / "workspaces/davinci-dashboard/.claude/skills/configure-subscription-rule"
    fixtures = json.loads((skill / "evals/cases.json").read_text())["nativeExamples"]
    documented = [json.loads(block) for block in re.findall(r"```json\s*\n(.*?)\n```", (
        skill / "references/examples.md").read_text(), re.S)]
    assert len(fixtures) == 6 and documented == [case["calls"] for case in fixtures]
    registry = load_contract_registry(root / "contracts/davinci-agent-v2.json")
    allowed = {"space.message_rule." + name for name in (
        "get_context", "search_options", "start_draft", "apply_draft", "review_draft")}
    for case in fixtures:
        assert case["request"] and case["evidence"]
        for call in case["calls"]:
            assert call["action"] in allowed
            assert "configurationIntent" not in call["arguments"]
            assert not call["arguments"].get("includeDataCheck")
            validate_json_schema(call["arguments"], dict(registry.get(call["action"]).input_schema))


def test_subscription_context_is_read_only_and_output_alias_requires_query_alias() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts/davinci-agent-v2.json")
    context = dict(registry.get("space.message_rule.get_context").input_schema)
    validate_json_schema({}, context)
    with pytest.raises(ValidationError):
        validate_json_schema({"configurationIntent": {"scenario": "data"}}, context)
    apply = dict(registry.get("space.message_rule.apply_draft").input_schema)
    for name in ("upsert_dataset_query", "bind_dataset_query"):
        operation = {"operation": name,
            "datasetRef" if name == "upsert_dataset_query" else "catalogDatasetRef": "widget:orders",
            "metrics": [{"fieldRef": "widget:orders/amount", "outputKey": "amount"}]}
        with pytest.raises(ValidationError):
            validate_json_schema({"expectedRevision": 1, "operations": [operation]}, apply)
        validate_json_schema({"expectedRevision": 1, "operations": [{**operation, "queryKey": "orders"}]}, apply)
        operation["metrics"][0].pop("outputKey")
        validate_json_schema({"expectedRevision": 1, "operations": [operation]}, apply)


def test_subscription_executable_search_and_initial_draft_contracts() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts/davinci-agent-v2.json")
    search = dict(registry.get("space.message_rule.search_options").input_schema)
    for request in ({"kind": "dashboard", "offset": 20},
                    {"kind": "field", "datasetRef": "d", "role": "dimension", "isEmployeeAccount": True, "offset": 20}):
        validate_json_schema(request, search)
    for request in ({"kind": "dataset", "offset": 20},
                    {"kind": "dashboard", "role": "metric"},
                    {"kind": "field", "datasetRef": "d", "offset": -1}):
        with pytest.raises(ValidationError):
            validate_json_schema(request, search)
    start = dict(registry.get("space.message_rule.start_draft").input_schema)
    validate_json_schema({"mode": "blank", "operations": [
        {"operation": "set_schedule", "frequency": "daily", "times": ["09:00"]},
        {"operation": "set_send_rule", "sendRule": "conditional"}
    ]}, start)
    for request in ({"mode": "blank"}, {"mode": "template", "templateRef": "template-1"}):
        validate_json_schema({**request, "operations": [
            {"operation": "set_finalize", "ruleName": "昨日订单预警"}
        ]}, start)
    with pytest.raises(ValidationError):
        validate_json_schema({"mode": "blank", "operations": [
            {"operation": "set_push_mode", "mode": "record"}
        ]}, start)
    apply = dict(registry.get("space.message_rule.apply_draft").input_schema)
    for expression in ("yesterday", "last_week"):
        with pytest.raises(ValidationError):
            validate_json_schema({"expectedRevision": 1, "operations": [{
                "operation": "upsert_dataset_query", "datasetRef": "d", "dimensionRefs": ["f"],
                "filters": [{"fieldRef": "f", "operator": "eq", "valueExp": expression, "values": []}]
            }]}, apply)


def test_space_subscription_navigation_uses_an_opaque_reference_and_closed_destination():
    """The native target extends space.open, never raw URL/identity injection."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    contract = load_contract_registry(root / "contracts/davinci-agent-v2.json").get("space.open")
    for request in ({"spaceRef": "space:1:9"},
                    {"spaceRef": "space:1:9", "destination": "subscription"}):
        validate_json_schema(request, dict(contract.input_schema))
    for request in ({"spaceRef": "s", "destination": "/share/another"},
                    {"spaceId": "123", "destination": "subscription"},
                    {"spaceRef": "s", "destination": "subscription", "actor": "other"}):
        with pytest.raises(ValidationError):
            validate_json_schema(request, dict(contract.input_schema))


@pytest.mark.parametrize("operation", [
    {"operation": "set_schedule", "frequency": "hourly", "hourlyMinute": 7},
    {"operation": "set_trigger_conditions", "queryRef": "q", "hitRecordLimit": 1001,
     "conditions": [{"outputRef": "f", "operator": "gt", "values": [1]}]},
    {"operation": "set_finalize", "ruleName": "名" * 31, "desiredStatus": "disabled"},
    {"operation": "set_content", "components": [{"type": "button", "text": "危险",
     "actionType": "link", "url": "javascript:alert(1)"}]},
    {"operation": "upsert_dataset_query", "datasetRef": "d", "spaceId": "forged"},
])
def test_subscription_contract_rejects_invalid_business_boundaries(operation) -> None:
    """Reject invented scope and unsafe/out-of-range configuration before dispatch."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    schema = load_contract_registry(root / "contracts/davinci-agent-v2.json").get(
        "space.message_rule.apply_draft").input_schema
    with pytest.raises(ValidationError):
        validate_json_schema({"expectedRevision": 1, "operations": [operation]}, dict(schema))


def test_v2_contract_exposes_personal_dashboard_group_creation() -> None:
    """Expose one bounded V2 personal-dashboard group mutation."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")
    contract = registry.get("workspace.dashboard_group.create")

    assert contract.executor == "frontend"
    assert contract.risk == "persistent"
    assert contract.context_effect == "mutates"
    assert contract.bundle == "personal-workspace"
    assert contract.sdk_tool_name == "workspace__dashboard_group__create"
    assert contract.timeout_ms == 30_000
    assert contract.input_schema == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 20}
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    assert "id" not in contract.output_schema["properties"]["data"]["properties"]

    page_schema = registry.raw_contract["pageStateSchema"]
    workspace_capabilities = page_schema["properties"]["permissions"][
        "properties"
    ]["workspaceCapabilities"]
    assert workspace_capabilities["properties"]["canCreateDashboardGroup"] == {
        "type": "boolean"
    }
    assert "canCreateDashboardGroup" in workspace_capabilities["required"]


def test_v2_contract_exposes_bounded_space_core_actions() -> None:
    """Expose complete space setup without turning every UI button into a tool."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")
    expected_actions = {
        "space.list",
        "space.get_context",
        "space.open",
        "space.create",
        "space.update_info",
        "space.invitation.respond",
        "space.member.get_context",
        "space.member.apply_changes",
        "space.menu.get_context",
        "space.menu.apply_changes",
        "space.dashboard.create_and_open",
    }

    assert expected_actions.issubset(registry.public_actions)
    assert all(registry.get(action).executor == "frontend" for action in expected_actions)
    assert {registry.get(action).bundle for action in expected_actions} == {
        "space-core"
    }

    create_schema = registry.get("space.create").input_schema
    assert create_schema["required"] == ["type", "name"]
    assert set(create_schema["properties"]) == {
        "type",
        "name",
        "description",
        "departmentName",
    }
    assert [
        variant["properties"]["type"]["const"]
        for variant in create_schema["oneOf"]
    ] == ["team", "organization"]
    assert create_schema["oneOf"][1]["required"] == [
        "type",
        "name",
        "departmentName",
    ]
    assert registry.get("space.member.get_context").input_schema["properties"][
        "mode"
    ]["enum"] == ["members", "invite_candidates"]
    member_variants = registry.get(
        "space.member.apply_changes"
    ).input_schema["oneOf"]
    assert [
        variant["properties"]["operation"]["const"]
        for variant in member_variants
    ] == ["invite", "set_role", "remove"]
    assert all(
        variant.get("additionalProperties") is False
        for variant in member_variants
    )

    menu_variants = registry.get("space.menu.apply_changes").input_schema[
        "oneOf"
    ]
    assert [
        variant["properties"]["operation"]["const"]
        for variant in menu_variants
    ] == ["create_group", "create_page", "update", "delete", "reorder"]
    assert all(
        variant.get("additionalProperties") is False
        for variant in menu_variants
    )
    for action in expected_actions:
        contract = registry.get(action)
        assert contract.timeout_ms in {15_000, 30_000, 120_000}
        assert {"obId", "oaId", "userId", "route", "url"}.isdisjoint(
            contract.input_schema.get("properties", {})
        )
    assert "departmentId" not in registry.get("space.create").input_schema[
        "properties"
    ]

    create_dashboard = registry.get("space.dashboard.create_and_open")
    assert create_dashboard.risk == "persistent"
    assert create_dashboard.context_effect == "mutates"
    assert create_dashboard.sdk_tool_name == "space__dashboard__create_and_open"
    assert create_dashboard.timeout_ms == 30_000
    assert create_dashboard.input_schema == {
        "type": "object",
        "properties": {
            "groupRef": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
            },
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 64,
            },
        },
        "required": ["groupRef", "name"],
        "additionalProperties": False,
    }
    assert create_dashboard.output_schema == {
        "type": "object",
        "properties": {
            "status": {"type": "string", "const": "success"},
            "summary": {"type": "string", "maxLength": 1000},
            "contextVersion": {"type": "integer", "minimum": 0},
            "toolSetId": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
            },
        },
        "required": ["status", "summary", "contextVersion", "toolSetId"],
        "additionalProperties": False,
    }


def test_v2_contract_exposes_fixed_personal_workspace_navigation() -> None:
    """Expose one argument-free escape from collaborative-space scope."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")
    contract = registry.get("ui.open_personal_workspace")

    assert contract.executor == "frontend"
    assert contract.bundle == "core-navigation"
    assert contract.risk == "temporary"
    assert contract.context_effect == "mutates"
    assert contract.input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert contract.sdk_tool_name == "ui__open_personal_workspace"


def test_all_public_v2_frontend_tools_pass_native_agui_validation() -> None:
    """Catch contract descriptions or schemas that would reject page requests."""
    from app.agui.catalog import to_ag_ui_tool
    from app.agui.contracts import load_contract_registry
    from app.agui.models import validate_native_frontend_tools

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")
    for contract in registry.public_contracts:
        if contract.executor == "frontend":
            tool = to_ag_ui_tool(contract)
            assert validate_native_frontend_tools([tool]) == (tool,)


def test_space_detail_tool_catalog_passes_native_agui_validation() -> None:
    """Keep owner detail-page tools valid at the Host request boundary."""
    from app.agui.catalog import to_ag_ui_tool
    from app.agui.contracts import load_contract_registry
    from app.agui.models import validate_native_frontend_tools

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")
    actions = [
        "space.get_context",
        "space.member.get_context",
        "space.member.apply_changes",
        "space.menu.get_context",
        "space.menu.apply_changes",
    ]
    tools = [to_ag_ui_tool(registry.get(action)) for action in actions]

    assert validate_native_frontend_tools(tools) == tuple(tools)


def test_v2_page_state_schema_publishes_only_trusted_collaboration_scope() -> None:
    """Expose the Wave 1 optional state fields in the generated V2 contract."""
    root = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (root / "contracts" / "davinci-agent-v2.json").read_text(encoding="utf-8")
    )
    schema = payload["pageStateSchema"]

    assert set(schema["properties"]) == {
        "schemaVersion",
        "page",
        "permissions",
        "ui",
        "revisions",
        "dataStatus",
    }
    page = schema["properties"]["page"]
    assert page["required"] == ["instanceId", "kind", "route"]
    assert page["properties"]["kind"]["enum"] == [
        "workspace",
        "dashboard",
        "dataset-marketplace",
        "dataset-editor",
        "collaborative-space",
        "other",
    ]
    assert page["properties"]["space"] == {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 200},
            "name": {"type": "string", "minLength": 1, "maxLength": 500},
            "role": {"type": "string", "enum": ["owner", "admin", "member"]},
        },
        "required": ["id", "role"],
        "additionalProperties": False,
    }
    assert page["properties"]["viewMode"] == {
        "type": "string",
        "enum": ["self", "delegated"],
    }
    assert page["properties"]["resource"] == {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["dashboard", "dataset"]},
            "id": {"type": "string", "minLength": 1, "maxLength": 200},
            "name": {"type": "string", "maxLength": 500},
        },
        "required": ["type", "id"],
        "additionalProperties": False,
    }
    assert schema["properties"]["permissions"]["properties"][
        "dashboardCapabilities"
    ] == {
        "type": "object",
        "properties": {
            "canReadData": {"type": "boolean"},
            "canUseRuntimeControls": {"type": "boolean"},
            "canInspectWidgetConfig": {"type": "boolean"},
            "canOpenSharePanel": {"type": "boolean"},
            "canOpenMessageRulePanel": {"type": "boolean"},
            "canPersistDashboard": {"type": "boolean"},
            "canPublishDashboard": {"type": "boolean"},
        },
        "required": [
            "canReadData",
            "canUseRuntimeControls",
            "canInspectWidgetConfig",
            "canOpenSharePanel",
            "canOpenMessageRulePanel",
            "canPersistDashboard",
            "canPublishDashboard",
        ],
        "additionalProperties": False,
    }
    permission_properties = schema["properties"]["permissions"]["properties"]
    assert permission_properties["workspaceCapabilities"] == {
        "type": "object",
        "properties": {
            "canCreateSpace": {"type": "boolean"},
            "canCreateDashboardGroup": {"type": "boolean"},
        },
        "required": ["canCreateSpace", "canCreateDashboardGroup"],
        "additionalProperties": False,
    }
    assert permission_properties["spaceCapabilities"] == {
        "type": "object",
        "properties": {"canManage": {"type": "boolean"}},
        "required": ["canManage"],
        "additionalProperties": False,
    }
    assert permission_properties["resourceCapabilities"] == {
        "type": "object",
        "properties": {"canPersist": {"type": "boolean"}},
        "required": ["canPersist"],
        "additionalProperties": False,
    }


def test_v2_contract_adds_widget_edit_tools_and_batch_layout() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v1 = load_contract_registry()
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    assert "dashboard.get_widget_edit_capabilities" not in v1.contracts_by_action
    assert "dashboard.apply_widget_edits" not in v1.contracts_by_action
    describe = v2.get("dashboard.get_widget_edit_capabilities")
    apply_edits = v2.get("dashboard.apply_widget_edits")
    layout = v2.get("dashboard.set_widget_layout")
    assert describe.executor == "frontend"
    assert describe.input_schema["properties"]["widgetIds"] == {
        "type": "array",
        "minItems": 1,
        "maxItems": 200,
        "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 100},
    }
    assert {
        "includeDatasetOptions",
        "datasetQuery",
        "datasetCursor",
        "datasetLimit",
    }.issubset(describe.input_schema["properties"])
    assert "widgetId" not in describe.input_schema["properties"]
    assert "required" not in describe.input_schema
    capability_data = describe.output_schema["properties"]["data"]["properties"]
    assert {
        "resourceId",
        "widgets",
        "commonCapabilities",
        "creationOptions",
        "datasetOptions",
        "datasetOptionTotalCount",
        "datasetOptionNextCursor",
    }.issubset(
        capability_data
    )
    creation_item = capability_data["creationOptions"]["items"]
    assert "titleMaxLength" in creation_item["required"]
    assert apply_edits.executor == "frontend"
    assert apply_edits.risk == "persistent"
    assert set(apply_edits.input_schema["properties"]) == {
        "operations",
        "preset",
        "expectedResourceRevision",
    }
    assert apply_edits.input_schema["oneOf"] == [
        {"required": ["operations"]},
        {"required": ["preset"]},
    ]
    edit_preset = apply_edits.input_schema["properties"]["preset"]
    assert edit_preset["required"] == ["theme"]
    assert edit_preset["properties"]["variant"] == {
        "type": "string",
        "enum": ["metric-unified", "metric-multicolor"],
    }
    assert edit_preset["properties"]["colors"] == {
        "type": "array",
        "minItems": 1,
        "maxItems": 6,
        "uniqueItems": True,
        "items": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
    }
    operations = apply_edits.input_schema["properties"]["operations"]
    assert operations["maxItems"] == 50
    assert operations["items"]["required"] == ["widgetId", "edits"]
    edit_value = operations["items"]["properties"]["edits"]["items"][
        "properties"
    ]["value"]
    assert edit_value["maxLength"] == 5000
    capability_item = capability_data["widgets"]["items"]["properties"][
        "capabilities"
    ]["items"]["properties"]
    assert "content" in capability_item["section"]["enum"]
    assert capability_item["valueSpec"]["properties"]["maxLength"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 5000,
    }
    output_data = apply_edits.output_schema["properties"]["data"]
    assert {
        "requestedCount",
        "appliedCount",
        "unchangedCount",
        "widgetResults",
    }.issubset(output_data["properties"])
    assert output_data["properties"]["presetVariant"] == {
        "type": "string",
        "enum": ["metric-unified", "metric-multicolor"],
    }
    assert output_data["properties"]["paletteName"] == {
        "type": "string",
        "maxLength": 100,
    }
    assert output_data["properties"]["paletteColors"] == {
        "type": "array",
        "minItems": 1,
        "maxItems": 6,
        "uniqueItems": True,
        "items": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
    }
    assert output_data["properties"]["paletteRank"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 4,
    }
    assert output_data["properties"]["paletteCount"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 4,
    }
    assert {
        "presetVariant",
        "paletteName",
        "paletteColors",
        "paletteRank",
        "paletteCount",
    }.isdisjoint(output_data.get("required", []))
    assert "required" not in layout.input_schema
    assert layout.input_schema["oneOf"] == [
        {"required": ["items"]},
        {"required": ["preset"]},
    ]
    assert layout.input_schema["properties"]["items"]["maxItems"] == 30
    preset = layout.input_schema["properties"]["preset"]
    assert preset["required"] == ["mode"]
    assert preset["properties"]["sizeOverrides"]["maxItems"] == 200
    assert preset["properties"]["typeSizes"]["maxItems"] == 20
    layout_issue = layout.output_schema["properties"]["issues"]["items"]
    assert "widgetIds" in layout_issue["properties"]
    assert "constraints" in layout_issue["properties"]


def test_v2_structure_and_layout_contracts_cover_full_dashboard_receipts() -> None:
    """V2 pages structure reads and bounds large mutation receipts truthfully."""
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v1 = load_contract_registry()
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    assert v1.get("dashboard.get_structure").output_schema != v2.get(
        "dashboard.get_structure"
    ).output_schema
    structure = v2.get("dashboard.get_structure")
    assert structure.input_schema["properties"]["pageSize"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 100,
    }
    assert structure.input_schema["properties"]["expectedResourceRevision"] == {
        "type": "integer",
        "minimum": 0,
    }
    data = structure.output_schema
    assert data["properties"]["widgets"]["maxItems"] == 100
    assert set(data["required"]) == {
        "summary",
        "resourceRevision",
        "totalCount",
        "rootCount",
        "childCount",
        "returnedCount",
        "hasMore",
        "widgets",
    }

    layout = v2.get("dashboard.set_widget_layout")
    output = layout.output_schema
    output_data = output["properties"]["data"]["properties"]
    assert output_data["layoutChanges"]["maxItems"] == 200
    assert output_data["layoutChangeCount"]["minimum"] == 0
    assert output_data["readbackAction"]["const"] == "dashboard.get_structure"
    issue = output["properties"]["issues"]["items"]["properties"]
    assert issue["widgetIds"]["maxItems"] == 200
    assert issue["widgetIdCount"]["minimum"] == 0
    assert output["properties"]["issues"]["maxItems"] == 50
    assert output["properties"]["issueCount"]["minimum"] == 0

    layout_receipt = {
        "status": "success",
        "data": {
            "resourceId": "dashboard-1",
            "resourceRevision": 8,
            "persisted": True,
            "layoutChanges": [
                {
                    "widgetId": str(index),
                    "before": {"x": 0, "y": index, "width": 6, "height": 4, "order": index},
                    "after": {"x": 6, "y": index, "width": 6, "height": 4, "order": index},
                }
                for index in range(40)
            ],
            "layoutChangeCount": 40,
            "returnedLayoutChangeCount": 40,
            "layoutChangesTruncated": False,
            "summary": {
                "rootCount": 40,
                "resizedCount": 0,
                "beforeHeight": 44,
                "afterHeight": 44,
                "sizingSkippedWidgetIds": [],
                "orderPreserved": True,
                "layoutQuality": {
                    "externalGapCells": 0,
                    "mixedHeightRowCount": 0,
                    "largestConnectedGapCells": 0,
                },
            },
        },
        "issues": [
            {
                "code": "COLLISION",
                "message": "overlap",
                "retryable": False,
                "widgetIds": [str(index) for index in range(31)],
                "widgetIdCount": 31,
                "returnedWidgetIdCount": 31,
                "widgetIdsTruncated": False,
            }
        ],
        "issueCount": 1,
        "returnedIssueCount": 1,
        "issuesTruncated": False,
    }
    Draft202012Validator(layout.output_schema).validate(layout_receipt)

    structure_page = {
        "summary": "100 of 200 widgets",
        "resourceRevision": 8,
        "totalCount": 200,
        "rootCount": 150,
        "childCount": 50,
        "returnedCount": 100,
        "hasMore": True,
        "nextCursor": "opaque-page-2",
        "widgets": [
            {
                "widgetId": str(index),
                "title": f"Widget {index}",
                "type": "chart",
                "parentId": None,
                "coordinateSpace": "root",
                "layoutEditable": True,
                "layout": {"x": 0, "y": index, "width": 6, "height": 4, "order": index},
            }
            for index in range(100)
        ],
    }
    Draft202012Validator(structure.output_schema).validate(structure_page)


def test_v2_contract_executes_widget_structure_tools_in_the_frontend() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v1 = load_contract_registry()
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    assert "dashboard.delete_widget" not in v1.contracts_by_action

    add = v2.get("dashboard.add_widget")
    copy = v2.get("dashboard.copy_widget")
    rename = v2.get("dashboard.rename_widget")
    delete = v2.get("dashboard.delete_widget")

    for contract in (add, rename):
        assert contract.version == "2.0"
        assert contract.executor == "frontend"
        assert contract.risk == "persistent"
        assert contract.timeout_ms == 30000
    for contract in (copy, delete):
        assert contract.version == "2.0"
        assert contract.executor == "frontend"
        assert contract.risk == "persistent"
        assert contract.timeout_ms == 120000

    assert set(add.input_schema["properties"]) == {
        "chartType",
        "title",
        "expectedResourceRevision",
    }
    assert add.input_schema["required"] == ["chartType"]
    assert set(copy.input_schema["properties"]) == {
        "widgetId",
        "title",
        "expectedResourceRevision",
    }
    assert copy.input_schema["required"] == ["widgetId"]
    assert set(rename.input_schema["properties"]) == {
        "widgetId",
        "title",
        "expectedResourceRevision",
    }
    assert rename.input_schema["required"] == ["widgetId", "title"]
    assert set(delete.input_schema["properties"]) == {
        "widgetId",
        "deleteChildren",
        "expectedResourceRevision",
    }
    assert delete.input_schema["required"] == ["widgetId"]
    assert add.output_schema == copy.output_schema
    assert copy.output_schema == rename.output_schema
    assert rename.output_schema == delete.output_schema


def test_v2_contract_adds_declarative_dashboard_authoring_tools() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v1 = load_contract_registry()
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    actions = {
        "dashboard.set_widget_dataset",
        "dashboard.get_publish_readiness",
        "dashboard.publish",
        "dashboard.get_filter_field_options",
        "dashboard.apply_global_filter_edits",
        "dashboard.set_widget_container",
        "dashboard.set_layout_child_order",
    }
    assert actions.isdisjoint(v1.contracts_by_action)

    contracts = {action: v2.get(action) for action in actions}
    assert all(contract.executor == "frontend" for contract in contracts.values())
    assert contracts["dashboard.get_publish_readiness"].risk == "read"
    assert contracts["dashboard.get_filter_field_options"].risk == "read"
    for action in actions - {
        "dashboard.get_publish_readiness",
        "dashboard.get_filter_field_options",
    }:
        assert contracts[action].risk == "persistent"
        assert contracts[action].context_effect == "mutates"

    dataset = contracts["dashboard.set_widget_dataset"].input_schema
    assert dataset["required"] == ["widgetId", "datasetUid", "datasetType"]
    assert set(dataset["properties"]) == {
        "widgetId",
        "datasetUid",
        "datasetType",
        "expectedResourceRevision",
    }

    filter_options = contracts[
        "dashboard.get_filter_field_options"
    ].input_schema
    assert set(filter_options["properties"]) == {
        "query",
        "datasetUid",
        "datasetType",
        "fieldId",
        "sourceUid",
        "includeQueryVariables",
        "cursor",
        "limit",
    }
    filter_options_output = contracts[
        "dashboard.get_filter_field_options"
    ].output_schema
    filter_options_data = filter_options_output["properties"]["data"]["properties"]
    assert {"totalCount", "nextCursor"}.issubset(filter_options_data)
    assert filter_options_data["fields"]["maxItems"] == 100
    enum_schema = filter_options_data["enumValues"]
    validate_json_schema({
        "items": [{"label": "上门订单", "value": 11}],
        "truncated": False, "exhaustive": False,
    }, enum_schema)
    validate_json_schema({
        "datasetUid": "10", "datasetType": "warehouseTopic",
        "fieldId": "410", "query": "上门",
    }, dict(filter_options))
    with pytest.raises(ValidationError):
        validate_json_schema({"fieldId": "410"}, dict(filter_options))
    assert "data" not in filter_options_output["required"]
    filter_edits = contracts[
        "dashboard.apply_global_filter_edits"
    ].input_schema
    assert filter_edits["properties"]["operations"]["maxItems"] == 50
    assert filter_edits["required"] == ["operations"]

    container = contracts["dashboard.set_widget_container"].input_schema
    assert container["required"] == ["widgetId", "containerWidgetId"]
    assert container["properties"]["containerWidgetId"]["type"] == [
        "string",
        "null",
    ]
    child_order = contracts["dashboard.set_layout_child_order"].input_schema
    assert child_order["required"] == ["layoutWidgetId", "orderedWidgetIds"]
    assert child_order["properties"]["orderedWidgetIds"]["uniqueItems"] is True

    readiness = contracts["dashboard.get_publish_readiness"].output_schema
    readiness_data = readiness["properties"]["data"]["properties"]
    assert {"ready", "publishStatus", "blockers"}.issubset(readiness_data)
    assert "data" not in readiness["required"]
    publish = contracts["dashboard.publish"]
    assert publish.timeout_ms == 120000
    assert publish.input_schema["properties"]["expectedResourceRevision"] == {
        "type": "integer",
        "minimum": 0,
    }

    # Input definitions are sent to the model on every continuation. Keep this
    # reviewed batch compact without hiding capabilities behind keyword routing.
    serialized_input_bytes = sum(
        len(
            json.dumps(
                {
                    "name": contract.action,
                    "description": contract.description,
                    "parameters": dict(contract.input_schema),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for contract in contracts.values()
    )
    assert serialized_input_bytes <= 8_000


def test_v2_generated_javascript_matches_parallel_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    contract_path = root / "contracts" / "davinci-agent-v2.json"
    generated_path = (
        root / "web" / "shared" / "generated" / "davinci-contracts-v2.js"
    )
    digest = hashlib.sha256(contract_path.read_bytes()).hexdigest()

    generated = generated_path.read_text(encoding="utf-8")
    assert "export const CONTRACT_VERSION = '2.0'" in generated
    assert "export const PROTOCOL_VERSION = 'agui-native-v2'" in generated
    assert f"export const CONTRACT_DIGEST = '{digest}'" in generated


def test_v2_contract_adds_apply_widget_spec() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    v2 = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")
    contract = v2.get("dashboard.apply_widget_spec")
    assert contract.executor == "frontend"
    assert contract.risk == "persistent"
    assert contract.context_effect == "mutates"
    assert contract.sdk_tool_name == "dashboard__apply_widget_spec"
    schema = contract.input_schema
    assert schema["required"] == ["spec"]
    assert schema["oneOf"] == [
        {"required": ["widgetId"]},
        {"required": ["create"]},
    ]
    assert set(schema["properties"]) == {
        "widgetId",
        "create",
        "dryRun",
        "expectedResourceRevision",
        "spec",
    }
    assert schema["properties"]["create"]["required"] == ["chartType"]
    spec = schema["properties"]["spec"]
    assert spec["required"] == ["dataset", "metrics", "dimensions", "filters"]
    assert set(spec["properties"]) == {
        "dataset",
        "metrics",
        "dimensions",
        "filters",
        "sort",
        "limit",
        "title",
        "comparison",
    }
    filt = spec["properties"]["filters"]["items"]
    assert filt["properties"]["value"]["items"]["maxLength"] == 200
    assert "last_days" in filt["properties"]["valueExp"]["enum"]
    assert "past_7_days" not in filt["properties"]["valueExp"]["enum"]
    assert "past_30_days" not in filt["properties"]["valueExp"]["enum"]
    comparison = spec["properties"]["comparison"]
    assert comparison["required"] == ["timeFieldId", "methods"]
    assert comparison["properties"]["methods"]["minItems"] == 1
    assert comparison["properties"]["methods"]["maxItems"] == 3
    method_item = comparison["properties"]["methods"]["items"]
    assert method_item["required"] == ["method", "calcType"]
    assert method_item["properties"]["method"]["enum"] == [
        "dayChain",
        "weekSame",
        "monthChain",
        "yearSame",
    ]
    assert method_item["properties"]["calcType"]["enum"] == ["diffRate", "diff"]
    out = contract.output_schema["properties"]["data"]["properties"]
    assert "appliedSpec" in out
    applied_comparison = out["appliedSpec"]["properties"]["comparison"]
    assert applied_comparison["type"] == ["object", "null"]
    assert applied_comparison["properties"]["methods"]["maxItems"] == 3
    assert "同环比" in contract.description or "comparison" in contract.description
    assert len(contract.description) < 700
    value_exp_description = filt["properties"]["valueExp"]["description"]
    assert "operator=eq" in value_exp_description
    assert "last_days" in value_exp_description
    assert '["X", "Y"]' in value_exp_description
    operator_description = filt["properties"]["operator"]["description"]
    value_description = filt["properties"]["value"]["description"]
    assert "fixed range" in operator_description
    assert "operator=between" in value_description
    assert "exactly two" in value_description
    comparison_description = comparison["description"]
    assert "Omit" in comparison_description
    assert "preserve" in comparison_description
    assert "stale" in comparison_description
    method_description = method_item["properties"]["method"]["description"]
    assert "dayChain" in method_description and "yearSame" in method_description


def test_v2_get_widget_config_returns_effective_spec_and_data_metadata_semantics() -> None:
    root = Path(__file__).resolve().parents[1]
    v2 = json.loads(
        (root / "contracts" / "davinci-agent-v2.json").read_text(encoding="utf-8")
    )
    templates = v2["schemaTemplates"]

    override = v2["toolOverrides"]["dashboard.get_widget_config"]
    assert override["outputTemplate"] == "dashboardWidgetConfigSummary"
    assert "effectiveSpec" in override["description"]

    summary = templates["dashboardWidgetConfigSummary"]
    assert summary["additionalProperties"] is False
    assert summary["required"] == ["summary"]
    assert summary["properties"]["effectiveSpec"]["type"] == "object"
    assert summary["properties"]["effectiveSpec"]["additionalProperties"] == {}
    assert "appliedSpec" in summary["properties"]["effectiveSpec"]["description"]
    assert set(summary["properties"]) == {
        "summary",
        "effectiveSpec",
        "contextVersion",
        "widgets",
        "refs",
    }

    refs_schema = summary["properties"]["refs"]
    assert summary["properties"]["widgets"]["items"]["properties"]["refs"] == refs_schema
    refs = {
        "datasetRef": "warehouseTopic:662",
        "fieldRefs": [
            {"role": "metric", "fieldId": "amount", "fieldRef": "warehouseTopic:662/amount"},
            {"role": "metric", "fieldId": "literal", "unresolvedReason": "DERIVED_OR_LITERAL_FIELD"},
        ],
    }
    validator = Draft202012Validator(summary)
    assert not list(validator.iter_errors({"summary": "配置", "refs": refs}))
    assert not list(validator.iter_errors({"summary": "批量", "widgets": [
        {"widgetId": "88", "summary": "配置", "refs": refs},
    ]}))
    assert list(validator.iter_errors({"summary": "配置", "refs": {**refs, "rawConfig": {}}}))
    write_spec = templates["widgetSpecApplyRequest"]["properties"]["spec"]
    assert "refs" not in write_spec["properties"]

    limit = templates["widgetSpecApplyRequest"]["properties"]["spec"]["properties"][
        "limit"
    ]
    assert "11001" in limit["description"] and "LIMIT_NOT_SUPPORTED" in limit[
        "description"
    ]

    data_request = templates["dashboardWidgetDataRequest"]["properties"]
    assert "does not change" in data_request["maxRows"]["description"]
    assert "rowCount" in data_request["includeMetadata"]["description"]
    assert "Defaults to true" in data_request["includeMetadata"]["description"]

    apply_tool = next(
        t for t in v2["toolAdditions"] if t["action"] == "dashboard.apply_widget_spec"
    )
    assert "Leaderboard 11001 only" in apply_tool["description"]


def test_v2_widget_group_readbacks_validate_native_series_state() -> None:
    """Validate grouped, disabled and legacy receipts at each public readback boundary."""
    root = Path(__file__).resolve().parents[1]
    v2 = json.loads(
        (root / "contracts" / "davinci-agent-v2.json").read_text(encoding="utf-8")
    )
    templates = v2["schemaTemplates"]
    apply_output = templates["widgetSpecApplyEnvelope"]
    config_output = templates["dashboardWidgetConfigSummary"]
    applied_schema = apply_output["properties"]["data"]["properties"]["appliedSpec"]
    applied_group = applied_schema["properties"]["group"]
    assert config_output["properties"]["effectiveSpec"]["properties"]["group"] == applied_group
    assert config_output["properties"]["widgets"]["items"]["properties"]["effectiveSpec"]["properties"]["group"] == applied_group

    spec = {
        "chartType": 4001,
        "dataset": {"datasetUid": "10", "datasetType": "warehouseTopic"},
        "metrics": [{"fieldId": "542", "name": "成交订单金额", "agg": "sum"}],
        "dimensions": [
            {"fieldId": "339", "name": "订单创建日期"},
            {"fieldId": "15124", "name": "战区"},
        ],
        "filters": [],
    }

    def receipts(readback: dict[str, object]) -> list[tuple[dict, dict]]:
        """Wrap one native spec in write, single-read and batch-read receipts."""
        return [
            (apply_output, {"status": "success", "observed": {"observedAt": "2026-09-15T00:00:00Z"}, "issues": [], "data": {
                "resourceId": "dashboard:1", "resourceRevision": 2,
                "persisted": True, "state": "updated", "widgetId": "16657",
                "appliedSpec": readback,
                "probe": {"rowCount": 376},
            }}),
            (config_output, {"summary": "折线图配置", "effectiveSpec": readback}),
            (config_output, {"summary": "批量配置", "widgets": [
                {"widgetId": "16657", "summary": "折线图配置", "effectiveSpec": readback},
            ]}),
        ]

    valid_readbacks = [
        spec,  # Existing receipts stay compatible; absence is not proof of grouping.
        {**spec, "group": None},
        {**spec, "group": {"enabled": False}},
        {**spec, "group": {
            "enabled": True, "fieldId": "15124", "name": "战区", "type": "varchar",
        }},
    ]
    for readback in valid_readbacks:
        for schema, receipt in receipts(readback):
            assert not list(Draft202012Validator(schema).iter_errors(receipt))

    for invalid_group in ({}, {"enabled": "true"}, {"enabled": True, "fieldId": 15124}, {"enabled": True, "groupKey": "15124"}):
        for schema, receipt in receipts({**spec, "group": invalid_group}):
            assert list(Draft202012Validator(schema).iter_errors(receipt))

    write_request = templates["widgetSpecApplyRequest"]
    request = {"create": {"chartType": 4001}, "spec": {
        "dataset": spec["dataset"], "metrics": [{"fieldId": "542", "agg": "sum"}],
        "dimensions": [{"fieldId": "339"}, {"fieldId": "15124"}], "filters": [],
    }}
    validator = Draft202012Validator(write_request)
    assert not list(validator.iter_errors(request))
    request["spec"]["group"] = {"enabled": True, "fieldId": "15124"}
    assert list(validator.iter_errors(request))


def test_v2_get_widget_config_accepts_a_bounded_widget_id_batch() -> None:
    root = Path(__file__).resolve().parents[1]
    v2 = json.loads((root / "contracts" / "davinci-agent-v2.json").read_text(encoding="utf-8"))
    templates = v2["schemaTemplates"]

    override = v2["toolOverrides"]["dashboard.get_widget_config"]
    assert override["inputTemplate"] == "dashboardWidgetConfigRequest"

    request = templates["dashboardWidgetConfigRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"widgetId", "widgetIds"}
    assert request["properties"]["widgetIds"]["maxItems"] == 30
    assert "required" not in request
    assert request["oneOf"] == [
        {"required": ["widgetId"]},
        {"required": ["widgetIds"]},
    ]

    summary = templates["dashboardWidgetConfigSummary"]["properties"]
    assert summary["widgets"]["maxItems"] == 30
    assert summary["widgets"]["items"]["properties"]["widgetId"]["type"] == "string"


def test_v2_conditional_inputs_match_runtime_requirements() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    def accepts(action: str, payload: object) -> None:
        validate_json_schema(payload, dict(registry.get(action).input_schema))

    def rejects(action: str, payload: object) -> None:
        with pytest.raises(ValidationError):
            accepts(action, payload)

    rejects("space.create", {"type": "organization", "name": "组织空间"})
    rejects(
        "space.create",
        {"type": "team", "name": "团队空间", "departmentName": "事业部"},
    )
    accepts(
        "space.create",
        {"type": "organization", "name": "组织空间", "departmentName": "事业部"},
    )
    accepts("space.create", {"type": "team", "name": "团队空间"})

    rejects("space.member.get_context", {"mode": "invite_candidates"})
    accepts("space.member.get_context", {"mode": "members"})
    accepts(
        "space.member.get_context",
        {"mode": "invite_candidates", "query": "Alex"},
    )

    rejects("dashboard.get_widget_config", {})
    rejects(
        "dashboard.get_widget_config",
        {"widgetId": "w1", "widgetIds": ["w2"]},
    )
    accepts("dashboard.get_widget_config", {"widgetId": "w1"})
    accepts("dashboard.get_widget_config", {"widgetIds": ["w1", "w2"]})

    spec = {
        "dataset": {"datasetUid": "662", "datasetType": "warehouseTopic"},
        "metrics": [],
        "dimensions": [],
        "filters": [],
    }
    rejects("dashboard.apply_widget_spec", {"spec": spec})
    rejects(
        "dashboard.apply_widget_spec",
        {"widgetId": "w1", "create": {"chartType": 1001}, "spec": spec},
    )
    accepts("dashboard.apply_widget_spec", {"widgetId": "w1", "spec": spec})
    accepts(
        "dashboard.apply_widget_spec",
        {"create": {"chartType": 1001}, "spec": spec},
    )

    rejects(
        "dashboard.apply_global_filter_edits",
        {"operations": [{"operation": "remove"}]},
    )
    rejects(
        "dashboard.apply_global_filter_edits",
        {"operations": [{"operation": "upsert"}]},
    )
    valid_binding = {
        "datasetUid": "662",
        "datasetType": "warehouseTopic",
        "key": "region",
        "name": "区域",
        "type": "varchar",
    }
    rejects(
        "dashboard.apply_global_filter_edits",
        {
            "operations": [{
                "operation": "upsert",
                "kind": "link",
                "alias": "区域",
                "operator": "eq",
                "bindings": [valid_binding, valid_binding],
            }]
        },
    )
    rejects(
        "dashboard.apply_global_filter_edits",
        {
            "operations": [{
                "operation": "upsert",
                "kind": "custom",
                "alias": "日期",
                "operator": "between",
                "value": ["2026-09-01"],
                "bindings": [valid_binding],
            }]
        },
    )
    accepts(
        "dashboard.apply_global_filter_edits",
        {"operations": [{"operation": "remove", "filterId": "filter-1"}]},
    )

    draft_base = {"expectedRevision": 1}
    invalid_draft_operations = [
        {"operation": "set_schedule", "frequency": "daily", "effectiveStart": "2026-09-01T00:00:00Z"},
        {"operation": "set_push_mode", "mode": "group", "queryRef": "query:1"},
        {"operation": "set_recipients"},
        {"operation": "set_content", "includeDataTable": True},
    ]
    for operation in invalid_draft_operations:
        rejects(
            "space.message_rule.apply_draft",
            {**draft_base, "operations": [operation]},
        )
    accepts(
        "space.message_rule.apply_draft",
        {
            **draft_base,
            "operations": [{
                "operation": "set_push_mode",
                "mode": "group",
                "queryRef": "query:1",
                "groupByFieldRef": "field:1",
            }],
        },
    )


def test_v2_all_canonical_schemas_are_valid_draft_2020_12() -> None:
    from app.agui.contracts import load_contract_registry

    root = Path(__file__).resolve().parents[1]
    registry = load_contract_registry(root / "contracts" / "davinci-agent-v2.json")

    for schema in registry.raw_contract["schemaTemplates"].values():
        Draft202012Validator.check_schema(schema)
    for schema in registry.raw_contract["errors"].values():
        Draft202012Validator.check_schema(schema)


def test_v2_capabilities_request_supports_section_and_creation_option_trimming() -> None:
    root = Path(__file__).resolve().parents[1]
    v2 = json.loads(
        (root / "contracts" / "davinci-agent-v2.json").read_text(
            encoding="utf-8"
        )
    )
    request = v2["schemaTemplates"]["widgetEditCapabilityRequest"]["properties"]
    assert "sections" in request
    assert "includeCreationOptions" in request
    assert request["sections"]["items"]["enum"] == [
        "appearance",
        "chart",
        "content",
        "legend",
        "metric",
        "title",
    ]
    assert request["sections"]["maxItems"] == 6
    assert request["includeCreationOptions"]["type"] == "boolean"

    data = v2["schemaTemplates"]["widgetEditCapabilitiesEnvelope"]["properties"][
        "data"
    ]
    assert "creationOptions" not in data["required"]


def test_v2_dry_run_probe_reports_the_values_a_grouped_query_found() -> None:
    """目录只给字段名，取值只能从数据里来。

    按维度分组试跑一次，返回行的那一列就是该字段的实际取值——筛选值写没写对
    靠它判断，否则只能改一次写一次去试。
    """
    root = Path(__file__).resolve().parents[1]
    v2 = json.loads(
        (root / "contracts" / "davinci-agent-v2.json").read_text(
            encoding="utf-8"
        )
    )
    envelope = v2["schemaTemplates"]["widgetSpecApplyEnvelope"]["properties"]
    probe = envelope["data"]["properties"]["probe"]
    sample_rows = probe["properties"]["sampleRows"]

    assert sample_rows["type"] == "array"
    # 高基数字段不能把回复淹掉；truncated 说明这不是完整枚举。
    assert sample_rows["maxItems"] == 20
    assert "truncated" in probe["properties"]
    assert sample_rows["items"]["additionalProperties"]["type"] == [
        "string",
        "number",
        "null",
    ]
    # rowCount 仍是唯一必填项：探针不可用时只有它。
    assert probe["required"] == ["rowCount"]
