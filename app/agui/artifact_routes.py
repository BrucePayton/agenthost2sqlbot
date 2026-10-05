from __future__ import annotations

import json

from fastapi import APIRouter, Request

from app.agui.snapshot_artifacts import (
    MAX_SNAPSHOT_BYTES,
    SnapshotArtifactStore,
    snapshot_owner_key,
)
from app.api.dependencies import Identity, get_services
from app.errors import AppError

router = APIRouter(prefix="/agent-api/artifacts")


@router.post("/snapshots")
async def upload_snapshot(
    request: Request,
    identity: Identity,
) -> dict[str, str]:
    if not get_services(request).settings.davinci_local_integration:
        raise AppError(
            "capability_unavailable",
            "Snapshot artifacts require the trusted Davinci gateway.",
            503,
        )
    raw = await request.body()
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise AppError("snapshot_too_large", "Snapshot exceeds the 8 MiB limit.", 413)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError("invalid_request", "Snapshot body must be JSON.", 422) from exc
    if not isinstance(body, dict) or set(body) != {"pageInstanceId", "artifact"}:
        raise AppError("invalid_request", "Snapshot body is invalid.", 422)
    page_instance_id = body.get("pageInstanceId")
    artifact = body.get("artifact")
    resource_id = artifact.get("dashboardId") if isinstance(artifact, dict) else None
    if (
        not isinstance(page_instance_id, str)
        or not isinstance(artifact, dict)
        or artifact.get("schemaVersion") != "davinci-dashboard-snapshot-v1"
        or artifact.get("pageInstanceId") != page_instance_id
        or not isinstance(resource_id, str)
        or not resource_id
    ):
        raise AppError("invalid_request", "Snapshot binding is invalid.", 422)
    store: SnapshotArtifactStore = request.app.state.snapshot_artifacts
    try:
        snapshot_ref = store.create(
            owner_key=snapshot_owner_key(identity.user_id),
            page_instance_id=page_instance_id,
            resource_id=resource_id,
            artifact=artifact,
        )
    except ValueError as exc:
        raise AppError("invalid_request", "Snapshot is invalid.", 422) from exc
    return {"snapshotRef": snapshot_ref}
