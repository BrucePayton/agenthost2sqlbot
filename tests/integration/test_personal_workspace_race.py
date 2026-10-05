import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


@pytest.mark.asyncio
async def test_postgres_concurrent_first_access_creates_one_personal_workspace() -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
    from app.workspaces.provisioner import PersonalWorkspaceProvisioner

    database = Database(os.environ["TEST_POSTGRES_URL"])
    await database.initialize()
    user_id = str(uuid.uuid4())
    identity = IdentityContext(user_id, "race-obid", "Race User", issuer="davinci")
    async with database.session() as db:
        db.add(
            UserRecord(
                id=user_id,
                external_subject=f"obid:race:{user_id}",
                display_name="Race User",
                provider="obid",
            )
        )
        await db.commit()

    provisioner = PersonalWorkspaceProvisioner(database, "example")
    results = await asyncio.gather(
        provisioner.ensure(identity), provisioner.ensure(identity)
    )

    assert results[0].id == results[1].id
    async with database.session() as db:
        assert await db.scalar(
            select(func.count())
            .select_from(WorkspaceRecord)
            .where(
                WorkspaceRecord.kind == "personal",
                WorkspaceRecord.owner_user_id == user_id,
            )
        ) == 1
        assert await db.scalar(
            select(func.count())
            .select_from(WorkspaceMemberRecord)
            .where(
                WorkspaceMemberRecord.workspace_id == results[0].id,
                WorkspaceMemberRecord.user_id == user_id,
                WorkspaceMemberRecord.role == "owner",
            )
        ) == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_postgres_concurrent_obid_resolution_returns_one_stable_user() -> None:
    from app.auth.identity_repository import IdentityRepository
    from app.db.base import Database

    first = Database(os.environ["TEST_POSTGRES_URL"])
    second = Database(os.environ["TEST_POSTGRES_URL"])
    await first.initialize()
    subject = f"obid:race:{uuid.uuid4()}"
    results = await asyncio.gather(
        IdentityRepository(first).resolve_or_create(
            "davinci", subject, {"name": "Race User"}, provider="obid"
        ),
        IdentityRepository(second).resolve_or_create(
            "davinci", subject, {"name": "Race User"}, provider="obid"
        ),
    )

    assert results[0].user_id == results[1].user_id
    await first.dispose()
    await second.dispose()


@pytest.mark.asyncio
async def test_postgres_rejects_personal_workspace_membership_drift() -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import UserRecord
    from app.workspaces.provisioner import PersonalWorkspaceProvisioner

    database = Database(os.environ["TEST_POSTGRES_URL"])
    await database.initialize()
    owner_id = str(uuid.uuid4())
    outsider_id = str(uuid.uuid4())
    async with database.session() as db:
        db.add_all(
            [
                UserRecord(
                    id=owner_id,
                    external_subject=f"test:{owner_id}",
                    display_name="Owner",
                    provider="obid",
                ),
                UserRecord(
                    id=outsider_id,
                    external_subject=f"test:{outsider_id}",
                    display_name="Outsider",
                    provider="obid",
                ),
            ]
        )
        await db.commit()
    workspace = await PersonalWorkspaceProvisioner(database, "example").ensure(
        IdentityContext(owner_id, "owner", "Owner", issuer="davinci")
    )

    statements = (
        (
            "INSERT INTO workspace_members "
            "(workspace_id,user_id,role,created_at) "
            "VALUES (:workspace_id,:outsider_id,'member',CURRENT_TIMESTAMP)"
        ),
        (
            "UPDATE workspace_members SET user_id=:outsider_id "
            "WHERE workspace_id=:workspace_id AND user_id=:owner_id"
        ),
        (
            "UPDATE workspace_members SET role='admin' "
            "WHERE workspace_id=:workspace_id AND user_id=:owner_id"
        ),
        (
            "DELETE FROM workspace_members "
            "WHERE workspace_id=:workspace_id AND user_id=:owner_id"
        ),
    )
    for statement in statements:
        with pytest.raises(DBAPIError):
            async with database.session() as db:
                await db.execute(
                    text(statement),
                    {
                        "workspace_id": workspace.id,
                        "owner_id": owner_id,
                        "outsider_id": outsider_id,
                    },
                )
                await db.commit()
    await database.dispose()
