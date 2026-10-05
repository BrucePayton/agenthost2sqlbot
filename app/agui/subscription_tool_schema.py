"""Derive the subscription model view from canonical input-schema annotations."""

from copy import deepcopy
from typing import Any


def _cannot_match_without_fields(schema: Any, hidden: set[str]) -> bool:
    """Recognize only canonical presence predicates made false by hidden inputs."""
    if not isinstance(schema, dict):
        return schema is False
    if hidden.intersection(schema.get("required", [])):
        return True
    alternatives = schema.get("anyOf")
    return bool(alternatives) and all(
        _cannot_match_without_fields(branch, hidden) for branch in alternatives
    )


def _references_hidden_fields(schema: Any, hidden: set[str]) -> bool:
    """Reject unfamiliar constraints instead of weakening a hidden-field rule."""
    if isinstance(schema, list):
        return any(_references_hidden_fields(item, hidden) for item in schema)
    if not isinstance(schema, dict):
        return False
    if hidden.intersection(schema.get("required", [])) or hidden.intersection(
        schema.get("properties", {})
    ):
        return True
    return any(_references_hidden_fields(value, hidden) for value in schema.values())


def _hide_optional_fields(schema: dict, hidden: set[str]) -> None:
    """Remove only compatibility constraints vacuous when marked fields are absent."""
    if hidden.intersection(schema.get("required", [])):
        raise ValueError("A required subscription input cannot be model-hidden")
    for name in hidden:
        schema.get("properties", {}).pop(name, None)
    if _cannot_match_without_fields(schema.get("if"), hidden):
        if "else" in schema:
            raise ValueError("Unsupported model-hidden conditional with else")
        schema.pop("if")
        schema.pop("then", None)
    negative = schema.get("not")
    if _cannot_match_without_fields(negative, hidden):
        schema.pop("not")
    elif isinstance(negative, dict) and set(negative) == {"anyOf"}:
        negative["anyOf"] = [branch for branch in negative["anyOf"]
                             if not _cannot_match_without_fields(branch, hidden)]
    dependencies = schema.get("dependentRequired", {})
    for name in list(dependencies):
        if name in hidden:
            dependencies.pop(name)
        elif hidden.intersection(dependencies[name]):
            raise ValueError("Unsupported dependency on a model-hidden input")
    if "dependentRequired" in schema and not dependencies:
        schema.pop("dependentRequired")


def _branch_field_hint(schema: dict) -> str:
    """Explain native conditional field exclusions as positive branch choices."""
    branches = []
    discriminator = None
    for rule in schema.get("allOf", []):
        if not isinstance(rule, dict) or set(rule) != {"if", "then"}:
            return ""
        condition, consequence = rule["if"], rule["then"]
        properties = condition.get("properties", {})
        if len(properties) != 1 or condition.get("required") != list(properties):
            return ""
        name, value = next(iter(properties.items()))
        if set(value) != {"const"} or discriminator not in (None, name):
            return ""
        excluded = consequence.get("not", {}).get("anyOf", [])
        if set(consequence) != {"not"} or not excluded or any(
            set(item) != {"required"} or len(item["required"]) != 1 for item in excluded
        ):
            return ""
        discriminator = name
        branches.append((value["const"], {item["required"][0] for item in excluded}))
    if not branches:
        return ""
    varying_fields = set.union(*(excluded for _, excluded in branches))
    clauses = [f"{value} [{', '.join(name for name in schema.get('properties', {}) if name in varying_fields - excluded)}]"
               for value, excluded in branches]
    return f"Fields by {discriminator}: {'; '.join(clauses)}. Other shared fields remain available."


def _project(schema: Any, inherited_hidden: set[str] | None = None) -> Any:
    """Project nested canonical objects without changing modern constraints."""
    if not isinstance(schema, dict):
        return deepcopy(schema)
    result = deepcopy(schema)
    hidden = set(inherited_hidden or ()) | {
        name for name, field in result.get("properties", {}).items()
        if isinstance(field, dict) and field.get("x-model-hidden") is True
    }
    _hide_optional_fields(result, hidden)
    for key in ("if", "then", "else", "not"):
        if _references_hidden_fields(result.get(key), hidden):
            raise ValueError(f"Unsupported model-hidden constraint: {key}")
    for key in ("properties", "$defs"):
        if key in result:
            result[key] = {name: _project(value) for name, value in result[key].items()}
    if "items" in result:
        result["items"] = _project(result["items"])
    for key in ("allOf", "oneOf", "anyOf"):
        if key in result:
            projected = [_project(branch, hidden) for branch in result[key]]
            result[key] = [branch for branch in projected if branch != {}] if key == "allOf" else projected
            if key == "allOf" and not result[key]:
                result.pop(key)
    for key in ("if", "then", "else", "not"):
        if key in result:
            result[key] = _project(result[key], hidden)
    for key in ("if", "then", "else", "not", "allOf", "oneOf", "anyOf"):
        if _references_hidden_fields(result.get(key), hidden):
            raise ValueError(f"Unsupported model-hidden constraint: {key}")
    hint = _branch_field_hint(result)
    if hint:
        result["description"] = " ".join(filter(None, (result.get("description"), hint)))
    result.pop("x-model-hidden", None)
    return result


def _local_references(value: Any) -> set[str]:
    """Collect canonical local definition names without expanding shared schemas."""
    if isinstance(value, list):
        return set().union(*(_local_references(item) for item in value))
    if not isinstance(value, dict):
        return set()
    ref = value.get("$ref", "")
    found = {ref.split("/")[2]} if isinstance(ref, str) and ref.startswith("#/$defs/") else set()
    return found | set().union(*(_local_references(item) for item in value.values()))


def project_subscription_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    """Hide annotated optional inputs and unreachable definitions; retain validation."""
    result = _project(parameters)
    definitions = result.pop("$defs", {})
    reachable = _local_references(result)
    pending = list(reachable)
    while pending:
        name = pending.pop()
        if name not in definitions:
            raise ValueError(f"Missing subscription schema definition: {name}")
        fresh = _local_references(definitions[name]) - reachable
        reachable.update(fresh)
        pending.extend(fresh)
    if reachable:
        result["$defs"] = {name: value for name, value in definitions.items() if name in reachable}
    return result
