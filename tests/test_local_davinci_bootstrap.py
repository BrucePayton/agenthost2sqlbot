import os
import re
from pathlib import Path

import pytest

from app.embed.local import LocalBootstrapStore
from tests.test_api import api_client

OBID = "00123"


@pytest.mark.asyncio
async def test_local_bootstrap_accepts_the_paired_davinci_frontend_digest(
    settings_factory,
) -> None:
    """Exercise real Host bootstrap/embed endpoints with the other checkout's digest."""
    default_webapp = Path(__file__).resolve().parents[2] / "davinci/webapp"
    webapp = Path(os.environ.get("DAVINCI_WEBAPP_ROOT", str(default_webapp)))
    frontend = webapp / "share/containers/WorkBenchNew/agent/contracts/generated-v2.ts"
    assert frontend.is_file(), (
        "Paired Davinci checkout is required; set DAVINCI_WEBAPP_ROOT. "
        "A missing cross-project handshake check must not be treated as passing."
    )
    match = re.search(
        r"export const CONTRACT_DIGEST = '([0-9a-f]{64})'",
        frontend.read_text(encoding="utf-8"),
    )
    assert match is not None
    frontend_digest = match.group(1)
    parent_origin = "https://davinci.example.test"

    async with api_client(
        settings_factory,
        settings_overrides={
            "davinci_local_integration": True,
            "davinci_local_public_origin": "https://agent.example.test",
            "davinci_local_parent_origins": (parent_origin,),
        },
    ) as client:
        bootstrap = await client.post(
            "/agent-api/session/bootstrap",
            json={
                "parentOrigin": parent_origin,
                "protocolVersion": "agui-native-v2",
                "obId": OBID,
            },
        )
        assert bootstrap.status_code == 200
        payload = bootstrap.json()
        # Do not substitute Host's own digest here: that hid the integration bug.
        assert payload["contractDigest"] == frontend_digest
        embedded = await client.post(
            "/embed/local",
            data={
                "bootstrapCode": payload["bootstrapCode"],
                "parentOrigin": parent_origin,
                "protocolVersion": payload["protocolVersion"],
                "contractVersion": payload["contractVersion"],
                "contractDigest": frontend_digest,
            },
        )
        assert embedded.status_code == 200
        assert frontend_digest in embedded.text


@pytest.mark.asyncio
async def test_local_bootstrap_is_disabled_by_default(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        response = await client.post(
            "/agent-api/session/bootstrap",
            json={
                "parentOrigin": "http://local.aihuishou.com:5002",
                "protocolVersion": "1.0",
                "obId": OBID,
            },
        )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_local_bootstrap_code_is_single_use_and_renders_v1_config(
    settings_factory,
) -> None:
    def enabled_settings():
        return settings_factory(
            davinci_local_integration=True,
            davinci_local_public_origin="http://127.0.0.1:8000",
            davinci_local_parent_origins=("http://local.aihuishou.com:5002",),
        )

    async with api_client(enabled_settings) as client:
        bootstrap = await client.post(
            "/agent-api/session/bootstrap",
            json={
                "parentOrigin": "http://local.aihuishou.com:5002",
                "protocolVersion": "1.0",
                "obId": OBID,
            },
        )
        payload = bootstrap.json()
        form = {
            "bootstrapCode": payload["bootstrapCode"],
            "parentOrigin": "http://local.aihuishou.com:5002",
            "protocolVersion": payload["protocolVersion"],
            "contractVersion": payload["contractVersion"],
            "contractDigest": payload["contractDigest"],
        }
        embedded = await client.post("/embed/local", data=form)
        replay = await client.post("/embed/local", data=form)

    assert bootstrap.status_code == 200
    assert payload["embedUrl"] == "http://127.0.0.1:8000/embed/local"
    assert embedded.status_code == 200
    assert "http://local.aihuishou.com:5002" in embedded.text
    assert re.search(r"/static/embed\.js\?v=[0-9a-f]{16}", embedded.text)
    assert payload["bootstrapCode"] not in embedded.text
    assert '"obId": "00123"' in embedded.text
    assert replay.status_code == 409


@pytest.mark.asyncio
async def test_local_bootstrap_locks_v2_contract_into_the_embed_code(
    settings_factory,
) -> None:
    def enabled_settings():
        return settings_factory(
            davinci_local_integration=True,
            davinci_local_public_origin="http://127.0.0.1:8000",
            davinci_local_parent_origins=("http://local.aihuishou.com:5002",),
        )

    async with api_client(enabled_settings) as client:
        bootstrap = await client.post(
            "/agent-api/session/bootstrap",
            json={
                "parentOrigin": "http://local.aihuishou.com:5002",
                "protocolVersion": "agui-native-v2",
                "obId": OBID,
            },
        )
        payload = bootstrap.json()
        v1_form = {
            "bootstrapCode": payload["bootstrapCode"],
            "parentOrigin": "http://local.aihuishou.com:5002",
            "protocolVersion": "1.0",
            "contractVersion": "1.0",
            "contractDigest": "wrong",
        }
        mismatched = await client.post("/embed/local", data=v1_form)
        embedded = await client.post(
            "/embed/local",
            data={
                "bootstrapCode": payload["bootstrapCode"],
                "parentOrigin": "http://local.aihuishou.com:5002",
                "protocolVersion": payload["protocolVersion"],
                "contractVersion": payload["contractVersion"],
                "contractDigest": payload["contractDigest"],
            },
        )

    assert bootstrap.status_code == 200
    assert payload["protocolVersion"] == "agui-native-v2"
    assert payload["contractVersion"] == "2.0"
    assert mismatched.status_code == 409
    assert embedded.status_code == 200
    assert '"protocolVersion": "agui-native-v2"' in embedded.text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "actor/1"])
async def test_local_bootstrap_requires_valid_obid(
    settings_factory, value: str | None
) -> None:
    def enabled_settings():
        return settings_factory(
            davinci_local_integration=True,
            davinci_local_parent_origins=("http://local.aihuishou.com:5002",),
        )

    body = {
        "parentOrigin": "http://local.aihuishou.com:5002",
        "protocolVersion": "agui-native-v2",
    }
    if value is not None:
        body["obId"] = value
    async with api_client(enabled_settings) as client:
        response = await client.post("/agent-api/session/bootstrap", json=body)

    assert response.status_code == 422


def test_local_bootstrap_record_binds_obid_origin_and_contract() -> None:
    store = LocalBootstrapStore()
    code, _expires_at = store.issue(
        "https://davinci.example.test",
        "agui-native-v2",
        "2.0",
        "f" * 64,
        "00123",
    )

    record = store.consume(
        code,
        "https://davinci.example.test",
        "agui-native-v2",
        "2.0",
        "f" * 64,
    )

    assert record.ob_id == "00123"
    assert record.parent_origin == "https://davinci.example.test"
    assert record.contract_digest == "f" * 64
    with pytest.raises(Exception, match="bootstrap code is invalid"):
        store.consume(
            code,
            "https://davinci.example.test",
            "agui-native-v2",
            "2.0",
            "f" * 64,
        )


@pytest.mark.asyncio
async def test_embed_form_obid_cannot_override_bound_identity(settings_factory) -> None:
    def enabled_settings():
        return settings_factory(
            davinci_local_integration=True,
            davinci_local_parent_origins=("http://local.aihuishou.com:5002",),
        )

    async with api_client(enabled_settings) as client:
        bootstrap = await client.post(
            "/agent-api/session/bootstrap",
            json={
                "parentOrigin": "http://local.aihuishou.com:5002",
                "protocolVersion": "agui-native-v2",
                "obId": "actor-a",
            },
        )
        payload = bootstrap.json()
        embedded = await client.post(
            "/embed/local",
            data={
                "bootstrapCode": payload["bootstrapCode"],
                "parentOrigin": "http://local.aihuishou.com:5002",
                "protocolVersion": payload["protocolVersion"],
                "contractVersion": payload["contractVersion"],
                "contractDigest": payload["contractDigest"],
                "obId": "actor-b",
            },
        )

    assert embedded.status_code == 200
    assert '"obId": "actor-a"' in embedded.text
    assert '"obId": "actor-b"' not in embedded.text


@pytest.mark.asyncio
async def test_passthrough_bootstrap_binds_verified_user_and_hides_davinci_token(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    settings = settings_factory(
        app_env="uat",
        identity_mode="davinci_passthrough",
        database_url=f"sqlite+aiosqlite:///{settings_factory().app_data_dir / 'auth.db'}",
        davinci_local_integration=True,
        davinci_local_parent_origins=("https://davinci.example.test",),
        davinci_api_base_url="https://davinci.test",
    )
    write_workspace(settings.workspaces_root, "example")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer davinci-user-token"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "payload": {"obId": "verified-user", "name": "Alice", "active": True},
            },
        )

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(handler),
    ) as davinci_client:
        app = create_app(
            settings=settings,
            runtime=FakeAgentRuntime(),
            davinci_auth_client=davinci_client,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            bootstrap = await client.post(
                "/agent-api/session/bootstrap",
                headers={"Authorization": "Bearer davinci-user-token"},
                json={
                    "parentOrigin": "https://davinci.example.test",
                    "protocolVersion": "agui-native-v2",
                    "obId": "spoofed-user",
                },
            )
            payload = bootstrap.json()
            embedded = await client.post(
                "/embed/local",
                data={
                    "bootstrapCode": payload["bootstrapCode"],
                    "parentOrigin": "https://davinci.example.test",
                    "protocolVersion": payload["protocolVersion"],
                    "contractVersion": payload["contractVersion"],
                    "contractDigest": payload["contractDigest"],
                },
            )
            session_token = re.search(
                r'"sessionToken": "([^"]+)"', embedded.text
            ).group(1)
            me = await client.get(
                "/api/me",
                headers={"Authorization": f"Bearer {session_token}"},
            )
            workspaces = await client.get(
                "/api/workspaces",
                headers={"Authorization": f"Bearer {session_token}"},
            )

    assert bootstrap.status_code == 200
    assert embedded.status_code == 200
    assert '"obId": "verified-user"' in embedded.text
    assert "spoofed-user" not in embedded.text
    assert "davinci-user-token" not in embedded.text
    assert session_token != "davinci-user-token"
    assert me.status_code == 200
    assert me.json()["external_subject"] == "verified-user"
    assert workspaces.status_code == 200
    assert [
        (workspace["name"], workspace["available"])
        for workspace in workspaces.json()
    ] == [("Alice 的工作区", True)]


@pytest.mark.asyncio
async def test_cookie_bootstrap_and_session_scoped_davinci_proxy(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    parent_origin = "https://davinci.example.test"
    cookie = "CASTGC=browser-session; branch_id=1"
    proxied_requests: list[tuple[str, str]] = []
    settings = settings_factory(
        app_env="uat",
        identity_mode="davinci_passthrough",
        database_url=(
            f"sqlite+aiosqlite:///{settings_factory().app_data_dir / 'cookie-auth.db'}"
        ),
        davinci_local_integration=True,
        davinci_local_parent_origins=(parent_origin,),
        davinci_api_base_url="https://davinci.test",
    )
    write_workspace(settings.workspaces_root, "actual")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == cookie
        assert "authorization" not in request.headers
        proxied_requests.append((request.method, request.url.path))
        if request.url.path == "/api/v3/users/currentUser":
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "payload": {
                        "obId": "verified-user",
                        "name": "Alice",
                        "active": True,
                    },
                },
            )
        assert request.method == "POST"
        assert request.url.path == "/api/v3/dataMarket/list"
        assert request.headers["content-type"].startswith("application/json")
        return httpx.Response(200, json={"code": 0, "data": {"list": []}})

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(handler),
    ) as davinci_client:
        app = create_app(
            settings=settings,
            runtime=FakeAgentRuntime(),
            davinci_auth_client=davinci_client,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            bootstrap = await client.post(
                "/agent-api/session/bootstrap",
                headers={"Cookie": cookie, "Origin": parent_origin},
                json={
                    "parentOrigin": parent_origin,
                    "protocolVersion": "agui-native-v2",
                    "obId": "spoofed-user",
                },
            )
            payload = bootstrap.json()
            embedded = await client.post(
                "/embed/local",
                data={
                    "bootstrapCode": payload["bootstrapCode"],
                    "parentOrigin": parent_origin,
                    "protocolVersion": payload["protocolVersion"],
                    "contractVersion": payload["contractVersion"],
                    "contractDigest": payload["contractDigest"],
                },
            )
            session_token = re.search(
                r'"sessionToken": "([^"]+)"', embedded.text
            ).group(1)
            current_user = await client.get(
                "/agent-api/davinci/api/v3/users/currentUser",
                headers={"Authorization": f"Bearer {session_token}"},
            )
            anonymous = await client.get("/agent-api/davinci/api/v3/users/currentUser")
            proxied = await client.post(
                "/agent-api/davinci/api/v3/dataMarket/list",
                headers={"Authorization": f"Bearer {session_token}"},
                json={"keyword": "成交金额"},
            )
            denied = await client.post(
                "/agent-api/davinci/api/v3/users/deleteAll",
                headers={"Authorization": f"Bearer {session_token}"},
            )

    assert bootstrap.status_code == 200
    assert embedded.status_code == 200
    assert current_user.status_code == 200
    assert current_user.json()["payload"]["obId"] == "verified-user"
    assert anonymous.status_code == 401
    assert proxied.status_code == 200
    assert proxied.json() == {"code": 0, "data": {"list": []}}
    assert denied.status_code == 404
    assert proxied_requests == [
        ("GET", "/api/v3/users/currentUser"),
        ("GET", "/api/v3/users/currentUser"),
        ("POST", "/api/v3/dataMarket/list"),
    ]
    assert "browser-session" not in embedded.text
    assert "spoofed-user" not in embedded.text


@pytest.mark.asyncio
async def test_obid_bootstrap_prefers_same_origin_cookie_for_data_mcp(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.memory.scopes import user_scope_key
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    parent_origin = "http://local.aihuishou.com:5003"
    cookie = "CASTGC=browser-session; branch_id=1"
    browser_user_agent = "Davinci-Browser/1.0"
    proxied_requests: list[tuple[str, str]] = []
    settings = settings_factory(
        identity_mode="obid",
        database_url=(
            f"sqlite+aiosqlite:///{settings_factory().app_data_dir / 'obid-mcp.db'}"
        ),
        davinci_local_integration=True,
        davinci_local_parent_origins=(parent_origin,),
        davinci_api_base_url="http://127.0.0.1:5003",
    )
    write_workspace(settings.workspaces_root, "actual")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == cookie
        assert "authorization" not in request.headers
        assert request.headers["user-agent"] == browser_user_agent
        assert request.headers["origin"] == parent_origin
        proxied_requests.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "code": 200,
                "payload": {"obId": "actor-a", "name": "Alice", "active": True},
            },
        )

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:5003",
        transport=httpx.MockTransport(handler),
    ) as davinci_client:
        app = create_app(
            settings=settings,
            runtime=FakeAgentRuntime(),
            davinci_auth_client=davinci_client,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            bootstrap = await client.post(
                "/agent-api/session/bootstrap",
                headers={
                    "Authorization": "Bearer stale-browser-token",
                    "Cookie": cookie,
                    "Origin": parent_origin,
                    "User-Agent": browser_user_agent,
                },
                json={
                    "parentOrigin": parent_origin,
                    "protocolVersion": "agui-native-v2",
                    "obId": "actor-a",
                },
            )
            assert proxied_requests == []

            identity = await app.state.services.identity_provider.resolve_ob_id(
                "actor-a"
            )
            session_token = (
                app.state.services.identity_provider.authorization_for_owner(
                    user_scope_key(identity.user_id)
                )
            )
            assert app.state.services.identity_provider.ob_id_for_owner(
                user_scope_key(identity.user_id)
            ) == "actor-a"
            current_user = await client.get(
                "/agent-api/davinci/api/v3/users/currentUser",
                headers={"Authorization": f"Bearer {session_token}"},
            )

    assert bootstrap.status_code == 200
    assert current_user.status_code == 200
    assert current_user.json()["payload"]["obId"] == "actor-a"
    assert proxied_requests == [("GET", "/api/v3/users/currentUser")]


@pytest.mark.asyncio
async def test_cookie_bootstrap_requires_matching_browser_origin(
    settings_factory,
) -> None:
    import httpx

    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.test_workspaces import write_workspace

    parent_origin = "https://davinci.example.test"
    settings = settings_factory(
        app_env="uat",
        identity_mode="davinci_passthrough",
        database_url=(
            f"sqlite+aiosqlite:///{settings_factory().app_data_dir / 'cookie-csrf.db'}"
        ),
        davinci_local_integration=True,
        davinci_local_parent_origins=(parent_origin,),
        davinci_api_base_url="https://davinci.test",
    )
    write_workspace(settings.workspaces_root, "actual")
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(handler),
    ) as davinci_client:
        app = create_app(
            settings=settings,
            runtime=FakeAgentRuntime(),
            davinci_auth_client=davinci_client,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            response = await client.post(
                "/agent-api/session/bootstrap",
                headers={
                    "Authorization": "Bearer stale-browser-token",
                    "Cookie": "CASTGC=browser-session",
                    "Origin": "https://evil.example.test",
                },
                json={
                    "parentOrigin": parent_origin,
                    "protocolVersion": "agui-native-v2",
                },
            )

    assert response.status_code == 403
    assert called is False
