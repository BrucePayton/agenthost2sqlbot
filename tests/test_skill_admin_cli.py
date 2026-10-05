from collections.abc import AsyncIterator

import pytest


@pytest.fixture
async def admin_cli_fixture(settings_factory) -> AsyncIterator[tuple[object, object]]:
    from app.db.base import Database
    from app.db.models import UserRecord

    settings = settings_factory()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    async with database.session() as db:
        db.add(
            UserRecord(
                id="known-user",
                external_subject="known-subject",
                display_name="Known User",
                provider="mock",
            )
        )
        await db.commit()
    try:
        yield settings, database
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_skill_admin_cli_grant(admin_cli_fixture) -> None:
    from app.db.models import PlatformRoleBindingRecord
    from app.skills.admin_cli import run

    settings, database = admin_cli_fixture
    assert await run(settings, ["grant", "--subject", "known-subject"]) == 0
    async with database.session() as db:
        binding = await db.get(
            PlatformRoleBindingRecord, ("known-user", "skill_admin")
        )
    assert binding is not None


@pytest.mark.asyncio
async def test_skill_admin_cli_grant_is_idempotent(admin_cli_fixture) -> None:
    from sqlalchemy import func, select

    from app.db.models import PlatformRoleBindingRecord
    from app.skills.admin_cli import run

    settings, database = admin_cli_fixture
    assert await run(settings, ["grant", "--subject", "known-subject"]) == 0
    assert await run(settings, ["grant", "--subject", "known-subject"]) == 0
    async with database.session() as db:
        count = await db.scalar(select(func.count()).select_from(PlatformRoleBindingRecord))
    assert count == 1


@pytest.mark.asyncio
async def test_skill_admin_cli_revoke(admin_cli_fixture) -> None:
    from app.db.models import PlatformRoleBindingRecord
    from app.skills.admin_cli import run

    settings, database = admin_cli_fixture
    await run(settings, ["grant", "--subject", "known-subject"])

    assert await run(settings, ["revoke", "--subject", "known-subject"]) == 0
    async with database.session() as db:
        binding = await db.get(
            PlatformRoleBindingRecord, ("known-user", "skill_admin")
        )
    assert binding is None


@pytest.mark.asyncio
async def test_skill_admin_cli_list(admin_cli_fixture, capsys) -> None:
    from app.skills.admin_cli import run

    settings, _database = admin_cli_fixture
    await run(settings, ["grant", "--subject", "known-subject"])
    capsys.readouterr()

    assert await run(settings, ["list"]) == 0
    assert capsys.readouterr().out == "known-subject\tKnown User\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["grant", "revoke"])
async def test_skill_admin_cli_unknown_subject_does_not_create_user(
    admin_cli_fixture, capsys, command
) -> None:
    from sqlalchemy import func, select

    from app.db.models import UserRecord
    from app.skills.admin_cli import run

    settings, database = admin_cli_fixture
    assert await run(settings, [command, "--subject", "missing-subject"]) != 0
    assert "Unknown user subject" in capsys.readouterr().err
    async with database.session() as db:
        count = await db.scalar(select(func.count()).select_from(UserRecord))
    assert count == 1
