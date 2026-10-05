from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "davinci-agent-v1.json"
)

ToolExecutor = Literal["frontend", "host"]
ToolRisk = Literal["read", "temporary", "draft", "changeset", "persistent"]
ContextEffect = Literal["none", "mutates"]

DEFAULT_TOOL_TIMEOUT_MS = 15_000
MIN_TOOL_TIMEOUT_MS = 1_000
MAX_TOOL_TIMEOUT_MS = 120_000


class ContractValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ToolContract:
    action: str
    version: str
    executor: ToolExecutor
    risk: ToolRisk
    context_effect: ContextEffect
    bundle: str
    sdk_tool_name: str
    description: str
    input_schema_id: str
    output_schema_id: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    public: bool
    timeout_ms: int


class ToolContractRegistry:
    def __init__(
        self,
        *,
        raw_contract: dict[str, Any],
        contracts_by_action: dict[str, ToolContract],
        error_schemas: dict[str, dict[str, Any]],
    ) -> None:
        self._raw_contract = copy.deepcopy(raw_contract)
        self.contracts_by_action = MappingProxyType(dict(contracts_by_action))
        self.error_schemas = MappingProxyType(copy.deepcopy(error_schemas))
        self.contract_version = raw_contract["contractVersion"]
        self.protocol_version = raw_contract["protocolVersion"]

    @property
    def raw_contract(self) -> dict[str, Any]:
        return copy.deepcopy(self._raw_contract)

    @property
    def public_contracts(self) -> tuple[ToolContract, ...]:
        return tuple(
            contract
            for contract in self.contracts_by_action.values()
            if contract.public
        )

    @property
    def public_actions(self) -> tuple[str, ...]:
        return tuple(contract.action for contract in self.public_contracts)

    @property
    def host_actions(self) -> tuple[str, ...]:
        return tuple(
            contract.action
            for contract in self.public_contracts
            if contract.executor == "host"
        )

    @property
    def internal_actions(self) -> tuple[str, ...]:
        return tuple(
            contract.action
            for contract in self.contracts_by_action.values()
            if not contract.public
        )

    def get(self, action: str) -> ToolContract:
        try:
            return self.contracts_by_action[action]
        except KeyError as exc:
            raise ValueError(f"Unknown Davinci action: {action}") from exc

    @staticmethod
    def dumps_contract(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _validate_strict_schema(schema: Mapping[str, Any], *, label: str) -> None:
    for name, definition in schema.get("$defs", {}).items():
        _validate_strict_schema(definition, label=f"{label}.$defs.{name}")
    if schema.get("type") == "object":
        additional = schema.get("additionalProperties")
        if additional is not False and not isinstance(additional, Mapping):
            raise ContractValidationError(f"{label} must forbid additional properties")
        if isinstance(additional, Mapping):
            _validate_strict_schema(additional, label=f"{label}.*")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ContractValidationError(f"{label} properties must be an object")
    # Conditional fragments often omit type while still declaring properties.
    # Walk them so a nested object cannot bypass the strict-object gate.
    for name, child in properties.items():
        if isinstance(child, Mapping):
            _validate_strict_schema(child, label=f"{label}.{name}")
    items = schema.get("items")
    if isinstance(items, Mapping):
        _validate_strict_schema(items, label=f"{label}[]")
    for keyword in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for index, branch in enumerate(branches):
                if isinstance(branch, Mapping):
                    _validate_strict_schema(
                        branch,
                        label=f"{label}.{keyword}[{index}]",
                    )
    for keyword in ("if", "then", "else", "not"):
        branch = schema.get(keyword)
        if isinstance(branch, Mapping):
            _validate_strict_schema(branch, label=f"{label}.{keyword}")


def _load_payload(path: Path, seen: frozenset[Path] = frozenset()) -> dict[str, Any]:
    resolved_path = path.resolve()
    if resolved_path in seen:
        raise ContractValidationError("contract inheritance cycle")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"cannot load contract: {path}") from exc
    if not isinstance(payload, dict):
        raise ContractValidationError("contract root must be an object")
    base_name = payload.get("extends")
    if base_name is not None:
        if (
            not isinstance(base_name, str)
            or not base_name
            or Path(base_name).name != base_name
        ):
            raise ContractValidationError("invalid contract base")
        base = _load_payload(
            path.parent / base_name,
            seen | {resolved_path},
        )
        merged = copy.deepcopy(base)
        merged["contractVersion"] = payload.get("contractVersion")
        merged["protocolVersion"] = payload.get("protocolVersion")
        merged["schemaTemplates"].update(
            copy.deepcopy(payload.get("schemaTemplates", {}))
        )
        overrides = payload.get("toolOverrides", {})
        if not isinstance(overrides, dict):
            raise ContractValidationError("toolOverrides must be an object")
        tools_by_action = {tool["action"]: tool for tool in merged["tools"]}
        for action, override in overrides.items():
            if action not in tools_by_action or not isinstance(override, dict):
                raise ContractValidationError(f"invalid tool override: {action}")
            tools_by_action[action].update(copy.deepcopy(override))
        additions = payload.get("toolAdditions", [])
        if not isinstance(additions, list):
            raise ContractValidationError("toolAdditions must be an array")
        existing_actions = set(tools_by_action)
        for addition in additions:
            if not isinstance(addition, dict):
                raise ContractValidationError("tool addition must be an object")
            action = addition.get("action")
            if (
                not isinstance(action, str)
                or not action
                or action in existing_actions
            ):
                raise ContractValidationError(f"invalid tool addition: {action}")
            merged["tools"].append(copy.deepcopy(addition))
            existing_actions.add(action)
        if "pageStateSchema" in payload:
            merged["pageStateSchema"] = copy.deepcopy(payload["pageStateSchema"])
        payload = merged
    return payload


def load_contract_registry(path: Path = CONTRACT_PATH) -> ToolContractRegistry:
    payload = _load_payload(path)
    contract_version = payload.get("contractVersion")
    protocol_version = payload.get("protocolVersion")
    if contract_version not in {"1.0", "2.0"}:
        raise ContractValidationError("unsupported contractVersion")
    if (contract_version, protocol_version) not in {
        ("1.0", "1.0"),
        ("2.0", "agui-native-v2"),
    }:
        raise ContractValidationError("unsupported protocolVersion")

    templates = payload.get("schemaTemplates")
    tools = payload.get("tools")
    errors = payload.get("errors")
    if not isinstance(templates, dict) or not isinstance(tools, list):
        raise ContractValidationError("schemaTemplates and tools are required")
    if not isinstance(errors, dict):
        raise ContractValidationError("errors are required")

    contracts: dict[str, ToolContract] = {}
    sdk_names: set[str] = set()
    for raw in tools:
        if not isinstance(raw, dict):
            raise ContractValidationError("tool entry must be an object")
        action = raw.get("action")
        sdk_name = raw.get("sdkToolName")
        if not isinstance(action, str) or not action:
            raise ContractValidationError("tool action is required")
        if action in contracts:
            raise ContractValidationError(f"duplicate action: {action}")
        if not isinstance(sdk_name, str) or not sdk_name:
            raise ContractValidationError(f"sdkToolName is required for {action}")
        if sdk_name in sdk_names:
            raise ContractValidationError(f"duplicate sdkToolName: {sdk_name}")
        if sdk_name != action.replace(".", "__"):
            raise ContractValidationError(f"sdkToolName is not reversible for {action}")

        input_template = raw.get("inputTemplate")
        output_template = raw.get("outputTemplate")
        if input_template not in templates or output_template not in templates:
            raise ContractValidationError(f"unknown schema template for {action}")
        input_schema = copy.deepcopy(templates[input_template])
        output_schema = copy.deepcopy(templates[output_template])
        _validate_strict_schema(input_schema, label=f"{action}.input")
        _validate_strict_schema(output_schema, label=f"{action}.output")

        executor = raw.get("executor")
        risk = raw.get("risk")
        context_effect = raw.get("contextEffect")
        timeout_ms = raw.get("timeoutMs", DEFAULT_TOOL_TIMEOUT_MS)
        if executor not in {"frontend", "host"}:
            raise ContractValidationError(f"invalid executor for {action}")
        if risk not in {"read", "temporary", "draft", "changeset", "persistent"}:
            raise ContractValidationError(f"invalid risk for {action}")
        if context_effect not in {"none", "mutates"}:
            raise ContractValidationError(f"invalid contextEffect for {action}")
        # Layout reserves measurement/save time around its 120-second solve.
        max_timeout_ms = (
            135_000 if action == "dashboard.set_widget_layout" else MAX_TOOL_TIMEOUT_MS
        )
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not MIN_TOOL_TIMEOUT_MS <= timeout_ms <= max_timeout_ms
        ):
            raise ContractValidationError(f"invalid timeoutMs for {action}")

        contracts[action] = ToolContract(
            action=action,
            version=raw.get("version"),
            executor=executor,
            risk=risk,
            context_effect=context_effect,
            bundle=str(raw.get("bundle")),
            sdk_tool_name=sdk_name,
            description=str(raw.get("description")),
            input_schema_id=f"davinci://v{contract_version}/input/{action}",
            output_schema_id=f"davinci://v{contract_version}/output/{action}",
            input_schema=MappingProxyType(input_schema),
            output_schema=MappingProxyType(output_schema),
            public=raw.get("public") is True,
            timeout_ms=timeout_ms,
        )
        sdk_names.add(sdk_name)

    for code, schema in errors.items():
        if not isinstance(schema, Mapping):
            raise ContractValidationError(f"error schema must be an object: {code}")
        _validate_strict_schema(schema, label=f"error.{code}")

    return ToolContractRegistry(
        raw_contract=payload,
        contracts_by_action=contracts,
        error_schemas=errors,
    )
