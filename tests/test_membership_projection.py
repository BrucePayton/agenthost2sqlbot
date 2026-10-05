import asyncio
from datetime import UTC, datetime, timedelta

import pytest


class FakeSpaceAuthority:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    async def fetch_membership(self, subject: str, workspace_external_id: str):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
async def membership_database(settings_factory):
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceRecord

    database = Database(settings_factory().resolved_database_url)
    await database.initialize()
    async with database.session() as session:
        session.add(
            UserRecord(
                id="user-1",
                external_subject="oidc:user-1",
                display_name="Alice",
                provider="oidc",
            )
        )
        session.add(
            WorkspaceRecord(id="team", name="Team", kind="team", config_json="{}")
        )
        await session.commit()
    try:
        yield database
    finally:
        await database.dispose()


def _identity():
    from app.auth.models import IdentityContext

    return IdentityContext(
        "user-1", "employee-1", "Alice", issuer="https://identity.example.test"
    )


def _membership(role: str, version: str):
    from app.auth.membership import AuthorityMembership

    return AuthorityMembership(role=role, source_version=version)


async def _stored_projection(database):
    from app.db.models import WorkspaceMembershipProjectionRecord

    async with database.session() as session:
        return await session.get(
            WorkspaceMembershipProjectionRecord, ("team", "user-1")
        )


@pytest.mark.asyncio
async def test_fresh_projection_avoids_remote_authority(membership_database) -> None:
    from app.auth.membership import MembershipProjectionService
    from app.db.models import WorkspaceMembershipProjectionRecord

    now = datetime.now(UTC)
    async with membership_database.session() as session:
        session.add(
            WorkspaceMembershipProjectionRecord(
                workspace_id="team",
                user_id="user-1",
                role="member",
                source_version="v1",
                expires_at=now + timedelta(minutes=5),
                refreshed_at=now,
            )
        )
        await session.commit()
    authority = FakeSpaceAuthority([AssertionError("authority must not be called")])
    service = MembershipProjectionService(
        membership_database, authority, ttl_seconds=60, now=lambda: now
    )

    membership = await service.require_current_membership(
        _identity(), "team", "sensitive"
    )

    assert membership.role == "member"
    assert authority.calls == 0


@pytest.mark.asyncio
async def test_expired_projection_refreshes_version_and_role(membership_database) -> None:
    from app.auth.membership import MembershipProjectionService
    from app.db.models import WorkspaceMembershipProjectionRecord

    now = datetime.now(UTC)
    async with membership_database.session() as session:
        session.add(
            WorkspaceMembershipProjectionRecord(
                workspace_id="team",
                user_id="user-1",
                role="admin",
                source_version="v1",
                expires_at=now - timedelta(seconds=1),
                refreshed_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()
    authority = FakeSpaceAuthority([_membership("member", "v2")])
    service = MembershipProjectionService(
        membership_database, authority, ttl_seconds=120, now=lambda: now
    )

    membership = await service.require_current_membership(_identity(), "team", "write")
    stored = await _stored_projection(membership_database)

    assert membership.role == "member"
    assert stored is not None and stored.source_version == "v2"
    stored_expiry = stored.expires_at
    if stored_expiry.tzinfo is None:  # SQLite drops timezone metadata.
        stored_expiry = stored_expiry.replace(tzinfo=UTC)
    assert stored_expiry == now + timedelta(seconds=120)


@pytest.mark.asyncio
async def test_removed_membership_is_deleted_and_denied(membership_database) -> None:
    from app.auth.membership import MembershipProjectionService
    from app.db.models import WorkspaceMembershipProjectionRecord
    from app.errors import AppError

    now = datetime.now(UTC)
    async with membership_database.session() as session:
        session.add(
            WorkspaceMembershipProjectionRecord(
                workspace_id="team",
                user_id="user-1",
                role="member",
                source_version="v1",
                expires_at=now - timedelta(seconds=1),
                refreshed_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()
    service = MembershipProjectionService(
        membership_database, FakeSpaceAuthority([None]), now=lambda: now
    )

    with pytest.raises(AppError) as exc_info:
        await service.require_current_membership(_identity(), "team", "sensitive")

    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "workspace_not_found",
    )
    assert await _stored_projection(membership_database) is None


@pytest.mark.asyncio
async def test_stale_projection_fails_closed_during_authority_outage(
    membership_database,
) -> None:
    from app.auth.membership import (
        MembershipAuthorityUnavailable,
        MembershipProjectionService,
    )
    from app.db.models import WorkspaceMembershipProjectionRecord
    from app.errors import AppError

    now = datetime.now(UTC)
    async with membership_database.session() as session:
        session.add(
            WorkspaceMembershipProjectionRecord(
                workspace_id="team",
                user_id="user-1",
                role="owner",
                source_version="v1",
                expires_at=now - timedelta(seconds=1),
                refreshed_at=now - timedelta(minutes=5),
            )
        )
        await session.commit()
    service = MembershipProjectionService(
        membership_database,
        FakeSpaceAuthority([MembershipAuthorityUnavailable("offline")]),
        now=lambda: now,
    )

    with pytest.raises(AppError) as exc_info:
        await service.require_current_membership(_identity(), "team", "write")

    assert (exc_info.value.status_code, exc_info.value.code) == (
        503,
        "membership_unavailable",
    )


@pytest.mark.asyncio
async def test_concurrent_expired_refresh_is_collapsed(membership_database) -> None:
    from app.auth.membership import MembershipProjectionService

    class BlockingAuthority:
        def __init__(self):
            self.calls = 0
            self.release = asyncio.Event()

        async def fetch_membership(self, subject, workspace_external_id):
            self.calls += 1
            await self.release.wait()
            return _membership("member", "v1")

    authority = BlockingAuthority()
    service = MembershipProjectionService(membership_database, authority)
    first = asyncio.create_task(
        service.require_current_membership(_identity(), "team", "write")
    )
    second = asyncio.create_task(
        service.require_current_membership(_identity(), "team", "write")
    )
    await asyncio.sleep(0)
    authority.release.set()
    results = await asyncio.gather(first, second)

    assert [result.role for result in results] == ["member", "member"]
    assert authority.calls == 1


@pytest.mark.asyncio
async def test_private_session_access_rechecks_removed_membership(
    membership_database,
) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.membership import MembershipProjectionService
    from app.db.models import SessionRecord
    from app.errors import AppError

    now = datetime.now(UTC)
    async with membership_database.session() as session:
        session.add(
            SessionRecord(
                id="private-session",
                workspace_id="team",
                created_by="user-1",
                title="Private",
                title_source="auto",
                status="idle",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/private-session",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    projections = MembershipProjectionService(
        membership_database, FakeSpaceAuthority([None]), now=lambda: now
    )
    access = WorkspaceAccessService(membership_database, projections)

    with pytest.raises(AppError) as exc_info:
        await access.require_session_owner(_identity(), "private-session")

    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "workspace_not_found",
    )
