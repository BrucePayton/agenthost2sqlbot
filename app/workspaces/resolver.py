from app.db.models import WorkspaceRecord
from app.errors import AppError
from app.skills.bundle import SkillBundleLimits, load_bundle_from_directory
from app.skills.models import SkillBundle
from app.workspaces.models import WorkspaceEntry
from app.workspaces.registry import WorkspaceRegistry


class WorkspaceTemplateResolver:
    def __init__(
        self,
        registry: WorkspaceRegistry,
        limits: SkillBundleLimits | None = None,
    ) -> None:
        self.registry = registry
        self.limits = limits or SkillBundleLimits()

    def resolve(
        self, workspace: WorkspaceRecord, *, require_available: bool = True
    ) -> WorkspaceEntry:
        if not workspace.template_id:
            raise AppError("workspace_invalid", "Workspace template is missing.", 409)
        try:
            return self.registry.get(
                workspace.template_id, require_available=require_available
            )
        except AppError as exc:
            if exc.code == "workspace_not_found":
                if not require_available:
                    return WorkspaceEntry(
                        id=workspace.template_id,
                        directory=self.registry.root / workspace.template_id,
                        available=False,
                        name=workspace.name,
                        description="Workspace template unavailable.",
                        manifest=None,
                        validation_errors=("workspace template is unavailable",),
                    )
                raise AppError(
                    "workspace_invalid", "Workspace template is unavailable.", 409
                ) from exc
            raise

    async def resolve_session_bundles(
        self,
        workspace: WorkspaceRecord,
        personal_bundles: tuple[tuple[str, SkillBundle], ...],
    ) -> tuple[tuple[str, SkillBundle], ...]:
        entry = self.resolve(workspace)
        manifest = entry.manifest
        assert manifest is not None
        merged: dict[str, tuple[str, SkillBundle]] = {}
        for skill_name in manifest.skills:
            if entry.skills_source_root is None:
                raise AppError(
                    "workspace_invalid", "Workspace Skill source is unavailable.", 409
                )
            bundle = load_bundle_from_directory(
                entry.skills_source_root / skill_name, self.limits
            )
            merged[bundle.name.casefold()] = (
                f"template:{workspace.template_id}:{bundle.name.casefold()}",
                bundle,
            )
        for skill_id, bundle in personal_bundles:
            merged[bundle.name.casefold()] = (skill_id, bundle)
        return tuple(merged[name] for name in sorted(merged))
