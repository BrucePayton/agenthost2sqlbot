import json
from pathlib import Path

import pytest
from ag_ui.core import Tool
from jsonschema import validate as validate_json_schema
from pydantic import ValidationError

from tests.agui_helpers import (
    dashboard_context,
    dashboard_tools,
    dataset_context,
    snapshot_payload,
)


def load_models():
    from app.agui import models

    return models


def _native_page_state() -> dict[str, object]:
    """Build the smallest valid native Dashboard page state for model tests."""
    return {
        "schemaVersion": "davinci-page-state-v1",
        "page": {
            "instanceId": "page-1",
            "kind": "dashboard",
            "route": "/share/workbench-new",
            "resource": {"type": "dashboard", "id": "88"},
        },
        "permissions": {
            "canRead": True,
            "canOperate": True,
            "canPersist": False,
        },
        "ui": {"busy": False, "activeFilters": []},
        "revisions": {"routeRevision": 2},
        "dataStatus": {"loadingWidgetIds": [], "errorWidgetIds": []},
    }


NATIVE_DASHBOARD_CAPABILITIES = {
    "canReadData": True,
    "canUseRuntimeControls": True,
    "canInspectWidgetConfig": True,
    "canOpenSharePanel": False,
    "canOpenMessageRulePanel": True,
    "canPersistDashboard": False,
    "canPublishDashboard": False,
}


@pytest.mark.parametrize("kind", ["other", "workspace", "dashboard", "collaborative-space"])
def test_subscription_workflow_matches_published_page_contract(kind: str) -> None:
    """Accept the subscription hint that Davinci includes before the first Run."""
    payload = _native_page_state()
    payload["page"] = {
        "instanceId": "subscription-page",
        "kind": kind,
        "route": "/share/workbench-new/subscription",
        "workflow": "subscription",
    }
    contract = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/davinci-agent-v2.json")
        .read_text()
    )
    validate_json_schema(payload, contract["pageStateSchema"])
    validated = load_models().validate_native_page_state(payload)
    assert validated["page"]["workflow"] == "subscription"
    assert validated["permissions"] == payload["permissions"]
    payload["page"].pop("workflow")
    assert "workflow" not in load_models().validate_native_page_state(payload)["page"]


def test_native_page_rejects_unknown_workflow_and_extra_fields() -> None:
    """Adding the optional hint must not relax the rest of the strict contract."""
    models = load_models()
    payload = _native_page_state()
    payload["page"]["workflow"] = "unknown-workflow"
    with pytest.raises(ValidationError):
        models.validate_native_page_state(payload)
    payload["page"]["workflow"] = "subscription"
    payload["page"]["unexpected"] = True
    with pytest.raises(ValidationError):
        models.validate_native_page_state(payload)


def test_dashboard_and_dataset_accept_stable_bounded_catalog() -> None:
    models = load_models()
    assert models.validate_frontend_tools(
        dashboard_context(), dashboard_tools()
    ) == tuple(dashboard_tools())
    assert models.validate_frontend_tools(
        dataset_context(), dashboard_tools()
    ) == tuple(dashboard_tools())


def test_tool_catalog_rejects_unknown_or_modified_definitions() -> None:
    models = load_models()
    unknown = Tool(name="deleteDashboard", description="Delete it", parameters={})
    modified = models.CAPTURE_TOOL.model_copy(
        update={"description": "Return arbitrary page HTML."}
    )

    with pytest.raises(ValueError, match="CAPABILITY_UNAVAILABLE"):
        models.validate_frontend_tools(dashboard_context(), [unknown])
    with pytest.raises(ValueError, match="invalid frontend tool definition"):
        models.validate_frontend_tools(dashboard_context(), [modified])


@pytest.mark.parametrize("length", [2001, 2149, 8000])
def test_native_tool_accepts_long_description(length: int) -> None:
    """Accept expanded tool guidance up to the character-based boundary."""
    models = load_models()
    tool = models.CAPTURE_TOOL.model_copy(update={"description": "描" * length})

    assert models.validate_native_frontend_tools([tool]) == (tool,)


def test_every_published_v2_tool_can_register_with_the_native_host() -> None:
    """Validate real catalog names, descriptions and schemas at the Host boundary."""
    from app.agui.contracts import load_contract_registry

    registry = load_contract_registry(
        Path(__file__).resolve().parents[1] / "contracts/davinci-agent-v2.json"
    )
    models = load_models()
    for contract in registry.public_contracts:
        if contract.executor != "frontend":
            continue
        tool = Tool(
            name=contract.action,
            description=contract.description,
            parameters=dict(contract.input_schema),
        )
        # Pages register a bounded subset, never the complete cross-page catalog.
        assert models.validate_native_frontend_tools([tool]) == (tool,)


@pytest.mark.parametrize("length", [0, 8001])
def test_native_tool_rejects_empty_or_oversized_description(length: int) -> None:
    """Keep empty descriptions and descriptions beyond the new limit invalid."""
    models = load_models()
    tool = models.CAPTURE_TOOL.model_copy(update={"description": "描" * length})

    with pytest.raises(ValueError, match="invalid tool description"):
        models.validate_native_frontend_tools([tool])


def test_snapshot_is_exact_canonical_and_bounded() -> None:
    models = load_models()
    content = json.dumps(snapshot_payload(), ensure_ascii=False)

    canonical = models.validate_tool_message_content(
        "dashboard.capture_current_view", content, None
    )
    snapshot = models.DashboardSnapshot.model_validate_json(canonical)

    assert snapshot.metrics.item_count == 4734
    assert snapshot.metrics.weekly_change_pct == -10.88
    assert snapshot.metrics.bid_amount == 40388380
    assert json.loads(canonical)["metrics"]["itemCount"] == 4734
    with pytest.raises(ValueError, match="64 KiB"):
        models.validate_tool_message_content(
            "dashboard.capture_current_view", "x" * 65_537, None
        )


def test_snapshot_rejects_unknown_fields_and_wrong_schema() -> None:
    models = load_models()
    payload = snapshot_payload()
    payload["unexpected"] = "not allowed"
    with pytest.raises(ValidationError):
        models.DashboardSnapshot.model_validate(payload)

    payload = snapshot_payload()
    payload["schemaVersion"] = "mock-dashboard-snapshot-v2"
    with pytest.raises(ValidationError):
        models.DashboardSnapshot.model_validate(payload)


def test_host_context_rejects_wrong_resource_shape() -> None:
    models = load_models()
    with pytest.raises(ValidationError, match="resourceId must be 1024"):
        models.HostContext(
            pageType="dashboard",
            resourceId="9999",
            contextVersion=1,
            supportedCapabilities=["dashboard.capture_current_view"],
            supportedCommands=["navigateTo"],
        )
    with pytest.raises(ValidationError, match="resourceId must be null"):
        models.HostContext(
            pageType="dataset",
            resourceId="1024",
            contextVersion=1,
            supportedCapabilities=[],
            supportedCommands=["navigateTo"],
        )


def test_native_page_state_accepts_space_shell_and_trusted_dashboard_scope() -> None:
    models = load_models()
    space_home = _native_page_state()
    space_home["page"] = {
        "instanceId": "page-1",
        "kind": "collaborative-space",
        "route": "/share/collaborative-space",
    }

    validated_home = models.validate_native_page_state(space_home)

    assert validated_home["page"] == space_home["page"]

    for view_mode in ("self", "delegated"):
        dashboard = _native_page_state()
        dashboard["page"] = {
            **dashboard["page"],
            "space": {"id": "1001", "name": "营销空间", "role": "admin"},
            "viewMode": view_mode,
        }
        dashboard["permissions"] = {
            **dashboard["permissions"],
            "dashboardCapabilities": NATIVE_DASHBOARD_CAPABILITIES,
        }

        validated = models.validate_native_page_state(dashboard)

        assert validated["page"]["space"] == {
            "id": "1001",
            "name": "营销空间",
            "role": "admin",
        }
        assert validated["page"]["viewMode"] == view_mode
        assert (
            validated["permissions"]["dashboardCapabilities"]
            == NATIVE_DASHBOARD_CAPABILITIES
        )

    fixed_space_page = _native_page_state()
    fixed_space_page["page"] = {
        "instanceId": "page-2",
        "kind": "collaborative-space",
        "route": "/share/collaborative-space/1001/message",
        "space": {"id": "1001", "name": "营销空间", "role": "owner"},
        "viewMode": "self",
    }

    validated_fixed_page = models.validate_native_page_state(fixed_space_page)

    assert validated_fixed_page["page"]["space"]["id"] == "1001"
    assert validated_fixed_page["page"]["viewMode"] == "self"


def test_native_page_state_accepts_personal_dashboard_group_capability() -> None:
    models = load_models()
    payload = _native_page_state()
    payload["permissions"] = {
        **payload["permissions"],
        "workspaceCapabilities": {
            "canCreateSpace": True,
            "canCreateDashboardGroup": True,
        },
    }

    validated = models.validate_native_page_state(payload)

    assert validated["permissions"]["workspaceCapabilities"] == {
        "canCreateSpace": True,
        "canCreateDashboardGroup": True,
    }


@pytest.mark.parametrize(
    ("space", "view_mode"),
    [
        ({"id": "", "role": "member"}, "self"),
        ({"id": "1001", "role": "viewer"}, "self"),
        ({"id": "1001", "role": "member"}, "impersonated"),
    ],
)
def test_native_page_state_rejects_invalid_space_or_view_mode(
    space: dict[str, str], view_mode: str
) -> None:
    models = load_models()
    payload = _native_page_state()
    payload["page"] = {
        **payload["page"],
        "space": space,
        "viewMode": view_mode,
    }

    with pytest.raises(ValidationError):
        models.validate_native_page_state(payload)


@pytest.mark.parametrize("mutation", ["missing", "extra", "not_boolean"])
def test_native_page_state_rejects_malformed_dashboard_capabilities(
    mutation: str,
) -> None:
    models = load_models()
    capabilities = dict(NATIVE_DASHBOARD_CAPABILITIES)
    if mutation == "missing":
        capabilities.pop("canPublishDashboard")
    elif mutation == "extra":
        capabilities["canDeleteDashboard"] = False
    else:
        capabilities["canReadData"] = "true"
    payload = _native_page_state()
    payload["permissions"] = {
        **payload["permissions"],
        "dashboardCapabilities": capabilities,
    }

    with pytest.raises(ValidationError):
        models.validate_native_page_state(payload)


def test_native_page_state_rejects_leaked_scope_and_internal_epoch() -> None:
    models = load_models()
    unrelated = _native_page_state()
    unrelated["page"] = {
        "instanceId": "page-1",
        "kind": "workspace",
        "route": "/share/workbench-new",
        "space": {"id": "1001", "role": "owner"},
    }
    with pytest.raises(ValidationError):
        models.validate_native_page_state(unrelated)

    leaked_epoch = _native_page_state()
    leaked_epoch["page"] = {
        **leaked_epoch["page"],
        "perspectiveEpoch": 3,
    }
    with pytest.raises(ValidationError):
        models.validate_native_page_state(leaked_epoch)


def test_navigation_ack_and_structured_error_are_validated() -> None:
    models = load_models()
    ack = json.dumps(
        {
            "schemaVersion": "davinci-ui-ack-v1",
            "status": "executed",
            "destination": "datasets",
            "path": "/datasets",
            "contextVersion": 4,
        }
    )
    error = json.dumps(
        {
            "schemaVersion": "davinci-tool-error-v1",
            "ok": False,
            "code": "CONTEXT_STALE",
            "message": "The page changed before the command ran.",
            "contextVersion": 4,
        }
    )

    assert json.loads(
        models.validate_tool_message_content("navigateTo", ack, None)
    )["status"] == "executed"
    assert json.loads(
        models.validate_tool_message_content("navigateTo", error, "CONTEXT_STALE")
    )["code"] == "CONTEXT_STALE"
    with pytest.raises(ValueError, match="tool error marker"):
        models.validate_tool_message_content("navigateTo", ack, "CONTEXT_STALE")
