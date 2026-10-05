import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.access import WorkspaceAccessService
from app.auth.identity_repository import IdentityRepository
from app.auth.models import IdentityContext
from app.bootstrap import build_app_services, initialize_app_services
from app.db.base import Database
from app.db.models import (
    AppMetadataRecord,
    SkillRecord,
    SkillVersionRecord,
    UserRecord,
)
from app.runtime.fake import FakeAgentRuntime
from app.skills.access import PlatformSkillAccessService
from app.skills.artifacts import FilesystemSkillArtifactStore
from app.skills.bundle import build_bundle, load_bundle_from_directory
from app.skills.repository import SkillRepository
from app.skills.service import SkillBootstrapService, SkillService
from app.workspaces.registry import WorkspaceRegistry
from app.workspaces.sync import WorkspaceSyncService
from tests.test_workspaces import write_workspace

OWNER = IdentityContext("owner", "owner", "Owner")
ADMIN = IdentityContext("admin", "admin", "Admin")


@pytest.mark.asyncio
async def test_startup_without_bootstrap_identity_imports_real_davinci_global_skill(
    settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DAVINCI_DATA_MCP_URL", "http://127.0.0.1:18000/mcp")
    settings = settings_factory(
        app_env="development",
        identity_mode="davinci_passthrough",
        davinci_api_base_url="https://davinci.example.test",
        workspaces_root=repository_root / "workspaces",
        personal_workspace_template_id="davinci-dashboard",
        claude_skills_root=None,
    )
    services = build_app_services(settings, runtime=FakeAgentRuntime())
    entries = services.workspaces.scan()
    davinci = next(entry for entry in entries if entry.id == "davinci-dashboard")
    assert davinci.available is True
    assert davinci.skills_source_root is not None
    assert (
        davinci.skills_source_root / "configure-dashboard-widget" / "SKILL.md"
    ).is_file()
    assert (
        davinci.skills_source_root / "configure-subscription-rule" / "SKILL.md"
    ).is_file()

    async with initialize_app_services(services):
        async with services.database.session() as db:
            global_skill = await db.scalar(
                select(SkillRecord).where(
                    SkillRecord.scope == "global",
                    SkillRecord.name == "configure-dashboard-widget",
                )
            )
            users = list((await db.scalars(select(UserRecord))).all())
            marker = await db.get(AppMetadataRecord, "global_skill_import_v1")
            source_markers = list(
                (
                    await db.scalars(
                        select(AppMetadataRecord).where(
                            AppMetadataRecord.key.like(
                                "global_skill_import_source_v1:%"
                            )
                        )
                    )
                ).all()
            )

        assert global_skill is not None
        assert global_skill.enabled is True
        assert global_skill.created_by is None
        assert users == []
        assert marker is not None
        assert {
            (value["name"], value["status"])
            for value in (
                json.loads(source.value_json) for source in source_markers
            )
        } == {
            ("configure-dashboard-widget", "created"),
            ("configure-subscription-rule", "created"),
            ("interpret-dashboard", "created"),
            ("locate-data", "created"),
            ("manage-space", "created"),
        }

        async with services.database.session() as db:
            version = await db.get(
                SkillVersionRecord, global_skill.current_version_id
            )
        assert version is not None
        assert version.created_by is None

        resolved = await IdentityRepository(services.database).resolve_or_create(
            "davinci",
            "obid:verified-user",
            {"name": "Verified User"},
            provider="davinci_passthrough",
        )
        identity = IdentityContext(
            resolved.user_id,
            "verified-user",
            resolved.display_name,
            issuer="davinci",
        )
        personal_workspace = await services.workspace_provisioner.ensure(identity)
        created = await services.sessions.create(personal_workspace.id, identity)
        snapshot = json.loads(created.workspace_snapshot_json)

        assert personal_workspace.kind == "personal"
        assert personal_workspace.id != "davinci-dashboard"
        assert snapshot["schema_version"] == 4
        assert [skill["name"] for skill in snapshot["skills"]] == [
            "configure-dashboard-widget",
            "configure-subscription-rule",
            "interpret-dashboard",
            "locate-data",
            "manage-space",
        ]
        assert (
            services.sessions.session_path(created)
            / "workspace"
            / ".claude"
            / "skills"
            / "configure-dashboard-widget"
            / "SKILL.md"
        ).is_file()
        assert (
            services.sessions.session_path(created)
            / "workspace"
            / ".claude"
            / "skills"
            / "configure-subscription-rule"
            / "SKILL.md"
        ).is_file()


@pytest.mark.asyncio
async def test_old_empty_bootstrap_marker_reconciles_later_davinci_source(
    settings_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy aggregate marker must not hide a newly available trusted source."""
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DAVINCI_DATA_MCP_URL", "http://127.0.0.1:18000/mcp")
    settings = settings_factory(
        workspaces_root=repository_root / "workspaces",
        claude_skills_root=None,
    )
    database = Database(settings.resolved_database_url)
    await database.initialize()
    artifact_store = FilesystemSkillArtifactStore(settings.resolved_skill_artifact_root)
    artifact_store.initialize()
    service = SkillService(
        SkillRepository(database, artifact_store, settings.skill_bundle_limits),
        WorkspaceAccessService(database),
        PlatformSkillAccessService(database),
        settings.skill_bundle_limits,
    )
    bootstrap = SkillBootstrapService(database, service)
    entries = WorkspaceRegistry(
        settings.workspaces_root,
        settings.claude_model,
        {"DAVINCI_DATA_MCP_URL": "http://127.0.0.1:18000/mcp"},
        allow_loopback_http_mcp=True,
    ).scan()
    davinci = next(entry for entry in entries if entry.id == "davinci-dashboard")
    assert davinci.available is True
    assert davinci.skills_source_root is not None
    expected_bundle = load_bundle_from_directory(
        davinci.skills_source_root / "configure-dashboard-widget"
    )

    try:
        empty = await bootstrap.run([], None, "personal", None)
        assert empty.items == ()

        reconciled = await bootstrap.run([davinci], None, "personal", None)

        assert [(item.name, item.status) for item in reconciled.items] == [
            ("configure-dashboard-widget", "created"),
            ("configure-subscription-rule", "created"),
            ("interpret-dashboard", "created"),
            ("locate-data", "created"),
            ("manage-space", "created"),
        ]
        async with database.session() as db:
            skill = await db.scalar(
                select(SkillRecord).where(
                    SkillRecord.scope == "global",
                    SkillRecord.name == "configure-dashboard-widget",
                )
            )
            assert skill is not None
            version = await db.get(SkillVersionRecord, skill.current_version_id)
        assert version is not None
        assert version.bundle_hash == expected_bundle.bundle_hash
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_real_davinci_skill_overwrite_reaches_new_session_snapshot(
    settings_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    publication_data_dir = tmp_path / "publication-data"
    monkeypatch.setenv("DAVINCI_DATA_MCP_URL", "http://127.0.0.1:18000/mcp")
    settings = settings_factory(
        app_env="development",
        identity_mode="davinci_passthrough",
        davinci_api_base_url="https://davinci.example.test",
        workspaces_root=repository_root / "workspaces",
        app_data_dir=publication_data_dir,
        personal_workspace_template_id="davinci-dashboard",
        claude_skills_root=None,
    )
    assert settings.resolved_database_url == (
        f"sqlite+aiosqlite:///{publication_data_dir / 'app.db'}"
    )
    services = build_app_services(settings, runtime=FakeAgentRuntime())
    entries = services.workspaces.scan()
    davinci = next(entry for entry in entries if entry.id == "davinci-dashboard")
    assert davinci.available is True
    assert davinci.skills_source_root is not None
    source_root = davinci.skills_source_root / "configure-dashboard-widget"
    incoming = load_bundle_from_directory(source_root)

    async with initialize_app_services(services):
        resolved = await IdentityRepository(services.database).resolve_or_create(
            "davinci",
            "obid:publication-user",
            {"name": "Publication User"},
            provider="davinci_passthrough",
        )
        identity = IdentityContext(
            resolved.user_id,
            "publication-user",
            resolved.display_name,
            issuer="davinci",
        )
        personal_workspace = await services.workspace_provisioner.ensure(identity)
        current = next(
            item
            for item in (
                await services.skills.catalog(personal_workspace.id, identity)
            ).global_skills
            if item.name == "configure-dashboard-widget"
        )
        previous_source = build_bundle(
            b"---\nname: configure-dashboard-widget\n"
            b"description: Use when configuring a Davinci Widget.\n"
            b"---\n# Previous guidance\n",
            (),
        )
        previous = await services.skills.import_global_bundle(
            identity,
            previous_source,
            on_conflict="overwrite",
            expected_hash=current.bundle_hash,
            origin={"type": "publication_test_previous"},
        )

        published = await services.skills.import_global_bundle(
            identity,
            incoming,
            on_conflict="overwrite",
            expected_hash=previous.skill.bundle_hash,
            origin={"type": "publication_test_directory"},
        )
        created = await services.sessions.create(personal_workspace.id, identity)
        snapshot = json.loads(created.workspace_snapshot_json)
        manifest = next(
            skill
            for skill in snapshot["skills"]
            if skill["name"] == "configure-dashboard-widget"
        )

        async with services.database.session() as db:
            current_record = await db.scalar(
                select(SkillRecord).where(
                    SkillRecord.scope == "global",
                    SkillRecord.name == "configure-dashboard-widget",
                )
            )
            assert current_record is not None
            current_version = await db.get(
                SkillVersionRecord, current_record.current_version_id
            )

        assert published.status == "overwritten"
        assert current_version is not None
        materialized_skill_root = (
            services.sessions.session_path(created)
            / "workspace/.claude/skills/configure-dashboard-widget"
        )
        materialized = load_bundle_from_directory(materialized_skill_root)
        assert (
            published.skill.bundle_hash,
            current_version.bundle_hash,
            manifest["bundle_hash"],
            materialized.bundle_hash,
        ) == (incoming.bundle_hash,) * 4
        # Verify the complete published guidance rather than one changing phrase.
        assert materialized.content == incoming.content
        assert "`PUBLISH_FAILED`" in (
            materialized_skill_root / "references/errors.md"
        ).read_text(encoding="utf-8")


def _write_skill(root: Path, name: str, *, description: str = "A Skill") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )


@pytest.fixture
async def bootstrap_fixture(
    tmp_path: Path, settings_factory
) -> AsyncIterator[dict[str, object]]:
    from app.db.models import PlatformRoleBindingRecord, UserRecord

    settings = settings_factory(
        mock_user_id="owner",
        mock_user_subject="owner",
        mock_user_display_name="Owner",
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner", "team": "owner"},
    )
    shared_root = tmp_path / "shared-skills"
    _write_skill(shared_root / "team-good", "team-good")
    _write_skill(shared_root / "team-conflict", "team-conflict")
    write_workspace(settings.workspaces_root, "personal", skills=())
    team = write_workspace(
        settings.workspaces_root, "team", skills=("team-good", "team-conflict")
    )
    manifest = team / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "skills:\n", "skills_root_env: SHARED_SKILLS_ROOT\nskills:\n"
        ),
        encoding="utf-8",
    )
    local_root = tmp_path / "local-skills"
    _write_skill(local_root / "personal-good", "personal-good")
    _write_skill(local_root / "malformed", "malformed", description="")

    database = Database(settings.resolved_database_url)
    await database.initialize()
    entries = WorkspaceRegistry(
        settings.workspaces_root,
        settings.claude_model,
        {"SHARED_SKILLS_ROOT": str(shared_root)},
    ).scan()
    await WorkspaceSyncService(database, settings).sync(entries, OWNER)
    async with database.session() as db:
        db.add(
            UserRecord(
                id=ADMIN.user_id,
                external_subject=ADMIN.external_subject,
                display_name=ADMIN.display_name,
                provider="mock",
            )
        )
        await db.flush()
        db.add(
            PlatformRoleBindingRecord(
                user_id=ADMIN.user_id,
                role="skill_admin",
                granted_by=None,
            )
        )
        await db.commit()
    artifact_store = FilesystemSkillArtifactStore(settings.resolved_skill_artifact_root)
    artifact_store.initialize()
    repository = SkillRepository(
        database, artifact_store, settings.skill_bundle_limits
    )
    service = SkillService(
        repository,
        WorkspaceAccessService(database),
        PlatformSkillAccessService(database),
        settings.skill_bundle_limits,
    )
    personal_conflict = (
        await service.import_personal_bundle(
            "personal",
            OWNER,
            load_bundle_from_directory(shared_root / "team-conflict"),
            origin={"type": "legacy_migration"},
        )
    ).skill
    _write_skill(
        shared_root / "team-conflict",
        "team-conflict",
        description="Changed trusted source",
    )
    try:
        yield {
            "bootstrap": SkillBootstrapService(database, service),
            "database": database,
            "entries": entries,
            "local_root": local_root,
            "personal_conflict": personal_conflict,
            "repository": repository,
            "service": service,
        }
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_bootstrap_publishes_trusted_sources_as_default_enabled_globals(
    bootstrap_fixture,
) -> None:
    bootstrap = bootstrap_fixture["bootstrap"]
    service = bootstrap_fixture["service"]

    report = await bootstrap.run(
        bootstrap_fixture["entries"],
        OWNER,
        "personal",
        bootstrap_fixture["local_root"],
    )

    assert report.summary() == {
        "created": 2,
        "skipped": 0,
        "conflict": 1,
        "failed": 1,
    }
    catalog = await service.catalog("personal", OWNER)
    assert [(item.name, item.enabled) for item in catalog.global_skills] == [
        ("personal-good", True),
        ("team-good", True),
    ]
    assert {item.origin["type"] for item in catalog.global_skills} == {
        "global_bootstrap"
    }
    assert [item.name for item in catalog.personal_skills] == ["team-conflict"]


@pytest.mark.asyncio
async def test_personal_collision_withholds_global_bootstrap_marker(
    bootstrap_fixture,
) -> None:
    bootstrap = bootstrap_fixture["bootstrap"]
    database = bootstrap_fixture["database"]

    report = await bootstrap.run(
        bootstrap_fixture["entries"], OWNER, "personal", None
    )

    assert report.summary()["conflict"] == 1
    async with database.session() as db:
        assert await db.get(AppMetadataRecord, "global_skill_import_v1") is None


@pytest.mark.asyncio
async def test_bootstrap_retry_writes_marker_after_failures_and_conflict_are_resolved(
    bootstrap_fixture,
) -> None:
    bootstrap = bootstrap_fixture["bootstrap"]
    database = bootstrap_fixture["database"]
    service = bootstrap_fixture["service"]
    local_root = bootstrap_fixture["local_root"]
    personal_conflict = bootstrap_fixture["personal_conflict"]
    first = await bootstrap.run(
        bootstrap_fixture["entries"], OWNER, "personal", local_root
    )

    (local_root / "malformed" / "SKILL.md").unlink()
    await service.archive_personal(
        "personal",
        personal_conflict.id,
        OWNER,
        expected_hash=personal_conflict.bundle_hash,
    )
    retried = await bootstrap.run(
        bootstrap_fixture["entries"], OWNER, "personal", local_root
    )

    assert first.blocks_completion is True
    assert retried.blocks_completion is False
    async with database.session() as db:
        marker = await db.get(AppMetadataRecord, "global_skill_import_v1")
    assert marker is not None


@pytest.mark.asyncio
async def test_existing_administrator_global_conflict_is_preserved_and_completes(
    bootstrap_fixture,
) -> None:
    service = bootstrap_fixture["service"]
    database = bootstrap_fixture["database"]
    local_root = bootstrap_fixture["local_root"]
    personal_conflict = bootstrap_fixture["personal_conflict"]
    (local_root / "malformed" / "SKILL.md").unlink()
    await service.archive_personal(
        "personal",
        personal_conflict.id,
        OWNER,
        expected_hash=personal_conflict.bundle_hash,
    )
    administrator_bundle = build_bundle(
        b"---\nname: team-good\ndescription: Administrator global\n---\n# admin\n",
        (),
    )
    administrator_global = await service.import_global_bundle(
        ADMIN, administrator_bundle, origin={"type": "administrator"}
    )

    report = await bootstrap_fixture["bootstrap"].run(
        bootstrap_fixture["entries"], OWNER, "personal", local_root
    )

    team_good = next(item for item in report.items if item.name == "team-good")
    assert (team_good.status, team_good.blocking) == ("conflict", False)
    assert report.blocks_completion is False
    current = await service.get_for_workspace(
        "personal", administrator_global.skill.id, OWNER
    )
    assert current.description == "Administrator global"
    async with database.session() as db:
        marker = await db.get(AppMetadataRecord, "global_skill_import_v1")
    assert marker is not None


async def _complete_bootstrap(fixture: dict[str, object]) -> None:
    local_root = fixture["local_root"]
    (local_root / "malformed" / "SKILL.md").unlink()
    conflict = fixture["personal_conflict"]
    await fixture["service"].archive_personal(
        "personal",
        conflict.id,
        OWNER,
        expected_hash=conflict.bundle_hash,
    )
    report = await fixture["bootstrap"].run(
        fixture["entries"], OWNER, "personal", local_root
    )
    assert report.blocks_completion is False


@pytest.mark.asyncio
async def test_completed_bootstrap_does_not_replace_administrator_version(
    bootstrap_fixture,
) -> None:
    await _complete_bootstrap(bootstrap_fixture)
    service = bootstrap_fixture["service"]
    team_good = next(
        item
        for item in (await service.catalog("personal", OWNER)).global_skills
        if item.name == "team-good"
    )
    replacement = build_bundle(
        b"---\nname: team-good\ndescription: Administrator version\n---\n# changed\n",
        (),
    )
    replaced = await service.import_global_bundle(
        ADMIN,
        replacement,
        on_conflict="overwrite",
        expected_hash=team_good.bundle_hash,
        origin={"type": "administrator"},
    )

    restarted = await bootstrap_fixture["bootstrap"].run(
        bootstrap_fixture["entries"],
        OWNER,
        "personal",
        bootstrap_fixture["local_root"],
    )

    assert restarted.items == ()
    current = await service.get_for_workspace("personal", team_good.id, OWNER)
    assert current.bundle_hash == replaced.skill.bundle_hash
    assert current.description == "Administrator version"


@pytest.mark.asyncio
async def test_completed_bootstrap_does_not_recreate_archived_global(
    bootstrap_fixture,
) -> None:
    await _complete_bootstrap(bootstrap_fixture)
    service = bootstrap_fixture["service"]
    team_good = next(
        item
        for item in (await service.catalog("personal", OWNER)).global_skills
        if item.name == "team-good"
    )
    await service.archive_global(
        team_good.id, ADMIN, expected_hash=team_good.bundle_hash
    )

    restarted = await bootstrap_fixture["bootstrap"].run(
        bootstrap_fixture["entries"],
        OWNER,
        "personal",
        bootstrap_fixture["local_root"],
    )

    assert restarted.items == ()
    names = {
        item.name for item in (await service.catalog("personal", OWNER)).global_skills
    }
    assert "team-good" not in names


@pytest.mark.asyncio
async def test_retry_does_not_recreate_replaced_and_archived_bootstrap_source(
    bootstrap_fixture,
) -> None:
    bootstrap = bootstrap_fixture["bootstrap"]
    service = bootstrap_fixture["service"]
    first = await bootstrap.run(
        bootstrap_fixture["entries"],
        OWNER,
        "personal",
        bootstrap_fixture["local_root"],
    )
    assert first.blocks_completion is True
    team_good = next(
        item
        for item in (await service.catalog("personal", OWNER)).global_skills
        if item.name == "team-good"
    )
    replacement = build_bundle(
        b"---\nname: team-good\ndescription: Administrator version\n---\n# changed\n",
        (),
    )
    replaced = await service.import_global_bundle(
        ADMIN,
        replacement,
        on_conflict="overwrite",
        expected_hash=team_good.bundle_hash,
        origin={"type": "administrator"},
    )
    await service.archive_global(
        replaced.skill.id,
        ADMIN,
        expected_hash=replaced.skill.bundle_hash,
    )

    retried = await bootstrap.run(
        bootstrap_fixture["entries"],
        OWNER,
        "personal",
        bootstrap_fixture["local_root"],
    )

    team_good_retry = next(item for item in retried.items if item.name == "team-good")
    assert team_good_retry.status == "skipped"
    names = {
        item.name for item in (await service.catalog("personal", OWNER)).global_skills
    }
    assert "team-good" not in names


@pytest.mark.asyncio
async def test_local_success_does_not_tombstone_same_named_failed_manifest_retry(
    bootstrap_fixture,
) -> None:
    bootstrap = bootstrap_fixture["bootstrap"]
    local_root = bootstrap_fixture["local_root"]
    manifest_root = local_root.parent / "personal-manifest-skills"
    _write_skill(
        manifest_root / "personal-good",
        "personal-good",
        description="",
    )
    entries = []
    for entry in bootstrap_fixture["entries"]:
        if entry.id != "personal":
            entries.append(entry)
            continue
        assert entry.manifest is not None
        entries.append(
            replace(
                entry,
                manifest=entry.manifest.model_copy(
                    update={"skills": ["personal-good"]}
                ),
                skills_source_root=manifest_root,
            )
        )

    first = await bootstrap.run(entries, OWNER, "personal", local_root)
    first_same_name = [
        item
        for item in first.items
        if item.workspace_id == "personal" and item.name == "personal-good"
    ]
    assert [item.status for item in first_same_name] == ["failed", "created"]

    _write_skill(
        manifest_root / "personal-good",
        "personal-good",
        description="Manifest retry content",
    )
    retried = await bootstrap.run(entries, OWNER, "personal", local_root)

    retried_same_name = [
        item
        for item in retried.items
        if item.workspace_id == "personal" and item.name == "personal-good"
    ]
    assert [item.status for item in retried_same_name] == ["conflict", "skipped"]


@pytest.mark.asyncio
async def test_changed_trusted_source_is_republished_on_the_next_run(
    bootstrap_fixture,
) -> None:
    bootstrap = bootstrap_fixture["bootstrap"]
    service = bootstrap_fixture["service"]
    local_root = bootstrap_fixture["local_root"]

    first = await bootstrap.run(
        bootstrap_fixture["entries"], OWNER, "personal", local_root
    )
    created = next(item for item in first.items if item.name == "personal-good")
    assert created.status == "created"

    _write_skill(
        local_root / "personal-good",
        "personal-good",
        description="Edited after the first bootstrap",
    )
    second = await bootstrap.run(
        bootstrap_fixture["entries"], OWNER, "personal", local_root
    )

    republished = next(item for item in second.items if item.name == "personal-good")
    assert republished.status == "created"
    catalog = await service.catalog("personal", OWNER)
    published = next(
        item for item in catalog.global_skills if item.name == "personal-good"
    )
    assert published.description == "Edited after the first bootstrap"

    # An unchanged source still costs nothing on the run after that.
    third = await bootstrap.run(
        bootstrap_fixture["entries"], OWNER, "personal", local_root
    )
    unchanged = next(item for item in third.items if item.name == "personal-good")
    assert unchanged.status == "skipped"


@pytest.mark.asyncio
async def test_bootstrap_marker_is_unique(bootstrap_fixture) -> None:
    await _complete_bootstrap(bootstrap_fixture)
    database = bootstrap_fixture["database"]
    async with database.session() as db:
        markers = list(
            (
                await db.scalars(
                    select(AppMetadataRecord).where(
                        AppMetadataRecord.key == "global_skill_import_v1"
                    )
                )
            ).all()
        )
    assert len(markers) == 1
