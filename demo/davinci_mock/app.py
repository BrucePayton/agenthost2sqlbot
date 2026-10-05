import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

MOCK_ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=MOCK_ROOT / "templates")


def _validate_agent_origin(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("agent_origin must be a loopback HTTP origin")
    if parsed.port is None:
        raise ValueError("agent_origin must include a port")
    return value.rstrip("/")


def create_mock_app(
    agent_origin: str = "http://127.0.0.1:8000",
) -> FastAPI:
    resolved_agent_origin = _validate_agent_origin(agent_origin)
    app = FastAPI(title="Mock Davinci", version="0.1.0")
    app.mount(
        "/static",
        StaticFiles(directory=MOCK_ROOT / "static"),
        name="davinci-static",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    async def shell(request: Request) -> HTMLResponse:
        mock_origin = f"{request.url.scheme}://{request.url.netloc}"
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "agent_origin": resolved_agent_origin,
                "mock_origin": mock_origin,
                "nonce": uuid.uuid4().hex,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        return await shell(request)

    @app.get("/dashboard/{dashboard_id}", response_class=HTMLResponse)
    async def dashboard(request: Request, dashboard_id: str) -> HTMLResponse:
        if dashboard_id != "1024":
            raise HTTPException(status_code=404, detail="Dashboard not found")
        return await shell(request)

    @app.get("/datasets", response_class=HTMLResponse)
    async def datasets(request: Request) -> HTMLResponse:
        return await shell(request)

    return app


app = create_mock_app(
    os.getenv("DAVINCI_AGENT_ORIGIN", "http://127.0.0.1:8000")
)
