from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import AsyncIterator

import pytest

VALID_SKILL_MD = b"""---
name: review-changes
description: Review changes
---
Review the proposed changes.
"""


def uploaded_skill(
    source_name: str = "review",
    skill_markdown: bytes = VALID_SKILL_MD,
    files: tuple[tuple[str, bytes], ...] = (),
):
    from app.skills.bundle import load_bundle_from_uploaded_files

    return load_bundle_from_uploaded_files(
        [
            (f"{source_name}/SKILL.md", skill_markdown),
            *((f"{source_name}/{path}", content) for path, content in files),
        ]
    )


@pytest.fixture
def bundle():
    from app.skills.bundle import build_bundle

    return build_bundle(
        VALID_SKILL_MD,
        [("references/policy.bin", b"\x00\xffpolicy")],
    )


@pytest.fixture
async def skill_service(settings_factory) -> AsyncIterator[object]:
    from app.auth.access import WorkspaceAccessService
    from app.db.base import Database
    from app.db.models import (
        PlatformRoleBindingRecord,
        UserRecord,
        WorkspaceMemberRecord,
        WorkspaceRecord,
    )
    from app.skills.access import PlatformSkillAccessService
    from app.skills.artifacts import FilesystemSkillArtifactStore
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService

    settings = settings_factory()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    async with database.session() as db:
        db.add_all(
            [
                UserRecord(
                    id="owner",
                    external_subject="owner",
                    display_name="Owner",
                    provider="mock",
                ),
                UserRecord(
                    id="admin",
                    external_subject="admin",
                    display_name="Admin",
                    provider="mock",
                ),
                UserRecord(
                    id="member",
                    external_subject="member",
                    display_name="Member",
                    provider="mock",
                ),
                UserRecord(
                    id="other-owner",
                    external_subject="other-owner",
                    display_name="Other Owner",
                    provider="mock",
                ),
                WorkspaceRecord(
                    id="personal",
                    name="Personal",
                    kind="team",
                    config_json="{}",
                    owner_user_id="owner",
                    template_id="example",
                ),
                WorkspaceRecord(
                    id="other-personal",
                    name="Other Personal",
                    kind="team",
                    config_json="{}",
                    owner_user_id="other-owner",
                    template_id="example",
                ),
                WorkspaceRecord(id="team", name="Team", kind="team", config_json="{}"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                WorkspaceMemberRecord(
                    workspace_id="personal", user_id="owner", role="owner"
                ),
                WorkspaceMemberRecord(
                    workspace_id="team", user_id="owner", role="owner"
                ),
                WorkspaceMemberRecord(
                    workspace_id="team", user_id="admin", role="admin"
                ),
                WorkspaceMemberRecord(
                    workspace_id="team", user_id="member", role="member"
                ),
                WorkspaceMemberRecord(
                    workspace_id="other-personal",
                    user_id="other-owner",
                    role="owner",
                ),
                PlatformRoleBindingRecord(
                    user_id="admin", role="skill_admin", granted_by=None
                ),
            ]
        )
        personal = await db.get(WorkspaceRecord, "personal")
        other_personal = await db.get(WorkspaceRecord, "other-personal")
        assert personal is not None and other_personal is not None
        personal.kind = "personal"
        other_personal.kind = "personal"
        await db.commit()
    artifact_store = FilesystemSkillArtifactStore(settings.resolved_skill_artifact_root)
    artifact_store.initialize()
    repository = SkillRepository(database, artifact_store, settings.skill_bundle_limits)
    platform_access = PlatformSkillAccessService(database)
    try:
        yield SkillService(
            repository,
            WorkspaceAccessService(database),
            platform_access,
            settings.skill_bundle_limits,
        )
    finally:
        await database.dispose()


@pytest.fixture
def owner():
    from app.auth.models import IdentityContext

    return IdentityContext("owner", "owner", "Owner")


@pytest.fixture
def admin():
    from app.auth.models import IdentityContext

    return IdentityContext("admin", "admin", "Admin")


@pytest.fixture
def member():
    from app.auth.models import IdentityContext

    return IdentityContext("member", "member", "Member")


@pytest.fixture
def other_owner():
    from app.auth.models import IdentityContext

    return IdentityContext("other-owner", "other-owner", "Other Owner")


@pytest.mark.asyncio
async def test_personal_upload_requires_personal_owner_and_defaults_enabled(
    skill_service, owner, admin, bundle
) -> None:
    from app.errors import AppError

    created = await skill_service.import_personal_bundle(
        "personal", owner, bundle, origin={"type": "browser_directory"}
    )

    assert created.status == "created"
    assert created.skill.summary.scope == "workspace"
    assert created.skill.summary.enabled is True
    with pytest.raises(AppError) as exc_info:
        await skill_service.import_personal_bundle(
            "personal", admin, bundle, origin={"type": "browser_directory"}
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
async def test_personal_replace_preserves_disabled_state_and_creates_version(
    skill_service, owner, bundle
) -> None:
    from app.skills.bundle import build_bundle

    created = (
        await skill_service.import_personal_bundle(
            "personal", owner, bundle, origin={"type": "browser_directory"}
        )
    ).skill
    await skill_service.set_personal_enabled(
        created.summary.id,
        owner,
        enabled=False,
        expected_hash=created.summary.version.bundle_hash,
    )
    changed_bundle = build_bundle(
        VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
        [("replacement.txt", b"replacement")],
    )

    replacement = (
        await skill_service.import_personal_bundle(
            "personal",
            owner,
            changed_bundle,
            on_conflict="overwrite",
            expected_hash=created.summary.version.bundle_hash,
            origin={"type": "archive"},
        )
    ).skill

    assert replacement.summary.id == created.summary.id
    assert replacement.summary.enabled is False
    assert replacement.summary.version.version_no == 2


@pytest.mark.asyncio
async def test_personal_overwrite_rejects_stale_hash_when_incoming_matches_current(
    skill_service, owner, bundle
) -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    created = (
        await skill_service.import_personal_bundle(
            "personal", owner, bundle, origin={"type": "archive"}
        )
    ).skill
    changed_bundle = build_bundle(
        VALID_SKILL_MD.replace(b"Review changes", b"Review safely"), ()
    )
    current = (
        await skill_service.import_personal_bundle(
            "personal",
            owner,
            changed_bundle,
            on_conflict="overwrite",
            expected_hash=created.bundle_hash,
            origin={"type": "archive"},
        )
    ).skill

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_personal_bundle(
            "personal",
            owner,
            changed_bundle,
            on_conflict="overwrite",
            expected_hash=created.bundle_hash,
            origin={"type": "archive"},
        )

    assert (exc_info.value.status_code, exc_info.value.code) == (409, "skill_changed")
    assert (await skill_service.repository.require_summary(current.id)).bundle_hash == (
        current.bundle_hash
    )


@pytest.mark.asyncio
async def test_personal_enable_hides_corrupt_skill_before_authorization(
    skill_service, owner, admin, bundle
) -> None:
    from sqlalchemy import update

    from app.db.models import SkillVersionRecord
    from app.errors import AppError

    created = (
        await skill_service.import_personal_bundle(
            "personal", owner, bundle, origin={"type": "archive"}
        )
    ).skill
    async with skill_service.repository.database.session() as db:
        await db.execute(
            update(SkillVersionRecord)
            .where(SkillVersionRecord.id == created.summary.version.id)
            .values(status="failed")
        )
        await db.commit()

    with pytest.raises(AppError) as exc_info:
        await skill_service.set_personal_enabled(
            created.id,
            admin,
            enabled=False,
            expected_hash=created.bundle_hash,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
async def test_global_catalog_defaults_enabled_and_setting_only_affects_new_resolution(
    skill_service, owner, admin
) -> None:
    published = await skill_service.import_global_uploaded_directory(
        admin,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )
    before = await skill_service.resolve_effective_skills("personal", owner)

    catalog = await skill_service.catalog("personal", owner)
    changed = await skill_service.set_global_enabled(
        "personal", published.skill.id, owner, enabled=False
    )
    after = await skill_service.resolve_effective_skills("personal", owner)

    assert [(item.name, item.enabled) for item in catalog.global_skills] == [
        ("review-changes", True)
    ]
    assert changed.enabled is False
    assert [item.skill.id for item in before] == [published.skill.id]
    assert after == ()
    assert before[0].bundle == published.skill.bundle


@pytest.mark.asyncio
async def test_effective_counts_are_kind_aware_and_use_one_query(
    skill_service, owner, other_owner, admin
) -> None:
    from sqlalchemy import event

    from app.skills.bundle import build_bundle

    def named_bundle(name: str, description: str):
        markdown = (
            f"---\nname: {name}\ndescription: {description}\n---\n# {description}\n"
        ).encode()
        return build_bundle(markdown, ())

    first_global = await skill_service.publish_trusted_global_bundle(
        named_bundle("global-one", "Global one"),
        created_by=admin.user_id,
        origin={"type": "test"},
    )
    await skill_service.publish_trusted_global_bundle(
        named_bundle("global-two", "Global two"),
        created_by=admin.user_id,
        origin={"type": "test"},
    )
    await skill_service.import_personal_bundle(
        "personal",
        owner,
        named_bundle("personal-one", "Personal one"),
        origin={"type": "test"},
    )
    await skill_service.import_personal_bundle(
        "other-personal",
        other_owner,
        named_bundle("personal-two", "Personal two"),
        origin={"type": "test"},
    )
    await skill_service.import_bundle(
        "team", owner, named_bundle("legacy-team-skill", "Legacy team"), enabled=True
    )
    await skill_service.set_global_enabled(
        "personal", first_global.skill.id, owner, enabled=False
    )

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = skill_service.repository.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        counts = await skill_service.count_effective_by_workspace(
            ("personal", "other-personal", "team")
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert counts == {"personal": 2, "other-personal": 3, "team": 0}
    assert len(statements) == 1


@pytest.mark.asyncio
async def test_registered_user_can_publish_global_skill(
    skill_service, owner
) -> None:
    published = await skill_service.import_global_uploaded_directory(
        owner,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )

    assert published.skill.scope == "global"


@pytest.mark.asyncio
async def test_registered_user_can_manage_global_skills(
    skill_service, owner
) -> None:
    assert await skill_service.platform_access.can_manage_global_skills(owner) is True


@pytest.mark.asyncio
async def test_platform_skill_admin_bootstrap_is_exact_and_idempotent(
    skill_service, owner
) -> None:
    from sqlalchemy import func, select

    from app.db.models import PlatformRoleBindingRecord

    await skill_service.platform_access.bootstrap_subjects(owner, (" owner ",))
    assert await skill_service.platform_access.is_skill_admin(owner) is False

    await skill_service.platform_access.bootstrap_subjects(owner, ("owner",))
    await skill_service.platform_access.bootstrap_subjects(owner, ("owner",))
    assert await skill_service.platform_access.is_skill_admin(owner) is True
    async with skill_service.repository.database.session() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(PlatformRoleBindingRecord)
            .where(PlatformRoleBindingRecord.user_id == owner.user_id)
        )
    assert count == 1


@pytest.mark.asyncio
async def test_registered_user_can_archive_another_users_global_skill(
    skill_service, owner, admin
) -> None:
    published = await skill_service.import_global_uploaded_directory(
        admin,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )
    await skill_service.archive_global(
        published.skill.id,
        owner,
        expected_hash=published.skill.bundle_hash,
    )
    assert (await skill_service.catalog("personal", owner)).global_skills == ()


@pytest.mark.asyncio
async def test_personal_upload_conflicts_with_global_name(
    skill_service, owner, admin, bundle
) -> None:
    from app.errors import AppError

    await skill_service.import_global_uploaded_directory(
        admin,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )
    with pytest.raises(AppError) as exc_info:
        await skill_service.import_personal_bundle(
            "personal", owner, bundle, origin={"type": "archive"}
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_name_conflict",
    )


@pytest.mark.asyncio
async def test_global_publish_reports_personal_conflict_count_without_user_ids(
    skill_service, owner, other_owner, admin, bundle
) -> None:
    from app.errors import AppError

    await skill_service.import_personal_bundle(
        "personal", owner, bundle, origin={"type": "archive"}
    )
    await skill_service.import_personal_bundle(
        "other-personal", other_owner, bundle, origin={"type": "archive"}
    )

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_global_uploaded_directory(
            admin,
            uploaded_skill(),
            on_conflict="fail",
            expected_hash=None,
            target_name=None,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_name_conflict",
    )
    assert exc_info.value.details == {"workspace_count": 2}
    assert "owner" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_same_hash_global_upload_is_idempotent(skill_service, admin) -> None:
    first = await skill_service.import_global_uploaded_directory(
        admin,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )
    repeated = await skill_service.import_global_uploaded_directory(
        admin,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )

    assert (first.status, repeated.status) == ("created", "already_imported")
    assert first.skill.id == repeated.skill.id
    assert first.skill.summary.version.id == repeated.skill.summary.version.id


@pytest.mark.asyncio
async def test_global_overwrite_rejects_stale_hash_when_incoming_matches_current(
    skill_service, admin
) -> None:
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    created = await skill_service.import_global_uploaded_directory(
        admin,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )
    changed_bundle = build_bundle(
        VALID_SKILL_MD.replace(b"Review changes", b"Review safely"), ()
    )
    current = await skill_service.import_global_bundle(
        admin,
        changed_bundle,
        on_conflict="overwrite",
        expected_hash=created.skill.bundle_hash,
        origin={"type": "administrator"},
    )

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_global_bundle(
            admin,
            changed_bundle,
            on_conflict="overwrite",
            expected_hash=created.skill.bundle_hash,
            origin={"type": "administrator"},
        )

    assert (exc_info.value.status_code, exc_info.value.code) == (409, "skill_changed")
    assert (
        await skill_service.repository.require_summary(current.skill.id)
    ).bundle_hash == current.skill.bundle_hash


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["insert", "replace", "toggle", "archive", "global_setting"]
)
async def test_personal_mutations_revalidate_workspace_in_write_transaction(
    skill_service, owner, admin, bundle, monkeypatch, operation
) -> None:
    from sqlalchemy import update

    from app.db.models import WorkspaceRecord
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    personal = None
    global_skill = None
    if operation in {"replace", "toggle", "archive"}:
        personal = (
            await skill_service.import_personal_bundle(
                "personal", owner, bundle, origin={"type": "archive"}
            )
        ).skill
    if operation == "global_setting":
        global_skill = (
            await skill_service.import_global_bundle(
                admin, bundle, origin={"type": "administrator"}
            )
        ).skill

    method_name = {
        "insert": "insert_versioned_skill",
        "replace": "replace_current_version",
        "toggle": "set_personal_enabled",
        "archive": "archive_skill",
        "global_setting": "set_global_setting",
    }[operation]
    original = getattr(skill_service.repository, method_name)

    async def change_workspace_kind_then_write(*args, **kwargs):
        async with skill_service.repository.database.session() as db:
            await db.execute(
                update(WorkspaceRecord)
                .where(WorkspaceRecord.id == "personal")
                .values(kind="team")
            )
            await db.commit()
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        skill_service.repository, method_name, change_workspace_kind_then_write
    )
    with pytest.raises(AppError) as exc_info:
        if operation == "insert":
            await skill_service.import_personal_bundle(
                "personal", owner, bundle, origin={"type": "archive"}
            )
        elif operation == "replace":
            assert personal is not None
            await skill_service.import_personal_bundle(
                "personal",
                owner,
                build_bundle(
                    VALID_SKILL_MD.replace(b"Review changes", b"Review safely"), ()
                ),
                on_conflict="overwrite",
                expected_hash=personal.bundle_hash,
                origin={"type": "archive"},
            )
        elif operation == "toggle":
            assert personal is not None
            await skill_service.set_personal_enabled(
                personal.id,
                owner,
                enabled=False,
                expected_hash=personal.bundle_hash,
            )
        elif operation == "archive":
            assert personal is not None
            await skill_service.archive_personal(
                "personal", personal.id, owner, expected_hash=personal.bundle_hash
            )
        else:
            assert global_skill is not None
            await skill_service.set_global_enabled(
                "personal", global_skill.id, owner, enabled=False
            )

    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "skill_scope_invalid",
    )
    if operation == "insert":
        assert (
            await skill_service.repository.list_catalog("personal")
        ).personal_skills == ()
    elif personal is not None:
        unchanged = await skill_service.repository.require_summary(personal.id)
        assert (unchanged.bundle_hash, unchanged.enabled) == (
            personal.bundle_hash,
            True,
        )
    else:
        catalog = await skill_service.repository.list_catalog("personal")
        assert [(item.id, item.enabled) for item in catalog.global_skills] == [
            (global_skill.id, True)
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["insert", "replace", "archive"])
async def test_global_mutations_revalidate_contributor_in_write_transaction(
    skill_service, admin, member, bundle, monkeypatch, operation
) -> None:
    from sqlalchemy import delete

    from app.db.models import UserRecord
    from app.errors import AppError
    from app.skills.bundle import build_bundle

    current = None
    if operation != "insert":
        current = (
            await skill_service.import_global_bundle(
                admin, bundle, origin={"type": "administrator"}
            )
        ).skill
    method_name = {
        "insert": "insert_versioned_skill",
        "replace": "replace_current_version",
        "archive": "archive_skill",
    }[operation]
    original = getattr(skill_service.repository, method_name)

    async def delete_contributor_then_write(*args, **kwargs):
        async with skill_service.repository.database.session() as db:
            await db.execute(
                delete(UserRecord).where(UserRecord.id == member.user_id)
            )
            await db.commit()
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        skill_service.repository,
        method_name,
        delete_contributor_then_write,
    )
    with pytest.raises(AppError) as exc_info:
        if operation == "insert":
            await skill_service.import_global_bundle(
                member, bundle, origin={"type": "collaborator"}
            )
        elif operation == "replace":
            assert current is not None
            await skill_service.import_global_bundle(
                member,
                build_bundle(
                    VALID_SKILL_MD.replace(b"Review changes", b"Review safely"), ()
                ),
                on_conflict="overwrite",
                expected_hash=current.bundle_hash,
                origin={"type": "collaborator"},
            )
        else:
            assert current is not None
            await skill_service.archive_global(
                current.id, member, expected_hash=current.bundle_hash
            )

    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "skill_not_found",
    )
    catalog = await skill_service.repository.list_catalog("personal")
    if current is None:
        assert catalog.global_skills == ()
    else:
        assert [(item.id, item.bundle_hash) for item in catalog.global_skills] == [
            (current.id, current.bundle_hash)
        ]


@pytest.mark.asyncio
async def test_global_archive_does_not_commit_after_contributor_is_deprovisioned(
    skill_service, admin, member, bundle, monkeypatch
) -> None:
    from sqlalchemy import delete, text
    from sqlalchemy.exc import OperationalError

    from app.db.models import SkillRecord, UserRecord
    from app.errors import AppError

    current = (
        await skill_service.import_global_bundle(
            admin, bundle, origin={"type": "administrator"}
        )
    ).skill
    authorization_checked = asyncio.Event()
    deprovision_attempt_finished = asyncio.Event()
    archive_finished = asyncio.Event()
    operation_order: list[str] = []
    archive_error: Exception | None = None
    original = skill_service.repository._require_mutation_authorization

    async def pause_after_authorization(db, authorization):
        await original(db, authorization)
        if authorization.mode == "global_contributor":
            authorization_checked.set()
            await deprovision_attempt_finished.wait()

    monkeypatch.setattr(
        skill_service.repository,
        "_require_mutation_authorization",
        pause_after_authorization,
    )

    async def archive() -> None:
        nonlocal archive_error
        try:
            await skill_service.archive_global(
                current.id, member, expected_hash=current.bundle_hash
            )
        except Exception as exc:  # noqa: BLE001 - asserted below as a valid race loser
            archive_error = exc
            operation_order.append("archive_rejected")
        else:
            operation_order.append("archive_committed")
        finally:
            archive_finished.set()

    async def deprovision() -> None:
        await authorization_checked.wait()
        was_blocked = False
        async with skill_service.repository.database.session() as db:
            await db.execute(text("PRAGMA busy_timeout=25"))
            try:
                await db.execute(
                    delete(UserRecord).where(UserRecord.id == member.user_id)
                )
                await db.commit()
            except OperationalError as exc:
                await db.rollback()
                assert "database is locked" in str(exc).lower()
                was_blocked = True
        if not was_blocked:
            operation_order.append("deprovision_committed")
            deprovision_attempt_finished.set()
            return

        deprovision_attempt_finished.set()
        await archive_finished.wait()
        async with skill_service.repository.database.session() as db:
            await db.execute(delete(UserRecord).where(UserRecord.id == member.user_id))
            await db.commit()
        operation_order.append("deprovision_committed")

    await asyncio.gather(archive(), deprovision())

    async with skill_service.repository.database.session() as db:
        stored = await db.get(SkillRecord, current.id)
        contributor = await db.get(UserRecord, member.user_id)
    assert stored is not None
    assert contributor is None
    if archive_error is None:
        assert operation_order == ["archive_committed", "deprovision_committed"]
        assert stored.archived_at is not None
    else:
        assert operation_order == ["deprovision_committed", "archive_rejected"]
        assert isinstance(archive_error, AppError)
        assert archive_error.code == "skill_not_found"
        assert stored.archived_at is None


@pytest.mark.asyncio
async def test_effective_resolution_loads_exact_ready_artifacts_in_name_order(
    skill_service, owner, admin
) -> None:
    from app.skills.bundle import build_bundle

    zeta = build_bundle(VALID_SKILL_MD.replace(b"review-changes", b"zeta-skill"), ())
    alpha = build_bundle(
        VALID_SKILL_MD.replace(b"review-changes", b"alpha-skill"),
        (("reference.bin", b"\x00artifact"),),
    )
    await skill_service.publish_trusted_global_bundle(
        zeta, created_by=admin.user_id, origin={"type": "test"}
    )
    personal = (
        await skill_service.import_personal_bundle(
            "personal", owner, alpha, origin={"type": "archive"}
        )
    ).skill

    resolved = await skill_service.resolve_effective_skills("personal", owner)

    assert [item.skill.name for item in resolved] == ["alpha-skill", "zeta-skill"]
    assert resolved[0].skill.version.id == personal.summary.version.id
    assert resolved[0].bundle.files[0].content == b"\x00artifact"


@pytest.mark.asyncio
async def test_effective_resolution_preserves_team_session_membership(
    skill_service, member
) -> None:
    assert await skill_service.resolve_effective_skills("team", member) == ()


@pytest.mark.asyncio
async def test_team_workspace_rejects_new_personal_skill_mutations(
    skill_service, owner, bundle
) -> None:
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_personal_bundle(
            "team", owner, bundle, origin={"type": "archive"}
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "skill_scope_invalid",
    )


@pytest.mark.asyncio
async def test_skill_update_replaces_content_but_preserves_files_and_checks_hash(
    skill_service, owner, bundle
) -> None:
    from app.errors import AppError

    created = await skill_service.import_bundle("personal", owner, bundle, enabled=True)
    updated_md = VALID_SKILL_MD.decode().replace("Review changes", "Review safely")
    updated = await skill_service.update(
        created.id, owner, content=updated_md, expected_hash=created.bundle_hash
    )
    assert updated.description == "Review safely"
    assert [(file.path, file.content) for file in updated.files] == [
        ("references/policy.bin", b"\x00\xffpolicy")
    ]
    with pytest.raises(AppError) as exc_info:
        await skill_service.update(
            created.id,
            owner,
            content=VALID_SKILL_MD.decode(),
            expected_hash=created.bundle_hash,
        )
    assert exc_info.value.code == "skill_changed"


@pytest.mark.asyncio
async def test_copy_is_independent_disabled_and_requires_both_manager_roles(
    skill_service, owner, bundle
) -> None:
    source = await skill_service.import_bundle("personal", owner, bundle, enabled=True)
    copied = await skill_service.copy(source.id, "team", owner)
    assert copied.id != source.id
    assert copied.bundle_hash == source.bundle_hash
    assert copied.enabled is False
    assert copied.origin == {
        "type": "workspace_copy",
        "source_skill_id": source.id,
        "source_hash": source.bundle_hash,
    }
    assert (await skill_service.get(source.id, owner)).workspace_id == "personal"
    assert (await skill_service.get(copied.id, owner)).workspace_id == "team"


@pytest.mark.asyncio
async def test_active_name_conflict_and_archive_name_reuse(
    skill_service, owner
) -> None:
    from app.errors import AppError

    created = await skill_service.create("team", owner, VALID_SKILL_MD.decode())
    with pytest.raises(AppError) as exc_info:
        await skill_service.create("team", owner, VALID_SKILL_MD.decode())
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_name_conflict",
    )
    await skill_service.archive(created.id, owner, expected_hash=created.bundle_hash)
    reused = await skill_service.create("team", owner, VALID_SKILL_MD.decode())
    assert reused.name == created.name
    assert reused.id != created.id


@pytest.mark.asyncio
async def test_active_name_conflict_is_ascii_case_insensitive_per_workspace(
    skill_service,
    owner,
) -> None:
    from app.errors import AppError

    upper_content = VALID_SKILL_MD.decode().replace(
        "review-changes",
        "Review-Changes",
    )
    original = await skill_service.create("team", owner, upper_content)

    with pytest.raises(AppError) as exc_info:
        await skill_service.create("team", owner, VALID_SKILL_MD.decode())
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_name_conflict",
    )

    other_workspace = await skill_service.create(
        "personal",
        owner,
        VALID_SKILL_MD.decode(),
    )
    assert other_workspace.name == "review-changes"

    await skill_service.archive(
        original.id,
        owner,
        expected_hash=original.bundle_hash,
    )
    reused = await skill_service.create("team", owner, VALID_SKILL_MD.decode())
    assert reused.name == "review-changes"


@pytest.mark.asyncio
async def test_concurrent_case_variant_inserts_leave_one_active_skill(
    skill_service,
    owner,
) -> None:
    from app.errors import AppError
    from app.skills.repository import StoredSkill

    upper_content = VALID_SKILL_MD.decode().replace(
        "review-changes",
        "Review-Changes",
    )
    results = await asyncio.gather(
        skill_service.create("team", owner, VALID_SKILL_MD.decode()),
        skill_service.create("team", owner, upper_content),
        return_exceptions=True,
    )

    assert sum(isinstance(result, StoredSkill) for result in results) == 1
    errors = [result for result in results if isinstance(result, AppError)]
    assert [(error.status_code, error.code) for error in errors] == [
        (409, "skill_name_conflict")
    ]
    assert len(await skill_service.list("team", owner)) == 1


@pytest.mark.asyncio
async def test_member_reads_with_a_single_membership_scoped_repository_query(
    skill_service, owner, member, monkeypatch
) -> None:

    created = await skill_service.create("team", owner, VALID_SKILL_MD.decode())

    async def reject_workspace_loop(*_args, **_kwargs):
        raise AssertionError("member reads must not loop through workspace lookups")

    monkeypatch.setattr(skill_service.repository, "get_stored", reject_workspace_loop)
    assert (await skill_service.get(created.id, member)).id == created.id


@pytest.mark.asyncio
async def test_member_cannot_mutate_skills(skill_service, owner, member) -> None:
    from app.errors import AppError

    created = await skill_service.create("team", owner, VALID_SKILL_MD.decode())
    assert [item.id for item in await skill_service.list("team", member)] == [
        created.id
    ]
    with pytest.raises(AppError) as create_error:
        await skill_service.create("team", member, VALID_SKILL_MD.decode())
    assert (create_error.value.status_code, create_error.value.code) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
async def test_update_denies_manager_removed_after_service_precheck(
    skill_service, owner, admin, bundle, monkeypatch
) -> None:
    from sqlalchemy import delete

    from app.db.models import WorkspaceMemberRecord
    from app.errors import AppError

    created = await skill_service.import_bundle("team", owner, bundle, enabled=True)
    replace_bundle = skill_service.repository.replace_bundle

    async def remove_manager_then_replace(*args, **kwargs):
        async with skill_service.repository.database.session() as db:
            await db.execute(
                delete(WorkspaceMemberRecord).where(
                    WorkspaceMemberRecord.workspace_id == "team",
                    WorkspaceMemberRecord.user_id == owner.user_id,
                )
            )
            await db.commit()
        return await replace_bundle(*args, **kwargs)

    monkeypatch.setattr(
        skill_service.repository, "replace_bundle", remove_manager_then_replace
    )
    with pytest.raises(AppError) as exc_info:
        await skill_service.update(
            created.id,
            owner,
            content=VALID_SKILL_MD.decode().replace("Review changes", "Review safely"),
            expected_hash=created.bundle_hash,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (404, "skill_not_found")
    assert (
        await skill_service.get(created.id, admin)
    ).bundle_hash == created.bundle_hash


@pytest.mark.asyncio
async def test_copy_denies_manager_removed_from_source_or_target_before_insert(
    skill_service, owner, bundle, monkeypatch
) -> None:
    from sqlalchemy import delete

    from app.db.models import WorkspaceMemberRecord
    from app.errors import AppError

    source = await skill_service.import_bundle("team", owner, bundle, enabled=True)
    insert_bundle = skill_service.repository.insert_bundle

    async def remove_manager_then_insert(*args, **kwargs):
        async with skill_service.repository.database.session() as db:
            await db.execute(
                delete(WorkspaceMemberRecord).where(
                    WorkspaceMemberRecord.workspace_id == "team",
                    WorkspaceMemberRecord.user_id == owner.user_id,
                )
            )
            await db.commit()
        return await insert_bundle(*args, **kwargs)

    monkeypatch.setattr(
        skill_service.repository, "insert_bundle", remove_manager_then_insert
    )
    with pytest.raises(AppError) as exc_info:
        await skill_service.copy(source.id, "personal", owner)
    assert (exc_info.value.status_code, exc_info.value.code) == (404, "skill_not_found")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption", ["artifact_bytes", "artifact_hash", "bundle_hash"]
)
async def test_enabled_bundle_load_rejects_corrupt_artifact_or_version_metadata(
    skill_service, owner, bundle, corruption
) -> None:
    from sqlalchemy import update

    from app.db.models import SkillVersionRecord
    from app.errors import AppError

    created = await skill_service.import_bundle("team", owner, bundle, enabled=True)
    if corruption == "artifact_bytes":
        artifact_store = skill_service.repository.artifacts
        artifact_store.root.joinpath(created.summary.version.artifact_key).write_bytes(
            b"tampered"
        )
    else:
        async with skill_service.repository.database.session() as db:
            values = (
                {"artifact_sha256": "sha256:" + "0" * 64}
                if corruption == "artifact_hash"
                else {"bundle_hash": "sha256:" + "0" * 64}
            )
            await db.execute(
                update(SkillVersionRecord)
                .where(SkillVersionRecord.id == created.summary.version.id)
                .values(**values)
            )
            await db.commit()
    with pytest.raises(AppError) as exc_info:
        await skill_service.get_enabled_bundles("team")
    assert (exc_info.value.status_code, exc_info.value.code) == (
        500,
        "skill_artifact_corrupt",
    )


@pytest.mark.asyncio
async def test_import_replaces_the_complete_file_set_and_round_trips_binary_files(
    skill_service, owner
) -> None:
    from app.skills.bundle import build_bundle

    original = build_bundle(
        VALID_SKILL_MD,
        [("old.txt", b"old"), ("binary.dat", b"\x00\xff\x80")],
    )
    created = await skill_service.import_bundle("team", owner, original, enabled=True)
    current = await skill_service.get(created.id, owner)
    replacement = build_bundle(
        VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
        [("new.txt", b"new")],
    )
    updated = await skill_service.repository.replace_bundle(
        created.id, current.bundle_hash, replacement, user_id=owner.user_id
    )
    assert [(file.path, file.content) for file in updated.files] == [
        ("new.txt", b"new")
    ]
    assert (await skill_service.get_enabled_bundles("team")) == (
        (updated.id, replacement),
    )


@pytest.mark.asyncio
async def test_failed_replacement_is_atomic_and_leaves_prior_skill_unchanged(
    skill_service, owner, bundle, monkeypatch
) -> None:
    from app.db.models import SkillVersionRecord
    from app.skills.bundle import build_bundle

    created = await skill_service.import_bundle("team", owner, bundle, enabled=True)
    replacement = build_bundle(
        VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
        [("new.txt", b"new")],
    )
    # A version-row failure must leave the prior current version selected.
    session_factory = skill_service.repository.database._session_factory
    original_session = session_factory.class_
    original_flush = original_session.flush

    async def fail_after_delete(self, *args, **kwargs):
        if any(isinstance(item, SkillVersionRecord) for item in self.new):
            raise RuntimeError("forced version insert failure")
        await original_flush(self, *args, **kwargs)

    monkeypatch.setattr(original_session, "flush", fail_after_delete)
    with pytest.raises(RuntimeError, match="forced version insert failure"):
        await skill_service.repository.replace_bundle(
            created.id, created.bundle_hash, replacement, user_id=owner.user_id
        )
    unchanged = await skill_service.get(created.id, owner)
    assert unchanged.bundle_hash == created.bundle_hash
    assert [(file.path, file.content) for file in unchanged.files] == [
        ("references/policy.bin", b"\x00\xffpolicy")
    ]


@pytest.mark.asyncio
async def test_enablement_uses_hash_cas_but_same_hash_writes_are_last_writer_wins(
    skill_service, owner, bundle
) -> None:
    from app.errors import AppError

    created = await skill_service.import_bundle("team", owner, bundle, enabled=False)
    enabled = await skill_service.set_enabled(
        created.id, owner, enabled=True, expected_hash=created.bundle_hash
    )
    disabled = await skill_service.set_enabled(
        created.id, owner, enabled=False, expected_hash=created.bundle_hash
    )
    assert enabled.enabled is True
    assert disabled.enabled is False
    with pytest.raises(AppError) as exc_info:
        await skill_service.set_enabled(
            created.id, owner, enabled=True, expected_hash="sha256:" + "0" * 64
        )
    assert exc_info.value.code == "skill_changed"


@pytest.mark.asyncio
async def test_import_archive_is_disabled_and_list_is_workspace_scoped(
    skill_service, owner
) -> None:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("SKILL.md", VALID_SKILL_MD)
    imported = await skill_service.import_archive("personal", owner, raw.getvalue())
    assert imported.enabled is False
    assert [item.id for item in await skill_service.list("personal", owner)] == [
        imported.id
    ]
    assert await skill_service.list("team", owner) == ()


@pytest.mark.asyncio
async def test_directory_import_is_disabled_idempotent_and_reports_conflict(
    skill_service, owner
) -> None:
    from app.errors import AppError

    upload = uploaded_skill(files=(("references/policy.bin", b"\x00\xff"),))
    created = await skill_service.import_uploaded_directory(
        "team", owner, upload, on_conflict="fail"
    )
    repeated = await skill_service.import_uploaded_directory(
        "team", owner, upload, on_conflict="fail"
    )

    assert created.status == "created"
    assert created.skill.enabled is False
    assert created.skill.origin == {
        "type": "browser_directory",
        "source_name": "review",
    }
    assert repeated.status == "already_imported"
    assert repeated.skill.id == created.skill.id
    assert repeated.skill.updated_at == created.skill.updated_at

    changed_upload = uploaded_skill(
        skill_markdown=VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
        files=(("references/policy.bin", b"\x00\xff"),),
    )
    with pytest.raises(AppError) as exc_info:
        await skill_service.import_uploaded_directory(
            "team", owner, changed_upload, on_conflict="fail"
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_import_conflict",
    )
    assert exc_info.value.details == {
        "skill_id": created.skill.id,
        "existing_hash": created.skill.bundle_hash,
        "incoming_hash": changed_upload.bundle.bundle_hash,
        "incoming_name": "review-changes",
    }


@pytest.mark.asyncio
async def test_directory_overwrite_preserves_identity_enablement_and_is_atomic(
    skill_service, owner
) -> None:
    from app.errors import AppError

    first = await skill_service.import_uploaded_directory(
        "team",
        owner,
        uploaded_skill(files=(("old.txt", b"old"),)),
        on_conflict="fail",
    )
    enabled = await skill_service.set_enabled(
        first.skill.id,
        owner,
        enabled=True,
        expected_hash=first.skill.bundle_hash,
    )
    changed_upload = uploaded_skill(
        source_name="replacement-folder",
        skill_markdown=VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
        files=(("new.bin", b"\x00new"),),
    )

    overwritten = await skill_service.import_uploaded_directory(
        "team",
        owner,
        changed_upload,
        on_conflict="overwrite",
        expected_hash=enabled.bundle_hash,
    )

    assert overwritten.status == "overwritten"
    assert overwritten.skill.id == first.skill.id
    assert overwritten.skill.enabled is True
    assert overwritten.skill.description == "Review safely"
    assert overwritten.skill.origin == {
        "type": "browser_directory",
        "source_name": "replacement-folder",
    }
    assert [(item.path, item.content) for item in overwritten.skill.files] == [
        ("new.bin", b"\x00new")
    ]

    third_upload = uploaded_skill(
        skill_markdown=VALID_SKILL_MD.replace(b"Review changes", b"Review thoroughly")
    )
    with pytest.raises(AppError) as exc_info:
        await skill_service.import_uploaded_directory(
            "team",
            owner,
            third_upload,
            on_conflict="overwrite",
            expected_hash=enabled.bundle_hash,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_changed",
    )
    current = await skill_service.get(first.skill.id, owner)
    assert current.bundle_hash == overwritten.skill.bundle_hash
    assert [(item.path, item.content) for item in current.files] == [
        ("new.bin", b"\x00new")
    ]


@pytest.mark.asyncio
async def test_directory_rename_creates_an_independent_disabled_skill(
    skill_service, owner
) -> None:
    from app.errors import AppError

    original = await skill_service.import_uploaded_directory(
        "team",
        owner,
        uploaded_skill(files=(("references/policy.bin", b"policy"),)),
        on_conflict="fail",
    )
    enabled = await skill_service.set_enabled(
        original.skill.id,
        owner,
        enabled=True,
        expected_hash=original.skill.bundle_hash,
    )
    changed_upload = uploaded_skill(
        skill_markdown=VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
        files=(("references/policy.bin", b"new policy"),),
    )

    renamed = await skill_service.import_uploaded_directory(
        "team",
        owner,
        changed_upload,
        on_conflict="rename",
        target_name="review-safely-copy",
    )

    assert renamed.status == "renamed"
    assert renamed.skill.id != original.skill.id
    assert renamed.skill.name == "review-safely-copy"
    assert renamed.skill.enabled is False
    assert renamed.skill.description == "Review safely"
    assert [(item.path, item.content) for item in renamed.skill.files] == [
        ("references/policy.bin", b"new policy")
    ]
    unchanged = await skill_service.get(original.skill.id, owner)
    assert unchanged.bundle_hash == enabled.bundle_hash
    assert unchanged.enabled is True
    assert [(item.path, item.content) for item in unchanged.files] == [
        ("references/policy.bin", b"policy")
    ]

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_uploaded_directory(
            "team",
            owner,
            changed_upload,
            on_conflict="rename",
            target_name="review-safely-copy",
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_import_conflict",
    )


@pytest.mark.asyncio
async def test_directory_import_requires_workspace_manager(
    skill_service, admin, member
) -> None:
    from app.errors import AppError

    imported = await skill_service.import_uploaded_directory(
        "team", admin, uploaded_skill(), on_conflict="fail"
    )
    assert imported.status == "created"

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_uploaded_directory(
            "team",
            member,
            uploaded_skill(source_name="another"),
            on_conflict="rename",
            target_name="member-copy",
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("on_conflict", "expected_hash", "target_name"),
    [
        ("unknown", None, None),
        ("fail", "sha256:" + "a" * 64, None),
        ("overwrite", None, None),
        ("overwrite", "not-a-hash", None),
        ("overwrite", "sha256:" + "a" * 64, "unexpected"),
        ("rename", None, None),
        ("rename", "sha256:" + "a" * 64, "copy"),
    ],
)
async def test_directory_import_rejects_invalid_conflict_fields(
    skill_service,
    owner,
    on_conflict: str,
    expected_hash: str | None,
    target_name: str | None,
) -> None:
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        await skill_service.import_uploaded_directory(
            "team",
            owner,
            uploaded_skill(),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "invalid_skill_bundle",
    )


@pytest.mark.asyncio
async def test_concurrent_identical_directory_imports_create_one_active_skill(
    skill_service, owner
) -> None:
    upload = uploaded_skill()
    results = await asyncio.gather(
        skill_service.import_uploaded_directory(
            "team", owner, upload, on_conflict="fail"
        ),
        skill_service.import_uploaded_directory(
            "team", owner, upload, on_conflict="fail"
        ),
    )

    assert sorted(result.status for result in results) == [
        "already_imported",
        "created",
    ]
    assert results[0].skill.id == results[1].skill.id
    assert len(await skill_service.list("team", owner)) == 1


@pytest.mark.asyncio
async def test_concurrent_different_directory_imports_report_one_conflict(
    skill_service, owner
) -> None:
    from app.errors import AppError

    results = await asyncio.gather(
        skill_service.import_uploaded_directory(
            "team", owner, uploaded_skill(), on_conflict="fail"
        ),
        skill_service.import_uploaded_directory(
            "team",
            owner,
            uploaded_skill(
                skill_markdown=VALID_SKILL_MD.replace(
                    b"Review changes", b"Review safely"
                )
            ),
            on_conflict="fail",
        ),
        return_exceptions=True,
    )

    assert sum(getattr(result, "status", None) == "created" for result in results) == 1
    errors = [result for result in results if isinstance(result, AppError)]
    assert [(error.status_code, error.code) for error in errors] == [
        (409, "skill_import_conflict")
    ]
    assert len(await skill_service.list("team", owner)) == 1
