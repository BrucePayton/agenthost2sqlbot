import asyncio

import pytest
from sqlalchemy import select


async def _identity(database, suffix: str):
    from app.auth.models import IdentityContext
    from app.db.models import UserRecord

    identity = IdentityContext(
        user_id=f"user-{suffix}",
        external_subject=f"obid-{suffix}",
        display_name=f"User {suffix}",
        issuer="davinci",
    )
    async with database.session() as db:
        db.add(
            UserRecord(
                id=identity.user_id,
                external_subject=f"obid:test:{suffix}",
                display_name=identity.display_name,
                provider="obid",
            )
        )
        await db.commit()
    return identity


@pytest.mark.asyncio
async def test_ensure_returns_one_personal_workspace_under_concurrency(
    tmp_path,
) -> None:
    from app.db.base import Database
    from app.db.models import WorkspaceMemberRecord, WorkspaceRecord
    from app.workspaces.provisioner import PersonalWorkspaceProvisioner

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'provision.db'}")
    await database.initialize()
    identity = await _identity(database, "same")
    provisioner = PersonalWorkspaceProvisioner(database, "example")

    results = await asyncio.gather(
        provisioner.ensure(identity), provisioner.ensure(identity)
    )

    assert results[0].id == results[1].id
    async with database.session() as db:
        assert len((await db.scalars(select(WorkspaceRecord))).all()) == 1
        memberships = (await db.scalars(select(WorkspaceMemberRecord))).all()
        assert [(item.user_id, item.role) for item in memberships] == [
            (identity.user_id, "owner")
        ]
    await database.dispose()


@pytest.mark.asyncio
async def test_two_users_receive_distinct_workspace_ids(tmp_path) -> None:
    from app.db.base import Database
    from app.workspaces.provisioner import PersonalWorkspaceProvisioner

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    await database.initialize()
    first = await _identity(database, "first")
    second = await _identity(database, "second")
    provisioner = PersonalWorkspaceProvisioner(database, "example")

    first_workspace = await provisioner.ensure(first)
    second_workspace = await provisioner.ensure(second)

    assert first_workspace.id != second_workspace.id
    assert first_workspace.owner_user_id == first.user_id
    assert second_workspace.owner_user_id == second.user_id
    assert first_workspace.template_id == second_workspace.template_id == "example"
    await database.dispose()
