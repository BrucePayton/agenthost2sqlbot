from __future__ import annotations

import pytest


def test_sdk_names_are_unique_reversible_and_executor_qualified() -> None:
    from app.agui.catalog import (
        DEFAULT_REGISTRY,
        action_for_sdk_qualified_name,
        sdk_qualified_name,
    )

    qualified_names: set[str] = set()
    for contract in DEFAULT_REGISTRY.public_contracts:
        qualified = sdk_qualified_name(contract)
        assert qualified not in qualified_names
        qualified_names.add(qualified)
        assert action_for_sdk_qualified_name(qualified) == contract.action
        expected_server = "davinci_core" if contract.executor == "host" else "davinci_ui"
        assert qualified.startswith(f"mcp__{expected_server}__")

    with pytest.raises(ValueError, match="Unknown Davinci SDK tool"):
        action_for_sdk_qualified_name("mcp__davinci_ui__not_registered")


def test_ag_ui_tool_projection_uses_canonical_description_and_input_schema() -> None:
    from app.agui.catalog import DEFAULT_REGISTRY, to_ag_ui_tool

    contract = DEFAULT_REGISTRY.get("dashboard.refresh_widget")
    projected = to_ag_ui_tool(contract)

    assert projected.name == "dashboard.refresh_widget"
    assert projected.description == contract.description
    assert projected.parameters == contract.input_schema


def test_internal_actions_never_project_to_public_tools() -> None:
    from app.agui.catalog import DEFAULT_REGISTRY, to_ag_ui_tool

    contract = DEFAULT_REGISTRY.get("resource.reload")

    assert contract.public is False
    with pytest.raises(ValueError, match="internal action"):
        to_ag_ui_tool(contract)
