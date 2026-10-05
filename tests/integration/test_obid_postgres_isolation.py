import os
import uuid
from pathlib import Path

import httpx
import pytest

from tests.agui_helpers import dashboard_tools
from tests.test_workspaces import write_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)


def _headers(ob_id: str) -> dict[str, str]:
    return {"X-Davinci-ObId": ob_id}


def _native_body(session_id: str) -> dict[str, object]:
    return {
        "threadId": session_id,
        "runId": str(uuid.uuid4()),
        "state": {
            "schemaVersion": "davinci-page-state-v1",
            "page": {
                "instanceId": "page-obid-isolation",
                "kind": "dashboard",
                "route": "/share/workbench-new?dashboard=88",
                "resource": {"type": "dashboard", "id": "88"},
            },
            "permissions": {
                "canRead": True,
                "canOperate": True,
                "canPersist": False,
            },
            "ui": {"busy": False, "activeFilters": []},
            "revisions": {"routeRevision": 1, "dataRevision": 1},
            "dataStatus": {"loadingWidgetIds": [], "errorWidgetIds": []},
        },
        "messages": [{"id": "user-1", "role": "user", "content": "读取页面"}],
        "tools": [tool.model_dump(by_alias=True) for tool in dashboard_tools()],
        "context": [],
        "forwardedProps": {
            "profile": "davinci-agui-native-v2",
        },
    }


async def _complete_turn(
    client: httpx.AsyncClient,
    session_id: str,
    request_id: str,
    *,
    headers: dict[str, str],
) -> str:
    accepted = await client.post(
        f"/api/sessions/{session_id}/turns",
        json={
            "message": f"message-{request_id}",
            "attachment_ids": [],
            "client_request_id": request_id,
        },
        headers=headers,
    )
    assert accepted.status_code == 202
    turn_id = accepted.json()["turn_id"]
    events = await client.get(f"/api/turns/{turn_id}/events", headers=headers)
    assert events.status_code == 200
    completed = await client.get(f"/api/turns/{turn_id}", headers=headers)
    assert completed.json()["status"] == "completed"
    return turn_id


@pytest.mark.asyncio
async def test_obid_users_are_isolated_and_recover_after_restart(
    tmp_path: Path,
) -> None:
    from app.config import Settings
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    write_workspace(workspaces_root, "example")
    data_dir = tmp_path / "data"
    settings = Settings(
        _env_file=None,
        anthropic_base_url="https://proxy.example.test",
        anthropic_api_key="integration-secret",
        workspaces_root=workspaces_root,
        app_data_dir=data_dir,
        app_env="uat",
        identity_mode="obid",
        app_runtime_mode="local_inline",
        database_url=os.environ["TEST_POSTGRES_URL"],
        personal_workspace_template_id="example",
    )
    actor_a = f"phase1-a-{uuid.uuid4().hex}"
    actor_b = f"phase1-b-{uuid.uuid4().hex}"
    headers_a = _headers(actor_a)
    headers_b = _headers(actor_b)

    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        me_a = await client.get("/api/me", headers=headers_a)
        me_b = await client.get("/api/me", headers=headers_b)
        assert me_a.status_code == me_b.status_code == 200
        user_a = me_a.json()["user_id"]
        user_b = me_b.json()["user_id"]
        assert user_a != user_b

        workspaces_a = await client.get("/api/workspaces", headers=headers_a)
        workspaces_b = await client.get("/api/workspaces", headers=headers_b)
        assert workspaces_a.status_code == workspaces_b.status_code == 200
        assert len(workspaces_a.json()) == len(workspaces_b.json()) == 1
        workspace_a = workspaces_a.json()[0]["id"]
        workspace_b = workspaces_b.json()[0]["id"]
        assert workspace_a != workspace_b
        assert workspaces_a.json()[0]["kind"] == "personal"
        assert workspaces_b.json()[0]["kind"] == "personal"

        first_session = await client.post(
            f"/api/workspaces/{workspace_a}/sessions", headers=headers_a
        )
        second_session = await client.post(
            f"/api/workspaces/{workspace_a}/sessions", headers=headers_a
        )
        foreign_session = await client.post(
            f"/api/workspaces/{workspace_b}/sessions", headers=headers_b
        )
        assert first_session.status_code == second_session.status_code == 201
        assert foreign_session.status_code == 201
        session_a1 = first_session.json()["id"]
        session_a2 = second_session.json()["id"]
        session_b = foreign_session.json()["id"]

        record_a1 = await app.state.services.sessions.get(session_a1)
        record_a2 = await app.state.services.sessions.get(session_a2)
        assert record_a1.session_dir != record_a2.session_dir
        assert app.state.services.sessions.session_path(record_a1).is_dir()
        assert app.state.services.sessions.session_path(record_a2).is_dir()

        memory_a1 = app.state.services.memory_scopes.resolve(user_a, workspace_a)
        memory_a2 = app.state.services.memory_scopes.resolve(user_a, workspace_a)
        memory_b = app.state.services.memory_scopes.resolve(user_b, workspace_b)
        assert memory_a1 == memory_a2
        assert memory_a1 != memory_b
        memory_marker = memory_a1.directory / "MEMORY.md"
        memory_marker.write_text("stable workspace memory", encoding="utf-8")

        turn_a1 = await _complete_turn(
            client, session_a1, f"request-{uuid.uuid4().hex}", headers=headers_a
        )
        await _complete_turn(
            client, session_a2, f"request-{uuid.uuid4().hex}", headers=headers_a
        )
        turn_b = await _complete_turn(
            client, session_b, f"request-{uuid.uuid4().hex}", headers=headers_b
        )
        resumed_a1 = await app.state.services.sessions.get(session_a1)
        resumed_a2 = await app.state.services.sessions.get(session_a2)
        assert resumed_a1.claude_session_id
        assert resumed_a2.claude_session_id
        assert resumed_a1.claude_session_id != resumed_a2.claude_session_id

        uploaded = await client.post(
            f"/api/sessions/{session_a1}/attachments",
            files=[("files", ("notes.txt", b"persisted attachment", "text/plain"))],
            headers=headers_a,
        )
        assert uploaded.status_code == 201
        attachment_a = uploaded.json()[0]
        workspace_path = (
            app.state.services.sessions.session_path(resumed_a1) / "workspace"
        )
        output_path = workspace_path / "outputs" / "report.txt"
        output_path.write_text("persisted output", encoding="utf-8")

        foreign_upload = await client.post(
            f"/api/sessions/{session_b}/attachments",
            files=[("files", ("foreign.txt", b"foreign", "text/plain"))],
            headers=headers_b,
        )
        assert foreign_upload.status_code == 201
        attachment_b = foreign_upload.json()[0]

        hidden_routes = (
            ("GET", f"/api/workspaces/{workspace_b}"),
            ("GET", f"/api/workspaces/{workspace_b}/sessions"),
            ("GET", f"/api/workspaces/{workspace_b}/skills"),
            ("GET", f"/api/sessions/{session_b}"),
            ("GET", f"/api/sessions/{session_b}/messages"),
            ("GET", f"/api/sessions/{session_b}/files"),
            ("GET", f"/api/turns/{turn_b}"),
            ("GET", f"/api/turns/{turn_b}/events"),
            ("GET", attachment_b["content_url"]),
        )
        for method, path in hidden_routes:
            hidden = await client.request(method, path, headers=headers_a)
            assert hidden.status_code == 404, (method, path, hidden.text)

        hidden_agui = await client.post(
            "/api/ag-ui", json=_native_body(session_b), headers=headers_a
        )
        assert hidden_agui.status_code == 404

        persisted_user_id = user_a
        persisted_workspace_id = workspace_a
        persisted_memory_path = memory_a1.directory

    restarted = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with restarted.router.lifespan_context(restarted), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted), base_url="http://testserver"
    ) as client:
        recovered_me = await client.get("/api/me", headers=headers_a)
        recovered_workspaces = await client.get(
            "/api/workspaces", headers=headers_a
        )
        recovered_sessions = await client.get(
            f"/api/workspaces/{persisted_workspace_id}/sessions",
            headers=headers_a,
        )
        recovered_messages = await client.get(
            f"/api/sessions/{session_a1}/messages", headers=headers_a
        )
        recovered_attachment = await client.get(
            attachment_a["content_url"], headers=headers_a
        )
        recovered_output = await client.get(
            f"/api/sessions/{session_a1}/files",
            params={"q": "report"},
            headers=headers_a,
        )

        assert recovered_me.json()["user_id"] == persisted_user_id
        assert [item["id"] for item in recovered_workspaces.json()] == [
            persisted_workspace_id
        ]
        assert {item["id"] for item in recovered_sessions.json()} == {
            session_a1,
            session_a2,
        }
        assert any(
            item["event_type"] == "turn.completed"
            for item in recovered_messages.json()
        )
        assert recovered_attachment.content == b"persisted attachment"
        assert recovered_output.json()["items"] == [
            {
                "path": "outputs/report.txt",
                "name": "report.txt",
                "size_bytes": len("persisted output"),
            }
        ]
        assert (
            restarted.state.services.memory_scopes.resolve(
                persisted_user_id, persisted_workspace_id
            ).directory
            == persisted_memory_path
        )
        assert memory_marker.read_text(encoding="utf-8") == "stable workspace memory"

        deleted = await client.delete(
            f"/api/sessions/{session_a1}", headers=headers_a
        )
        assert deleted.status_code == 204
        assert not (data_dir / resumed_a1.session_dir).exists()
        assert (data_dir / resumed_a2.session_dir).exists()
        assert memory_marker.read_text(encoding="utf-8") == "stable workspace memory"
        assert (
            await client.get(f"/api/turns/{turn_a1}", headers=headers_a)
        ).status_code == 404
