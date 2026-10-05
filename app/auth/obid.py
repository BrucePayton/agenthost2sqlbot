import re

from fastapi import Request

from app.auth.identity_repository import IdentityRepository
from app.auth.models import IdentityContext

OBID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ObIdIdentityError(LookupError):
    pass


def normalize_ob_id(value: str | None) -> str:
    canonical = (value or "").strip()
    if OBID_PATTERN.fullmatch(canonical) is None:
        raise ObIdIdentityError("missing or invalid Davinci OBID")
    return canonical


class ObIdIdentityProvider:
    def __init__(self, identities: IdentityRepository) -> None:
        self.identities = identities

    async def resolve(self, request: Request) -> IdentityContext:
        return await self.resolve_ob_id(request.headers.get("X-Davinci-ObId"))

    async def resolve_ob_id(self, value: str | None) -> IdentityContext:
        """Resolve one normalized Davinci OBID into the canonical local identity."""
        ob_id = normalize_ob_id(value)
        resolved = await self.identities.resolve_or_create(
            "davinci",
            f"obid:{ob_id}",
            {"name": f"Davinci {ob_id}"},
            provider="obid",
        )
        return IdentityContext(
            user_id=resolved.user_id,
            external_subject=ob_id,
            display_name=resolved.display_name,
            issuer="davinci",
            email=None,
        )

    async def resolve_bootstrap_identity(self) -> IdentityContext | None:
        return None
