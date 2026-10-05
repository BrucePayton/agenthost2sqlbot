import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.requests import Request

ISSUER = "https://identity.example.test"
AUDIENCE = "workspace-agent"


def _request(token: str, **extra_headers: str) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())]
    headers.extend((key.lower().encode(), value.encode()) for key, value in extra_headers.items())
    return Request({"type": "http", "headers": headers})


def _key_pair(kid: str):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return private_key, jwk


def _token(private_key, kid: str, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "employee-123",
        "name": "Alice",
        "email": "alice@example.test",
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
async def oidc_dependencies(settings_factory):
    from app.auth.identity_repository import IdentityRepository
    from app.db.base import Database

    database = Database(settings_factory().resolved_database_url)
    await database.initialize()
    try:
        yield database, IdentityRepository(database)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_oidc_verifies_token_and_maps_stable_internal_identity(
    settings_factory, oidc_dependencies
) -> None:
    from app.auth.oidc import OidcIdentityProvider

    _database, identities = oidc_dependencies
    private_key, jwk = _key_pair("key-1")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"keys": [jwk]})
    )
    settings = settings_factory(
        identity_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_uri=f"{ISSUER}/.well-known/jwks.json",
    )
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OidcIdentityProvider(settings, identities, http_client=client)
        first = await provider.resolve(
            _request(_token(private_key, "key-1"), **{"X-User-ID": "forged"})
        )
        second = await provider.resolve(_request(_token(private_key, "key-1")))

    assert first == second
    assert first.user_id != "employee-123"
    assert first.user_id != "forged"
    assert first.issuer == ISSUER
    assert first.external_subject == "employee-123"
    assert first.display_name == "Alice"
    async with _database.session() as session:
        from sqlalchemy import select

        from app.db.models import UserRecord

        user = (
            await session.scalars(
                select(UserRecord).where(UserRecord.id == first.user_id)
            )
        ).one()
    assert user.provider == "oidc"
    assert user.external_subject.startswith("oidc:")
    assert "employee-123" not in user.external_subject


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_overrides", "expected"),
    [
        ({"iss": "https://attacker.example"}, "issuer"),
        ({"aud": "other-service"}, "audience"),
        ({"exp": datetime.now(UTC) - timedelta(minutes=5)}, "expired"),
        ({"nbf": datetime.now(UTC) + timedelta(minutes=5)}, "not yet valid"),
        ({"sub": ""}, "subject"),
    ],
)
async def test_oidc_rejects_invalid_trust_claims(
    settings_factory, oidc_dependencies, claim_overrides, expected
) -> None:
    from app.auth.oidc import IdentityVerificationError, OidcIdentityProvider

    _database, identities = oidc_dependencies
    private_key, jwk = _key_pair("key-1")
    settings = settings_factory(
        identity_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_uri=f"{ISSUER}/jwks",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"keys": [jwk]})
        )
    ) as client:
        provider = OidcIdentityProvider(settings, identities, http_client=client)
        with pytest.raises(IdentityVerificationError, match=expected):
            await provider.resolve(
                _request(_token(private_key, "key-1", **claim_overrides))
            )


@pytest.mark.asyncio
async def test_oidc_refreshes_jwks_once_for_rotated_key(
    settings_factory, oidc_dependencies
) -> None:
    from app.auth.oidc import OidcIdentityProvider

    _database, identities = oidc_dependencies
    _old_private_key, old_jwk = _key_pair("old")
    new_private_key, new_jwk = _key_pair("new")
    responses = iter(({"keys": [old_jwk]}, {"keys": [new_jwk]}))
    calls = 0

    def jwks(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=next(responses))

    settings = settings_factory(
        identity_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_uri=f"{ISSUER}/jwks",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(jwks)) as client:
        provider = OidcIdentityProvider(settings, identities, http_client=client)
        identity = await provider.resolve(_request(_token(new_private_key, "new")))

    assert identity.external_subject == "employee-123"
    assert calls == 2


@pytest.mark.asyncio
async def test_oidc_rejects_algorithm_confusion(settings_factory, oidc_dependencies) -> None:
    from app.auth.oidc import IdentityVerificationError, OidcIdentityProvider

    _database, identities = oidc_dependencies
    _private_key, jwk = _key_pair("key-1")
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "employee-123"},
        "shared-secret-with-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "key-1"},
    )
    settings = settings_factory(
        identity_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        oidc_jwks_uri=f"{ISSUER}/jwks",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"keys": [jwk]})
        )
    ) as client:
        provider = OidcIdentityProvider(settings, identities, http_client=client)
        with pytest.raises(IdentityVerificationError, match="algorithm"):
            await provider.resolve(_request(token))


def test_production_rejects_mock_identity(settings_factory) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="OIDC"):
        settings_factory(
            app_env="production",
            database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
            identity_mode="mock",
            space_authority_url="https://spaces.example.test",
            space_authority_token="space-secret",
        )
