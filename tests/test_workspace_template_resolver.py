from datetime import UTC, datetime

import pytest

from tests.test_workspaces import write_workspace


def _workspace(template_id: str):
    from app.db.models import WorkspaceRecord

    now = datetime.now(UTC)
    return WorkspaceRecord(
        id="product-workspace",
        name="Product Workspace",
        kind="personal",
        config_json="{}",
        owner_user_id="owner",
        template_id=template_id,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_dynamic_workspace_resolves_shared_example_template(tmp_path) -> None:
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.resolver import WorkspaceTemplateResolver

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(root, "example", skills=("summary",))
    registry = WorkspaceRegistry(root, "model", {})
    registry.scan()

    resolved = WorkspaceTemplateResolver(registry).resolve(_workspace("example"))

    assert resolved.id == "example"
    assert resolved.directory == root / "example"


@pytest.mark.asyncio
async def test_template_skills_are_baseline_and_personal_skill_overrides_by_name(
    tmp_path,
) -> None:
    from app.skills.bundle import build_bundle
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.resolver import WorkspaceTemplateResolver

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(root, "example", skills=("summary", "review"))
    registry = WorkspaceRegistry(root, "model", {})
    registry.scan()
    override = build_bundle(
        b"---\nname: SUMMARY\ndescription: Personal summary\n---\n# Personal\n",
        (),
    )

    bundles = await WorkspaceTemplateResolver(registry).resolve_session_bundles(
        _workspace("example"), (("personal-summary", override),)
    )

    assert [(skill_id, bundle.name) for skill_id, bundle in bundles] == [
        ("template:example:review", "review"),
        ("personal-summary", "SUMMARY"),
    ]


def test_missing_template_is_listed_unavailable_and_session_create_fails(
    tmp_path,
) -> None:
    from app.errors import AppError
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.resolver import WorkspaceTemplateResolver

    root = tmp_path / "workspaces"
    root.mkdir()
    resolver = WorkspaceTemplateResolver(WorkspaceRegistry(root, "model", {}))

    listed = resolver.resolve(_workspace("missing"), require_available=False)
    assert listed.available is False
    with pytest.raises(AppError) as exc_info:
        resolver.resolve(_workspace("missing"))
    assert exc_info.value.code == "workspace_invalid"
