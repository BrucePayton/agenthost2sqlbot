import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tests.conftest import synchronize_workspaces
from tests.test_workspaces import write_workspace

BINARY_PAYLOAD = b"\x00\xff\x80pinned-binary\x00"


def _renamed_bundle(bundle, name: str, description: str):
    from app.skills.bundle import build_bundle

    content = (
        f"---\nname: {name}\ndescription: {description}\n---\nUse {name}.\n"
    ).encode()
    return build_bundle(
        content,
        [(item.path, item.content) for item in bundle.files],
    )


@pytest.fixture
def session_skill_bundles():
    from app.skills.bundle import build_bundle

    first = build_bundle(
        b"""---
name: review
description: Review version one
---
Review the original content.
""",
        [("references/policy.bin", BINARY_PAYLOAD)],
    )
    second = build_bundle(
        b"""---
name: review
description: Review version two
---
Review the updated content.
""",
        [("references/policy.bin", BINARY_PAYLOAD)],
    )
    disabled = build_bundle(
        b"""---
name: disabled-skill
description: This Skill is disabled
---
Disabled.
""",
        [],
    )
    archived = build_bundle(
        b"""---
name: archived-skill
description: This Skill is archived
---
Archived.
""",
        [],
    )
    return first, second, disabled, archived


@pytest.fixture
async def session_skill_fixture(
    settings_factory,
) -> AsyncIterator[tuple[object, object, object, Path]]:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.sessions.service import SessionService
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual", skills=())
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    skills = SkillService(
        SkillRepository(database),
        WorkspaceAccessService(database),
        settings.skill_bundle_limits,
    )
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        skills=skills,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    try:
        yield sessions, skills, owner, settings.app_data_dir
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_session_creation_persists_creator(session_skill_fixture) -> None:
    sessions, _skills, owner, _data_dir = session_skill_fixture

    created = await sessions.create("actual", owner)

    assert created.created_by == owner.user_id
    assert (await sessions.get(created.id)).created_by == owner.user_id


@pytest.mark.asyncio
async def test_claim_legacy_sessions_reassigns_and_removes_reserved_user(
    session_skill_fixture,
) -> None:
    from datetime import UTC, datetime

    from app.db.models import SessionRecord, UserRecord
    from app.sessions.service import LEGACY_SESSION_OWNER_ID

    sessions, _skills, owner, _data_dir = session_skill_fixture
    now = datetime.now(UTC)
    async with sessions.database.session() as db:
        db.add(
            UserRecord(
                id=LEGACY_SESSION_OWNER_ID,
                external_subject="migration:legacy-session-owner",
                display_name="Legacy Session Owner",
                provider="migration",
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            SessionRecord(
                id="legacy-session",
                workspace_id="actual",
                created_by=LEGACY_SESSION_OWNER_ID,
                title="Legacy Session",
                title_source="auto",
                status="idle",
                workspace_snapshot_json='{"schema_version":2,"skills":[]}',
                workspace_snapshot_hash="legacy-snapshot-hash",
                session_dir="sessions/legacy-session",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    claimed = await sessions.claim_legacy_sessions(owner.user_id)

    assert claimed == 1
    assert (await sessions.get("legacy-session")).created_by == owner.user_id
    async with sessions.database.session() as db:
        assert await db.get(UserRecord, LEGACY_SESSION_OWNER_ID) is None


@pytest.mark.asyncio
async def test_new_session_snapshot_pins_global_and_personal_version_ids(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    sessions, skills, owner, data_dir = session_skill_fixture
    personal_bundle, _replacement, _disabled, _archived = session_skill_bundles
    global_bundle = _renamed_bundle(
        personal_bundle, "global-review", "Pinned global review"
    )
    global_skill = (
        await skills.publish_trusted_global_bundle(
            global_bundle,
            created_by=owner.user_id,
            origin={"type": "test"},
        )
    ).skill
    personal_skill = (
        await skills.import_personal_bundle(
            "actual",
            owner,
            personal_bundle,
            origin={"type": "test"},
        )
    ).skill

    created = await sessions.create("actual", owner)

    snapshot = json.loads(created.workspace_snapshot_json)
    manifests = {item["id"]: item for item in snapshot["skills"]}
    assert snapshot["schema_version"] == 4
    assert set(manifests) == {global_skill.id, personal_skill.id}
    assert set(manifests[global_skill.id]) == {
        "id",
        "scope",
        "version_id",
        "version_no",
        "name",
        "description",
        "bundle_hash",
        "artifact_key",
        "files",
    }
    assert manifests[global_skill.id]["scope"] == "global"
    assert manifests[personal_skill.id]["scope"] == "workspace"
    assert manifests[global_skill.id]["version_id"] == global_skill.summary.version.id
    assert (
        manifests[personal_skill.id]["version_id"] == personal_skill.summary.version.id
    )
    assert manifests[global_skill.id]["artifact_key"] == (
        global_skill.summary.version.artifact_key
    )
    assert manifests[personal_skill.id]["files"] == [
        {
            "path": "SKILL.md",
            "sha256": "sha256:"
            + hashlib.sha256(personal_bundle.content.encode()).hexdigest(),
            "size_bytes": len(personal_bundle.content.encode()),
        },
        {
            "path": "references/policy.bin",
            "sha256": personal_bundle.files[0].sha256,
            "size_bytes": len(BINARY_PAYLOAD),
        },
    ]
    assert (
        data_dir
        / created.session_dir
        / "workspace/.claude/skills/global-review/SKILL.md"
    ).is_file()


@pytest.mark.asyncio
async def test_disabling_global_changes_only_later_session(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    sessions, skills, owner, data_dir = session_skill_fixture
    source, _replacement, _disabled, _archived = session_skill_bundles
    bundle = _renamed_bundle(source, "global-review", "Pinned global review")
    global_skill = (
        await skills.publish_trusted_global_bundle(
            bundle,
            created_by=owner.user_id,
            origin={"type": "test"},
        )
    ).skill
    first = await sessions.create("actual", owner)

    await skills.set_global_enabled("actual", global_skill.id, owner, enabled=False)
    second = await sessions.create("actual", owner)

    first_snapshot = json.loads(first.workspace_snapshot_json)
    second_snapshot = json.loads(second.workspace_snapshot_json)
    assert first_snapshot["skills"][0]["version_id"] == (
        global_skill.summary.version.id
    )
    assert first_snapshot["skills"][0]["scope"] == "global"
    assert second_snapshot["skills"] == []
    assert (
        data_dir / first.session_dir / "workspace/.claude/skills/global-review/SKILL.md"
    ).is_file()


@pytest.mark.asyncio
async def test_replacing_personal_skill_changes_only_later_session(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    sessions, skills, owner, data_dir = session_skill_fixture
    first_bundle, second_bundle, _disabled, _archived = session_skill_bundles
    original = (
        await skills.import_personal_bundle(
            "actual", owner, first_bundle, origin={"type": "test"}
        )
    ).skill
    first = await sessions.create("actual", owner)

    replacement = (
        await skills.import_personal_bundle(
            "actual",
            owner,
            second_bundle,
            on_conflict="overwrite",
            expected_hash=original.bundle_hash,
            origin={"type": "test"},
        )
    ).skill
    second = await sessions.create("actual", owner)

    first_manifest = json.loads(first.workspace_snapshot_json)["skills"][0]
    second_manifest = json.loads(second.workspace_snapshot_json)["skills"][0]
    assert first_manifest["version_id"] == original.summary.version.id
    assert second_manifest["version_id"] == replacement.summary.version.id
    assert first_manifest["version_id"] != second_manifest["version_id"]
    assert (
        data_dir / first.session_dir / "workspace/.claude/skills/review/SKILL.md"
    ).read_text(encoding="utf-8") == first_bundle.content
    assert (
        data_dir / second.session_dir / "workspace/.claude/skills/review/SKILL.md"
    ).read_text(encoding="utf-8") == second_bundle.content


@pytest.mark.asyncio
async def test_materializer_verifies_artifact_and_bundle_hash_before_publish(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    from app.errors import AppError
    from app.sessions.snapshot import build_session_snapshot
    from app.skills.models import ResolvedSkillBundle
    from app.workspaces.materializer import materialize_session_workspace

    sessions, skills, owner, data_dir = session_skill_fixture
    first_bundle, second_bundle, _disabled, _archived = session_skill_bundles
    stored = (
        await skills.import_personal_bundle(
            "actual", owner, first_bundle, origin={"type": "test"}
        )
    ).skill
    resolved = ResolvedSkillBundle(stored.summary, first_bundle)
    snapshot = build_session_snapshot(sessions.registry.get("actual"), (resolved,))
    mismatched = ResolvedSkillBundle(stored.summary, second_bundle)

    with pytest.raises(AppError) as exc_info:
        materialize_session_workspace(
            sessions.registry.get("actual"),
            "hash-mismatch",
            data_dir,
            snapshot,
            (mismatched,),
            limits=skills.limits,
        )

    assert exc_info.value.code == "skill_artifact_corrupt"
    assert not (data_dir / "sessions/hash-mismatch").exists()
    assert not tuple((data_dir / "sessions").glob(".hash-mismatch.tmp-*"))


@pytest.mark.asyncio
async def test_materializer_delegates_opaque_artifact_key_validation(
    session_skill_fixture,
    session_skill_bundles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from app.errors import AppError
    from app.sessions.snapshot import build_session_snapshot
    from app.skills.models import ResolvedSkillBundle
    from app.workspaces import materializer

    sessions, skills, owner, data_dir = session_skill_fixture
    bundle, _replacement, _disabled, _archived = session_skill_bundles
    stored = (
        await skills.import_personal_bundle(
            "actual", owner, bundle, origin={"type": "test"}
        )
    ).skill
    opaque_key = "opaque-artifact-identity"
    opaque_summary = replace(
        stored.summary,
        version=replace(stored.summary.version, artifact_key=opaque_key),
    )
    resolved = ResolvedSkillBundle(opaque_summary, bundle)
    snapshot = build_session_snapshot(sessions.registry.get("actual"), (resolved,))

    def accepts_opaque_key(artifact_key: str, bundle_hash: str) -> bool:
        return (artifact_key, bundle_hash) == (opaque_key, bundle.bundle_hash)

    monkeypatch.setattr(
        materializer,
        "artifact_key_matches_bundle",
        accepts_opaque_key,
    )

    workspace = materializer.materialize_session_workspace(
        sessions.registry.get("actual"),
        "opaque-artifact-key",
        data_dir,
        snapshot,
        (resolved,),
        limits=skills.limits,
    )
    assert (workspace.workspace_dir / ".claude/skills/review/SKILL.md").read_text(
        encoding="utf-8"
    ) == bundle.content

    tampered = ResolvedSkillBundle(
        replace(
            opaque_summary,
            version=replace(opaque_summary.version, artifact_key="tampered-key"),
        ),
        bundle,
    )
    with pytest.raises(AppError) as exc_info:
        materializer.materialize_session_workspace(
            sessions.registry.get("actual"),
            "tampered-artifact-key",
            data_dir,
            snapshot,
            (tampered,),
            limits=skills.limits,
        )

    assert exc_info.value.code == "skill_artifact_corrupt"
    assert not (data_dir / "sessions/tampered-artifact-key").exists()
    assert not tuple((data_dir / "sessions").glob(".tampered-artifact-key.tmp-*"))


@pytest.mark.asyncio
async def test_missing_artifact_fails_session_creation_without_partial_workspace(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    from sqlalchemy import func, select

    from app.db.models import SessionRecord
    from app.errors import AppError

    sessions, skills, owner, data_dir = session_skill_fixture
    bundle, _replacement, _disabled, _archived = session_skill_bundles
    stored = (
        await skills.import_personal_bundle(
            "actual", owner, bundle, origin={"type": "test"}
        )
    ).skill
    skills.repository.artifacts.delete(stored.summary.version.artifact_key)

    with pytest.raises(AppError) as exc_info:
        await sessions.create("actual", owner)

    assert exc_info.value.code == "skill_artifact_corrupt"
    sessions_root = data_dir / "sessions"
    assert not sessions_root.exists() or list(sessions_root.iterdir()) == []
    async with sessions.database.session() as db:
        assert await db.scalar(select(func.count()).select_from(SessionRecord)) == 0


@pytest.mark.asyncio
async def test_unavailable_artifact_fails_without_session_row_or_workspace(
    session_skill_fixture,
    session_skill_bundles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, select

    from app.db.models import SessionRecord
    from app.errors import AppError

    sessions, skills, owner, data_dir = session_skill_fixture
    bundle, _replacement, _disabled, _archived = session_skill_bundles
    await skills.import_personal_bundle(
        "actual", owner, bundle, origin={"type": "test"}
    )

    def unavailable(*_args, **_kwargs):
        raise OSError("artifact backend unavailable")

    monkeypatch.setattr(skills.repository.artifacts, "read", unavailable)

    with pytest.raises(AppError) as exc_info:
        await sessions.create("actual", owner)

    assert exc_info.value.code == "skill_artifact_unavailable"
    sessions_root = data_dir / "sessions"
    assert not sessions_root.exists() or list(sessions_root.iterdir()) == []
    async with sessions.database.session() as db:
        assert await db.scalar(select(func.count()).select_from(SessionRecord)) == 0


@pytest.mark.asyncio
async def test_session_autocomplete_uses_snapshot_not_current_catalog(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    from app.sessions.catalog import SkillCatalogItem

    sessions, skills, owner, _data_dir = session_skill_fixture
    first_bundle, second_bundle, _disabled, _archived = session_skill_bundles
    original = (
        await skills.import_personal_bundle(
            "actual", owner, first_bundle, origin={"type": "test"}
        )
    ).skill
    first = await sessions.create("actual", owner)
    await skills.import_personal_bundle(
        "actual",
        owner,
        second_bundle,
        on_conflict="overwrite",
        expected_hash=original.bundle_hash,
        origin={"type": "test"},
    )
    second = await sessions.create("actual", owner)

    assert await sessions.list_skills(first.id) == (
        SkillCatalogItem("review", "Review version one"),
    )
    assert await sessions.list_skills(second.id) == (
        SkillCatalogItem("review", "Review version two"),
    )


@pytest.mark.asyncio
async def test_runtime_snapshot_options_and_skills_are_preserved(
    session_skill_fixture,
    session_skill_bundles,
) -> None:
    sessions, skills, owner, _data_dir = session_skill_fixture
    bundle, _replacement, _disabled, _archived = session_skill_bundles
    await skills.import_personal_bundle(
        "actual", owner, bundle, origin={"type": "test"}
    )

    created = await sessions.create("actual", owner)

    snapshot = json.loads(created.workspace_snapshot_json)
    template = json.loads(sessions.registry.get("actual").snapshot_json)
    assert snapshot["model"] == template["model"]
    assert snapshot["allowed_tools"] == template["allowed_tools"]
    assert snapshot["mcp_servers"] == template["mcp_servers"]
    assert [skill["name"] for skill in snapshot["skills"]] == ["review"]


@pytest.mark.asyncio
async def test_sessions_pin_enabled_managed_skill_bundles(
    session_skill_fixture, session_skill_bundles
) -> None:
    sessions, skills, owner, data_dir = session_skill_fixture
    bundle_v1, bundle_v2, disabled_bundle, archived_bundle = session_skill_bundles
    first_skill = await skills.import_bundle("actual", owner, bundle_v1, enabled=True)
    await skills.import_bundle("actual", owner, disabled_bundle, enabled=False)
    archived_skill = await skills.import_bundle(
        "actual", owner, archived_bundle, enabled=True
    )
    await skills.archive(
        archived_skill.id,
        owner,
        expected_hash=archived_skill.bundle_hash,
    )

    first = await sessions.create("actual", owner)
    first_skill_dir = data_dir / first.session_dir / "workspace/.claude/skills/review"
    first_path = first_skill_dir / "SKILL.md"
    assert first_path.read_text(encoding="utf-8") == bundle_v1.content
    assert first_path.is_symlink() is False
    assert first_skill_dir.is_symlink() is False
    assert (first_skill_dir / "references/policy.bin").read_bytes() == BINARY_PAYLOAD
    assert not (first_skill_dir.parent / "disabled-skill").exists()
    assert not (first_skill_dir.parent / "archived-skill").exists()

    await skills.update(
        first_skill.id,
        owner,
        content=bundle_v2.content,
        expected_hash=first_skill.bundle_hash,
    )
    second = await sessions.create("actual", owner)
    second_path = (
        data_dir / second.session_dir / "workspace/.claude/skills/review/SKILL.md"
    )

    assert first_path.read_text(encoding="utf-8") == bundle_v1.content
    assert second_path.read_text(encoding="utf-8") == bundle_v2.content
    first_snapshot = json.loads(first.workspace_snapshot_json)
    second_snapshot = json.loads(second.workspace_snapshot_json)
    assert first_snapshot["schema_version"] == 4
    assert "skills_root_env" not in first_snapshot
    assert (
        first_snapshot["skills"][0]["bundle_hash"]
        != (second_snapshot["skills"][0]["bundle_hash"])
    )

    first_path.write_text("not the pinned manifest", encoding="utf-8")
    assert [
        (item.name, item.description) for item in await sessions.list_skills(first.id)
    ] == [("review", "")]


@pytest.mark.asyncio
async def test_session_materialization_failure_leaves_no_orphan_directory(
    session_skill_fixture,
    session_skill_bundles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.errors import AppError
    from app.workspaces import materializer

    sessions, skills, owner, data_dir = session_skill_fixture
    bundle_v1, _bundle_v2, _disabled, _archived = session_skill_bundles
    await skills.import_bundle("actual", owner, bundle_v1, enabled=True)

    def fail_verification(*_args, **_kwargs):
        raise AppError(
            "skill_materialization_failed",
            "Skill materialization failed.",
            500,
        )

    monkeypatch.setattr(materializer, "load_bundle_from_directory", fail_verification)

    with pytest.raises(AppError) as exc_info:
        await sessions.create("actual", owner)

    assert exc_info.value.code == "skill_artifact_corrupt"
    assert list((data_dir / "sessions").iterdir()) == []


@pytest.mark.asyncio
async def test_session_database_commit_failure_removes_materialized_directory(
    session_skill_fixture,
    session_skill_bundles,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, skills, owner, data_dir = session_skill_fixture
    bundle_v1, _bundle_v2, _disabled, _archived = session_skill_bundles
    await skills.import_bundle("actual", owner, bundle_v1, enabled=True)

    async def fail_commit(_self) -> None:
        raise RuntimeError("forced Session commit failure")

    monkeypatch.setattr(
        sessions.database._session_factory.class_,
        "commit",
        fail_commit,
    )

    with pytest.raises(RuntimeError, match="forced Session commit failure"):
        await sessions.create("actual", owner)

    assert list((data_dir / "sessions").iterdir()) == []


@pytest.mark.asyncio
async def test_session_create_has_no_fallible_refresh_after_commit(
    session_skill_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, _skills, owner, data_dir = session_skill_fixture

    async def fail_refresh(*_args, **_kwargs) -> None:
        raise RuntimeError("refresh must not run after Session commit")

    monkeypatch.setattr(
        sessions.database._session_factory.class_,
        "refresh",
        fail_refresh,
    )

    created = await sessions.create("actual", owner)

    assert (data_dir / created.session_dir).is_dir()
    assert (await sessions.get(created.id)).id == created.id


@pytest.mark.asyncio
async def test_session_post_commit_exit_failure_preserves_committed_directory(
    session_skill_fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import func, select

    from app.db.models import SessionRecord

    sessions, _skills, owner, data_dir = session_skill_fixture
    real_session = sessions.database.session

    @asynccontextmanager
    async def fail_after_commit():
        async with real_session() as db:
            yield db
            session_count = await db.scalar(
                select(func.count()).select_from(SessionRecord)
            )
            if session_count:
                raise RuntimeError("forced post-commit context exit failure")

    monkeypatch.setattr(sessions.database, "session", fail_after_commit)

    with pytest.raises(RuntimeError, match="post-commit context exit failure"):
        await sessions.create("actual", owner)

    async with sessions.database.engine.connect() as connection:
        committed = (
            await connection.execute(
                select(SessionRecord.id, SessionRecord.session_dir)
            )
        ).one()
    assert committed.id
    assert (data_dir / committed.session_dir).is_dir()


@pytest.mark.asyncio
async def test_session_uses_configured_permissive_limits_at_every_bundle_boundary(
    settings_factory,
) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.sessions.service import SessionService
    from app.skills.bundle import SkillBundleLimits, build_bundle
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual", skills=())
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    limits = SkillBundleLimits(
        max_file_bytes=1,
        max_total_bytes=64 * 1024,
        max_files=201,
    )
    repository = SkillRepository(database, limits)
    skills = SkillService(repository, WorkspaceAccessService(database), limits)
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        skills=skills,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    bundle = build_bundle(
        b"---\nname: many-files\ndescription: Uses configured limits\n---\n",
        [(f"references/{index:03}.bin", b"") for index in range(201)],
        limits,
    )

    created = await skills.import_bundle("actual", owner, bundle, enabled=True)
    assert (await skills.get_enabled_bundles("actual"))[0][1] == bundle

    session = await sessions.create("actual", owner)

    skill_dir = (
        settings.app_data_dir
        / session.session_dir
        / "workspace/.claude/skills/many-files"
    )
    assert len(tuple((skill_dir / "references").iterdir())) == 201
    assert json.loads(session.workspace_snapshot_json)["skills"][0]["id"] == created.id
    await database.dispose()


@pytest.mark.asyncio
async def test_strict_configured_limits_reject_import_read_and_materialization(
    settings_factory,
) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.errors import AppError
    from app.sessions.snapshot import build_session_snapshot
    from app.skills.bundle import SkillBundleLimits, build_bundle
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.materializer import materialize_session_workspace
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual", skills=())
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    entry = registry.scan()[0]
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    permissive = SkillBundleLimits(max_file_bytes=1, max_total_bytes=4096, max_files=2)
    strict = SkillBundleLimits(max_file_bytes=1, max_total_bytes=4096, max_files=1)
    access = WorkspaceAccessService(database)
    permissive_skills = SkillService(
        SkillRepository(database, permissive),
        access,
        permissive,
    )
    strict_skills = SkillService(
        SkillRepository(database, strict),
        access,
        strict,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    bundle = build_bundle(
        b"---\nname: two-files\ndescription: Exceeds strict limit\n---\n",
        [("a.bin", b""), ("b.bin", b"")],
        permissive,
    )

    with pytest.raises(AppError) as import_error:
        await strict_skills.import_bundle("actual", owner, bundle, enabled=True)
    assert import_error.value.code == "skill_bundle_too_large"

    created = await permissive_skills.import_bundle(
        "actual", owner, bundle, enabled=True
    )
    with pytest.raises(AppError) as read_error:
        await strict_skills.get_enabled_bundles("actual")
    assert read_error.value.code == "skill_artifact_corrupt"

    from app.skills.models import ResolvedSkillBundle

    resolved = (ResolvedSkillBundle(created.summary, bundle),)
    snapshot = build_session_snapshot(entry, resolved)
    with pytest.raises(AppError) as materialization_error:
        materialize_session_workspace(
            entry,
            "strict-limits",
            settings.app_data_dir,
            snapshot,
            resolved,
            limits=strict,
        )
    assert materialization_error.value.code == "skill_artifact_corrupt"
    assert list((settings.app_data_dir / "sessions").iterdir()) == []
    await database.dispose()


@pytest.mark.asyncio
async def test_session_service_creates_renames_lists_and_deletes(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.sessions.service import SessionService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    registry = WorkspaceRegistry(
        settings.workspaces_root, settings.claude_model, environ={}
    )
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    service = SessionService(database, registry, settings.app_data_dir)
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )

    created = await service.create("actual", owner)

    assert created.title == "新会话"
    assert created.title_source == "auto"
    assert created.status == "idle"
    assert created.claude_session_id is None
    assert settings.app_data_dir.joinpath(created.session_dir, "workspace").is_dir()
    assert len(await service.list_for_workspace("actual", settings.mock_user_id)) == 1

    renamed = await service.rename(created.id, "  Quarterly review  ")
    assert renamed.title == "Quarterly review"
    assert renamed.title_source == "user"

    await service.delete(created.id)

    assert await service.list_for_workspace("actual", settings.mock_user_id) == []
    assert not settings.app_data_dir.joinpath(created.session_dir).exists()
    await database.dispose()


@pytest.mark.asyncio
async def test_session_lists_are_filtered_by_creator(settings_factory) -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord
    from app.sessions.service import SessionService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"team": "owner"},
    )
    write_workspace(settings.workspaces_root, "team")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    async with database.session() as db:
        db.add(
            UserRecord(
                id="member",
                external_subject="member",
                display_name="Member",
                provider="test",
            )
        )
        await db.flush()
        db.add(
            WorkspaceMemberRecord(
                workspace_id="team",
                user_id="member",
                role="member",
            )
        )
        await db.commit()
    service = SessionService(database, registry, settings.app_data_dir)
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    member = IdentityContext("member", "member", "Member")
    owner_session = await service.create("team", owner)
    member_session = await service.create("team", member)

    assert [
        item.id
        for item in await service.list_for_workspace("team", settings.mock_user_id)
    ] == [owner_session.id]
    assert [item.id for item in await service.list_for_workspace("team", "member")] == [
        member_session.id
    ]
    await database.dispose()


@pytest.mark.asyncio
async def test_session_service_protects_running_session_and_sets_auto_title(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.errors import AppError
    from app.sessions.service import SessionService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    service = SessionService(database, registry, settings.app_data_dir)
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    created = await service.create("actual", owner)

    titled = await service.set_auto_title(
        created.id, "This is the first meaningful user request that is long"
    )
    assert titled.title == "This is the first meaningful"

    await service.set_status(created.id, "running")
    with pytest.raises(AppError) as exc_info:
        await service.delete(created.id)
    assert exc_info.value.code == "session_busy"

    await database.dispose()


@pytest.mark.asyncio
async def test_new_sessions_capture_mcp_changes_without_rewriting_existing_snapshots(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.sessions.service import SessionService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    workspace = write_workspace(settings.workspaces_root, "actual")
    environ = {"DOCS_MCP_URL": "https://mcp.example.test"}
    registry = WorkspaceRegistry(
        settings.workspaces_root,
        settings.claude_model,
        environ=environ,
    )
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    service = SessionService(database, registry, settings.app_data_dir)
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )

    existing = await service.create("actual", owner)
    manifest_path = workspace / "workspace.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "mcp_servers: {}",
            """mcp_servers:
  docs:
    type: http
    url_env: DOCS_MCP_URL""",
        ),
        encoding="utf-8",
    )
    registry.scan()
    new = await service.create("actual", owner)

    persisted_existing = await service.get(existing.id)
    assert json.loads(persisted_existing.workspace_snapshot_json)["mcp_servers"] == {}
    assert json.loads(new.workspace_snapshot_json)["mcp_servers"] == {
        "docs": {
            "authorization_env": None,
            "type": "http",
            "url_env": "DOCS_MCP_URL",
        }
    }

    await database.dispose()


@pytest.mark.asyncio
async def test_session_lock_registry_serializes_same_session() -> None:
    from app.sessions.locks import SessionLockRegistry

    registry = SessionLockRegistry()

    async with registry.acquire("session-1"):
        assert registry.locked("session-1") is True
        assert registry.locked("session-2") is False

    assert registry.locked("session-1") is False


@pytest.mark.asyncio
async def test_session_materializes_exact_resolved_instruction_bytes(
    session_skill_fixture, monkeypatch
) -> None:
    """A template edit after resolution must not change the pinned runtime instructions."""
    from app.instructions.service import instructions_hash

    sessions, _skills, owner, data_dir = session_skill_fixture
    entry = sessions.registry.get("actual")
    template = entry.directory / "CLAUDE.md"
    template.write_text("Resolved version A\n", encoding="utf-8")
    original_get = sessions.instructions.get

    async def resolve_then_edit(*args, **kwargs):
        """Simulate a concurrent template publish in the resolution/materialization gap."""
        resolved = await original_get(*args, **kwargs)
        template.write_text("Changed version B\n", encoding="utf-8")
        return resolved

    monkeypatch.setattr(sessions.instructions, "get", resolve_then_edit)
    created = await sessions.create("actual", owner)
    copied = data_dir / created.session_dir / "workspace" / "CLAUDE.md"
    assert copied.read_text() == "Resolved version A\n"
    assert instructions_hash(copied.read_text()).removeprefix("sha256:") in created.workspace_snapshot_json
