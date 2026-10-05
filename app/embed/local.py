from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from app.auth.davinci_passthrough import (
    DavinciBootstrapGrant,
    DavinciObIdIdentityProvider,
    DavinciPassthroughIdentityProvider,
)
from app.auth.obid import normalize_ob_id
from app.errors import AppError
from app.preferences import get_preferences
from app.web.routes import embed_asset_revision, templates

logger = logging.getLogger(__name__)

router = APIRouter()
_v2_contract_path = Path(CONTRACT_PATH).with_name("davinci-agent-v2.json")
_profiles = {
    "1.0": (
        load_contract_registry(),
        hashlib.sha256(Path(CONTRACT_PATH).read_bytes()).hexdigest(),
    ),
    "agui-native-v2": (
        load_contract_registry(_v2_contract_path),
        hashlib.sha256(_v2_contract_path.read_bytes()).hexdigest(),
    ),
}
_davinci_proxy_allowlist = {
    ("GET", "/api/v3/users/currentUser"),
    ("POST", "/api/v3/dataMarket/list"),
    ("POST", "/api/v3/dataMarket/selectableDataset/exactList"),
    ("POST", "/api/v3/dataMarket/batchAuthorizeDatasets"),
    ("POST", "/api/v3/dataMarket/product/auth/scope/list"),
}
_davinci_session_providers = (
    DavinciObIdIdentityProvider,
    DavinciPassthroughIdentityProvider,
)


@dataclass(frozen=True)
class LocalBootstrapRecord:
    ob_id: str
    parent_origin: str
    protocol_version: str
    contract_version: str
    contract_digest: str
    expires_at: datetime
    davinci_grant: DavinciBootstrapGrant | None = field(default=None, repr=False)


class LocalBootstrapStore:
    def __init__(self) -> None:
        self._codes: dict[str, LocalBootstrapRecord] = {}

    def issue(
        self,
        parent_origin: str,
        protocol_version: str,
        contract_version: str,
        contract_digest: str,
        ob_id: str,
        davinci_grant: DavinciBootstrapGrant | None = None,
    ) -> tuple[str, datetime]:
        code = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=60)
        self._codes[code] = LocalBootstrapRecord(
            ob_id=normalize_ob_id(ob_id),
            parent_origin=parent_origin,
            protocol_version=protocol_version,
            contract_version=contract_version,
            contract_digest=contract_digest,
            expires_at=expires_at,
            davinci_grant=davinci_grant,
        )
        return code, expires_at

    def consume(
        self,
        code: str,
        parent_origin: str,
        protocol_version: str,
        contract_version: str,
        contract_digest: str,
    ) -> LocalBootstrapRecord:
        record = self._codes.pop(code, None)
        if (
            record is None
            or record.parent_origin != parent_origin
            or record.protocol_version != protocol_version
            or record.contract_version != contract_version
            or record.contract_digest != contract_digest
            or record.expires_at <= datetime.now(UTC)
        ):
            raise AppError("SESSION_MISMATCH", "Local bootstrap code is invalid.", 409)
        return record


def _exact_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AppError("invalid_request", "parentOrigin is invalid.", 422)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise AppError("invalid_request", "parentOrigin must be an exact origin.", 422)
    return f"{parsed.scheme}://{parsed.netloc}"


def _require_matching_cookie_origin(request: Request, parent_origin: str) -> None:
    """Reject browser-cookie capture unless Fetch origin matches the bound parent."""
    try:
        request_origin = _exact_origin(request.headers.get("Origin", ""))
    except AppError as exc:
        raise AppError(
            "ORIGIN_REJECTED",
            "Browser session bootstrap origin is missing.",
            403,
        ) from exc
    if request_origin != parent_origin:
        raise AppError(
            "ORIGIN_REJECTED",
            "Browser session bootstrap origin does not match.",
            403,
        )


@router.post("/agent-api/session/bootstrap")
async def local_bootstrap(request: Request) -> Response:
    settings = request.app.state.services.settings
    if not settings.davinci_local_integration:
        return Response(status_code=204)
    body = await request.json()
    parent_origin = _exact_origin(str(body.get("parentOrigin", "")))
    if parent_origin not in settings.davinci_local_parent_origins:
        raise AppError("ORIGIN_REJECTED", "Local Davinci origin is not allowed.", 403)
    protocol_version = body.get("protocolVersion")
    profile = _profiles.get(protocol_version)
    if profile is None:
        raise AppError("CONTRACT_MISMATCH", "Protocol version does not match.", 409)
    registry, contract_digest = profile
    provider = request.app.state.services.identity_provider
    davinci_grant = None
    authorization = request.headers.get("Authorization")
    cookie = request.headers.get("Cookie")
    if (cookie or "").strip():
        # A browser cookie is the authoritative same-origin Davinci session when
        # both credential forms are present, so validate its capture boundary first.
        _require_matching_cookie_origin(request, parent_origin)
    if isinstance(provider, DavinciPassthroughIdentityProvider):
        logger.info(
            "Davinci bootstrap credentials authorization=%s cookie=%s",
            bool((authorization or "").strip()),
            bool((cookie or "").strip()),
        )
        try:
            davinci_grant = await provider.authenticate(
                authorization,
                cookie=cookie,
                browser_headers=request.headers,
            )
        except LookupError as exc:
            logger.warning("Davinci bootstrap rejected reason=%s", exc)
            raise AppError(
                "AUTHENTICATION_REQUIRED",
                "Davinci login session is invalid.",
                401,
            ) from exc
        ob_id = davinci_grant.ob_id
    else:
        try:
            ob_id = normalize_ob_id(body.get("obId"))
        except LookupError as exc:
            raise AppError("invalid_request", "obId is invalid.", 422) from exc
        if isinstance(provider, DavinciObIdIdentityProvider) and (
            (authorization or "").strip() or (cookie or "").strip()
        ):
            try:
                await provider.capture_browser_session(
                    ob_id,
                    authorization,
                    cookie=cookie,
                    browser_headers=request.headers,
                )
            except LookupError as exc:
                raise AppError(
                    "AUTHENTICATION_REQUIRED",
                    "Davinci browser session is invalid.",
                    401,
                ) from exc
    code, expires_at = request.app.state.local_davinci_bootstrap.issue(
        parent_origin,
        registry.protocol_version,
        registry.contract_version,
        contract_digest,
        ob_id,
        davinci_grant,
    )
    # The launcher renders only after this reply, so shipping the saved position
    # here puts the button in place on first paint without another round trip.
    preferences = await _saved_preferences(request, davinci_grant)
    return JSONResponse(
        {
            "enabled": True,
            "embedUrl": f"{settings.davinci_local_public_origin.rstrip('/')}/embed/local",
            "bootstrapCode": code,
            "expiresAt": expires_at.isoformat(),
            "protocolVersion": registry.protocol_version,
            "contractVersion": registry.contract_version,
            "contractDigest": contract_digest,
            "launcher": preferences.get("launcher"),
        }
    )


async def _saved_preferences(
    request: Request, grant: DavinciBootstrapGrant | None
) -> dict:
    """UI choices of the verified Davinci user; the ObId mock mode has no user."""
    if grant is None:
        return {}
    return await get_preferences(
        request.app.state.services.database, grant.identity.user_id
    )


@router.api_route(
    "/agent-api/davinci/{path:path}",
    methods=["GET", "POST"],
)
async def proxy_davinci_for_mcp(path: str, request: Request) -> Response:
    settings = request.app.state.services.settings
    provider = request.app.state.services.identity_provider
    upstream_path = f"/{path.lstrip('/')}"
    if (
        not settings.davinci_local_integration
        or not isinstance(provider, _davinci_session_providers)
        or (request.method, upstream_path) not in _davinci_proxy_allowlist
    ):
        raise AppError("not_found", "Davinci proxy route was not found.", 404)
    try:
        response = await provider.request_as_session(
            request.headers.get("Authorization"),
            request.method,
            upstream_path,
            query=request.url.query,
            content=await request.body(),
            content_type=request.headers.get("Content-Type"),
            accept=request.headers.get("Accept"),
        )
    except LookupError as exc:
        raise AppError(
            "AUTHENTICATION_REQUIRED",
            "Davinci Host session is invalid.",
            401,
        ) from exc
    except httpx.RequestError as exc:
        logger.warning(
            "Davinci proxy transport failed error=%s",
            type(exc).__name__,
        )
        raise AppError(
            "UPSTREAM_UNAVAILABLE",
            "Davinci proxy request failed.",
            502,
        ) from exc
    logger.info(
        "Davinci proxy upstream method=%s path=%s status=%s",
        request.method,
        upstream_path,
        response.status_code,
    )
    headers = {}
    if content_type := response.headers.get("Content-Type"):
        headers["Content-Type"] = content_type
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=headers,
    )


@router.post("/embed/local", response_class=HTMLResponse)
async def local_embed(request: Request) -> HTMLResponse:
    settings = request.app.state.services.settings
    if not settings.davinci_local_integration:
        raise AppError("not_found", "Local Davinci integration is disabled.", 404)
    form = await request.form()
    parent_origin = _exact_origin(str(form.get("parentOrigin", "")))
    protocol_version = str(form.get("protocolVersion", ""))
    profile = _profiles.get(protocol_version)
    if profile is None:
        raise AppError("CONTRACT_MISMATCH", "Embed contract does not match.", 409)
    registry, contract_digest = profile
    if (
        form.get("contractVersion") != registry.contract_version
        or form.get("contractDigest") != contract_digest
    ):
        raise AppError("CONTRACT_MISMATCH", "Embed contract does not match.", 409)
    record = request.app.state.local_davinci_bootstrap.consume(
        str(form.get("bootstrapCode", "")),
        parent_origin,
        protocol_version,
        registry.contract_version,
        contract_digest,
    )
    embed_config = {
        "parentOrigin": parent_origin,
        "protocolVersion": registry.protocol_version,
        "contractVersion": registry.contract_version,
        "contractDigest": contract_digest,
        "obId": record.ob_id,
        "selectableModels": list(settings.claude_selectable_models),
        "defaultModel": settings.claude_model,
        "defaultEffort": settings.claude_default_effort,
    }
    if record.davinci_grant is not None:
        provider = request.app.state.services.identity_provider
        if not isinstance(provider, DavinciPassthroughIdentityProvider):
            raise AppError("SESSION_MISMATCH", "Davinci session provider changed.", 409)
        embed_config["sessionToken"] = provider.issue_session(record.davinci_grant)
    preferences = await _saved_preferences(request, record.davinci_grant)
    if "panelSize" in preferences:
        embed_config["panelSize"] = preferences["panelSize"]
    return templates.TemplateResponse(
        request=request,
        name="embed.html",
        context={
            "app_name": "Workspace Agent",
            "embed_config": embed_config,
            "asset_revision": embed_asset_revision(),
            "security_marker": settings.security_marker,
        },
    )
