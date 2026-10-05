from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.db.models import WorkspaceInstructionRecord


def test_instruction_record_is_keyed_by_workspace() -> None:
    # 一人一个 personal workspace，所以 workspace_id 就是隔离键。
    table = WorkspaceInstructionRecord.__table__
    assert table.name == "workspace_instructions"
    assert [c.name for c in table.primary_key.columns] == ["workspace_id"]
    workspace_fk = next(iter(table.c.workspace_id.foreign_keys))
    assert workspace_fk.column.table.name == "workspaces"
    assert workspace_fk.ondelete == "CASCADE"
    updated_by_fk = next(iter(table.c.updated_by.foreign_keys))
    assert updated_by_fk.ondelete == "RESTRICT"
    assert table.c.content.nullable is False
    assert table.c.size_bytes.nullable is False


@pytest.mark.asyncio
async def test_migration_creates_the_instructions_table(tmp_path: Path) -> None:
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'instructions.db'}")
    await database.initialize()
    async with database.engine.connect() as connection:
        tables = set(
            await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        )
        columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("workspace_instructions")
            )
        }
        primary_key = await connection.run_sync(
            lambda sync: inspect(sync).get_pk_constraint("workspace_instructions")
        )
        foreign_keys = await connection.run_sync(
            lambda sync: inspect(sync).get_foreign_keys("workspace_instructions")
        )
        migration_head = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    assert "workspace_instructions" in tables
    assert columns.keys() == {
        "workspace_id",
        "content",
        "content_hash",
        "size_bytes",
        "updated_by",
        "created_at",
        "updated_at",
    }
    assert columns["content"]["nullable"] is False
    assert columns["size_bytes"]["nullable"] is False
    assert primary_key["constrained_columns"] == ["workspace_id"]
    assert {
        foreign_key["referred_table"]: foreign_key["constrained_columns"]
        for foreign_key in foreign_keys
    } == {"workspaces": ["workspace_id"], "users": ["updated_by"]}
    assert migration_head == "0016"
    await database.dispose()


@pytest.mark.asyncio
async def test_deleting_a_workspace_drops_its_instructions(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceRecord

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'cascade.db'}")
    await database.initialize()
    now = datetime.now(UTC)
    async with database.session() as db:
        db.add_all(
            [
                UserRecord(
                    id="owner-a",
                    external_subject="owner-a",
                    display_name="Owner A",
                    provider="test",
                ),
                WorkspaceRecord(
                    id="personal-a",
                    name="Personal A",
                    kind="team",
                    config_json="{}",
                ),
            ]
        )
        await db.flush()
        db.add(
            WorkspaceInstructionRecord(
                workspace_id="personal-a",
                content="# mine",
                content_hash="sha256:" + "0" * 64,
                size_bytes=6,
                updated_by="owner-a",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    async with database.session() as db:
        workspace = await db.get(WorkspaceRecord, "personal-a")
        assert workspace is not None
        await db.delete(workspace)
        await db.commit()
    async with database.session() as db:
        assert await db.get(WorkspaceInstructionRecord, "personal-a") is None
    await database.dispose()
