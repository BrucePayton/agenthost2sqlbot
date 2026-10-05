import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agui.artifact_routes import router as artifact_router
from app.agui.bridge import FrontendToolBridgeRegistry
from app.agui.routes import router as agui_router
from app.api.routes import router as api_router
from app.auth.provider import IdentityProvider
from app.bootstrap import build_app_services, initialize_app_services
from app.config import Settings
from app.dashboard_layout.routes import router as dashboard_layout_router
from app.data_mcp.routes import router as data_agent_router
from app.debug.routes import router as debug_router
from app.embed.local import LocalBootstrapStore
from app.embed.local import router as local_davinci_router
from app.errors import AppError
from app.inspector.routes import router as inspector_router
from app.instructions.routes import router as instructions_router
from app.runtime.base import AgentRuntime
from app.skills.routes import router as skills_router
from app.web.routes import WEB_ROOT
from app.web.routes import router as web_router

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    runtime: AgentRuntime | None = None,
    identity_provider: IdentityProvider | None = None,
    frontend_tool_bridges: FrontendToolBridgeRegistry | None = None,
    davinci_auth_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    services = build_app_services(
        resolved_settings,
        runtime=runtime,
        identity_provider=identity_provider,
        frontend_tool_bridges=frontend_tool_bridges,
        davinci_auth_client=davinci_auth_client,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with initialize_app_services(services):
            app.state.services = services
            yield

    app = FastAPI(
        title="Claude Workspace Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.local_davinci_bootstrap = LocalBootstrapStore()
    app.state.snapshot_artifacts = services.snapshot_artifacts

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if 0 < len(incoming) <= 64 else str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.envelope(_request_id(request)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        error = AppError("invalid_request", "Request validation failed.", 422)
        return JSONResponse(
            status_code=error.status_code,
            content=error.envelope(_request_id(request)),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        error = AppError(
            "not_found" if exc.status_code == 404 else "http_error",
            str(exc.detail),
            exc.status_code,
        )
        return JSONResponse(
            status_code=error.status_code,
            content=error.envelope(_request_id(request)),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled request error", exc_info=exc)
        error = AppError("internal_error", "An unexpected error occurred.", 500)
        return JSONResponse(
            status_code=500, content=error.envelope(_request_id(request))
        )

    app.include_router(agui_router)
    app.include_router(dashboard_layout_router)
    app.include_router(data_agent_router)
    app.include_router(artifact_router)
    app.include_router(api_router)
    app.include_router(skills_router)
    app.include_router(instructions_router)
    app.include_router(local_davinci_router)
    if (
        resolved_settings.app_env == "development"
        and resolved_settings.identity_mode == "obid"
    ):
        app.include_router(debug_router)
    if resolved_settings.session_inspector_enabled:
        app.include_router(inspector_router)
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")
    app.include_router(web_router)
    return app


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


app = create_app()
