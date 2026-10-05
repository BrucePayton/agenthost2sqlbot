"""Offline business fixture using schemas exported from production Data MCP.

No Data MCP checkout/install is needed at runtime. Both requests and receipts
are validated locally; this server never connects to any business API.
"""

import copy
import json
import sys
import time
from pathlib import Path

from jsonschema import ValidationError, validate

CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures/davinci_feedback_contracts.json"
)
if __name__ == "__main__" and len(sys.argv) > 3:
    CONTRACT_PATH = Path(sys.argv[3])
CONTRACTS = json.loads(CONTRACT_PATH.read_text())


def early_options(case: str) -> list[dict]:
    """Parameterize fixture business identities independently of production routing."""
    same = case in {"early_same_dataset", "early_definition_missing"}
    datasets = [
        "warehouseTopic:741",
        "warehouseTopic:741" if same else "warehouseTopic:852",
    ]
    names = ["渠道结算", "渠道结算" if same else "仓储核算"]
    return [
        {
            "datasetRef": datasets[0],
            "datasetName": names[0],
            "fieldRef": datasets[0] + "/booked_amount",
            "fieldName": "结算额",
            "description": "已入账金额，包含退货",
            "label": names[0] + " · 结算额",
        },
        {
            "datasetRef": datasets[1],
            "datasetName": names[1],
            "fieldRef": datasets[1] + "/accepted_value",
            "fieldName": "结算额",
            "description": "验收入库估值，不代表实际支付",
            "label": names[1] + " · 结算额",
        },
    ]


def early_responses(case: str, contracts: dict | None = None) -> dict:
    """Build varied catalog evidence while retaining production response schemas."""
    contracts = contracts or CONTRACTS
    responses = copy.deepcopy(contracts["responses"]["combination"])
    search = responses["catalog.search_datasets"]
    template = copy.deepcopy(search["candidates"][0])
    schema_template = copy.deepcopy(
        next(iter(responses["catalog.get_dataset_schema"].values()))
    )
    grouped = {}
    schemas = {}
    for option in early_options(case):
        ref = option["datasetRef"]
        field = copy.deepcopy(template["matchedFields"][0])
        field.update(
            fieldRef=option["fieldRef"],
            name=option["fieldName"],
            description=option["description"],
        )
        if ref not in grouped:
            candidate = copy.deepcopy(template)
            candidate.update(
                datasetRef=ref, name=option["datasetName"], matchedFields=[]
            )
            grouped[ref] = candidate
        grouped[ref]["matchedFields"].append(field)
    search["candidates"] = list(grouped.values())
    search["coverage"]["totalMatches"] = len(grouped)
    for ref, candidate in grouped.items():
        schema = copy.deepcopy(schema_template)
        schema.update(
            dataset=candidate,
            fields=candidate["matchedFields"],
            fieldCount=len(candidate["matchedFields"]),
        )
        schemas[ref] = schema
    responses["catalog.get_dataset_schema"] = schemas
    responses["analytics.resolve_data_requirements"]["clarification"]["options"] = (
        early_options(case)
    )
    if case == "early_definition_missing":
        # Exported by running the real CatalogService with an incomplete search
        # provider and complete schema provider; not an invented successful receipt.
        search.clear()
        search.update(copy.deepcopy(contracts["definitionSupplement"]["result"]))
    if case == "early_no_result":
        search["candidates"] = []
        search["coverage"]["totalMatches"] = 0
    return responses


def fixture_payload(case: str, name: str, arguments: dict) -> dict:
    """Validate real serialized contracts and return deterministic business data."""
    tool = CONTRACTS["tools"][name]
    validate(arguments, tool["inputSchema"])
    if name == "catalog.get_dataset_schema" and any(
        ref.rsplit("/", 1)[0] != arguments["datasetRef"]
        for ref in (arguments.get("fieldRefs") or [])
    ):
        raise ValueError("fieldRefs must belong to datasetRef")
    responses = (
        early_responses(case)
        if case.startswith("early_")
        else CONTRACTS["responses"][
            "combination" if case == "combination" else "default"
        ]
    )
    payload = copy.deepcopy(responses[name])
    if name == "catalog.get_dataset_schema":
        payload = payload[arguments["datasetRef"]]
        if arguments.get("fieldRefs"):
            requested = set(arguments["fieldRefs"])
            payload["fields"] = [
                field for field in payload["fields"] if field["fieldRef"] in requested
            ]
    elif name == "catalog.list_dataset_usages":
        payload["datasetRef"] = arguments["datasetRef"]
    elif name == "catalog.search_datasets":
        payload["coverage"]["scope"] = arguments.get("scope", "authorized")
    validate(payload, tool["outputSchema"])
    return payload


async def serve(trace: Path, case: str) -> None:
    """Serve only checked-in fixture data over isolated stdio transport."""
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server = Server("feedback-fixture")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """Advertise production descriptions and schema-constrained inputs."""
        return [types.Tool(**tool) for tool in CONTRACTS["tools"].values()]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> dict | types.CallToolResult:
        """Record public fixture parameters and surface schema violations."""
        entry = {"name": name, "arguments": arguments, "atMonotonic": time.monotonic()}
        if case in {"early_permission", "early_upstream"}:
            validate(arguments, CONTRACTS["tools"][name]["inputSchema"])
            message = (
                "PERMISSION_DENIED: 当前账号没有此搜索范围的访问权限"
                if case == "early_permission"
                else "UPSTREAM_UNAVAILABLE: 数据目录服务暂时不可用"
            )
            entry.update(
                result={"isError": True, "message": message}, resultChars=len(message)
            )
            with trace.open("a") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=message)]
            )
        try:
            payload = fixture_payload(case, name, arguments)
        except (ValueError, KeyError, ValidationError) as error:
            entry["parameterError"] = True
            with trace.open("a") as stream:
                stream.write(json.dumps(entry) + "\n")
            return types.CallToolResult(
                isError=True,
                content=[
                    types.TextContent(
                        type="text", text="INVALID_ARGUMENT: " + str(error)
                    )
                ],
            )
        serialized = json.dumps(payload, ensure_ascii=False)
        entry.update(result=payload, resultChars=len(serialized))
        with trace.open("a") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return payload

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(serve(Path(sys.argv[1]), sys.argv[2]))
