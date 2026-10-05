from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import Request

from app.auth.identity_repository import IdentityRepository
from app.auth.models import IdentityContext
from app.auth.obid import ObIdIdentityProvider, normalize_ob_id
from app.memory.scopes import user_scope_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DavinciBootstrapGrant:
    identity: IdentityContext
    ob_id: str
    credential: DavinciUpstreamCredential = field(repr=False)


@dataclass(frozen=True, slots=True)
class DavinciUpstreamCredential:
    authorization: str | None = field(default=None, repr=False)
    cookie: str | None = field(default=None, repr=False)
    browser_headers: tuple[tuple[str, str], ...] = field(
        default=(),
        repr=False,
    )

    def headers(self) -> dict[str, str]:
        headers = dict(self.browser_headers)
        if self.authorization is not None:
            # The shared HTTP client has a cookie jar.  An explicit empty Cookie
            # header prevents a previous browser session from leaking into a
            # bearer-authenticated request for another user.
            headers.update({"Authorization": self.authorization, "Cookie": ""})
            return headers
        if self.cookie is not None:
            headers["Cookie"] = self.cookie
            return headers
        raise LookupError("Davinci credential is missing")


@dataclass(frozen=True, slots=True)
class _DavinciSession:
    identity: IdentityContext
    ob_id: str
    credential: DavinciUpstreamCredential = field(repr=False)
    owner_key: str
    expires_at: datetime


class DavinciSessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._sessions: dict[str, _DavinciSession] = {}
        self._latest_by_owner: dict[str, str] = {}

    def issue(self, grant: DavinciBootstrapGrant) -> str:
        self._purge_expired()
        token = secrets.token_urlsafe(32)
        owner_key = user_scope_key(grant.identity.user_id)
        self._sessions[token] = _DavinciSession(
            identity=grant.identity,
            ob_id=grant.ob_id,
            credential=grant.credential,
            owner_key=owner_key,
            expires_at=datetime.now(UTC) + self._ttl,
        )
        self._latest_by_owner[owner_key] = token
        return token

    def resolve(self, token: str) -> _DavinciSession:
        self._purge_expired()
        record = self._sessions.get(token)
        if record is None:
            raise LookupError("Davinci Host session is missing or expired")
        return record

    def authorization_for_owner(self, owner_key: str) -> str:
        self._purge_expired()
        token = self._latest_by_owner.get(owner_key)
        if token is None:
            raise LookupError("Davinci credential is unavailable for this user")
        self.resolve(token)
        return token

    def ob_id_for_owner(self, owner_key: str) -> str:
        """从仍有效的登录会话取得真实 OB 账号，保留账号前导零。"""
        return self.resolve(self.authorization_for_owner(owner_key)).ob_id

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            token
            for token, record in self._sessions.items()
            if record.expires_at <= now
        ]
        for token in expired:
            record = self._sessions.pop(token)
            if self._latest_by_owner.get(record.owner_key) == token:
                self._latest_by_owner.pop(record.owner_key, None)


class DavinciPassthroughIdentityProvider:
    """Validate Davinci login credentials and issue opaque Host sessions."""

    def __init__(
        self,
        identities: IdentityRepository,
        client: httpx.AsyncClient,
        *,
        current_user_path: str,
        session_ttl_seconds: int,
        owns_client: bool = False,
    ) -> None:
        self.identities = identities
        self.client = client
        self.current_user_path = current_user_path
        self.sessions = DavinciSessionStore(session_ttl_seconds)
        self.owns_client = owns_client

    async def authenticate(
        self,
        authorization: str | None,
        *,
        cookie: str | None = None,
        browser_headers: Mapping[str, str] | None = None,
    ) -> DavinciBootstrapGrant:
        credential = _upstream_credential(
            authorization,
            cookie,
            browser_headers=browser_headers,
        )
        credential_kind = "bearer" if credential.authorization else "cookie"
        try:
            response = await self.client.get(
                self.current_user_path,
                headers=credential.headers(),
            )
        except httpx.RequestError as exc:
            logger.warning(
                "Davinci current-user verification unavailable credential=%s",
                credential_kind,
            )
            raise LookupError("Davinci identity verification is unavailable") from exc
        logger.info(
            "Davinci current-user verification credential=%s status=%s redirect=%s",
            credential_kind,
            response.status_code,
            response.is_redirect,
        )
        if (
            (response.status_code in {400, 401, 403} or response.is_redirect)
            and credential.cookie is not None
            and (authorization or "").strip()
        ):
            logger.info(
                "Davinci current-user cookie rejected; retrying verified bearer"
            )
            credential = _upstream_credential(
                authorization,
                None,
                browser_headers=browser_headers,
            )
            credential_kind = "bearer"
            try:
                response = await self.client.get(
                    self.current_user_path,
                    headers=credential.headers(),
                )
            except httpx.RequestError as exc:
                logger.warning(
                    "Davinci current-user verification unavailable credential=%s",
                    credential_kind,
                )
                raise LookupError(
                    "Davinci identity verification is unavailable"
                ) from exc
            logger.info(
                "Davinci current-user verification credential=%s status=%s redirect=%s",
                credential_kind,
                response.status_code,
                response.is_redirect,
            )
        if (
            response.status_code in {400, 401, 403}
            or response.is_redirect
            or response.is_error
        ):
            raise LookupError("Davinci rejected the current login session")
        try:
            envelope = response.json()
        except ValueError as exc:
            raise LookupError("Davinci returned invalid current-user data") from exc
        user = _current_user(envelope)
        ob_id = normalize_ob_id(str(user.get("obId") or user.get("account") or ""))
        if user.get("active") is False:
            raise LookupError("Davinci current user is inactive")
        display_name = str(user.get("name") or user.get("userName") or ob_id).strip()
        resolved = await self.identities.resolve_or_create(
            "davinci",
            f"obid:{ob_id}",
            {"name": display_name},
            provider="davinci_passthrough",
        )
        identity = IdentityContext(
            user_id=resolved.user_id,
            external_subject=ob_id,
            display_name=resolved.display_name,
            issuer="davinci",
        )
        return DavinciBootstrapGrant(identity, ob_id, credential)

    def issue_session(self, grant: DavinciBootstrapGrant) -> str:
        return self.sessions.issue(grant)

    async def resolve(self, request: Request) -> IdentityContext:
        token = _bearer_token(request.headers.get("Authorization"))
        return self.sessions.resolve(token).identity

    async def resolve_bootstrap_identity(self) -> IdentityContext | None:
        return None

    def authorization_for_owner(self, owner_key: str) -> str:
        return self.sessions.authorization_for_owner(owner_key)

    def ob_id_for_owner(self, owner_key: str) -> str:
        """为 MCP 工具参数提供当前登录用户的 OB 账号。"""
        return self.sessions.ob_id_for_owner(owner_key)

    async def request_as_session(
        self,
        authorization: str | None,
        method: str,
        path: str,
        *,
        query: str,
        content: bytes,
        content_type: str | None,
        accept: str | None,
    ) -> httpx.Response:
        token = _bearer_token(authorization)
        record = self.sessions.resolve(token)
        headers = record.credential.headers()
        if content_type:
            headers["Content-Type"] = content_type
        if accept:
            headers["Accept"] = accept
        return await self.client.request(
            method,
            path,
            headers=headers,
            content=content,
            params=query,
        )

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()


class DavinciObIdIdentityProvider(ObIdIdentityProvider):
    """Keep OBID identity while brokering browser credentials for Davinci MCP."""

    def __init__(
        self,
        identities: IdentityRepository,
        client: httpx.AsyncClient,
        *,
        session_ttl_seconds: int,
        owns_client: bool = False,
    ) -> None:
        super().__init__(identities)
        self.client = client
        self.sessions = DavinciSessionStore(session_ttl_seconds)
        self.owns_client = owns_client

    async def capture_browser_session(
        self,
        ob_id: str,
        authorization: str | None,
        *,
        cookie: str | None = None,
        browser_headers: Mapping[str, str] | None = None,
    ) -> str:
        """Bind a browser credential to the same canonical identity used by OBID."""
        identity = await self.resolve_ob_id(ob_id)
        credential = _upstream_credential(
            authorization,
            cookie,
            browser_headers=browser_headers,
        )
        return self.sessions.issue(
            DavinciBootstrapGrant(identity, identity.external_subject, credential)
        )

    def authorization_for_owner(self, owner_key: str) -> str:
        """Return the opaque Host token for the owner's latest browser session."""
        return self.sessions.authorization_for_owner(owner_key)

    def ob_id_for_owner(self, owner_key: str) -> str:
        """为 MCP 工具参数提供已绑定浏览器会话的 OB 账号。"""
        return self.sessions.ob_id_for_owner(owner_key)

    async def request_as_session(
        self,
        authorization: str | None,
        method: str,
        path: str,
        *,
        query: str,
        content: bytes,
        content_type: str | None,
        accept: str | None,
    ) -> httpx.Response:
        """Proxy one allowlisted Davinci request with the captured browser session."""
        token = _bearer_token(authorization)
        record = self.sessions.resolve(token)
        headers = record.credential.headers()
        if content_type:
            headers["Content-Type"] = content_type
        if accept:
            headers["Accept"] = accept
        return await self.client.request(
            method,
            path,
            headers=headers,
            content=content,
            params=query,
        )

    async def aclose(self) -> None:
        """Close the owned Davinci gateway client during Host shutdown."""
        if self.owns_client:
            await self.client.aclose()


def _bearer_token(authorization: str | None) -> str:
    scheme, separator, token = (authorization or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise LookupError("Bearer token is required")
    return token.strip()


def _upstream_credential(
    authorization: str | None,
    cookie: str | None,
    *,
    browser_headers: Mapping[str, str] | None = None,
) -> DavinciUpstreamCredential:
    forwarded = _forwarded_browser_headers(browser_headers)
    cookie_value = (cookie or "").strip()
    if cookie_value:
        if len(cookie_value) > 16_384:
            raise LookupError("Davinci browser session cookie is invalid")
        # The same-origin browser cookie reflects the session accepted by the
        # Davinci gateway; SPA bearer state can be stale during local integration.
        return DavinciUpstreamCredential(
            cookie=cookie_value,
            browser_headers=forwarded,
        )
    if (authorization or "").strip():
        return DavinciUpstreamCredential(
            authorization=f"Bearer {_bearer_token(authorization)}",
            browser_headers=forwarded,
        )
    raise LookupError("Davinci browser session cookie is required")


def _forwarded_browser_headers(
    headers: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    """Retain only browser metadata needed by the same-origin Davinci gateway."""
    if headers is None:
        return ()
    allowed = {
        "accept-language",
        "origin",
        "referer",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "user-agent",
        "x-requested-with",
    }
    return tuple(
        (name, value)
        for name, value in headers.items()
        if name.lower() in allowed and value.strip()
    )


def _current_user(envelope: object) -> Mapping[str, object]:
    if not isinstance(envelope, Mapping):
        # Invalid upstream data is an authentication failure, not a caller type error.
        raise LookupError("Davinci current-user response was rejected")  # noqa: TRY004
    header = envelope.get("header")
    code = header.get("code") if isinstance(header, Mapping) else envelope.get("code")
    if code is not None and code not in {0, 200}:
        logger.warning(
            "Davinci current-user response code rejected code=%r type=%s",
            code,
            type(code).__name__,
        )
        raise LookupError("Davinci current-user response was rejected")
    user = envelope.get("payload", envelope.get("data"))
    if not isinstance(user, Mapping):
        # This is an authentication failure caused by an invalid upstream
        # response, rather than a caller passing the wrong Python type.
        raise LookupError("Davinci current user is missing")  # noqa: TRY004
    if not str(user.get("obId") or user.get("account") or "").strip():
        raise LookupError("Davinci current user has no OBID")
    return user
