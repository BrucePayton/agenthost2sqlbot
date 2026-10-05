import asyncio
import json
import time
from collections.abc import Mapping

import httpx
import jwt
from fastapi import Request
from jwt.algorithms import RSAAlgorithm

from app.auth.identity_repository import IdentityRepository
from app.auth.models import IdentityContext
from app.config import Settings


class IdentityVerificationError(LookupError):
    pass


class OidcIdentityProvider:
    def __init__(
        self,
        settings: Settings,
        identities: IdentityRepository,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.identities = identities
        self.http_client = http_client
        self._keys: dict[str, object] = {}
        self._keys_loaded_at = 0.0
        self._refresh_lock = asyncio.Lock()

    async def resolve(self, request: Request) -> IdentityContext:
        token = self._bearer_token(request)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityVerificationError("invalid token header") from exc
        if header.get("alg") != "RS256":
            raise IdentityVerificationError("token algorithm is not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise IdentityVerificationError("token key id is missing")

        key = await self._key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=str(self.settings.oidc_issuer),
                audience=self.settings.oidc_audience,
                leeway=self.settings.oidc_clock_skew_seconds,
                options={"require": ["iss", "aud", "exp", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise IdentityVerificationError("token expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise IdentityVerificationError("token is not yet valid") from exc
        except jwt.InvalidIssuerError as exc:
            raise IdentityVerificationError("token issuer validation failed") from exc
        except jwt.InvalidAudienceError as exc:
            raise IdentityVerificationError("token audience validation failed") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise IdentityVerificationError(
                f"token {exc.claim} claim is missing"
            ) from exc
        except jwt.PyJWTError as exc:
            raise IdentityVerificationError("token signature validation failed") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise IdentityVerificationError("token subject is missing")
        profile = {
            key: value
            for key in ("name", "email")
            if isinstance((value := claims.get(key)), str) and value
        }
        resolved = await self.identities.resolve_or_create(
            str(self.settings.oidc_issuer), subject, profile, provider="oidc"
        )
        return IdentityContext(
            user_id=resolved.user_id,
            external_subject=subject,
            display_name=resolved.display_name,
            issuer=str(self.settings.oidc_issuer),
            email=profile.get("email"),
        )

    async def resolve_bootstrap_identity(self) -> IdentityContext | None:
        return None

    @staticmethod
    def _bearer_token(request: Request) -> str:
        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise IdentityVerificationError("Bearer token is required")
        return token.strip()

    async def _key_for(self, kid: str) -> object:
        expired = (
            time.monotonic() - self._keys_loaded_at
            >= self.settings.oidc_jwks_ttl_seconds
        )
        if expired or not self._keys:
            await self._refresh_keys()
        key = self._keys.get(kid)
        if key is None:
            await self._refresh_keys(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise IdentityVerificationError("token key id is unknown")
        return key

    async def _refresh_keys(self, *, force: bool = False) -> None:
        async with self._refresh_lock:
            cache_fresh = (
                self._keys
                and time.monotonic() - self._keys_loaded_at
                < self.settings.oidc_jwks_ttl_seconds
            )
            if cache_fresh and not force:
                return
            payload = await self._fetch_jwks()
            raw_keys = payload.get("keys")
            if not isinstance(raw_keys, list):
                raise IdentityVerificationError("JWKS response has no keys")
            parsed: dict[str, object] = {}
            for item in raw_keys:
                if not isinstance(item, Mapping):
                    continue
                kid = item.get("kid")
                if item.get("kty") != "RSA" or not isinstance(kid, str):
                    continue
                try:
                    parsed[kid] = RSAAlgorithm.from_jwk(json.dumps(dict(item)))
                except (KeyError, ValueError) as exc:
                    raise IdentityVerificationError("JWKS key is invalid") from exc
            self._keys = parsed
            self._keys_loaded_at = time.monotonic()

    async def _fetch_jwks(self) -> dict[str, object]:
        try:
            if self.http_client is not None:
                response = await self.http_client.get(str(self.settings.oidc_jwks_uri))
            else:
                timeout = httpx.Timeout(
                    connect=self.settings.oidc_connect_timeout_seconds,
                    read=self.settings.oidc_read_timeout_seconds,
                    write=self.settings.oidc_read_timeout_seconds,
                    pool=self.settings.oidc_connect_timeout_seconds,
                )
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(str(self.settings.oidc_jwks_uri))
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IdentityVerificationError("OIDC JWKS is unavailable") from exc
        if not isinstance(payload, dict):
            raise IdentityVerificationError("OIDC JWKS is invalid")
        return payload
