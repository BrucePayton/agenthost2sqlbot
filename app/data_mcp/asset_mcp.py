from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.errors import AppError


@dataclass(frozen=True, slots=True)
class AssetField:
    ref: str
    name: str
    data_type: str
    description: str
    required: bool
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorizedAsset:
    ref: str
    name: str
    description: str
    fields: tuple[AssetField, ...]
    required_filters: tuple[dict[str, Any], ...]
    metadata_version: str | None


class AssetAuthorizationPort(Protocol):
    async def authorize(
        self, *, user_subject: str, dataset_refs: list[str]
    ) -> dict[str, AuthorizedAsset]: ...

    async def search_authorized(
        self, *, user_subject: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


class AssetMcpClient:
    """Deterministic, fail-closed client for the external read-only Asset MCP."""

    def __init__(self, settings: Settings) -> None:
        self.url = str(settings.asset_mcp_url)
        self.timeout = settings.asset_mcp_timeout_seconds

    async def aclose(self) -> None:
        return None

    async def authorize(
        self, *, user_subject: str, dataset_refs: list[str]
    ) -> dict[str, AuthorizedAsset]:
        refs = list(dict.fromkeys(ref.strip() for ref in dataset_refs if ref.strip()))
        if not refs or len(refs) != len(dataset_refs):
            raise AppError("asset_reference_invalid", "Dataset references are invalid.", 400)
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout), trust_env=False
                ) as client,
                streamable_http_client(self.url, http_client=client) as streams,
                ClientSession(streams[0], streams[1]) as session,
            ):
                await session.initialize()
                check = _payload(
                    await session.call_tool(
                        "access.check_resources",
                        {"obId": user_subject, "datasetRefs": refs},
                    ),
                    "access.check_resources",
                )
                _require_authorized(check, refs)
                assets: dict[str, AuthorizedAsset] = {}
                for ref in refs:
                    schema = _payload(
                        await session.call_tool(
                            "catalog.get_dataset_schema",
                            {
                                "obId": user_subject,
                                "datasetRef": ref,
                                "limit": 200,
                            },
                        ),
                        "catalog.get_dataset_schema",
                    )
                    assets[ref] = _parse_asset_schema(ref, schema)
                return assets
        except AppError:
            raise
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise AppError(
                "asset_mcp_unavailable",
                "Asset authorization service is unavailable.",
                503,
            ) from exc
        except Exception as exc:
            raise AppError(
                "asset_mcp_invalid_response",
                "Asset authorization service returned an invalid response.",
                502,
            ) from exc

    async def search_authorized(
        self, *, user_subject: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout), trust_env=False
                ) as client,
                streamable_http_client(self.url, http_client=client) as streams,
                ClientSession(streams[0], streams[1]) as session,
            ):
                await session.initialize()
                payload = _payload(
                    await session.call_tool(
                        "catalog.search_datasets",
                        {
                            "obId": user_subject,
                            "query": query,
                            "scope": "authorized",
                            "limit": min(max(limit, 1), 50),
                            "maxMatchedFields": 10,
                        },
                    ),
                    "catalog.search_datasets",
                )
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "asset_mcp_unavailable",
                "Asset authorization service is unavailable.",
                503,
            ) from exc
        candidates = payload.get("candidates")
        return list(candidates) if isinstance(candidates, list) else []


def _payload(result: Any, tool_name: str) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise AppError(
            "asset_mcp_tool_failed", f"Asset MCP tool {tool_name} failed.", 502
        )
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value.get("data") if isinstance(value.get("data"), dict) else value
    raise AppError(
        "asset_mcp_invalid_response",
        f"Asset MCP tool {tool_name} returned no JSON object.",
        502,
    )


def _require_authorized(payload: dict[str, Any], expected_refs: list[str]) -> None:
    resources = payload.get("resources")
    if not isinstance(resources, list):
        raise AppError(
            "asset_mcp_invalid_response", "Asset authorization result is invalid.", 502
        )
    by_ref = {
        str(item.get("datasetRef")): item
        for item in resources
        if isinstance(item, dict) and item.get("datasetRef")
    }
    denied = [
        ref
        for ref in expected_refs
        if by_ref.get(ref, {}).get("permission") != "authorized"
    ]
    if denied:
        raise AppError(
            "data_agent_no_permission",
            "One or more datasets are not authorized for the current user.",
            403,
            details={"dataset_refs": denied},
        )


def _parse_asset_schema(ref: str, payload: dict[str, Any]) -> AuthorizedAsset:
    if payload.get("status") not in {"ok", "partial"}:
        raise AppError(
            "asset_schema_unavailable", "Authorized dataset schema is unavailable.", 502
        )
    dataset = payload.get("dataset")
    fields = payload.get("fields")
    if not isinstance(dataset, dict) or dataset.get("datasetRef") != ref:
        raise AppError("asset_schema_mismatch", "Dataset schema identity mismatch.", 502)
    if dataset.get("permission") != "authorized" or not isinstance(fields, list):
        raise AppError(
            "data_agent_no_permission", "Dataset schema is not authorized.", 403
        )
    parsed_fields = tuple(
        AssetField(
            ref=str(item.get("fieldRef") or ""),
            name=str(item.get("name") or "").strip(),
            data_type=str(item.get("dataType") or "unknown"),
            description=str(item.get("description") or ""),
            required=bool(item.get("required") or item.get("requiredCondition")),
            roles=tuple(str(role) for role in item.get("roles") or []),
        )
        for item in fields
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    )
    if not parsed_fields:
        raise AppError("asset_schema_empty", "Authorized dataset has no fields.", 403)
    required_filters = payload.get("requiredFilters") or []
    if not isinstance(required_filters, list):
        raise AppError(
            "asset_mcp_invalid_response", "requiredFilters must be a list.", 502
        )
    return AuthorizedAsset(
        ref=ref,
        name=str(dataset.get("name") or ref),
        description=str(dataset.get("description") or ""),
        fields=parsed_fields,
        required_filters=tuple(
            item for item in required_filters if isinstance(item, dict)
        ),
        metadata_version=(
            str(payload["metadataVersion"]) if payload.get("metadataVersion") else None
        ),
    )
