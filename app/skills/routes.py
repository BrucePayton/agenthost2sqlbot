from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import AppServices, Identity, get_services
from app.auth.models import IdentityContext
from app.errors import AppError
from app.skills.bundle import load_bundle_from_uploaded_files
from app.skills.models import ManagedSkillSummary, StoredSkill
from app.skills.schemas import (
    GlobalSkillSettingIn,
    SkillArchive,
    SkillCatalogOut,
    SkillDetailOut,
    SkillEnabledIn,
    SkillFileOut,
    SkillImportOut,
    SkillOut,
)
from app.skills.service import SkillImportResult
from app.skills.uploads import read_multipart_archive, read_multipart_directory

router = APIRouter(prefix="/api")
Services = Annotated[AppServices, Depends(get_services)]


@router.get("/workspaces/{workspace_id}/skills", response_model=SkillCatalogOut)
async def list_skills(
    workspace_id: str, services: Services, identity: Identity
) -> SkillCatalogOut:
    catalog = await services.skills.catalog(workspace_id, identity)
    return SkillCatalogOut(
        global_=[_skill_out(item) for item in catalog.global_skills],
        personal=[_skill_out(item) for item in catalog.personal_skills],
        effective_count=catalog.effective_count,
    )


@router.get(
    "/workspaces/{workspace_id}/skills/{skill_id}",
    response_model=SkillDetailOut,
)
async def get_workspace_skill(
    workspace_id: str,
    skill_id: str,
    services: Services,
    identity: Identity,
) -> SkillDetailOut:
    return _skill_detail_out(
        await services.skills.get_for_workspace(workspace_id, skill_id, identity)
    )


@router.post(
    "/workspaces/{workspace_id}/skills/import",
    response_model=SkillImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_skill(
    workspace_id: str,
    request: Request,
    response: Response,
    services: Services,
    identity: Identity,
) -> SkillImportOut:
    await services.workspace_access.require_personal_owner(identity, workspace_id)
    upload = await read_multipart_archive(
        request, services.settings.max_skill_bundle_size_bytes
    )
    result = await services.skills.import_personal_archive(
        workspace_id,
        identity,
        upload.raw,
        on_conflict=upload.on_conflict,
        expected_hash=upload.expected_hash,
        target_name=upload.target_name,
    )
    return _skill_import_out(result, response)


@router.post(
    "/workspaces/{workspace_id}/skills/import-directory",
    response_model=SkillImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_skill_directory(
    workspace_id: str,
    request: Request,
    response: Response,
    services: Services,
    identity: Identity,
) -> SkillImportOut:
    await services.workspace_access.require_personal_owner(identity, workspace_id)
    upload = await read_multipart_directory(request, services.skills.limits)
    uploaded = load_bundle_from_uploaded_files(upload.files, services.skills.limits)
    result = await services.skills.import_personal_uploaded_directory(
        workspace_id,
        identity,
        uploaded,
        on_conflict=upload.on_conflict,
        expected_hash=upload.expected_hash,
        target_name=upload.target_name,
    )
    return _skill_import_out(result, response)


@router.patch(
    "/workspaces/{workspace_id}/skills/{skill_id}/enabled",
    response_model=SkillOut,
)
async def set_personal_skill_enabled(
    workspace_id: str,
    skill_id: str,
    body: SkillEnabledIn,
    services: Services,
    identity: Identity,
) -> SkillOut:
    await _require_personal_skill(workspace_id, skill_id, services, identity)
    return _skill_out(
        await services.skills.set_personal_enabled(
            skill_id,
            identity,
            enabled=body.enabled,
            expected_hash=body.expected_hash,
        )
    )


@router.delete(
    "/workspaces/{workspace_id}/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_personal_skill(
    workspace_id: str,
    skill_id: str,
    body: SkillArchive,
    services: Services,
    identity: Identity,
) -> Response:
    await _require_personal_skill(workspace_id, skill_id, services, identity)
    await services.skills.archive_personal(
        workspace_id,
        skill_id,
        identity,
        expected_hash=body.expected_hash,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/workspaces/{workspace_id}/global-skills/{skill_id}/setting",
    response_model=SkillOut,
)
async def set_global_skill_setting(
    workspace_id: str,
    skill_id: str,
    body: GlobalSkillSettingIn,
    services: Services,
    identity: Identity,
) -> SkillOut:
    await services.workspace_access.require_personal_owner(identity, workspace_id)
    return _skill_out(
        await services.skills.set_global_enabled(
            workspace_id,
            skill_id,
            identity,
            enabled=body.enabled,
        )
    )


@router.post(
    "/admin/global-skills/import",
    response_model=SkillImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_global_skill(
    request: Request,
    response: Response,
    services: Services,
    identity: Identity,
) -> SkillImportOut:
    await services.platform_skill_access.require_global_contributor(identity)
    upload = await read_multipart_archive(
        request, services.settings.max_skill_bundle_size_bytes
    )
    result = await services.skills.import_global_archive(
        identity,
        upload.raw,
        on_conflict=upload.on_conflict,
        expected_hash=upload.expected_hash,
        target_name=upload.target_name,
    )
    return _skill_import_out(result, response)


@router.post(
    "/admin/global-skills/import-directory",
    response_model=SkillImportOut,
    status_code=status.HTTP_201_CREATED,
)
async def import_global_skill_directory(
    request: Request,
    response: Response,
    services: Services,
    identity: Identity,
) -> SkillImportOut:
    await services.platform_skill_access.require_global_contributor(identity)
    upload = await read_multipart_directory(request, services.skills.limits)
    uploaded = load_bundle_from_uploaded_files(upload.files, services.skills.limits)
    result = await services.skills.import_global_uploaded_directory(
        identity,
        uploaded,
        on_conflict=upload.on_conflict,
        expected_hash=upload.expected_hash,
        target_name=upload.target_name,
    )
    return _skill_import_out(result, response)


@router.get("/admin/global-skills/{skill_id}", response_model=SkillDetailOut)
async def get_global_skill(
    skill_id: str, services: Services, identity: Identity
) -> SkillDetailOut:
    await services.platform_skill_access.require_global_contributor(identity)
    skill = await services.skills.repository.get_stored(skill_id)
    if skill is None or skill.scope != "global":
        raise AppError("skill_not_found", "Skill not found.", 404)
    return _skill_detail_out(skill)


@router.delete(
    "/admin/global-skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def archive_global_skill(
    skill_id: str,
    body: SkillArchive,
    services: Services,
    identity: Identity,
) -> Response:
    await services.platform_skill_access.require_global_contributor(identity)
    await services.skills.archive_global(
        skill_id, identity, expected_hash=body.expected_hash
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Compatibility aliases for existing personal-Skill clients. Manual creation,
# raw-content editing, and cross-Workspace copying are intentionally not exposed.
@router.get("/skills/{skill_id}", response_model=SkillDetailOut)
async def get_skill(
    skill_id: str, services: Services, identity: Identity
) -> SkillDetailOut:
    workspace_id = await _require_unscoped_personal_skill(
        skill_id, services, identity
    )
    return _skill_detail_out(
        await services.skills.get_for_workspace(workspace_id, skill_id, identity)
    )


@router.patch("/skills/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: str, body: SkillEnabledIn, services: Services, identity: Identity
) -> SkillOut:
    await _require_unscoped_personal_skill(skill_id, services, identity)
    return _skill_out(
        await services.skills.set_personal_enabled(
            skill_id,
            identity,
            enabled=body.enabled,
            expected_hash=body.expected_hash,
        )
    )


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_skill(
    skill_id: str, body: SkillArchive, services: Services, identity: Identity
) -> Response:
    workspace_id = await _require_unscoped_personal_skill(
        skill_id, services, identity
    )
    await services.skills.archive_personal(
        workspace_id,
        skill_id,
        identity,
        expected_hash=body.expected_hash,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _require_unscoped_personal_skill(
    skill_id: str,
    services: AppServices,
    identity: IdentityContext,
) -> str:
    workspace_id = await services.skills.repository.get_active_workspace_id(skill_id)
    if workspace_id is None:
        raise AppError("skill_not_found", "Skill not found.", 404)
    workspace = await services.workspace_repository.get(workspace_id)
    if workspace is None or workspace.kind != "personal":
        raise AppError("skill_not_found", "Skill not found.", 404)
    await services.workspace_access.require_personal_owner(identity, workspace.id)
    return workspace.id


async def _require_personal_skill(
    workspace_id: str,
    skill_id: str,
    services: AppServices,
    identity: IdentityContext,
) -> None:
    await services.workspace_access.require_personal_owner(identity, workspace_id)
    summary = await services.skills.repository.get_summary(skill_id)
    if (
        summary is None
        or summary.scope != "workspace"
        or summary.workspace_id != workspace_id
    ):
        raise AppError("skill_not_found", "Skill not found.", 404)


def _skill_import_out(
    result: SkillImportResult, response: Response
) -> SkillImportOut:
    response.status_code = (
        status.HTTP_201_CREATED
        if result.status in {"created", "renamed"}
        else status.HTTP_200_OK
    )
    return SkillImportOut(status=result.status, skill=_skill_out(result.skill))


def _skill_out(skill: ManagedSkillSummary | StoredSkill) -> SkillOut:
    summary = skill.summary if isinstance(skill, StoredSkill) else skill
    return SkillOut(
        id=summary.id,
        scope=summary.scope,
        workspace_id=summary.workspace_id,
        name=summary.name,
        description=summary.description,
        enabled=summary.enabled,
        version_id=summary.version.id,
        version_no=summary.version.version_no,
        bundle_hash=summary.bundle_hash,
        origin=summary.origin,
        updated_at=summary.updated_at,
    )


def _skill_detail_out(skill: StoredSkill) -> SkillDetailOut:
    return SkillDetailOut(
        **_skill_out(skill).model_dump(),
        content=skill.content,
        files=[
            SkillFileOut(
                path=file.path,
                mime_type=file.mime_type,
                size_bytes=file.size_bytes,
                sha256=file.sha256,
            )
            for file in skill.files
        ],
    )
