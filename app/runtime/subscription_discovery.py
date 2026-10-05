"""Retain bounded source/field evidence; never turn name matches into configuration.

Only tool receipts populate evidence. Native writes still validate permissions
and references, and semantic choices remain with the model or the user.
"""

from copy import deepcopy
import json
import re
from urllib.parse import unquote
from typing import Any

def _catalog_field(ref: str, dataset_ref: str) -> bool:
    """Check the two catalog encodings without inventing or rewriting IDs."""
    if ref.startswith("v2/"):
        match = re.fullmatch(r"v2/([A-Za-z][A-Za-z0-9_-]{0,63}):([^/]+)/([^/]+)", ref)
        if not match or any(not re.fullmatch(r"(?:[A-Za-z0-9_.~-]|%[0-9a-fA-F]{2})+", part)
                            for part in match.groups()[1:]):
            return False
        try:
            return f"{match[1]}:{unquote(match[2], errors='strict')}" == dataset_ref
        except UnicodeError:
            return False
    prefix = dataset_ref + "/"
    return ref.startswith(prefix) and bool(ref[len(prefix):]) and not re.search(
        r"[\s/\ufeff]", ref[len(prefix):]) and not re.search(r"[\s/\ufeff]", dataset_ref.split(":", 1)[1])



CATALOG_READ_TOOLS = frozenset({
    "mcp__davinci_data__catalog_search_datasets",
    "mcp__davinci_data__catalog_get_dataset_schema",
    "mcp__davinci_data__catalog_search_fields",
})
_FIELD_KEYS = {
    "fieldRef", "ref", "name", "label", "description", "roles", "role", "dataType",
    "sourceRef", "required", "requiredCondition", "isEmployeeAccount",
    "employeeAccountType", "filterCapabilities", "aggregation", "aggregations",
}


def catalog_scope_key(page_key: Any) -> dict:
    """Catalog metadata survives tab navigation, but not an owner/page change."""
    page_key = page_key if isinstance(page_key, dict) else {}
    return {key: page_key.get(key) for key in ("instance", "space")}


def catalog_payload(response: Any) -> dict:
    """Unwrap an MCP receipt without treating an error or arbitrary text as facts."""
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except (ValueError, TypeError):
            return {}
    if not isinstance(response, dict) or response.get("isError"):
        return {}
    structured = response.get("structuredContent")
    if isinstance(structured, dict):
        return catalog_payload(structured)
    if isinstance(response.get("content"), list):
        for block in response["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                parsed = catalog_payload(block.get("text"))
                if parsed:
                    return parsed
        return {}
    if response.get("status") in {"error", "failed", "permission_required"}:
        return {}
    return response


def record_catalog_evidence(task: dict, tool_name: str, arguments: dict, response: Any,
                            *, scope_key: Any = None) -> bool:
    """Record exact catalog facts, including the limits of filtered schema reads."""
    payload = catalog_payload(response)
    if tool_name not in CATALOG_READ_TOOLS or not payload:
        return False
    scope = catalog_scope_key(scope_key if scope_key is not None else task.get("scope_key"))
    current_scope = task.get("scope_key")
    if current_scope is not None and scope != catalog_scope_key(current_scope):
        return False
    task.setdefault("scope_key", scope)
    evidence = task.setdefault("data_evidence", {"datasets": {}})
    datasets = evidence.setdefault("datasets", {})
    if tool_name.endswith("catalog_search_datasets"):
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            return False
        rows = []
        for item in candidates[:20]:
            if not isinstance(item, dict) or not isinstance(item.get("datasetRef"), str):
                continue
            ref = item["datasetRef"]
            rows.append({"ref": ref, "label": item.get("name", ref),
                         "description": item.get("description"), "referenceKind": "catalog",
                         "permission": item.get("permission")})
            entry = datasets.setdefault(ref, {"datasetRef": ref, "fields": [], "complete": False})
            if entry.get("scope_key") != scope:
                entry.update(fields=[], complete=False)
            entry.update(name=item.get("name"), scope_key=scope)
            _merge_fields(entry, item.get("matchedFields", []))
        # A bounded directory supports explicit choices among shown candidates;
        # it never proves no other source exists or automatically changes a source.
        evidence["candidates"] = {
            "kind": "dataset", "referenceKind": "catalog", "results": rows,
            "truncated": bool(payload.get("nextCursor")) or len(candidates) > 20,
            "parentRef": None, "query": arguments.get("query"),
            "coverage": deepcopy(payload.get("coverage", {})),
        }
    elif tool_name.endswith("catalog_get_dataset_schema"):
        ref = payload.get("datasetRef") or (payload.get("dataset") or {}).get("datasetRef") or arguments.get("datasetRef")
        if not isinstance(ref, str) or not isinstance(payload.get("fields"), list):
            return False
        entry = datasets.setdefault(ref, {"datasetRef": ref, "fields": [], "complete": False})
        # No query/roles/fieldRefs restriction and every field returned are both
        # necessary. 'partial' may concern relations while fields are complete.
        filtered = any(arguments.get(key) for key in ("query", "roles", "fieldRefs"))
        count = payload.get("fieldCount")
        complete = (not filtered and payload.get("truncated") is False and
                    type(count) is int and count == len(payload["fields"]) and count <= 200)
        metadata_changed = (payload.get("metadataVersion") is not None and entry.get("metadataVersion") is not None
                            and payload["metadataVersion"] != entry["metadataVersion"])
        if complete or entry.get("scope_key") != scope or metadata_changed:
            entry["fields"] = []
            entry["complete"] = False
        _merge_fields(entry, payload["fields"])
        entry.update(scope_key=scope, fieldCount=count,
                     complete=complete or entry.get("complete", False),
                     coverage={"filtered": filtered, "truncated": payload.get("truncated"),
                               "returnedCount": len(payload["fields"])})
        if payload.get("metadataVersion") is not None:
            entry["metadataVersion"] = payload["metadataVersion"]
        for key in ("requiredFilters", "grainFieldRefs", "issues"):
            if key in payload:
                entry[key] = deepcopy(payload[key])
    elif tool_name.endswith("catalog_search_fields"):
        # Search endpoints may expose different result envelopes. Only explicit
        # field source references can join them; never guess from equal names.
        rows = payload.get("fields", payload.get("matches", []))
        if not isinstance(rows, list):
            return False
        for field in rows:
            if not isinstance(field, dict):
                continue
            field_ref = field.get("fieldRef")
            # A self-built dataset field may originate in a warehouse table.
            # sourceRef describes provenance, not the selected dataset identity.
            requested = arguments.get("datasetRefs") or []
            matching = [ref for ref in requested if isinstance(ref, str) and ":" in ref and
                        isinstance(field_ref, str) and (_catalog_field(field_ref, ref) or
                        not field_ref.startswith("v2/") and field_ref.startswith(ref + "/"))]
            if len(matching) != 1:
                continue
            ref = matching[0]
            entry = datasets.setdefault(ref, {"datasetRef": ref, "fields": [], "complete": False})
            if entry.get("scope_key") != scope:
                entry.update(fields=[], complete=False)
            entry["scope_key"] = scope
            _merge_fields(entry, [field])
    # Bound retained evidence by source, without dropping the selected source.
    native = task.get("native_readback") or {}
    selected = {query.get("datasetRef") for query in native.get("queries", [])
                if isinstance(query, dict)}
    while len(datasets) > 6:
        victim = next((key for key in datasets if key not in selected), next(iter(datasets)))
        del datasets[victim]
    return True


def _merge_fields(entry: dict, fields: list) -> None:
    """Deduplicate by exact reference and preserve existing definitions on partial reads."""
    merged = {(item.get("fieldRef") or item.get("ref")): item for item in entry.get("fields", [])}
    for field in fields:
        if not isinstance(field, dict):
            continue
        ref = field.get("fieldRef") or field.get("ref")
        if not isinstance(ref, str) or not ref:
            continue
        clean = {key: deepcopy(value) for key, value in field.items() if key in _FIELD_KEYS}
        merged[ref] = {**merged.get(ref, {}), **clean}
    entry["fields"] = list(merged.values())[:200]


def subscription_field_evidence(task: dict) -> dict:
    """Expose source-scoped metadata, without choosing sources or inferring gaps."""
    evidence = task.get("data_evidence") or {}
    scope = catalog_scope_key(task.get("scope_key"))
    datasets = [deepcopy(entry) for entry in (evidence.get("datasets") or {}).values()
                if isinstance(entry, dict) and entry.get("scope_key") == scope]
    native = task.get("native_readback") or {}
    current = (native.get("taskId") == task.get("native_task_id")
               and type(native.get("revision")) is int
               and native.get("revision") == task.get("revision"))
    queries = deepcopy(native.get("queries", [])) if current else []
    if not datasets and not queries:
        return {}
    return {"datasets": datasets, "configuredQueries": queries,
            "notice": "目录字段与当前查询输出属于不同引用范围；同名不证明相同口径。"
                      "目录未完整返回不代表字段不存在，已配置输出不代表已查询实际数据。"
                      "复用当前有效引用，只补必要证据。"}
