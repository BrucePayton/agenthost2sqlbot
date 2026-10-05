from __future__ import annotations

from ag_ui.core import Tool

from app.agui.contracts import (
    ToolContract,
    ToolContractRegistry,
    load_contract_registry,
)

DEFAULT_REGISTRY = load_contract_registry()


def sdk_qualified_name(contract: ToolContract) -> str:
    server = "davinci_core" if contract.executor == "host" else "davinci_ui"
    return f"mcp__{server}__{contract.sdk_tool_name}"


def action_for_sdk_qualified_name(
    qualified_name: str,
    registry: ToolContractRegistry = DEFAULT_REGISTRY,
) -> str:
    for contract in registry.public_contracts:
        if sdk_qualified_name(contract) == qualified_name:
            return contract.action
    raise ValueError(f"Unknown Davinci SDK tool: {qualified_name}")


def to_ag_ui_tool(contract: ToolContract) -> Tool:
    if not contract.public:
        raise ValueError(f"Cannot project internal action: {contract.action}")
    return Tool(
        name=contract.action,
        description=contract.description,
        parameters=dict(contract.input_schema),
    )
