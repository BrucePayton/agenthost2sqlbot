from contextlib import asynccontextmanager

import httpx
import pytest

from tests.test_workspaces import write_workspace


@asynccontextmanager
async def debug_client(settings_factory, **overrides):
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(**overrides)
    write_workspace(settings.workspaces_root, "example")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, app


@pytest.mark.asyncio
async def test_debug_catalog_lists_existing_obid_counts_and_locates_session(
    settings_factory,
) -> None:
    async with debug_client(
        settings_factory,
        app_env="development",
        identity_mode="obid",
        personal_workspace_template_id="example",
    ) as (client, app):
        assert (await client.get("/api/debug/users")).json() == []

        headers = {"X-Davinci-ObId": "12901"}
        workspaces = (await client.get("/api/workspaces", headers=headers)).json()
        workspace_id = workspaces[0]["id"]
        created = await client.post(
            f"/api/workspaces/{workspace_id}/sessions", headers=headers
        )
        session_id = created.json()["id"]

        users = await client.get("/api/debug/users")
        located = await client.get(f"/api/debug/sessions/{session_id}")

        async with app.state.services.database.session() as db:
            from sqlalchemy import func, select

            from app.db.models import UserRecord

            user_count = int(await db.scalar(select(func.count(UserRecord.id))) or 0)

    assert users.status_code == 200
    assert users.json() == [{
        "ob_id": "12901",
        "display_name": "Davinci 12901",
        "workspace_count": 1,
        "session_count": 1,
    }]
    assert located.status_code == 200
    assert located.json() == {
        "session_id": session_id,
        "workspace_id": workspace_id,
        "title": "新会话",
        "ob_id": "12901",
    }
    assert user_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        {"app_env": "development", "identity_mode": "mock"},
        {"app_env": "test", "identity_mode": "obid"},
    ],
)
async def test_debug_routes_do_not_exist_outside_development_obid(
    settings_factory, profile
) -> None:
    async with debug_client(settings_factory, **profile) as (client, _app):
        users = await client.get("/api/debug/users")
        session = await client.get("/api/debug/sessions/not-found")

    assert users.status_code == 404
    assert session.status_code == 404
