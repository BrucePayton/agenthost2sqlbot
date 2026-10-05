from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from starlette.requests import Request


def _request(ob_id: str | None) -> Request:
    headers = [] if ob_id is None else [(b"x-davinci-obid", ob_id.encode())]
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


def test_normalize_obid_preserves_opaque_value() -> None:
    from app.auth.obid import normalize_ob_id

    assert normalize_ob_id(" 00123 ") == "00123"
    assert normalize_ob_id("Actor.A-7_b") == "Actor.A-7_b"


@pytest.mark.parametrize("value", [None, "", "  ", "actor/1", "a" * 65])
def test_normalize_obid_rejects_invalid_value(value: str | None) -> None:
    from app.auth.obid import ObIdIdentityError, normalize_ob_id

    with pytest.raises(ObIdIdentityError, match="missing or invalid"):
        normalize_ob_id(value)


@pytest.mark.asyncio
async def test_obid_provider_maps_stable_internal_identity(identity_database) -> None:
    from app.auth.identity_repository import IdentityRepository
    from app.auth.obid import ObIdIdentityProvider
    from app.db.models import IdentityMappingRecord, UserRecord

    first = ObIdIdentityProvider(IdentityRepository(identity_database))
    second = ObIdIdentityProvider(IdentityRepository(identity_database))

    first_identity = await first.resolve(_request(" 00123 "))
    second_identity = await second.resolve(_request("00123"))

    assert first_identity == second_identity
    assert first_identity.user_id != "00123"
    assert first_identity.external_subject == "00123"
    assert first_identity.issuer == "davinci"
    async with identity_database.session() as session:
        user = await session.get(UserRecord, first_identity.user_id)
        mapping = (
            await session.scalars(
                select(IdentityMappingRecord).where(
                    IdentityMappingRecord.issuer == "davinci",
                    IdentityMappingRecord.subject == "obid:00123",
                )
            )
        ).one()
    assert user is not None and user.provider == "obid"
    assert user.external_subject.startswith("obid:")
    assert "00123" not in user.external_subject
    assert mapping.user_id == user.id


@pytest.mark.asyncio
async def test_obid_provider_has_no_bootstrap_identity(identity_database) -> None:
    from app.auth.identity_repository import IdentityRepository
    from app.auth.obid import ObIdIdentityProvider

    provider = ObIdIdentityProvider(IdentityRepository(identity_database))

    assert await provider.resolve_bootstrap_identity() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "actor/1"])
async def test_obid_identity_failure_is_stable_401(
    settings_factory, value: str | None
) -> None:
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    from app.auth.identity_repository import IdentityRepository
    from app.auth.obid import ObIdIdentityProvider
    from app.db.base import Database

    app_database = Database(settings.resolved_database_url)
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        identity_provider=ObIdIdentityProvider(IdentityRepository(app_database)),
    )
    await app_database.initialize()
    try:
        headers = {} if value is None else {"X-Davinci-ObId": value}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/me", headers=headers)
    finally:
        await app_database.dispose()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_missing"
