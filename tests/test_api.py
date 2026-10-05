import asyncio
import json
import shutil
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.test_workspaces import write_workspace


@asynccontextmanager
async def api_client(
    settings_factory, *, delay=0, settings_overrides: dict[str, object] | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(**(settings_overrides or {}))
    if not settings.workspaces_root.joinpath("actual").exists():
        write_workspace(settings.workspaces_root, "actual")
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(chunks=("hello", " world"), delay_seconds=delay),
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client


class ForeignResourceIdentityProvider:
    async def resolve(self, request):
        from app.auth.models import IdentityContext

        match request.headers.get("X-Test-User"):
            case "outsider":
                return IdentityContext("outsider", "outsider", "Outsider")
            case "missing":
                raise LookupError("identity missing")
            case _:
                return IdentityContext("owner", "owner", "Owner")

    async def resolve_bootstrap_identity(self):
        from app.auth.models import IdentityContext

        return IdentityContext("owner", "owner", "Owner")


@pytest.fixture
async def foreign_resource_client(settings_factory) -> AsyncIterator[httpx.AsyncClient]:
    from app.db.models import (
        AttachmentRecord,
        SessionRecord,
        TurnRecord,
        UserRecord,
    )
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from app.sessions.snapshot import build_session_snapshot
    from app.workspaces.materializer import materialize_session_workspace

    settings = settings_factory(
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner", "team": "owner"},
    )
    write_workspace(settings.workspaces_root, "actual")
    write_workspace(settings.workspaces_root, "team")
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        identity_provider=ForeignResourceIdentityProvider(),
    )
    async with app.router.lifespan_context(app):
        now = datetime.now(UTC)
        entry = app.state.services.workspaces.get("team")
        snapshot = build_session_snapshot(entry, ())
        materialized = materialize_session_workspace(
            entry,
            "foreign",
            settings.app_data_dir,
            snapshot,
            (),
        )
        attachment_path = materialized.workspace_dir / "attachments" / "foreign.txt"
        attachment_path.write_bytes(b"foreign")
        async with app.state.services.database.session() as db:
            db.add(
                UserRecord(
                    id="outsider",
                    external_subject="outsider",
                    display_name="Outsider",
                    provider="mock",
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.flush()
            db.add(
                SessionRecord(
                    id="foreign",
                    workspace_id="team",
                    created_by="outsider",
                    title="Foreign session",
                    title_source="auto",
                    status="idle",
                    workspace_snapshot_json=materialized.snapshot_json,
                    workspace_snapshot_hash=materialized.snapshot_hash,
                    session_dir=materialized.relative_session_dir,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.flush()
            db.add(
                TurnRecord(
                    id="foreign-turn",
                    session_id="foreign",
                    client_request_id="foreign-request",
                    status="completed",
                    input_text="foreign",
                    created_at=now,
                )
            )
            await db.flush()
            db.add(
                AttachmentRecord(
                    id="foreign-attachment",
                    session_id="foreign",
                    turn_id=None,
                    status="pending",
                    original_filename="foreign.txt",
                    stored_filename="foreign.txt",
                    mime_type="text/plain",
                    size_bytes=7,
                    sha256="0" * 64,
                    relative_path="sessions/foreign/workspace/attachments/foreign.txt",
                    created_at=now,
                )
            )
            await db.commit()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client


async def _request_protected_route(
    client: httpx.AsyncClient, method: str, path: str, *, headers: dict[str, str]
) -> httpx.Response:
    if path.endswith("/attachments") and method == "POST":
        return await client.request(
            method,
            path,
            files=[("files", ("upload.txt", b"upload", "text/plain"))],
            headers=headers,
        )
    if path.endswith("/turns"):
        return await client.request(
            method,
            path,
            json={
                "message": "hello",
                "attachment_ids": [],
                "client_request_id": "foreign-client-request",
            },
            headers=headers,
        )
    if method == "PATCH":
        return await client.request(
            method, path, json={"title": "Foreign rename"}, headers=headers
        )
    return await client.request(method, path, json={}, headers=headers)


FOREIGN_RESOURCE_ROUTES = [
    ("GET", "/api/sessions/foreign"),
    ("PATCH", "/api/sessions/foreign"),
    ("DELETE", "/api/sessions/foreign"),
    ("GET", "/api/sessions/foreign/messages"),
    ("GET", "/api/sessions/foreign/context"),
    ("GET", "/api/sessions/foreign/skills"),
    ("GET", "/api/sessions/foreign/files"),
    ("GET", "/api/sessions/foreign/attachments"),
    ("POST", "/api/sessions/foreign/attachments"),
    ("POST", "/api/sessions/foreign/turns"),
    ("GET", "/api/turns/foreign-turn"),
    ("GET", "/api/turns/foreign-turn/events"),
    ("POST", "/api/turns/foreign-turn/cancel"),
    ("GET", "/api/attachments/foreign-attachment/content"),
    ("DELETE", "/api/attachments/foreign-attachment"),
]

WORKSPACE_OWNED_ROUTES = [
    ("GET", "/api/workspaces"),
    ("GET", "/api/workspaces/team"),
    ("GET", "/api/workspaces/team/sessions"),
    ("POST", "/api/workspaces/team/sessions"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), FOREIGN_RESOURCE_ROUTES)
async def test_existing_resource_routes_hide_foreign_workspace(
    foreign_resource_client, method: str, path: str
) -> None:
    response = await _request_protected_route(
        foreign_resource_client,
        method,
        path,
        headers={"X-Test-User": "outsider"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] in {
        "workspace_not_found",
        "session_not_found",
        "turn_not_found",
        "attachment_not_found",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), FOREIGN_RESOURCE_ROUTES)
async def test_same_workspace_routes_hide_another_creators_resources(
    foreign_resource_client, method: str, path: str
) -> None:
    response = await _request_protected_route(
        foreign_resource_client,
        method,
        path,
        headers={},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] in {
        "session_not_found",
        "turn_not_found",
        "attachment_not_found",
    }


@pytest.mark.asyncio
async def test_same_workspace_session_list_is_private_to_creator(
    foreign_resource_client,
) -> None:
    response = await foreign_resource_client.get("/api/workspaces/team/sessions")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), WORKSPACE_OWNED_ROUTES[1:])
async def test_workspace_routes_hide_foreign_workspace(
    foreign_resource_client, method: str, path: str
) -> None:
    response = await _request_protected_route(
        foreign_resource_client,
        method,
        path,
        headers={"X-Test-User": "outsider"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "workspace_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"), FOREIGN_RESOURCE_ROUTES + WORKSPACE_OWNED_ROUTES
)
async def test_workspace_owned_routes_require_identity(
    foreign_resource_client, method: str, path: str
) -> None:
    response = await _request_protected_route(
        foreign_resource_client,
        method,
        path,
        headers={"X-Test-User": "missing"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "identity_missing"


@pytest.mark.asyncio
async def test_health_and_workspace_api_do_not_expose_secrets(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        health = await client.get("/api/health")
        workspaces = await client.get("/api/workspaces")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ready",
        "database": "ok",
        "memory": "ok",
        "workspace_count": 1,
        "valid_workspace_count": 1,
        "identity_mode": "mock",
        "deployment_constraint": "configured",
        "runtime": {
            "mode": "local_inline",
            "cohort": "local",
            "image_digest": "local",
            "protocol_version": "1",
            "capabilities": [
                "resume",
                "interrupt",
                "auto_memory",
                "mcp",
                "skills",
            ],
            "dependencies": {
                "claude_agent_sdk": "0.2.128",
                "claude_cli": "bundled-with-sdk",
                "mcp_python_sdk": "1.29.0",
                "mcp_python_sdk_v2": False,
            },
        },
    }
    assert workspaces.status_code == 200
    assert workspaces.json()[0]["id"] == "actual"
    assert workspaces.json()[0]["available"] is True
    assert workspaces.json()[0]["skill_count"] == 1
    assert workspaces.json()[0]["can_manage_global_skills"] is True
    assert workspaces.json()[0]["personal_memory_enabled"] is True
    combined = health.text + workspaces.text
    assert "top-secret-test-key" not in combined
    assert "proxy.example" not in combined


@pytest.mark.asyncio
async def test_workspace_skill_count_uses_effective_catalog_and_collaboration_capability(
    settings_factory,
) -> None:
    async with api_client(settings_factory) as client:
        initial = await client.get("/api/workspaces")
        catalog = await client.get("/api/workspaces/actual/skills")
        skill = catalog.json()["global"][0]
        disabled = await client.put(
            f"/api/workspaces/actual/global-skills/{skill['id']}/setting",
            json={"enabled": False},
        )
        after = await client.get("/api/workspaces/actual")
        after_catalog = await client.get("/api/workspaces/actual/skills")

    assert initial.status_code == 200
    assert initial.json()[0]["can_manage_global_skills"] is True
    assert initial.json()[0]["skill_count"] == catalog.json()["effective_count"] == 1
    assert disabled.status_code == 200
    assert after.json()["skill_count"] == after_catalog.json()["effective_count"] == 0
    assert after.json()["can_manage_global_skills"] is True


@pytest.mark.asyncio
async def test_uat_obid_health_exposes_deployment_boundary(settings_factory) -> None:
    from types import SimpleNamespace

    from app.api.routes import health
    from app.db.base import Database

    settings = settings_factory(
        app_env="uat",
        identity_mode="obid",
        database_url="postgresql+asyncpg://agent:test@localhost/agent",
    )
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    services = SimpleNamespace(
        database=database,
        workspaces=SimpleNamespace(all=list),
        memory_scopes=SimpleNamespace(ready=True),
        runtime=None,
        settings=settings,
        execution_availability=None,
    )
    try:
        payload = await health(services)
    finally:
        await database.dispose()

    assert payload["identity_mode"] == "obid"
    assert payload["deployment_constraint"] == "single_instance"
    assert payload["security_marker"] == "UAT_OBID_UNVERIFIED"


@pytest.mark.asyncio
async def test_external_health_is_degraded_without_worker(settings_factory) -> None:
    from types import SimpleNamespace

    from app.api.routes import health
    from app.db.base import Database
    from app.sandbox.heartbeat import WorkerAvailabilitySnapshot

    class UnavailableExecution:
        async def snapshot(self) -> WorkerAvailabilitySnapshot:
            return WorkerAvailabilitySnapshot("degraded", "unavailable", None)

    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    settings = settings_factory(
        anthropic_api_key=None,
        app_runtime_mode="opensandbox_docker",
        app_runtime_cohort="docker-web",
        app_runtime_image_digest="sha256:" + "a" * 64,
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://opensandbox-server:8080",
        opensandbox_runner_runtime="fake",
        opensandbox_allowed_hosts=(),
    )
    services = SimpleNamespace(
        database=database,
        workspaces=SimpleNamespace(all=lambda: [SimpleNamespace(available=True)]),
        memory_scopes=SimpleNamespace(ready=True),
        runtime=None,
        settings=settings,
        execution_availability=UnavailableExecution(),
    )

    try:
        payload = await health(services)
    finally:
        await database.dispose()

    assert payload["status"] == "degraded"
    assert payload["execution"] == {
        "status": "degraded",
        "worker": "unavailable",
        "last_seen_at": None,
    }
    assert "instance" not in str(payload).lower()


@pytest.mark.asyncio
async def test_startup_claims_legacy_sessions_for_bootstrap_user(
    settings_factory,
) -> None:
    from app.db.base import Database
    from app.db.models import SessionRecord, UserRecord, WorkspaceRecord
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from app.sessions.service import LEGACY_SESSION_OWNER_ID

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    database = Database(settings.resolved_database_url)
    await database.initialize()
    now = datetime.now(UTC)
    async with database.session() as db:
        db.add(
            UserRecord(
                id=LEGACY_SESSION_OWNER_ID,
                external_subject="migration:legacy-session-owner",
                display_name="Legacy Session Owner",
                provider="migration",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            WorkspaceRecord(
                id="actual",
                name="Actual",
                kind="team",
                config_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        await db.flush()
        db.add(
            SessionRecord(
                id="legacy-session",
                workspace_id="actual",
                created_by=LEGACY_SESSION_OWNER_ID,
                title="Legacy",
                title_source="auto",
                status="idle",
                workspace_snapshot_json="{}",
                workspace_snapshot_hash="hash",
                session_dir="sessions/legacy-session",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    await database.dispose()

    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app):
        async with app.state.services.database.session() as db:
            session = await db.get(SessionRecord, "legacy-session")
            legacy_user = await db.get(UserRecord, LEGACY_SESSION_OWNER_ID)
        assert session is not None
        assert session.created_by == settings.mock_user_id
        assert legacy_user is None


def test_app_services_share_one_session_lifecycle_lock(settings_factory) -> None:
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    services = app.state.services

    assert services.sessions.locks is services.attachments.locks
    assert services.attachments.locks is services.turns.locks


def test_app_services_share_frontend_tool_bridge_registry(settings_factory) -> None:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.main import create_app

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    frontend_tool_bridges = FrontendToolBridgeRegistry()

    app = create_app(
        settings=settings,
        frontend_tool_bridges=frontend_tool_bridges,
    )
    services = app.state.services

    assert services.frontend_tool_bridges is frontend_tool_bridges
    assert services.runtime.frontend_tool_bridges is frontend_tool_bridges


@pytest.mark.asyncio
async def test_app_shutdown_clears_frontend_tool_bridges(settings_factory) -> None:
    from app.agui.bridge import FrontendToolBridgeRegistry
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime
    from tests.agui_helpers import dashboard_context, dashboard_tools

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    frontend_tool_bridges = FrontendToolBridgeRegistry()
    app = create_app(
        settings=settings,
        runtime=FakeAgentRuntime(),
        frontend_tool_bridges=frontend_tool_bridges,
    )

    async with app.router.lifespan_context(app):
        frontend_tool_bridges.register(
            "thread-1",
            "run-1",
            dashboard_context(),
            dashboard_tools(),
        )
        assert frontend_tool_bridges.active_run_count == 1

    assert frontend_tool_bridges.active_run_count == 0


@pytest.mark.asyncio
async def test_second_app_instance_does_not_interrupt_active_turn(
    settings_factory,
) -> None:
    from app.db.models import SessionRecord, TurnRecord
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    first = create_app(settings=settings, runtime=FakeAgentRuntime())
    second = create_app(settings=settings, runtime=FakeAgentRuntime())

    async with first.router.lifespan_context(first):
        now = datetime.now(UTC)
        async with first.state.services.database.session() as db:
            db.add(
                SessionRecord(
                    id="active-session",
                    workspace_id="actual",
                    created_by=settings.mock_user_id,
                    title="Active session",
                    title_source="auto",
                    status="running",
                    workspace_snapshot_json="{}",
                    workspace_snapshot_hash="hash",
                    session_dir="sessions/active-session",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.add(
                TurnRecord(
                    id="active-turn",
                    session_id="active-session",
                    client_request_id="active-request",
                    status="running",
                    input_text="hello",
                    created_at=now,
                )
            )
            await db.commit()

        duplicate_error = None
        try:
            async with second.router.lifespan_context(second):
                pass
        except RuntimeError as exc:
            duplicate_error = exc

        async with first.state.services.database.session() as db:
            session = await db.get(SessionRecord, "active-session")
            turn = await db.get(TurnRecord, "active-turn")

        assert session is not None and session.status == "running"
        assert turn is not None and turn.status == "running"
        assert duplicate_error is not None
        assert "already running" in str(duplicate_error)


@pytest.mark.asyncio
async def test_session_crud_and_message_history(settings_factory) -> None:
    settings = settings_factory()
    async with api_client(settings_factory) as client:
        created = await client.post("/api/workspaces/actual/sessions")
        session_id = created.json()["id"]
        workspace = settings.app_data_dir / "sessions" / session_id / "workspace"
        (workspace / "report.txt").write_text("report", encoding="utf-8")
        listed = await client.get("/api/workspaces/actual/sessions")
        renamed = await client.patch(
            f"/api/sessions/{session_id}", json={"title": "Review"}
        )
        turn = await client.post(
            f"/api/sessions/{session_id}/turns",
            json={
                "message": "hello",
                "attachment_ids": [],
                "file_references": ["report.txt"],
                "client_request_id": "request-1",
            },
        )
        turn_id = turn.json()["turn_id"]
        await client.get(f"/api/turns/{turn_id}/events")
        history = await client.get(f"/api/sessions/{session_id}/messages")
        deleted = await client.delete(f"/api/sessions/{session_id}")

    assert created.status_code == 201
    assert listed.json()[0]["id"] == session_id
    assert renamed.json()["title"] == "Review"
    assert turn.status_code == 202
    assert history.json()[0]["payload"]["file_references"] == ["report.txt"]
    assert [event["event_type"] for event in history.json()][-1] == "turn.completed"
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_session_context_exposes_persisted_snapshot_without_secret_values(
    settings_factory,
) -> None:
    async with api_client(settings_factory) as client:
        created = await client.post("/api/workspaces/actual/sessions")
        session_id = created.json()["id"]
        context = await client.get(f"/api/sessions/{session_id}/context")

    assert context.status_code == 200
    body = context.json()
    assert body["session_id"] == session_id
    assert body["workspace_id"] == "actual"
    assert body["workspace_snapshot_hash"]
    assert body["workspace_snapshot"]["id"] == "actual"
    assert body["workspace_snapshot"]["allowed_tools"]
    assert body["claude_session_id"] is None
    assert "top-secret-test-key" not in context.text


@pytest.mark.asyncio
async def test_attachment_upload_download_and_delete(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        created = await client.post("/api/workspaces/actual/sessions")
        session_id = created.json()["id"]
        uploaded = await client.post(
            f"/api/sessions/{session_id}/attachments",
            files=[("files", ("notes.txt", b"hello", "application/octet-stream"))],
        )
        attachment = uploaded.json()[0]
        content = await client.get(attachment["content_url"])
        deleted = await client.delete(f"/api/attachments/{attachment['id']}")
        missing = await client.get(attachment["content_url"])

    assert uploaded.status_code == 201
    assert attachment["mime_type"] == "text/plain"
    assert content.content == b"hello"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert deleted.status_code == 204
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_turn_api_is_idempotent_rejects_parallel_and_can_cancel(
    settings_factory,
) -> None:
    async with api_client(settings_factory, delay=0.2) as client:
        session_id = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        payload = {
            "message": "slow",
            "attachment_ids": [],
            "client_request_id": "request-slow",
        }
        first = await client.post(f"/api/sessions/{session_id}/turns", json=payload)
        duplicate = await client.post(f"/api/sessions/{session_id}/turns", json=payload)
        parallel = await client.post(
            f"/api/sessions/{session_id}/turns",
            json={**payload, "client_request_id": "request-other"},
        )
        cancelled = await client.post(f"/api/turns/{first.json()['turn_id']}/cancel")
        await client.get(f"/api/turns/{first.json()['turn_id']}/events")

    assert first.status_code == 202
    assert duplicate.json()["turn_id"] == first.json()["turn_id"]
    assert parallel.status_code == 409
    assert parallel.json()["error"]["code"] == "session_busy"
    assert cancelled.status_code == 202


@pytest.mark.asyncio
async def test_validation_errors_use_stable_envelope(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        response = await client.post(
            "/api/workspaces/actual/sessions/not-a-route",
            json={"anthropic_api_key": "top-secret-test-key"},
        )
        missing = await client.get("/api/sessions/does-not-exist")

    assert response.status_code == 404
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "session_not_found"
    assert missing.json()["error"]["request_id"]
    assert "top-secret-test-key" not in json.dumps(missing.json())


@pytest.mark.asyncio
async def test_session_skill_and_file_catalog_apis(settings_factory) -> None:
    settings = settings_factory()
    async with api_client(settings_factory) as client:
        session_id = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        workspace = settings.app_data_dir / "sessions" / session_id / "workspace"
        (workspace / "outputs").mkdir(exist_ok=True)
        (workspace / "outputs/report.html").write_text("report", encoding="utf-8")
        (workspace / ".hidden.txt").write_text("hidden", encoding="utf-8")

        skills = await client.get(f"/api/sessions/{session_id}/skills")
        files = await client.get(
            f"/api/sessions/{session_id}/files", params={"q": "report"}
        )

    assert skills.status_code == 200
    assert skills.json()["items"] == [{"name": "summary", "description": "summary"}]
    assert files.status_code == 200
    assert files.json() == {
        "items": [
            {
                "path": "outputs/report.html",
                "name": "report.html",
                "size_bytes": 6,
            }
        ],
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_file_catalog_is_isolated_per_session(settings_factory) -> None:
    settings = settings_factory()
    async with api_client(settings_factory) as client:
        session_a = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        session_b = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        workspace_a = settings.app_data_dir / "sessions" / session_a / "workspace"
        workspace_b = settings.app_data_dir / "sessions" / session_b / "workspace"
        (workspace_a / "only-a.txt").write_text("a", encoding="utf-8")
        (workspace_b / "only-b.txt").write_text("b", encoding="utf-8")

        response = await client.get(
            f"/api/sessions/{session_a}/files", params={"q": "only"}
        )

    assert [item["path"] for item in response.json()["items"]] == ["only-a.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symlink_kind", ["current_session", "sessions_root"])
async def test_catalog_apis_reject_owned_session_symlinks_without_leaking(
    settings_factory, symlink_kind: str
) -> None:
    settings = settings_factory()
    async with api_client(settings_factory) as client:
        session_a = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        session_b = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        sessions_root = settings.app_data_dir / "sessions"
        session_a_path = sessions_root / session_a
        session_b_path = sessions_root / session_b
        workspace_b = session_b_path / "workspace"
        leaked_file = "only-session-b.txt"
        leaked_description = "session-b-secret-description"
        (workspace_b / leaked_file).write_text("b", encoding="utf-8")
        (workspace_b / ".claude/skills/summary/SKILL.md").write_text(
            f"---\nname: summary\ndescription: {leaked_description}\n---\n# summary\n",
            encoding="utf-8",
        )

        if symlink_kind == "current_session":
            shutil.rmtree(session_a_path)
            session_a_path.symlink_to(session_b_path, target_is_directory=True)
        else:
            legitimate_sessions = settings.app_data_dir / "legitimate-sessions"
            sessions_root.rename(legitimate_sessions)
            redirected_sessions = settings.app_data_dir / "redirected-sessions"
            redirected_sessions.mkdir()
            shutil.copytree(
                legitimate_sessions / session_b,
                redirected_sessions / session_a,
            )
            sessions_root.symlink_to(redirected_sessions, target_is_directory=True)

        skills_response = await client.get(f"/api/sessions/{session_a}/skills")
        files_response = await client.get(f"/api/sessions/{session_a}/files")

    assert skills_response.status_code == 400
    assert skills_response.json()["error"]["code"] == "file_reference_invalid"
    assert skills_response.json()["error"]["message"] == (
        "The Session workspace path is invalid."
    )
    assert leaked_description not in skills_response.text

    assert files_response.status_code == 400
    assert files_response.json()["error"]["code"] == "file_reference_invalid"
    assert files_response.json()["error"]["message"] == (
        "The Session workspace path is invalid."
    )
    assert files_response.json()["error"]["request_id"]
    assert leaked_file not in files_response.text


@pytest.mark.asyncio
async def test_skill_catalog_does_not_follow_nested_skill_symlink(
    settings_factory,
) -> None:
    settings = settings_factory()
    async with api_client(settings_factory) as client:
        session_a = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        session_b = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        workspace_a = settings.app_data_dir / "sessions" / session_a / "workspace"
        workspace_b = settings.app_data_dir / "sessions" / session_b / "workspace"
        skill_a = workspace_a / ".claude/skills/summary"
        skill_b = workspace_b / ".claude/skills/summary"
        leaked_description = "session-b-secret-description"
        (skill_b / "SKILL.md").write_text(
            f"---\nname: summary\ndescription: {leaked_description}\n---\n# summary\n",
            encoding="utf-8",
        )
        shutil.rmtree(skill_a)
        skill_a.symlink_to(skill_b, target_is_directory=True)

        response = await client.get(f"/api/sessions/{session_a}/skills")

    assert response.status_code == 200
    assert response.json()["items"] == [{"name": "summary", "description": ""}]
    assert leaked_description not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_operation", ["scandir", "session_path"])
async def test_file_catalog_work_does_not_block_health_request(
    settings_factory,
    monkeypatch: pytest.MonkeyPatch,
    blocked_operation: str,
) -> None:
    from app.sessions import catalog
    from app.sessions.service import SessionService

    settings = settings_factory()
    async with api_client(settings_factory) as client:
        session_id = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        workspace = settings.app_data_dir / "sessions" / session_id / "workspace"
        work_started = threading.Event()
        release_work = threading.Event()

        def wait_until_released() -> None:
            work_started.set()
            if not release_work.wait(timeout=3.0):
                raise AssertionError("test did not release blocked catalog work")

        if blocked_operation == "scandir":
            real_scandir = catalog.os.scandir

            def blocking_scandir(path):
                if Path(path) == workspace:
                    wait_until_released()
                return real_scandir(path)

            monkeypatch.setattr(catalog.os, "scandir", blocking_scandir)
        else:
            real_session_path = SessionService.session_path

            def blocking_session_path(self, record):
                wait_until_released()
                return real_session_path(self, record)

            monkeypatch.setattr(SessionService, "session_path", blocking_session_path)

        watchdog = threading.Timer(1.0, release_work.set)
        watchdog.daemon = True
        watchdog.start()
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        scan_request = asyncio.create_task(
            client.get(f"/api/sessions/{session_id}/files")
        )
        try:
            while not work_started.is_set():
                await asyncio.sleep(0.01)
            health = await client.get("/api/health")
            health_elapsed = loop.time() - started_at
        finally:
            release_work.set()
            scan_response = await scan_request
            watchdog.cancel()

    assert health.status_code == 200
    assert health_elapsed < 0.5
    assert scan_response.status_code == 200


@pytest.mark.asyncio
async def test_file_catalog_errors_use_stable_envelope(settings_factory) -> None:
    async with api_client(settings_factory) as client:
        session_id = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        invalid_query = await client.get(
            f"/api/sessions/{session_id}/files", params={"q": "x" * 201}
        )
        missing = await client.get("/api/sessions/does-not-exist/files")

    assert invalid_query.status_code == 422
    assert invalid_query.json()["error"]["code"] == "invalid_request"
    assert invalid_query.json()["error"]["request_id"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "session_not_found"
    assert missing.json()["error"]["request_id"]


@pytest.mark.asyncio
async def test_session_service_validates_file_references_for_record(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app):
        record = await app.state.services.sessions.create(
            "actual",
            IdentityContext(
                settings.mock_user_id,
                settings.mock_user_subject,
                settings.mock_user_display_name,
            ),
        )
        workspace = app.state.services.sessions.session_path(record) / "workspace"
        (workspace / "report.txt").write_text("report", encoding="utf-8")

        references = app.state.services.sessions.validate_file_references_for_record(
            record, ["report.txt"]
        )

    assert references == ("report.txt",)


@pytest.mark.asyncio
async def test_turn_api_rejects_invalid_file_references(settings_factory) -> None:
    settings = settings_factory()
    async with api_client(settings_factory) as client:
        session_id = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        workspace = settings.app_data_dir / "sessions" / session_id / "workspace"
        (workspace / "report.txt").write_text("report", encoding="utf-8")
        for index in range(21):
            (workspace / f"file-{index}.txt").write_text("file", encoding="utf-8")

        bad_references = [
            (["../outside.txt"], 400, "file_reference_invalid"),
            (["/tmp/outside.txt"], 400, "file_reference_invalid"),
            (["missing-from-session-a.txt"], 400, "file_reference_invalid"),
            (["report.txt", "report.txt"], 400, "file_reference_invalid"),
            ([f"file-{index}.txt" for index in range(21)], 422, "invalid_request"),
        ]
        responses = []
        for index, (references, _status_code, _code) in enumerate(bad_references):
            responses.append(
                await client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={
                        "message": "review files",
                        "attachment_ids": [],
                        "file_references": references,
                        "client_request_id": f"invalid-reference-{index}",
                    },
                )
            )

    for response, (_references, status_code, code) in zip(
        responses, bad_references, strict=True
    ):
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code
