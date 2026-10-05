import hashlib
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

WEB_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=WEB_ROOT / "templates")
router = APIRouter()
_EMBED_STATIC_ASSETS = ("embed.css", "embed.js", "skill-manager.js")


def asset_revision(*asset_names: str) -> str:
    """Digest static assets so a template can cache-bust its own scripts.

    `/static` is mounted with the default caching, so a browser happily keeps
    an old script next to a freshly rendered no-store page — which is how the
    inspector once shipped a new template driven by stale JavaScript.
    """
    digest = hashlib.sha256()
    for asset_name in asset_names:
        digest.update(asset_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update((WEB_ROOT / "static" / asset_name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def embed_asset_revision() -> str:
    return asset_revision(*_EMBED_STATIC_ASSETS)


@router.get("/", response_class=HTMLResponse)
async def workbench(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": "Workspace Agent",
            "asset_revision": asset_revision("app.css", "app.js"),
            "max_files_per_turn": request.app.state.services.settings.max_files_per_turn,
            "debug_identity_enabled": (
                request.app.state.services.settings.app_env == "development"
                and request.app.state.services.settings.identity_mode == "obid"
            ),
        },
    )


@router.get("/data-agents", response_class=HTMLResponse)
async def data_agents(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="data-agents.html",
        context={
            "asset_revision": asset_revision("data-agents.css", "data-agents.js", "data-agent-chat.js", "vendor/g2.min.js"),
        },
    )


@router.get("/embed", response_class=HTMLResponse)
async def embedded_agent(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="embed.html",
        context={
            "app_name": "Workspace Agent",
            "embed_config": None,
            "asset_revision": embed_asset_revision(),
            "security_marker": request.app.state.services.settings.security_marker,
        },
    )
