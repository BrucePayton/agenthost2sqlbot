"""Refresh checked-in feedback fixtures from a supplied Data MCP checkout.

Development utility only: the Host test suite uses the generated JSON and does
not import or install Data MCP. This command never starts a server or uses APIs.
"""

import argparse
import ast
import asyncio
import copy
import hashlib
import json
import re
import sys
from pathlib import Path


def export_definition_supplement(responses: dict) -> dict:
    """Execute bounded production metadata completion against synthetic providers."""
    from davinci_data_mcp.auth.models import TrustedIdentity
    from davinci_data_mcp.contracts.access import AccessCheckResult, ResourceAccess
    from davinci_data_mcp.contracts.catalog import (
        DatasetSchemaResult,
        DatasetSearchRequest,
        DatasetSearchResult,
    )
    from davinci_data_mcp.contracts.common import PermissionState, ResultStatus
    from davinci_data_mcp.services.catalog import CatalogService
    from davinci_feedback_fixture import early_responses

    payloads = early_responses("early_same_dataset", {"responses": responses})
    upstream = copy.deepcopy(payloads["catalog.search_datasets"])
    for candidate in upstream["candidates"]:
        for field in candidate["matchedFields"]:
            field["description"] = None

    class Probe:
        """Record source reads while exercising the real service and live ACL path."""

        def __init__(self):
            self.schema_reads = []
            self.access_checks = 0

        async def search(self, request, identity):
            """Return identities without their source definitions."""
            return DatasetSearchResult.model_validate(upstream)

        async def get_schema(self, ref, identity):
            """Return the authoritative fixture definitions for the requested dataset."""
            self.schema_reads.append(ref.root)
            result = DatasetSchemaResult.model_validate(
                payloads["catalog.get_dataset_schema"][ref.root]
            )
            # The real Davinci provider lacks relations even when fields are complete.
            return result.model_copy(
                update={
                    "status": ResultStatus.PARTIAL,
                    "issues": ["relation_summary_unavailable"],
                }
            )

        async def check(self, request, identity):
            """Authorize only fixture datasets and exact fixture fields."""
            self.access_checks += 1
            return AccessCheckResult(
                status=ResultStatus.OK,
                resources=[
                    ResourceAccess(
                        dataset_ref=ref,
                        permission=PermissionState.AUTHORIZED,
                        authorized_field_refs=[
                            f["fieldRef"]
                            for f in payloads["catalog.get_dataset_schema"][ref.root][
                                "fields"
                            ]
                        ],
                    )
                    for ref in request.dataset_refs
                ],
            )

    probe = Probe()
    service = CatalogService(probe, probe, probe)
    principal = TrustedIdentity(
        subject="synthetic",
        ob_id="101",
        delegated_token="synthetic",
        scopes=frozenset({"davinci:data:read"}),
    )
    result = asyncio.run(
        service.search_datasets(
            DatasetSearchRequest(query="结算额", limit=3, maxMatchedFields=3), principal
        )
    )
    return {
        "upstreamSearch": upstream,
        "result": result.model_dump(mode="json", by_alias=True),
        "schemaReads": probe.schema_reads,
        "accessChecks": probe.access_checks,
        "measurement": "Production service executed at fixture export; not live upstream latency",
    }


def export_fixture(source: Path, target: Path) -> None:
    """Validate canonical samples with production models and export their schemas."""
    sys.path.insert(0, str(source / "src"))
    from davinci_data_mcp.contracts.catalog import (
        DatasetSchemaRequest,
        DatasetSchemaResult,
        DatasetSearchRequest,
        DatasetSearchResult,
        DatasetUsagesResult,
    )
    from davinci_data_mcp.contracts.common import CamelModel
    from davinci_data_mcp.contracts.refs import _DATASET_REF, _FIELD_REF, DatasetRef
    from davinci_data_mcp.contracts.resolution import (
        ResolveDataRequirementsRequest,
        ResolveDataRequirementsResult,
    )
    from pydantic import Field, create_model

    # This tool has no request model in production; mirror its actual signature.
    usages_request = create_model(
        "DatasetUsagesRequest",
        __base__=CamelModel,
        datasetRef=(DatasetRef, ...),
        limit=(int, Field(default=10, ge=1, le=20)),
    )
    models = {
        "catalog.get_dataset_schema": (DatasetSchemaRequest, DatasetSchemaResult),
        "catalog.search_datasets": (DatasetSearchRequest, DatasetSearchResult),
        "catalog.list_dataset_usages": (usages_request, DatasetUsagesResult),
        "analytics.resolve_data_requirements": (
            ResolveDataRequirementsRequest,
            ResolveDataRequirementsResult,
        ),
    }
    descriptions = {}
    for name in ("catalog_tools.py", "resolution_tools.py"):
        parsed = ast.parse((source / "src/davinci_data_mcp/mcp" / name).read_text())
        for node in ast.walk(parsed):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "tool"
            ):
                keywords = {item.arg: item.value for item in node.keywords}
                if "name" in keywords and "description" in keywords:
                    descriptions[ast.literal_eval(keywords["name"])] = ast.literal_eval(
                        keywords["description"]
                    )

    def field(ref: str, name: str, description: str) -> dict:
        """Create a complete authorized metric summary."""
        return {
            "fieldRef": ref,
            "name": name,
            "description": description,
            "roles": ["metric"],
            "dataType": "decimal",
        }

    def candidate(ref: str, name: str, fields: list[dict]) -> dict:
        """Create the real DatasetCandidate envelope used by search and schema."""
        return {
            "datasetRef": ref,
            "name": name,
            "datasetType": "warehouseTopic",
            "permission": "authorized",
            "matchedFields": fields,
            "scores": {"keyword": 1, "final": 1},
        }

    net = field(
        "warehouseTopic:662/net_sales",
        "净成交额",
        "支付金额减去已退款金额，排除取消订单；单位元",
    )
    paid = field("warehouseTopic:662/paid", "成交额", "支付成功金额，包含已退款订单")
    accepted = field(
        "warehouseTopic:663/accepted", "成交额", "验收入库估价金额，不代表实际支付"
    )
    defaults = [candidate("warehouseTopic:662", "回收订单", [net])]
    combinations = [
        candidate("warehouseTopic:662", "回收订单", [paid]),
        candidate("warehouseTopic:663", "门店回收", [accepted]),
    ]
    responses = {}
    for case, candidates in (("default", defaults), ("combination", combinations)):
        responses[case] = {
            "catalog.search_datasets": {
                "status": "ok",
                "candidates": candidates,
                "nextCursor": None,
                "coverage": {
                    "scope": "authorized",
                    "upstreamPage": 1,
                    "upstreamPageSize": 5,
                    "totalMatches": len(candidates),
                    "exhausted": True,
                },
                "issues": [],
            },
            "catalog.get_dataset_schema": {
                item["datasetRef"]: {
                    "status": "ok",
                    "dataset": item,
                    "fields": item["matchedFields"],
                    "requiredFilters": [],
                    "sourceRefs": [],
                    "relationSummary": None,
                    "metadataVersion": "feedback-v1",
                    "fieldCount": 1,
                    "truncated": False,
                    "issues": [],
                }
                for item in candidates
            },
            "catalog.list_dataset_usages": {
                "status": "ok",
                "datasetRef": "warehouseTopic:662",
                "usages": [
                    {
                        "dashboardId": "333",
                        "name": "回收经营",
                        "resourceType": "CUSTOM",
                        "evidence": "dataset_association",
                    }
                ],
                "truncated": False,
                "supportedResourceTypes": ["CUSTOM", "SPACE"],
                "coverageComplete": False,
                "issues": [
                    "FIELD_BINDINGS_NOT_CHECKED",
                    "RESOURCE_TYPE_COVERAGE_LIMITED",
                ],
            },
            "analytics.resolve_data_requirements": {
                "status": "need_clarification",
                "clarification": {
                    "question": "选择哪一个数据集和指标口径？",
                    "requirement": "成交额",
                    "options": [
                        {
                            "label": item["name"] + " · 成交额",
                            "datasetRef": item["datasetRef"],
                            "datasetName": item["name"],
                            "fieldRef": item["matchedFields"][0]["fieldRef"],
                            "fieldName": "成交额",
                            "description": item["matchedFields"][0]["description"],
                        }
                        for item in combinations
                    ],
                },
                "issues": ["AMBIGUOUS_DATASET_SELECTION"],
            },
        }
        for tool_name, payload in responses[case].items():
            model = models[tool_name][1]
            if tool_name == "catalog.get_dataset_schema":
                responses[case][tool_name] = {
                    ref: model.model_validate(value).model_dump(
                        mode="json", by_alias=True
                    )
                    for ref, value in payload.items()
                }
            else:
                responses[case][tool_name] = model.model_validate(payload).model_dump(
                    mode="json", by_alias=True
                )

    tools = {}
    for name, (request, response) in models.items():
        schemas = {
            "inputSchema": request.model_json_schema(by_alias=True),
            "outputSchema": response.model_json_schema(by_alias=True),
        }
        # Pydantic field_validator regexes are not emitted in JSON Schema;
        # preserve their exact production patterns in the portable snapshot.
        for schema in schemas.values():
            for ref, pattern in (
                ("DatasetRef", _DATASET_REF.pattern),
                ("FieldRef", _FIELD_REF.pattern),
            ):
                if ref in schema.get("$defs", {}):
                    schema["$defs"][ref]["pattern"] = re.sub(
                        r"\(\?P<[^>]+>", "(?:", pattern
                    )
        nullable = {
            "catalog.get_dataset_schema": ("fieldRefs", "roles"),
            "catalog.search_datasets": (
                "datasetTypes",
                "businessDomains",
                "maxMatchedFields",
            ),
            "analytics.resolve_data_requirements": ("hints",),
        }.get(name, ())
        # Transport signatures accept None, then normalize it before the
        # service request model. Keep that public tool behavior in the fixture.
        for key in nullable:
            value = schemas["inputSchema"]["properties"][key]
            value.pop("default", None)
            schemas["inputSchema"]["properties"][key] = {
                "anyOf": [value, {"type": "null"}],
                "default": None,
            }
        tools[name] = {"name": name, "description": descriptions[name], **schemas}
    files = [
        "contracts/catalog.py",
        "contracts/refs.py",
        "contracts/resolution.py",
        "mcp/catalog_tools.py",
        "mcp/resolution_tools.py",
        "services/catalog.py",
    ]
    data = {
        "provenance": {
            "generator": "tests/live/export_davinci_feedback_fixture.py",
            "validatedWithProductionPydantic": True,
            "sourceSha256": {
                file: hashlib.sha256(
                    (source / "src/davinci_data_mcp" / file).read_bytes()
                ).hexdigest()
                for file in files
            },
        },
        "tools": tools,
        "responses": responses,
        "definitionSupplement": export_definition_supplement(responses),
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print("Validated and exported", len(tools), "production tool contracts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    export_fixture(args.source, args.target)
