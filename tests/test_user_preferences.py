"""Per-user UI preferences: schema, merge semantics and the API boundary."""

from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from tests.test_api import foreign_resource_client  # noqa: F401 - pytest fixture


@pytest.mark.asyncio
async def test_migration_creates_user_preferences_table(tmp_path: Path) -> None:
    from app.db.base import Database

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    await database.initialize()
    async with database.engine.connect() as connection:
        columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("user_preferences")
            )
        }
        primary_key = await connection.run_sync(
            lambda sync: inspect(sync).get_pk_constraint("user_preferences")
        )
        foreign_keys = await connection.run_sync(
            lambda sync: inspect(sync).get_foreign_keys("user_preferences")
        )
        migration_head = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )
    await database.dispose()

    assert set(columns) == {"user_id", "preferences_json", "updated_at"}
    assert columns["preferences_json"]["nullable"] is False
    assert primary_key["constrained_columns"] == ["user_id"]
    assert [(fk["referred_table"], fk["constrained_columns"]) for fk in foreign_keys] == [
        ("users", ["user_id"])
    ]
    assert migration_head == "0016"


async def _database_with_user(tmp_path: Path, user_id: str = "alice"):
    """A migrated database holding one real user row for the FK."""
    from datetime import UTC, datetime

    from app.db.base import Database
    from app.db.models import UserRecord

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'prefs.db'}")
    await database.initialize()
    now = datetime.now(UTC)
    async with database.session() as db:
        db.add(
            UserRecord(
                id=user_id,
                external_subject=user_id,
                display_name=user_id.title(),
                provider="mock",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    return database


@pytest.mark.asyncio
async def test_unknown_user_has_no_preferences(tmp_path: Path) -> None:
    from app.preferences import get_preferences

    database = await _database_with_user(tmp_path)
    try:
        assert await get_preferences(database, "alice") == {}
        assert await get_preferences(database, "nobody") == {}
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_merge_keeps_other_keys_and_rounds_launcher(tmp_path: Path) -> None:
    from app.api.schemas import PreferencesIn
    from app.preferences import get_preferences, merge_preferences

    database = await _database_with_user(tmp_path)
    try:
        first = await merge_preferences(
            database, "alice", PreferencesIn(panelSize="large")
        )
        second = await merge_preferences(
            database, "alice", PreferencesIn(launcher={"x": 0.123456, "y": 1})
        )
        stored = await get_preferences(database, "alice")
    finally:
        await database.dispose()

    assert first == {"panelSize": "large"}
    assert second == {"panelSize": "large", "launcher": {"x": 0.1235, "y": 1.0}}
    assert stored == second


@pytest.mark.asyncio
async def test_merge_null_clears_only_that_key(tmp_path: Path) -> None:
    from app.api.schemas import PreferencesIn
    from app.preferences import get_preferences, merge_preferences

    database = await _database_with_user(tmp_path)
    try:
        await merge_preferences(
            database,
            "alice",
            PreferencesIn(panelSize="medium", launcher={"x": 0.5, "y": 0.5}),
        )
        cleared = await merge_preferences(
            database, "alice", PreferencesIn(launcher=None)
        )
        untouched = await merge_preferences(database, "alice", PreferencesIn())
        stored = await get_preferences(database, "alice")
    finally:
        await database.dispose()

    assert cleared == {"panelSize": "medium"}
    assert untouched == {"panelSize": "medium"}
    assert stored == {"panelSize": "medium"}


@pytest.mark.asyncio
async def test_put_preferences_merges_and_returns_the_full_document(
    settings_factory,
) -> None:
    from tests.test_api import api_client

    async with api_client(settings_factory) as client:
        first = await client.put("/api/me/preferences", json={"panelSize": "large"})
        second = await client.put(
            "/api/me/preferences", json={"launcher": {"x": 0.2, "y": 0.9}}
        )

    assert first.status_code == 200, first.text
    assert first.json() == {"panelSize": "large", "launcher": None}
    assert second.json() == {"panelSize": "large", "launcher": {"x": 0.2, "y": 0.9}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"panelSize": "huge"},
        {"launcher": {"x": 1.5, "y": 0}},
        {"launcher": {"x": 0.5}},
        {"launcher": "left"},
        {"theme": "dark"},
    ],
)
async def test_put_preferences_rejects_invalid_values(
    settings_factory, payload: dict
) -> None:
    from tests.test_api import api_client

    async with api_client(settings_factory) as client:
        response = await client.put("/api/me/preferences", json=payload)

    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_preferences_belong_to_the_calling_user(foreign_resource_client) -> None:  # noqa: F811
    owner = await foreign_resource_client.put(
        "/api/me/preferences", json={"panelSize": "fullscreen"}
    )
    outsider = await foreign_resource_client.put(
        "/api/me/preferences", json={}, headers={"X-Test-User": "outsider"}
    )

    assert owner.status_code == 200, owner.text
    assert owner.json()["panelSize"] == "fullscreen"
    assert outsider.status_code == 200, outsider.text
    assert outsider.json() == {"panelSize": None, "launcher": None}


PARENT = "http://local.aihuishou.com:5002"
BOOTSTRAP_BODY = {"parentOrigin": PARENT, "protocolVersion": "agui-native-v2", "obId": "00123"}


def _enabled_settings(settings_factory):
    def factory():
        return settings_factory(
            davinci_local_integration=True,
            davinci_local_public_origin="http://127.0.0.1:8000",
            davinci_local_parent_origins=(PARENT,),
        )

    return factory


def _embed_form(payload: dict) -> dict:
    return {
        "bootstrapCode": payload["bootstrapCode"],
        "parentOrigin": PARENT,
        "protocolVersion": payload["protocolVersion"],
        "contractVersion": payload["contractVersion"],
        "contractDigest": payload["contractDigest"],
    }


def _embed_config(html: str) -> dict:
    import json
    import re

    match = re.search(
        r'<script id="davinciEmbedConfig" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match, "embed page has no embedConfig"
    return json.loads(match.group(1))


@pytest.mark.asyncio
async def test_obid_mode_reports_no_saved_preferences(settings_factory) -> None:
    from tests.test_api import api_client

    async with api_client(_enabled_settings(settings_factory)) as client:
        bootstrap = await client.post("/agent-api/session/bootstrap", json=BOOTSTRAP_BODY)
        embedded = await client.post("/embed/local", data=_embed_form(bootstrap.json()))

    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["launcher"] is None
    assert "panelSize" not in _embed_config(embedded.text)


@pytest.mark.asyncio
async def test_passthrough_bootstrap_and_embed_carry_the_saved_preferences(
    settings_factory,
) -> None:
    import httpx

    from app.auth.davinci_passthrough import DavinciPassthroughIdentityProvider
    from app.auth.identity_repository import IdentityRepository
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    async def davinci(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer davinci-user-token"
        return httpx.Response(
            200,
            json={"code": 200, "payload": {"obId": "00123", "name": "Alice", "active": True}},
        )

    settings = _enabled_settings(settings_factory)()
    write_workspace(settings.workspaces_root, "actual")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    headers = {"Authorization": "Bearer davinci-user-token"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            base_url="https://davinci.test", transport=httpx.MockTransport(davinci)
        ) as upstream,
    ):
        app.state.services.identity_provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(app.state.services.database),
            upstream,
            current_user_path="/api/v3/users/currentUser",
            session_ttl_seconds=3600,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            fresh = await client.post("/agent-api/session/bootstrap", json=BOOTSTRAP_BODY, headers=headers)
            fresh_embed = await client.post("/embed/local", data=_embed_form(fresh.json()))
            session_token = _embed_config(fresh_embed.text)["sessionToken"]
            saved = await client.put(
                "/api/me/preferences",
                json={"panelSize": "large", "launcher": {"x": 0.25, "y": 0.75}},
                headers={"Authorization": f"Bearer {session_token}"},
            )
            again = await client.post("/agent-api/session/bootstrap", json=BOOTSTRAP_BODY, headers=headers)
            again_embed = await client.post("/embed/local", data=_embed_form(again.json()))

    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["launcher"] is None
    assert "panelSize" not in _embed_config(fresh_embed.text)
    assert saved.status_code == 200, saved.text
    assert again.json()["launcher"] == {"x": 0.25, "y": 0.75}
    assert _embed_config(again_embed.text)["panelSize"] == "large"
