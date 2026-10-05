from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.alembic.versions.rev_0001_legacy_baseline import create_legacy_schema


@pytest.mark.asyncio
async def test_migrations_create_current_schema_on_fresh_database(
    tmp_path: Path,
) -> None:
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    await database.initialize()
    async with database.engine.connect() as connection:
        tables = set(
            await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        )
        session_foreign_keys = await connection.run_sync(
            lambda sync: inspect(sync).get_foreign_keys("sessions")
        )
        session_columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("sessions")
            )
        }
        session_indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes("sessions")
        )
        turn_columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("turns")
            )
        }
        attempt_columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("turn_attempts")
            )
        }
        heartbeat_columns = {
            column["name"]
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("worker_heartbeats")
            )
        }
        workspace_columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("workspaces")
            )
        }
        workspace_indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes("workspaces")
        )
        skill_columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("skills")
            )
        }
        skill_foreign_keys = await connection.run_sync(
            lambda sync: inspect(sync).get_foreign_keys("skills")
        )
        settings_primary_key = await connection.run_sync(
            lambda sync: inspect(sync).get_pk_constraint(
                "workspace_global_skill_settings"
            )
        )
        platform_role_primary_key = await connection.run_sync(
            lambda sync: inspect(sync).get_pk_constraint("platform_role_bindings")
        )
        name_lock_primary_key = await connection.run_sync(
            lambda sync: inspect(sync).get_pk_constraint("skill_name_locks")
        )
        skill_index_sql = await connection.scalar(
            text(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'index' AND name = 'uq_skills_workspace_active_name'
                """
            )
        )
        migration_head = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
        foreign_keys_enabled = await connection.scalar(text("PRAGMA foreign_keys"))
        foreign_key_violations = (
            await connection.execute(text("PRAGMA foreign_key_check"))
        ).all()
    assert {
        "alembic_version",
        "sessions",
        "turns",
        "messages",
        "attachments",
        "users",
        "workspaces",
        "workspace_members",
        "skills",
        "skill_files",
        "app_metadata",
        "identity_mappings",
        "workspace_membership_projections",
        "config_snapshots",
        "turn_attempts",
        "turn_events",
        "session_sandboxes",
        "memory_scope_leases",
        "worker_heartbeats",
        "skill_versions",
        "workspace_global_skill_settings",
        "workspace_instructions",
        "platform_role_bindings",
            "skill_name_locks",
            "data_agents",
            "data_agent_datasets",
            "data_agent_ask_sessions",
            "data_agent_tickets",
            "data_agent_results",
            "data_agent_audit",
    } <= tables
    assert session_columns["created_by"]["nullable"] is False
    assert {"config_snapshot_id", "deleted_at", "deleted_by"} <= session_columns.keys()
    assert {
        "effect_state",
        "finalization_status",
        "warning_code",
        "cancel_requested_at",
        "execution_barrier_at",
        "uncached_input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "total_input_tokens",
        "model_api_turns",
        "frontend_tool_calls",
        "tool_search_calls",
        "tool_set_changes",
        "catalog_digest_changes",
    } <= turn_columns.keys()
    assert {
        "sandbox_generation",
        "sandbox_id",
        "command_session_id",
        "command_execution_id",
    } <= attempt_columns.keys()
    assert heartbeat_columns == {
        "instance_id",
        "runtime_cohort",
        "protocol_version",
        "runner_runtime",
        "image_digest",
        "status",
        "started_at",
        "last_seen_at",
    }
    assert {index["name"] for index in session_indexes} >= {
        "ix_sessions_creator_workspace_updated"
    }
    assert {
        foreign_key["name"]: foreign_key["constrained_columns"]
        for foreign_key in session_foreign_keys
    } == {
        "fk_sessions_workspace": ["workspace_id"],
        "fk_sessions_creator": ["created_by"],
        "fk_sessions_config_snapshot": ["config_snapshot_id"],
        "fk_sessions_deleted_by": ["deleted_by"],
    }
    assert skill_index_sql is not None
    assert "normalized_name" in skill_index_sql.lower()
    assert "WHERE archived_at IS NULL AND scope = 'workspace'" in skill_index_sql
    assert skill_columns["workspace_id"]["nullable"] is True
    assert {"scope", "normalized_name", "current_version_id"} <= skill_columns.keys()
    assert any(
        foreign_key["constrained_columns"] == ["current_version_id"]
        and foreign_key["referred_table"] == "skill_versions"
        for foreign_key in skill_foreign_keys
    )
    assert settings_primary_key["constrained_columns"] == ["workspace_id", "skill_id"]
    assert platform_role_primary_key["constrained_columns"] == ["user_id", "role"]
    assert name_lock_primary_key["constrained_columns"] == ["normalized_name"]
    assert {"owner_user_id", "template_id"} <= workspace_columns.keys()
    assert "uq_workspaces_personal_owner" in {
        index["name"] for index in workspace_indexes
    }
    assert migration_head == "0016"
    assert foreign_keys_enabled == 1
    assert foreign_key_violations == []
    await database.dispose()


@pytest.mark.asyncio
async def test_rev_0009_preserves_legacy_skill_bytes_for_startup_backfill(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.db.migrations import SCRIPT_LOCATION

    legacy_skill_markdown = "---\nname: Review-Changes\n---\nReview changes.\n"
    legacy_supporting_bytes = b"\x00supporting\xffbytes"
    path = tmp_path / "legacy-skill.db"
    engine = create_engine(f"sqlite:///{path}")
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0008")
        connection.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, external_subject, display_name, provider, created_at, updated_at
                ) VALUES (
                    'legacy-owner', 'legacy-owner', 'Legacy Owner', 'test',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO workspaces (
                    id, name, kind, config_json, created_at, updated_at
                ) VALUES (
                    'legacy-workspace', 'Legacy Workspace', 'team', '{}',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO skills (
                    id, workspace_id, name, description, content, enabled,
                    bundle_hash, config_json, created_by, archived_at,
                    created_at, updated_at
                ) VALUES (
                    'legacy-skill', 'legacy-workspace', 'Review-Changes',
                    'Review changes', :content, 1, :bundle_hash, '{}',
                    'legacy-owner', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "content": legacy_skill_markdown,
                "bundle_hash": "sha256:" + "a" * 64,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO skill_files (
                    skill_id, path, content_blob, mime_type, size_bytes, sha256
                ) VALUES (
                    'legacy-skill', 'reference.bin', :content_blob,
                    'application/octet-stream', :size_bytes, :sha256
                )
                """
            ),
            {
                "content_blob": legacy_supporting_bytes,
                "size_bytes": len(legacy_supporting_bytes),
                "sha256": "sha256:" + "b" * 64,
            },
        )
    engine.dispose()

    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    async with database.engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT scope, normalized_name, current_version_id, content, "
                    "bundle_hash FROM skills WHERE id='legacy-skill'"
                )
            )
        ).one()
        supporting_size = await connection.scalar(
            text(
                "SELECT length(content_blob) FROM skill_files "
                "WHERE skill_id='legacy-skill'"
            )
        )
    assert row.scope == "workspace"
    assert row.normalized_name == "review-changes"
    assert row.current_version_id is None
    assert row.content == legacy_skill_markdown
    assert row.bundle_hash == "sha256:" + "a" * 64
    assert supporting_size == len(legacy_supporting_bytes)
    await database.dispose()


@pytest.mark.asyncio
async def test_personal_workspace_identity_migration_backfills_legacy_owner(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.db.migrations import SCRIPT_LOCATION

    path = tmp_path / "legacy-personal.db"
    engine = create_engine(f"sqlite:///{path}")
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
        connection.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, external_subject, display_name, provider, created_at, updated_at) "
                "VALUES ('owner', 'owner', 'Owner', 'mock', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspaces (id, name, kind, config_json, created_at, updated_at) "
                "VALUES ('example', 'Example', 'team', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO workspace_members (workspace_id, user_id, role, created_at) "
                "VALUES ('example', 'owner', 'owner', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(text("UPDATE workspaces SET kind='personal' WHERE id='example'"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
        row = connection.execute(
            text(
                "SELECT owner_user_id, template_id FROM workspaces WHERE id='example'"
            )
        ).one()
    engine.dispose()

    assert row == ("owner", "example")


@pytest.mark.parametrize("owner_count", [0, 2])
def test_personal_workspace_identity_migration_fails_closed_for_invalid_owner_count(
    tmp_path: Path, owner_count: int
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.db.migrations import SCRIPT_LOCATION

    engine = create_engine(f"sqlite:///{tmp_path / f'invalid-{owner_count}.db'}")
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
        connection.commit()
    with engine.begin() as connection:
        for index in range(max(owner_count, 1)):
            connection.execute(
                text(
                    "INSERT INTO users (id, external_subject, display_name, provider, created_at, updated_at) "
                    "VALUES (:id, :id, :id, 'mock', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": f"owner-{index}"},
            )
        connection.execute(
            text(
                "INSERT INTO workspaces (id, name, kind, config_json, created_at, updated_at) "
                "VALUES ('example', 'Example', 'team', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        if owner_count:
            for index in range(owner_count):
                connection.execute(
                    text(
                        "INSERT INTO workspace_members (workspace_id, user_id, role, created_at) "
                        "VALUES ('example', :id, 'owner', CURRENT_TIMESTAMP)"
                    ),
                    {"id": f"owner-{index}"},
                )
        # Temporarily remove the SQLite guard so malformed legacy data can be simulated.
        connection.execute(text("DROP TRIGGER trg_workspaces_personal_kind"))
        connection.execute(text("UPDATE workspaces SET kind='personal' WHERE id='example'"))

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(RuntimeError, match="exactly one owner"):
            command.upgrade(config, "head")
        connection.rollback()
    engine.dispose()


@pytest.mark.asyncio
async def test_migrations_adopt_legacy_schema_and_preserve_session(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(create_legacy_schema)
        await connection.execute(
            text(
                """
                INSERT INTO sessions (
                    id, workspace_id, title, title_source, status,
                    workspace_snapshot_json, workspace_snapshot_hash, session_dir,
                    created_at, updated_at
                ) VALUES (
                    'legacy-session', 'legacy-space', 'Legacy', 'auto', 'idle',
                    '{}', 'old-hash', 'sessions/legacy-session', CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO turns (
                    id, session_id, client_request_id, status, input_text, created_at
                ) VALUES (
                    'legacy-turn', 'legacy-session', 'legacy-request', 'completed',
                    'hello', CURRENT_TIMESTAMP
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO messages (
                    id, session_id, turn_id, sequence, event_type, role, payload_json,
                    created_at
                ) VALUES (
                    'legacy-message', 'legacy-session', 'legacy-turn', 1,
                    'message.user', 'user', '{}', CURRENT_TIMESTAMP
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO attachments (
                    id, session_id, turn_id, status, original_filename,
                    stored_filename, mime_type, size_bytes, sha256, relative_path,
                    created_at
                ) VALUES (
                    'legacy-attachment', 'legacy-session', 'legacy-turn', 'bound',
                    'input.txt', 'legacy-attachment.txt', 'text/plain', 5,
                    '0000000000000000000000000000000000000000000000000000000000000000',
                    'sessions/legacy-session/workspace/attachments/legacy-attachment.txt',
                    CURRENT_TIMESTAMP
                )
                """
            )
        )
    await engine.dispose()

    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    async with database.engine.connect() as connection:
        workspace = (
            await connection.execute(
                text("SELECT id, kind FROM workspaces WHERE id = 'legacy-space'")
            )
        ).one()
        session_count = await connection.scalar(
            text("SELECT COUNT(*) FROM sessions WHERE id = 'legacy-session'")
        )
        turn_count = await connection.scalar(
            text("SELECT COUNT(*) FROM turns WHERE id = 'legacy-turn'")
        )
        message_count = await connection.scalar(
            text("SELECT COUNT(*) FROM messages WHERE id = 'legacy-message'")
        )
        attachment_count = await connection.scalar(
            text("SELECT COUNT(*) FROM attachments WHERE id = 'legacy-attachment'")
        )
        legacy_creator = await connection.scalar(
            text("SELECT created_by FROM sessions WHERE id = 'legacy-session'")
        )
        event = (
            await connection.execute(
                text(
                    "SELECT turn_id, sequence, event_type, role, payload_json "
                    "FROM turn_events WHERE turn_id = 'legacy-turn'"
                )
            )
        ).one()
    assert workspace == ("legacy-space", "team")
    assert session_count == 1
    assert turn_count == message_count == attachment_count == 1
    assert legacy_creator == "legacy-session-owner"
    assert event == ("legacy-turn", 1, "message.user", "user", "{}")
    await database.dispose()


def test_nocase_migration_fails_closed_without_losing_case_variant_rows(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.db.migrations import SCRIPT_LOCATION

    engine = create_engine(f"sqlite:///{tmp_path / 'case-variants.db'}")
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, external_subject, display_name, provider, created_at, updated_at
                ) VALUES (
                    'owner', 'owner', 'Owner', 'mock', CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO workspaces (
                    id, name, kind, config_json, created_at, updated_at
                ) VALUES (
                    'team', 'Team', 'team', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        for skill_id, name in (("upper", "Review"), ("lower", "review")):
            connection.execute(
                text(
                    """
                    INSERT INTO skills (
                        id, workspace_id, name, description, content, enabled,
                        bundle_hash, config_json, created_by, archived_at,
                        created_at, updated_at
                    ) VALUES (
                        :skill_id, 'team', :name, 'description', 'content', 1,
                        :bundle_hash, '{}', 'owner', NULL, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "skill_id": skill_id,
                    "name": name,
                    "bundle_hash": "sha256:" + "0" * 64,
                },
            )

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        with pytest.raises(
            RuntimeError,
            match="case-insensitive active Skill name conflicts",
        ):
            command.upgrade(config, "head")
        if connection.in_transaction():
            connection.rollback()
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        rows = connection.execute(text("SELECT id, name FROM skills ORDER BY id")).all()
        index_sql = connection.scalar(
            text(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'index' AND name = 'uq_skills_workspace_active_name'
                """
            )
        )

    assert version == "0002"
    assert rows == [("lower", "review"), ("upper", "Review")]
    assert index_sql is not None
    assert "lower(name)" not in index_sql.lower()
    engine.dispose()


def test_nocase_migration_preserves_non_conflicting_0002_skills_and_files(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from app.db.migrations import SCRIPT_LOCATION

    engine = create_engine(f"sqlite:///{tmp_path / 'preserved-skills.db'}")
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
        if connection.in_transaction():
            connection.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (
                    id, external_subject, display_name, provider, created_at, updated_at
                ) VALUES ('owner', 'owner', 'Owner', 'mock', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO workspaces (
                    id, name, kind, config_json, created_at, updated_at
                ) VALUES ('team', 'Team', 'team', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        for skill_id, name in (("alpha", "Alpha"), ("beta", "beta")):
            connection.execute(
                text(
                    """
                    INSERT INTO skills (
                        id, workspace_id, name, description, content, enabled,
                        bundle_hash, config_json, created_by, archived_at,
                        created_at, updated_at
                    ) VALUES (
                        :skill_id, 'team', :name, :name, :name, 1,
                        :bundle_hash, '{}', 'owner', NULL, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "skill_id": skill_id,
                    "name": name,
                    "bundle_hash": "sha256:"
                    + ("a" if skill_id == "alpha" else "b") * 64,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO skill_files (
                        skill_id, path, content_blob, mime_type, size_bytes, sha256
                    ) VALUES (:skill_id, 'reference.bin', :content, 'application/octet-stream', 2, :sha256)
                    """
                ),
                {
                    "skill_id": skill_id,
                    "content": b"\x00\xff",
                    "sha256": "sha256:" + "c" * 64,
                },
            )

    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        if connection.in_transaction():
            connection.commit()
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        skills = connection.execute(
            text("SELECT id, name FROM skills ORDER BY id")
        ).all()
        files = connection.execute(
            text(
                "SELECT skill_id, path, content_blob FROM skill_files ORDER BY skill_id"
            )
        ).all()
        index_sql = connection.scalar(
            text(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'index' AND name = 'uq_skills_workspace_active_name'
                """
            )
        )
        command.upgrade(config, "head")
        if connection.in_transaction():
            connection.commit()

    assert version == "0016"
    assert skills == [("alpha", "Alpha"), ("beta", "beta")]
    assert files == [
        ("alpha", "reference.bin", b"\x00\xff"),
        ("beta", "reference.bin", b"\x00\xff"),
    ]
    assert index_sql is not None and "normalized_name" in index_sql.lower()
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                    INSERT INTO skills (
                        id, scope, workspace_id, name, normalized_name, description,
                        content, enabled, bundle_hash, config_json, created_by, archived_at,
                        created_at, updated_at
                    ) VALUES (
                        'case-conflict', 'workspace', 'team', 'ALPHA', 'alpha',
                        'conflict', 'content', 1, :bundle_hash, '{}', 'owner', NULL,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    """
            ),
            {"bundle_hash": "sha256:" + "d" * 64},
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_personal_workspace_membership_guards(tmp_path: Path) -> None:
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'personal.db'}")
    await database.initialize()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO users (id, external_subject, display_name, provider, created_at, updated_at)
                VALUES
                    ('owner', 'owner-subject', 'Owner', 'mock', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                    ('member', 'member-subject', 'Member', 'mock', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO workspaces (id, name, kind, config_json, created_at, updated_at)
                VALUES
                    ('personal', 'Personal', 'team', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )

    async with database.engine.connect() as connection:
        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    """
                    INSERT INTO workspaces (id, name, kind, config_json, created_at, updated_at)
                    VALUES (
                        'rejected-personal', 'Rejected', 'personal', '{}',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        await connection.rollback()

        with pytest.raises(IntegrityError):
            await connection.execute(
                text("UPDATE workspaces SET kind = 'personal' WHERE id = 'personal'")
            )
        await connection.rollback()

        await connection.execute(
            text(
                """
                INSERT INTO workspace_members (workspace_id, user_id, role, created_at)
                VALUES ('personal', 'owner', 'owner', CURRENT_TIMESTAMP)
                """
            )
        )
        await connection.commit()
        await connection.execute(
            text(
                "UPDATE workspaces "
                "SET kind = 'personal', owner_user_id = 'owner', "
                "template_id = 'personal' WHERE id = 'personal'"
            )
        )
        await connection.commit()

        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    """
                    INSERT INTO workspace_members (workspace_id, user_id, role, created_at)
                    VALUES ('personal', 'member', 'member', CURRENT_TIMESTAMP)
                    """
                )
            )
        await connection.rollback()

        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    """
                    INSERT INTO workspace_members (workspace_id, user_id, role, created_at)
                    VALUES ('personal', 'member', 'owner', CURRENT_TIMESTAMP)
                    """
                )
            )
        await connection.rollback()

        with pytest.raises(IntegrityError):
            await connection.execute(
                text("DELETE FROM workspace_members WHERE workspace_id = 'personal'")
            )
        await connection.rollback()

        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    """
                    UPDATE workspace_members SET role = 'admin'
                    WHERE workspace_id = 'personal' AND user_id = 'owner'
                    """
                )
            )
        await connection.rollback()

    await database.dispose()


@pytest.mark.asyncio
async def test_failed_sqlite_migration_restores_foreign_key_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import migrations
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'failed.db'}")

    def fail_upgrade(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(migrations.command, "upgrade", fail_upgrade)
    async with database.engine.connect() as connection:
        with pytest.raises(RuntimeError, match="migration failed"):
            await connection.run_sync(migrations.run_migrations)
        assert await connection.scalar(text("PRAGMA foreign_keys")) == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_task_feedback_migration_preserves_legacy_votes(settings_factory):
    """Import the latest task vote without changing a byte of the legacy ratings."""
    from sqlalchemy import create_engine

    from app.db.migrations import run_migrations
    from tests.test_api import api_client
    from tests.test_message_feedback import completed_reply

    async with api_client(settings_factory) as client:
        session, first_message, _ = await completed_reply(client)
        accepted = await client.post(
            f"/api/sessions/{session}/turns",
            json={"message": "2", "client_request_id": "migration-clarification"},
        )
        await client.get(f"/api/turns/{accepted.json()['turn_id']}/events")
        messages = (await client.get(f"/api/sessions/{session}/messages")).json()
        last_message = next(m["id"] for m in reversed(messages) if m["event_type"] == "message.assistant.completed")
        other_session, other_message, _ = await completed_reply(client)

    # The database belongs only to pytest's tmp_path. Remove the empty new table
    # to reconstruct revision 0012 using real, internally consistent session fixtures.
    engine = create_engine(settings_factory().resolved_database_url.replace("sqlite+aiosqlite:", "sqlite:"))
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM task_feedback")) == 0
        connection.execute(text("DROP TABLE question_feedback"))
        connection.execute(text("DROP TABLE task_feedback"))
        connection.execute(text("DROP TABLE user_preferences"))
        connection.execute(text("DROP TABLE data_agent_audit"))
        connection.execute(text("DROP TABLE data_agent_results"))
        connection.execute(text("DROP TABLE data_agent_tickets"))
        connection.execute(text("DROP TABLE data_agent_ask_sessions"))
        connection.execute(text("DROP TABLE data_agent_datasets"))
        connection.execute(text("DROP TABLE data_agents"))
        connection.execute(text("UPDATE alembic_version SET version_num = '0012'"))
        actor = connection.scalar(text("SELECT created_by FROM sessions WHERE id=:session"), {"session": session})
        for message, rating, created, updated in [
            (first_message, "up", "2026-09-05 01:00:00", "2026-09-05 01:00:00"),
            (last_message, "down", "2026-09-06 02:00:00", "2026-09-06 03:00:00"),
            (other_message, "up", "2026-09-06 04:00:00", "2026-09-06 04:00:00"),
        ]:
            connection.execute(text(
                "INSERT INTO message_feedback (actor_id, message_id, rating, reasons_json, comment, created_at, updated_at) "
                "VALUES (:actor, :message, :rating, :reasons, :comment, :created, :updated)"
            ), {"actor": actor, "message": message, "rating": rating, "reasons": '["slow"]' if rating == "down" else '[]',
                "comment": "原始备注" + rating, "created": created, "updated": updated})
        legacy = connection.execute(text("SELECT * FROM message_feedback ORDER BY message_id")).all()
    with engine.connect() as connection:
        run_migrations(connection)
        assert connection.execute(text("SELECT * FROM message_feedback ORDER BY message_id")).all() == legacy
        votes = connection.execute(text("SELECT * FROM task_feedback")).mappings().all()
        assert len(votes) == 2
        vote = next(row for row in votes if row["session_id"] == session)
        assert vote["message_id"] == last_message
        assert vote["rating"] == "down" and vote["comment"] == "原始备注down"
        assert vote["reasons_json"] == '["slow"]'
        assert vote["created_at"] == "2026-09-05 01:00:00"
        assert any(row["session_id"] == other_session for row in votes)
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        keys = inspect(connection).get_pk_constraint("task_feedback")["constrained_columns"]
        assert keys == ["actor_id", "session_id"]
        question_votes = connection.execute(text("SELECT * FROM question_feedback")).mappings().all()
        assert len(question_votes) == len(votes)
        for migrated in question_votes:
            original = next(row for row in votes if row["session_id"] == migrated["session_id"])
            for key in original:
                if key in {"created_at", "updated_at"}:
                    assert datetime.fromisoformat(migrated[key]) == datetime.fromisoformat(original[key])
                else:
                    assert migrated[key] == original[key]
            expected_turn = connection.scalar(text("SELECT turn_id FROM messages WHERE id=:id"), {"id": migrated["message_id"]})
            assert migrated["question_id"] == expected_turn
        assert inspect(connection).get_pk_constraint("question_feedback")["constrained_columns"] == ["actor_id", "question_id"]
    engine.dispose()
