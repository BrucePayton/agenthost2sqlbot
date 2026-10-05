from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.requests import Request


def _request(token: str | None) -> Request:
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    return Request({"type": "http", "headers": headers})


@pytest.fixture
async def identity_database(settings_factory) -> AsyncIterator[object]:
    from app.db.base import Database

    database = Database(settings_factory().resolved_database_url)
    await database.initialize()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_passthrough_authenticates_davinci_token_and_issues_opaque_session(
    identity_database,
) -> None:
    from app.auth.davinci_passthrough import DavinciPassthroughIdentityProvider
    from app.auth.identity_repository import IdentityRepository
    from app.memory.scopes import user_scope_key

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/users/currentUser"
        assert request.headers["authorization"] == "Bearer davinci-user-token"
        assert request.headers["cookie"] == ""
        return httpx.Response(
            200,
            json={
                "code": 200,
                "payload": {
                    "obId": "00123",
                    "account": "00123",
                    "name": "Alice",
                    "active": True,
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.cookies.set("CASTGC", "stale-other-user", domain="davinci.test")
        provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(identity_database),
            client,
            current_user_path="/api/v3/users/currentUser",
            session_ttl_seconds=3600,
        )
        grant = await provider.authenticate("Bearer davinci-user-token")
        session_token = provider.issue_session(grant)
        identity = await provider.resolve(_request(session_token))

    assert grant.ob_id == "00123"
    assert identity.external_subject == "00123"
    assert identity.display_name == "Alice"
    assert session_token != "davinci-user-token"
    assert provider.authorization_for_owner(user_scope_key(identity.user_id)) == (
        session_token
    )
    assert provider.ob_id_for_owner(user_scope_key(identity.user_id)) == "00123"


def test_mcp_ob_id_is_bound_to_the_owner_and_requires_a_live_session() -> None:
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from app.auth.davinci_passthrough import (
        DavinciBootstrapGrant,
        DavinciSessionStore,
        DavinciUpstreamCredential,
    )
    from app.auth.models import IdentityContext
    from app.memory.scopes import user_scope_key

    sessions = DavinciSessionStore(3600)
    alice = IdentityContext("internal-alice", "00123", "Alice")
    bob = IdentityContext("internal-bob", "00456", "Bob")
    alice_token = sessions.issue(DavinciBootstrapGrant(
        alice, "00123", DavinciUpstreamCredential(authorization="Bearer alice-token")
    ))
    sessions.issue(DavinciBootstrapGrant(
        bob, "00456", DavinciUpstreamCredential(authorization="Bearer bob-token")
    ))

    assert sessions.ob_id_for_owner(user_scope_key(alice.user_id)) == "00123"
    assert sessions.ob_id_for_owner(user_scope_key(bob.user_id)) == "00456"
    with pytest.raises(LookupError):
        sessions.ob_id_for_owner(user_scope_key("unbound-user"))

    sessions._sessions[alice_token] = replace(
        sessions.resolve(alice_token), expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    with pytest.raises(LookupError):
        sessions.ob_id_for_owner(user_scope_key(alice.user_id))
    assert sessions.ob_id_for_owner(user_scope_key(bob.user_id)) == "00456"


@pytest.mark.asyncio
async def test_passthrough_authenticates_davinci_browser_cookie(
    identity_database,
) -> None:
    from app.auth.davinci_passthrough import DavinciPassthroughIdentityProvider
    from app.auth.identity_repository import IdentityRepository
    from app.memory.scopes import user_scope_key

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v3/users/currentUser"
        assert "authorization" not in request.headers
        assert request.headers["cookie"] == "CASTGC=browser-session; branch_id=1"
        return httpx.Response(
            200,
            json={
                "payload": {
                    "obId": "00123",
                    "name": "Alice",
                    "active": True,
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.cookies.set("CASTGC", "stale-other-user", domain="davinci.test")
        provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(identity_database),
            client,
            current_user_path="/api/v3/users/currentUser",
            session_ttl_seconds=3600,
        )
        grant = await provider.authenticate(
            None,
            cookie="CASTGC=browser-session; branch_id=1",
        )
        session_token = provider.issue_session(grant)

    assert grant.ob_id == "00123"
    assert (
        provider.authorization_for_owner(user_scope_key(grant.identity.user_id))
        == session_token
    )
    assert "browser-session" not in repr(grant)


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie_status", [302, 401], ids=["login-redirect", "unauthorized"])
async def test_passthrough_falls_back_to_bearer_when_browser_cookie_is_rejected(
    identity_database,
    cookie_status: int,
) -> None:
    from app.auth.davinci_passthrough import DavinciPassthroughIdentityProvider
    from app.auth.identity_repository import IdentityRepository

    attempted_credentials: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("cookie") == "CASTGC=expired-browser-session":
            attempted_credentials.append("cookie")
            return httpx.Response(cookie_status)
        assert request.headers["authorization"] == "Bearer davinci-user-token"
        assert request.headers["cookie"] == ""
        attempted_credentials.append("bearer")
        return httpx.Response(
            200,
            json={
                "code": 200,
                "payload": {
                    "obId": "00123",
                    "name": "Alice",
                    "active": True,
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(identity_database),
            client,
            current_user_path="/api/v3/users/currentUser",
            session_ttl_seconds=3600,
        )
        grant = await provider.authenticate(
            "Bearer davinci-user-token",
            cookie="CASTGC=expired-browser-session",
        )

    assert attempted_credentials == ["cookie", "bearer"]
    assert grant.ob_id == "00123"
    assert grant.credential.headers()["Authorization"] == "Bearer davinci-user-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401),
        httpx.Response(200, json={"code": 200, "payload": None}),
        httpx.Response(200, json={"code": 200, "payload": {"name": "No identity"}}),
    ],
    ids=["unauthorized", "null-user", "missing-obid"],
)
async def test_passthrough_fails_closed_when_davinci_user_is_not_verified(
    identity_database,
    response: httpx.Response,
) -> None:
    from app.auth.davinci_passthrough import DavinciPassthroughIdentityProvider
    from app.auth.identity_repository import IdentityRepository

    async with httpx.AsyncClient(
        base_url="https://davinci.test",
        transport=httpx.MockTransport(lambda _request: response),
    ) as client:
        provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(identity_database),
            client,
            current_user_path="/api/v3/users/currentUser",
            session_ttl_seconds=3600,
        )
        with pytest.raises(LookupError):
            await provider.authenticate("Bearer davinci-user-token")


@pytest.mark.asyncio
async def test_passthrough_rejects_missing_or_unknown_host_session(
    identity_database,
) -> None:
    from app.auth.davinci_passthrough import DavinciPassthroughIdentityProvider
    from app.auth.identity_repository import IdentityRepository

    async with httpx.AsyncClient(base_url="https://davinci.test") as client:
        provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(identity_database),
            client,
            current_user_path="/api/v3/users/currentUser",
            session_ttl_seconds=3600,
        )
        with pytest.raises(LookupError):
            await provider.resolve(_request(None))
        with pytest.raises(LookupError):
            await provider.resolve(_request("unknown-host-session"))
