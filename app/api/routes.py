import json
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import text

from app.api.dependencies import AppServices, Identity, get_services
from app.api.schemas import (
    AttachmentOut,
    MeOut,
    MessageEventOut,
    MessageFeedbackIn,
    PreferencesIn,
    PreferencesOut,
    SessionContextOut,
    SessionFileOut,
    SessionFilesOut,
    SessionOut,
    SessionRename,
    SessionSkillOut,
    SessionSkillsOut,
    TurnAccepted,
    TurnCreate,
    TurnOut,
    WorkspaceOut,
)
from app.auth.models import WorkspaceRole
from app.db.models import AttachmentRecord, TurnEventRecord, WorkspaceRecord
from app.errors import AppError
from app.feedback import list_feedback, save_feedback
from app.preferences import merge_preferences
from app.runtime.cohorts import probe_runtime_cohort
from app.turns.sse import stream_turn_events
from app.workspaces.models import WorkspaceEntry

router = APIRouter(prefix="/api")
Services = Annotated[AppServices, Depends(get_services)]


@router.get("/health")
async def health(services: Services) -> dict:
    async with services.database.session() as db:
        await db.execute(text("SELECT 1"))
    entries = services.workspaces.all()
    execution = (
        await services.execution_availability.snapshot()
        if services.execution_availability is not None
        else None
    )
    payload = {
        "status": (
            "degraded"
            if execution is not None and execution.status == "degraded"
            else "ready"
        ),
        "database": "ok",
        "memory": "ok" if services.memory_scopes.ready else "unavailable",
        "workspace_count": len(entries),
        "valid_workspace_count": sum(entry.available for entry in entries),
        "runtime": probe_runtime_cohort(
            services.runtime, services.settings
        ).to_health_dict(),
        "identity_mode": services.settings.identity_mode,
        "deployment_constraint": services.settings.deployment_constraint,
    }
    if services.settings.security_marker is not None:
        payload["security_marker"] = services.settings.security_marker
    if execution is not None:
        payload["execution"] = execution.to_health_dict()
    return payload


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(services: Services, identity: Identity) -> list[WorkspaceOut]:
    if services.settings.identity_mode in {"obid", "davinci_passthrough"}:
        await services.workspace_provisioner.ensure(identity)
    membership_items = await services.workspace_access.list_memberships(identity)
    memberships = {
        membership.workspace_id: membership for membership in membership_items
    }
    workspaces = await services.workspace_repository.list_for_user(identity.user_id)
    can_manage_global = await services.platform_skill_access.can_manage_global_skills(
        identity
    )
    counts = await services.skills.count_effective_by_workspace(
        tuple(item.workspace_id for item in membership_items)
    )
    return [
        _workspace_out(
            workspace,
            services.workspace_templates.resolve(workspace, require_available=False),
            memberships[workspace.id].role,
            services,
            counts[workspace.id],
            can_manage_global,
        )
        for workspace in workspaces
        if workspace.id in memberships
    ]


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: str, services: Services, identity: Identity
) -> WorkspaceOut:
    membership = await services.workspace_access.require_member(identity, workspace_id)
    can_manage_global = await services.platform_skill_access.can_manage_global_skills(
        identity
    )
    counts = await services.skills.count_effective_by_workspace((workspace_id,))
    workspace = await services.workspace_repository.get(workspace_id)
    if workspace is None:
        raise AppError("workspace_not_found", "Workspace not found.", 404)
    return _workspace_out(
        workspace,
        services.workspace_templates.resolve(workspace, require_available=False),
        membership.role,
        services,
        counts[workspace_id],
        can_manage_global,
    )


@router.get("/me", response_model=MeOut)
async def me(identity: Identity) -> MeOut:
    return MeOut(
        user_id=identity.user_id,
        external_subject=identity.external_subject,
        display_name=identity.display_name,
    )


@router.put("/me/preferences", response_model=PreferencesOut)
async def update_preferences(
    payload: PreferencesIn, services: Services, identity: Identity
) -> PreferencesOut:
    """Merge the caller's UI choices; the reply is the whole saved document."""
    merged = await merge_preferences(services.database, identity.user_id, payload)
    return PreferencesOut(**merged)


@router.get("/workspaces/{workspace_id}/sessions", response_model=list[SessionOut])
async def list_sessions(
    workspace_id: str, services: Services, identity: Identity
) -> list[SessionOut]:
    await services.workspace_access.require_member(identity, workspace_id)
    workspace = await services.workspace_repository.get(workspace_id)
    if workspace is None:
        raise AppError("workspace_not_found", "Workspace not found.", 404)
    services.workspace_templates.resolve(workspace)
    return [
        SessionOut.model_validate(item)
        for item in await services.sessions.list_for_workspace(
            workspace_id, identity.user_id
        )
    ]


@router.post(
    "/workspaces/{workspace_id}/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    workspace_id: str, services: Services, identity: Identity
) -> SessionOut:
    await services.workspace_access.require_member(identity, workspace_id)
    return SessionOut.model_validate(
        await services.sessions.create(workspace_id, identity)
    )


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str, services: Services, identity: Identity
) -> SessionOut:
    return SessionOut.model_validate(
        await services.workspace_access.require_session_owner(identity, session_id)
    )


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: str, body: SessionRename, services: Services, identity: Identity
) -> SessionOut:
    await services.workspace_access.require_session_owner(identity, session_id)
    return SessionOut.model_validate(
        await services.sessions.rename(session_id, body.title)
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, services: Services, identity: Identity
) -> Response:
    await services.workspace_access.require_session_owner(identity, session_id)
    await services.sessions.delete(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageEventOut])
async def session_messages(
    session_id: str, services: Services, identity: Identity
) -> list[MessageEventOut]:
    await services.workspace_access.require_session_owner(identity, session_id)
    return [
        _message_out(record)
        for record in await services.sessions.list_messages(session_id)
    ]


@router.get("/sessions/{session_id}/context", response_model=SessionContextOut)
async def session_context(
    session_id: str, services: Services, identity: Identity
) -> SessionContextOut:
    record = await services.workspace_access.require_session_owner(identity, session_id)
    return SessionContextOut(
        session_id=record.id,
        workspace_id=record.workspace_id,
        claude_session_id=record.claude_session_id,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        workspace_snapshot_hash=record.workspace_snapshot_hash,
        workspace_snapshot=json.loads(record.workspace_snapshot_json),
    )


@router.get("/sessions/{session_id}/skills", response_model=SessionSkillsOut)
async def session_skills(
    session_id: str, services: Services, identity: Identity
) -> SessionSkillsOut:
    await services.workspace_access.require_session_owner(identity, session_id)
    items = await services.sessions.list_skills(session_id)
    return SessionSkillsOut(
        items=[
            SessionSkillOut(name=item.name, description=item.description)
            for item in items
        ]
    )


@router.get("/sessions/{session_id}/files", response_model=SessionFilesOut)
async def session_files(
    session_id: str,
    services: Services,
    identity: Identity,
    q: Annotated[str, Query(max_length=200)] = "",
) -> SessionFilesOut:
    await services.workspace_access.require_session_owner(identity, session_id)
    result = await services.sessions.search_files(session_id, q)
    return SessionFilesOut(
        items=[
            SessionFileOut(path=item.path, name=item.name, size_bytes=item.size_bytes)
            for item in result.items
        ],
        truncated=result.truncated,
    )


@router.get(
    "/sessions/{session_id}/attachments",
    response_model=list[AttachmentOut],
)
async def list_pending_attachments(
    session_id: str,
    services: Services,
    identity: Identity,
) -> list[AttachmentOut]:
    await services.workspace_access.require_session_owner(identity, session_id)
    return [
        _attachment_out(record)
        for record in await services.attachments.list_pending(session_id)
    ]


@router.post(
    "/sessions/{session_id}/attachments",
    response_model=list[AttachmentOut],
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachments(
    session_id: str,
    services: Services,
    identity: Identity,
    files: Annotated[list[UploadFile], File()],
) -> list[AttachmentOut]:
    await services.workspace_access.require_session_owner(identity, session_id)
    return [
        _attachment_out(record)
        for record in await services.attachments.upload(session_id, files)
    ]


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str, services: Services, identity: Identity
) -> Response:
    await services.workspace_access.require_attachment_owner(identity, attachment_id)
    await services.attachments.delete(attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/attachments/{attachment_id}/content")
async def attachment_content(
    attachment_id: str, services: Services, identity: Identity
) -> FileResponse:
    record = await services.workspace_access.require_attachment_owner(
        identity, attachment_id
    )
    path = services.attachments.resolve_path(record)
    if not path.is_file():
        raise AppError("attachment_not_found", "Attachment not found.", 404)
    return FileResponse(
        path,
        media_type=record.mime_type,
        filename=record.original_filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/sessions/{session_id}/turns",
    response_model=TurnAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_turn(
    session_id: str, body: TurnCreate, services: Services, identity: Identity
) -> TurnAccepted:
    await services.workspace_access.require_session_owner(identity, session_id)
    turn = await services.turns.start(
        session_id,
        body.message,
        body.attachment_ids,
        body.client_request_id,
        file_references=body.file_references,
    )
    return TurnAccepted(
        turn_id=turn.id,
        status=turn.status,
        events_url=f"/api/turns/{turn.id}/events",
    )


@router.get("/turns/{turn_id}", response_model=TurnOut)
async def get_turn(turn_id: str, services: Services, identity: Identity) -> TurnOut:
    return TurnOut.model_validate(
        await services.workspace_access.require_turn_owner(identity, turn_id)
    )


@router.get("/turns/{turn_id}/events")
async def turn_events(
    turn_id: str,
    services: Services,
    identity: Identity,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    await services.workspace_access.require_turn_owner(identity, turn_id)
    after_sequence = 0
    if last_event_id:
        try:
            after_sequence = int(last_event_id)
        except ValueError as exc:
            raise AppError(
                "invalid_request", "Last-Event-ID must be an integer."
            ) from exc
        if after_sequence < 0:
            raise AppError("invalid_request", "Last-Event-ID cannot be negative.")
    return StreamingResponse(
        stream_turn_events(
            services.event_stream,
            turn_id,
            after_sequence=after_sequence,
            heartbeat_seconds=services.settings.sse_heartbeat_seconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/turns/{turn_id}/cancel",
    response_model=TurnOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_turn(turn_id: str, services: Services, identity: Identity) -> TurnOut:
    await services.workspace_access.require_turn_owner(identity, turn_id)
    return TurnOut.model_validate(await services.turns.cancel(turn_id))


def _workspace_out(
    workspace: WorkspaceRecord,
    entry: WorkspaceEntry,
    role: WorkspaceRole,
    services: AppServices,
    skill_count: int,
    can_manage_global_skills: bool,
) -> WorkspaceOut:
    manifest = entry.manifest
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        description=entry.description,
        available=entry.available,
        model=manifest.model if manifest else None,
        skill_count=skill_count,
        mcp_server_count=len(manifest.mcp_servers) if manifest else 0,
        validation_errors=list(entry.validation_errors),
        kind=workspace.kind,
        role=role.value,
        can_manage_skills=role in {WorkspaceRole.OWNER, WorkspaceRole.ADMIN},
        can_manage_global_skills=can_manage_global_skills,
        personal_memory_enabled=services.memory_scopes.ready,
    )


def _attachment_out(record: AttachmentRecord) -> AttachmentOut:
    return AttachmentOut(
        id=record.id,
        session_id=record.session_id,
        turn_id=record.turn_id,
        status=record.status,
        original_filename=record.original_filename,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        content_url=f"/api/attachments/{record.id}/content",
        created_at=record.created_at,
    )


def _message_out(record: TurnEventRecord) -> MessageEventOut:
    return MessageEventOut(
        id=record.id,
        session_id=record.session_id,
        turn_id=record.turn_id,
        sequence=record.sequence,
        event_type=record.event_type,
        role=record.role,
        payload=json.loads(record.payload_json),
        created_at=record.created_at,
    )


@router.put("/sessions/{session_id}/messages/{message_id}/feedback")
async def put_message_feedback(session_id: str, message_id: str, payload: MessageFeedbackIn,
                               services: Services, identity: Identity) -> dict:
    """Save feedback only for a completed reply in the caller's own session."""
    await services.workspace_access.require_session_owner(identity, session_id)
    return await save_feedback(services.database, session_id, message_id, identity.user_id, payload)


@router.get("/sessions/{session_id}/feedback")
async def get_message_feedback(session_id: str, services: Services, identity: Identity) -> list[dict]:
    """Restore the caller's saved votes without exposing another session."""
    await services.workspace_access.require_session_owner(identity, session_id)
    return await list_feedback(services.database, session_id, identity.user_id)


@router.get("/sessions/{session_id}/frontendToolRecovery")
async def get_frontend_tool_recovery(session_id: str, services: Services, identity: Identity) -> dict:
    """Expose only the caller's original pending calls and their durable continuation state."""
    await services.workspace_access.require_session_owner(identity, session_id)
    states = await services.turns.repository.frontend_tool_recovery(session_id)
    return {"sessionId": session_id, "calls": [
        {"toolCallId": state["call"].tool_call_id, "originRunId": state["call"].origin_run_id,
         "toolName": state["call"].public_name, "arguments": state["call"].arguments,
         "continuationRunId": state["continuation_run_id"], "continuationStatus": state["continuation_status"],
         "page": state["page_state"].get("page", {})}
        for state in states if state["continuation_status"] != "completed"
    ]}
