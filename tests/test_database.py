import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError


def test_production_requires_postgres_database(settings_factory) -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        settings_factory(app_env="production")

    with pytest.raises(ValidationError, match="PostgreSQL"):
        settings_factory(
            app_env="production",
            database_url="sqlite+aiosqlite:////tmp/workspace.db",
        )


def test_postgres_database_credentials_are_redacted(settings_factory) -> None:
    settings = settings_factory(
        app_env="production",
        app_runtime_mode="execution_disabled",
        database_url=(
            "postgresql+asyncpg://workspace:super-secret@db.example.test/workspace"
        ),
        identity_mode="oidc",
        oidc_issuer="https://identity.example.test",
        oidc_audience="workspace-agent",
        oidc_jwks_uri="https://identity.example.test/jwks",
        space_authority_url="https://spaces.example.test",
        space_authority_token="space-secret",
    )

    assert settings.resolved_database_url.endswith("@db.example.test/workspace")
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings.redacted_summary())


@pytest.mark.asyncio
async def test_database_enables_foreign_keys_and_cascades_session_children(
    settings_factory,
) -> None:
    from app.db.base import Database
    from app.db.models import (
        AttachmentRecord,
        MessageRecord,
        SessionRecord,
        TurnRecord,
        UserRecord,
        WorkspaceRecord,
    )

    settings = settings_factory()
    database = Database(settings.resolved_database_url)
    await database.initialize()

    now = datetime.now(UTC)
    async with database.session() as db:
        db.add(
            UserRecord(
                id="owner",
                external_subject="owner",
                display_name="Owner",
                provider="test",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            WorkspaceRecord(
                id="example", name="Example", kind="team", config_json="{}"
            )
        )
        await db.flush()
        db.add(
            SessionRecord(
                id="session-1",
                workspace_id="example",
                created_by="owner",
                title="Session",
                title_source="auto",
                status="idle",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/session-1",
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            TurnRecord(
                id="turn-1",
                session_id="session-1",
                client_request_id="request-1",
                status="completed",
                input_text="hello",
                created_at=now,
            )
        )
        await db.flush()
        db.add(
            MessageRecord(
                id="message-1",
                session_id="session-1",
                turn_id="turn-1",
                sequence=1,
                event_type="message.user",
                role="user",
                payload_json="{}",
                created_at=now,
            )
        )
        db.add(
            AttachmentRecord(
                id="attachment-1",
                session_id="session-1",
                turn_id="turn-1",
                status="bound",
                original_filename="input.txt",
                stored_filename="attachment-1.txt",
                mime_type="text/plain",
                size_bytes=5,
                sha256="0" * 64,
                relative_path="sessions/session-1/workspace/attachments/attachment-1.txt",
                created_at=now,
            )
        )
        await db.commit()

        session = await db.get(SessionRecord, "session-1")
        assert session is not None
        await db.delete(session)
        await db.commit()

        for model in (TurnRecord, MessageRecord, AttachmentRecord):
            count = await db.scalar(select(func.count()).select_from(model))
            assert count == 0

    await database.dispose()


@pytest.mark.asyncio
async def test_database_rejects_duplicate_personal_workspace_owner(
    settings_factory,
) -> None:
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord

    database = Database(settings_factory().resolved_database_url)
    await database.initialize()
    async with database.session() as db:
        db.add(UserRecord(id="owner", external_subject="owner", display_name="Owner", provider="test"))
        for workspace_id in ("personal-a", "personal-b"):
            db.add(
                WorkspaceRecord(
                    id=workspace_id,
                    name=workspace_id,
                    kind="team",
                    config_json="{}",
                    owner_user_id="owner",
                    template_id="example",
                )
            )
        await db.flush()
        for workspace_id in ("personal-a", "personal-b"):
            db.add(
                WorkspaceMemberRecord(
                    workspace_id=workspace_id, user_id="owner", role="owner"
                )
            )
        await db.flush()
        first = await db.get(WorkspaceRecord, "personal-a")
        second = await db.get(WorkspaceRecord, "personal-b")
        assert first is not None and second is not None
        first.kind = "personal"
        await db.flush()
        second.kind = "personal"
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
    await database.dispose()


@pytest.mark.asyncio
async def test_initialize_interrupts_stale_turns(settings_factory) -> None:
    from app.db.base import Database
    from app.db.models import (
        MessageRecord,
        SessionRecord,
        TurnEventRecord,
        TurnRecord,
        UserRecord,
        WorkspaceRecord,
    )

    settings = settings_factory()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    now = datetime.now(UTC)

    async with database.session() as db:
        db.add(
            UserRecord(
                id="owner",
                external_subject="owner",
                display_name="Owner",
                provider="test",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            WorkspaceRecord(
                id="example", name="Example", kind="team", config_json="{}"
            )
        )
        await db.flush()
        db.add(
            SessionRecord(
                id="session-1",
                workspace_id="example",
                created_by="owner",
                title="Session",
                title_source="auto",
                status="running",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/session-1",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            TurnRecord(
                id="turn-1",
                session_id="session-1",
                client_request_id="request-1",
                status="running",
                input_text="hello",
                created_at=now,
            )
        )
        await db.commit()

    await database.interrupt_stale_turns()

    async with database.session() as db:
        session = await db.get(SessionRecord, "session-1")
        turn = await db.get(TurnRecord, "turn-1")
        messages = list(
            (
                await db.scalars(
                    select(MessageRecord).where(MessageRecord.turn_id == "turn-1")
                )
            ).all()
        )
        durable_events = list(
            (
                await db.scalars(
                    select(TurnEventRecord).where(TurnEventRecord.turn_id == "turn-1")
                )
            ).all()
        )
        assert session is not None and session.status == "interrupted"
        assert turn is not None and turn.status == "interrupted"
        assert turn.completed_at is not None
        assert len(messages) == 1
        assert messages[0].sequence == 1
        assert messages[0].event_type == "turn.interrupted"
        payload = json.loads(messages[0].payload_json)
        assert payload["code"] == "service_restarted"
        assert payload["message"] == "服务在该 Turn 执行期间退出，任务已中断。"
        assert len(durable_events) == 1
        assert durable_events[0].event_type == "turn.interrupted"

    await database.dispose()


async def _create_skill_constraint_database(settings_factory):
    from app.db.base import Database

    database = Database(settings_factory().resolved_database_url)
    await database.initialize()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO users (
                    id, external_subject, display_name, provider, created_at, updated_at
                ) VALUES (
                    'skill-owner', 'skill-owner', 'Skill Owner', 'test',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO workspaces (
                    id, name, kind, config_json, created_at, updated_at
                ) VALUES
                    ('personal-a', 'Personal A', 'team', '{}',
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('personal-b', 'Personal B', 'team', '{}',
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
    return database


async def _insert_test_skill(
    database,
    *,
    skill_id: str,
    scope: str,
    workspace_id: str | None,
    normalized_name: str,
    enabled: bool = True,
    bundle_hash: str = "sha256:" + "a" * 64,
) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO skills (
                    id, scope, workspace_id, name, normalized_name, description,
                    content, enabled, bundle_hash, config_json, created_by,
                    archived_at, created_at, updated_at
                ) VALUES (
                    :skill_id, :scope, :workspace_id, :normalized_name,
                    :normalized_name, 'Test Skill', 'legacy', :enabled,
                    :bundle_hash, '{}', 'skill-owner', NULL,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "skill_id": skill_id,
                "scope": scope,
                "workspace_id": workspace_id,
                "normalized_name": normalized_name,
                "enabled": enabled,
                "bundle_hash": bundle_hash,
            },
        )


async def _insert_test_version(
    database,
    *,
    version_id: str,
    version_no: int,
    bundle_hash: str,
    status: str = "ready",
) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO skill_versions (
                    id, skill_id, version_no, bundle_hash, artifact_key,
                    artifact_sha256, manifest_json, size_bytes, status,
                    created_by, created_at
                ) VALUES (
                    :version_id, 'versioned-skill', :version_no, :bundle_hash,
                    :artifact_key, :artifact_sha256, '{}', 10, :status,
                    'skill-owner', CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "version_id": version_id,
                "version_no": version_no,
                "bundle_hash": bundle_hash,
                "artifact_key": f"skills/{version_id}.zip",
                "artifact_sha256": "sha256:" + "b" * 64,
                "status": status,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "workspace_id", "enabled"),
    [
        ("global", "personal-a", True),
        ("global", None, False),
        ("workspace", None, True),
        ("invalid", "personal-a", True),
    ],
)
async def test_skill_scope_workspace_constraint_rejects_invalid_combinations(
    settings_factory, scope: str, workspace_id: str | None, enabled: bool
) -> None:
    database = await _create_skill_constraint_database(settings_factory)
    try:
        with pytest.raises(IntegrityError):
            await _insert_test_skill(
                database,
                skill_id="invalid-skill",
                scope=scope,
                workspace_id=workspace_id,
                normalized_name="invalid-skill",
                enabled=enabled,
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_skill_active_name_constraints_are_scope_aware(settings_factory) -> None:
    database = await _create_skill_constraint_database(settings_factory)
    try:
        await _insert_test_skill(
            database,
            skill_id="global-one",
            scope="global",
            workspace_id=None,
            normalized_name="review",
        )
        await _insert_test_skill(
            database,
            skill_id="personal-one",
            scope="workspace",
            workspace_id="personal-a",
            normalized_name="local",
        )
        with pytest.raises(IntegrityError):
            await _insert_test_skill(
                database,
                skill_id="global-two",
                scope="global",
                workspace_id=None,
                normalized_name="review",
                bundle_hash="sha256:" + "c" * 64,
            )
        with pytest.raises(IntegrityError):
            await _insert_test_skill(
                database,
                skill_id="personal-two",
                scope="workspace",
                workspace_id="personal-a",
                normalized_name="local",
                bundle_hash="sha256:" + "d" * 64,
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_skill_version_status_constraint_rejects_invalid_value(
    settings_factory,
) -> None:
    database = await _create_skill_constraint_database(settings_factory)
    try:
        await _insert_test_skill(
            database,
            skill_id="versioned-skill",
            scope="workspace",
            workspace_id="personal-a",
            normalized_name="versioned",
        )
        with pytest.raises(IntegrityError):
            await _insert_test_version(
                database,
                version_id="invalid-version",
                version_no=1,
                bundle_hash="sha256:" + "e" * 64,
                status="published",
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_skill_version_constraints_reject_duplicate_number_and_hash(
    settings_factory,
) -> None:
    database = await _create_skill_constraint_database(settings_factory)
    try:
        await _insert_test_skill(
            database,
            skill_id="versioned-skill",
            scope="workspace",
            workspace_id="personal-a",
            normalized_name="versioned",
        )
        await _insert_test_version(
            database,
            version_id="version-one",
            version_no=1,
            bundle_hash="sha256:" + "f" * 64,
        )
        with pytest.raises(IntegrityError):
            await _insert_test_version(
                database,
                version_id="duplicate-number",
                version_no=1,
                bundle_hash="sha256:" + "1" * 64,
            )
        with pytest.raises(IntegrityError):
            await _insert_test_version(
                database,
                version_id="duplicate-hash",
                version_no=2,
                bundle_hash="sha256:" + "f" * 64,
            )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_platform_role_constraint_rejects_unknown_role(settings_factory) -> None:
    database = await _create_skill_constraint_database(settings_factory)
    try:
        async with database.engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    text(
                        """
                        INSERT INTO platform_role_bindings (
                            user_id, role, granted_by, created_at
                        ) VALUES (
                            'skill-owner', 'workspace_admin', 'skill-owner',
                            CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
    finally:
        await database.dispose()
