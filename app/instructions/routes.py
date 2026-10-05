"""工作区指令（每个 workspace 一份 CLAUDE.md）的四个端点。

鉴权与个人 Skill 一致走 `require_personal_owner`，未授权按仓库惯例返回 404
而非 403（防枚举）。GET 始终返回当前实际会生效的全文，编辑器打开即可编辑。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.dependencies import AppServices, Identity, get_services
from app.api.schemas import (
    InstructionsDefaultOut,
    InstructionsIn,
    InstructionsOut,
    InstructionsReset,
)
from app.auth.models import IdentityContext
from app.db.models import UserRecord, WorkspaceRecord
from app.errors import AppError
from app.instructions.service import WorkspaceInstructions

router = APIRouter(prefix="/api")
Services = Annotated[AppServices, Depends(get_services)]


@router.get(
    "/workspaces/{workspace_id}/instructions",
    response_model=InstructionsOut,
)
async def get_instructions(
    workspace_id: str, services: Services, identity: Identity
) -> InstructionsOut:
    workspace = await _require_personal_workspace(workspace_id, services, identity)
    return await _instructions_out(
        await services.instructions.get(workspace, identity), services
    )


@router.get(
    "/workspaces/{workspace_id}/instructions/default",
    response_model=InstructionsDefaultOut,
)
async def get_default_instructions(
    workspace_id: str, services: Services, identity: Identity
) -> InstructionsDefaultOut:
    workspace = await _require_personal_workspace(workspace_id, services, identity)
    default = await services.instructions.get_default(workspace)
    return InstructionsDefaultOut(
        content=default.content,
        source=default.source,
        content_hash=default.content_hash,
        size_bytes=default.size_bytes,
    )


@router.put(
    "/workspaces/{workspace_id}/instructions",
    response_model=InstructionsOut,
)
async def save_instructions(
    workspace_id: str,
    body: InstructionsIn,
    services: Services,
    identity: Identity,
) -> InstructionsOut:
    workspace = await _require_personal_workspace(workspace_id, services, identity)
    saved = await services.instructions.save(
        workspace,
        identity,
        body.content,
        expected_hash=body.expected_hash,
    )
    return await _instructions_out(saved, services)


@router.delete(
    "/workspaces/{workspace_id}/instructions",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reset_instructions(
    workspace_id: str,
    services: Services,
    identity: Identity,
    body: InstructionsReset | None = None,
) -> Response:
    """删覆盖即还原默认；`expected_hash` 可省略，首次保存前本就无 hash 可带。"""
    workspace = await _require_personal_workspace(workspace_id, services, identity)
    await services.instructions.reset(
        workspace,
        identity,
        expected_hash=body.expected_hash if body is not None else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _require_personal_workspace(
    workspace_id: str, services: AppServices, identity: IdentityContext
) -> WorkspaceRecord:
    await services.workspace_access.require_personal_owner(identity, workspace_id)
    workspace = await services.workspace_repository.get(workspace_id)
    if workspace is None:
        raise AppError("workspace_not_found", "Workspace not found.", 404)
    return workspace


async def _instructions_out(
    instructions: WorkspaceInstructions, services: AppServices
) -> InstructionsOut:
    return InstructionsOut(
        content=instructions.content,
        source=instructions.source,
        content_hash=instructions.content_hash,
        size_bytes=instructions.size_bytes,
        max_bytes=services.instructions.max_bytes,
        updated_at=instructions.updated_at,
        updated_by_name=await _display_name(services, instructions.updated_by),
    )


async def _display_name(services: AppServices, user_id: str | None) -> str | None:
    if user_id is None:
        return None
    async with services.database.session() as db:
        user = await db.get(UserRecord, user_id)
    return user.display_name if user is not None else None
