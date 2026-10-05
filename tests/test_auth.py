from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from tests.test_workspaces import write_workspace


@pytest.fixture
async def database_with_memberships(settings_factory):
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.sync import WorkspaceSyncService

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner", "team": "owner"},
    )
    write_workspace(settings.workspaces_root, "personal")
    write_workspace(settings.workspaces_root, "team")
    database = Database(settings.resolved_database_url)
    await database.initialize()
    identity = IdentityContext("owner", "owner", "Owner")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    await WorkspaceSyncService(database, settings).sync(registry.scan(), identity)
    async with database.session() as db:
        db.add(
            UserRecord(
                id="member",
                external_subject="member",
                display_name="Member",
                provider="mock",
            )
        )
        db.add(
            WorkspaceMemberRecord(
                workspace_id="team", user_id="member", role="member"
            )
        )
        await db.commit()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_workspace_access_enforces_roles(database_with_memberships) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.errors import AppError

    access = WorkspaceAccessService(database_with_memberships)
    member = IdentityContext(
        user_id="member", external_subject="member", display_name="Member"
    )
    assert (await access.require_member(member, "team")).role == "member"
    with pytest.raises(AppError) as exc_info:
        await access.require_manager(member, "team")
    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "workspace_not_found",
    )


@pytest.mark.asyncio
async def test_team_member_cannot_access_another_creators_session(
    database_with_memberships,
) -> None:
    from datetime import UTC, datetime

    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.models import SessionRecord
    from app.errors import AppError

    now = datetime.now(UTC)
    async with database_with_memberships.session() as db:
        db.add(
            SessionRecord(
                id="owner-session",
                workspace_id="team",
                created_by="owner",
                title="Owner Session",
                title_source="auto",
                status="idle",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/owner-session",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    access = WorkspaceAccessService(database_with_memberships)
    member = IdentityContext("member", "member", "Member")
    assert (await access.require_member(member, "team")).role == "member"
    with pytest.raises(AppError) as exc_info:
        await access.require_session_owner(member, "owner-session")
    assert (exc_info.value.status_code, exc_info.value.code) == (
        404,
        "session_not_found",
    )


@pytest.mark.asyncio
async def test_sync_promotes_existing_personal_placeholder_and_keeps_legacy_access(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import WorkspaceMemberRecord, WorkspaceRecord
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.sync import WorkspaceSyncService

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner"},
    )
    write_workspace(settings.workspaces_root, "personal")
    database = Database(settings.resolved_database_url)
    await database.initialize()
    async with database.session() as db:
        db.add(
            WorkspaceRecord(
                id="personal", name="Legacy", kind="team", config_json="{}"
            )
        )
        db.add(
            WorkspaceRecord(
                id="legacy", name="Legacy Session", kind="team", config_json="{}"
            )
        )
        await db.commit()

    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    identity = IdentityContext("owner", "owner", "Owner")
    await WorkspaceSyncService(database, settings).sync(registry.scan(), identity)

    async with database.session() as db:
        personal = await db.get(WorkspaceRecord, "personal")
        legacy_member = await db.get(WorkspaceMemberRecord, ("legacy", "owner"))
    assert personal is not None and personal.kind == "personal"
    assert legacy_member is not None and legacy_member.role == "owner"
    await database.dispose()


class HeaderIdentityProvider:
    async def resolve(self, request):
        from app.auth.models import IdentityContext

        if request.headers.get("X-Test-User") == "outsider":
            return IdentityContext("outsider", "outsider", "Outsider")
        return IdentityContext("owner", "owner", "Owner")

    async def resolve_bootstrap_identity(self):
        from app.auth.models import IdentityContext

        return IdentityContext("owner", "owner", "Owner")


@asynccontextmanager
async def client_with_provider(
    settings_factory, provider
) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        mock_personal_workspace_id="personal",
        mock_workspace_roles={"personal": "owner", "team": "owner"},
    )
    write_workspace(settings.workspaces_root, "personal")
    write_workspace(settings.workspaces_root, "team")
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        identity_provider=provider,
    )
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
async def api_for_two_users(settings_factory):
    async with client_with_provider(
        settings_factory, HeaderIdentityProvider()
    ) as client:
        yield client, client


@pytest.mark.asyncio
async def test_list_workspaces_returns_only_current_user_memberships(
    api_for_two_users,
) -> None:
    owner_client, outsider_client = api_for_two_users
    assert [
        item["id"] for item in (await owner_client.get("/api/workspaces")).json()
    ] == ["personal", "team"]
    assert (
        await outsider_client.get(
            "/api/workspaces", headers={"X-Test-User": "outsider"}
        )
    ).json() == []


class MissingIdentityProvider:
    def __init__(self, bootstrap_identity) -> None:
        self.bootstrap_identity = bootstrap_identity

    async def resolve(self, request):
        raise LookupError("identity missing")

    async def resolve_bootstrap_identity(self):
        return self.bootstrap_identity


@pytest.mark.asyncio
async def test_missing_identity_uses_stable_401(settings_factory) -> None:
    from app.auth.models import IdentityContext

    owner = IdentityContext("owner", "owner", "Owner")
    async with client_with_provider(
        settings_factory, MissingIdentityProvider(bootstrap_identity=owner)
    ) as client:
        response = await client.get("/api/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_missing"
