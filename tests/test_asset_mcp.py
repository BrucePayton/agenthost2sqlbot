from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.data_mcp.asset_mcp import (
    _parse_asset_schema,
    _payload,
    _require_authorized,
)
from app.errors import AppError


def test_asset_permission_requires_every_requested_dataset() -> None:
    payload = {
        "status": "ok",
        "resources": [
            {"datasetRef": "warehouseTable:a", "permission": "authorized"},
            {
                "datasetRef": "warehouseTable:b",
                "permission": "discoverable_but_unauthorized",
            },
        ],
    }
    with pytest.raises(AppError) as raised:
        _require_authorized(payload, ["warehouseTable:a", "warehouseTable:b"])
    assert raised.value.code == "data_agent_no_permission"
    assert raised.value.details == {"dataset_refs": ["warehouseTable:b"]}


def test_asset_schema_preserves_permission_metadata() -> None:
    asset = _parse_asset_schema(
        "warehouseTable:knowledge.sync_job",
        {
            "status": "partial",
            "dataset": {
                "datasetRef": "warehouseTable:knowledge.sync_job",
                "name": "同步任务",
                "description": "受治理同步任务",
                "permission": "authorized",
            },
            "fields": [
                {
                    "fieldRef": "warehouseTable:knowledge.sync_job/1",
                    "name": "status",
                    "dataType": "varchar",
                    "description": "状态",
                    "required": True,
                    "roles": ["dimension"],
                }
            ],
            "requiredFilters": [{"fieldRef": "x", "operator": "eq"}],
            "metadataVersion": "sha256:test",
        },
    )
    assert asset.ref == "warehouseTable:knowledge.sync_job"
    assert asset.fields[0].required is True
    assert asset.required_filters == ({"fieldRef": "x", "operator": "eq"},)


def test_mcp_payload_rejects_tool_error_without_echoing_content() -> None:
    result = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text='{"password":"must-not-leak"}')],
        structuredContent=None,
    )
    with pytest.raises(AppError) as raised:
        _payload(result, "access.check_resources")
    assert raised.value.code == "asset_mcp_tool_failed"
    assert "must-not-leak" not in raised.value.message
