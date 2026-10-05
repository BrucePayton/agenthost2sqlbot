import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from tests.test_workspaces import write_workspace

PASSWORD = "test-session-inspector-password"


@asynccontextmanager
async def inspector_client(settings_factory, *, enabled: bool = True):
    from app.main import create_app
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        app_env="development",
        identity_mode="obid",
        personal_workspace_template_id="example",
        session_inspector_enabled=enabled,
        session_inspector_password=PASSWORD if enabled else None,
    )
    write_workspace(settings.workspaces_root, "example")
    app = create_app(settings=settings, runtime=FakeAgentRuntime())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client, app


async def create_obid_session(client: httpx.AsyncClient, ob_id: str) -> str:
    headers = {"X-Davinci-ObId": ob_id}
    workspaces = (await client.get("/api/workspaces", headers=headers)).json()
    workspace_id = workspaces[0]["id"]
    response = await client.post(
        f"/api/workspaces/{workspace_id}/sessions",
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_inspector_requires_a_password_when_enabled(settings_factory) -> None:
    with pytest.raises(ValidationError, match="APP_SESSION_INSPECTOR_PASSWORD"):
        settings_factory(session_inspector_enabled=True)


@pytest.mark.asyncio
async def test_inspector_routes_are_absent_when_disabled(settings_factory) -> None:
    async with inspector_client(settings_factory, enabled=False) as (client, _app):
        page = await client.get("/inspector")
        sessions = await client.get("/api/inspector/sessions")

    assert page.status_code == 404
    assert sessions.status_code == 404


@pytest.mark.asyncio
async def test_inspector_requires_basic_auth_and_renders_read_only_page(
    settings_factory,
) -> None:
    async with inspector_client(settings_factory) as (client, _app):
        missing = await client.get("/inspector")
        wrong = await client.get(
            "/api/inspector/sessions",
            auth=("inspector", "wrong-password"),
        )
        page = await client.get("/inspector", auth=("inspector", PASSWORD))

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == 'Basic realm="Session Inspector"'
    assert wrong.status_code == 401
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert 'id="inspectorSessionList"' in page.text
    assert 'id="inspectorSearchInput"' in page.text
    assert "/static/session-inspector-page.js?v=" in page.text
    assert "/static/inspector.css?v=" in page.text
    # A stale-JS page must not fall back to downloading the inspector page itself.
    assert 'id="inspectorExportLink" class="link-button" download' in page.text
    assert 'href="#"' not in page.text
    assert 'id="messageInput"' not in page.text


@pytest.mark.asyncio
async def test_inspector_lists_searches_and_reads_cross_user_database_records(
    settings_factory,
) -> None:
    async with inspector_client(settings_factory) as (client, app):
        first_session_id = await create_obid_session(client, "12901")
        second_session_id = await create_obid_session(client, "12902")

        from app.db.models import TurnEventRecord, TurnRecord

        turn_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with app.state.services.database.session() as db:
            db.add(
                TurnRecord(
                    id=turn_id,
                    session_id=first_session_id,
                    client_request_id="inspector-test-request",
                    status="completed",
                    input_text="分析仪表盘",
                    started_at=now,
                    completed_at=now,
                    created_at=now,
                )
            )
            await db.flush()
            db.add(
                TurnEventRecord(
                    id=str(uuid.uuid4()),
                    session_id=first_session_id,
                    turn_id=turn_id,
                    sequence=1,
                    event_type="message.user",
                    role="user",
                    payload_json=json.dumps(
                        {
                            "text": "分析仪表盘",
                            "headers": {"Authorization": "Bearer must-not-leak"},
                            "input_tokens": 321,
                            "sessionToken": "must-not-leak-either",
                        }
                    ),
                    created_at=now,
                )
            )
            await db.commit()

        auth = ("inspector", PASSWORD)
        catalog = await client.get(
            "/api/inspector/sessions?limit=20&offset=0",
            auth=auth,
        )
        searched = await client.get(
            "/api/inspector/sessions?query=12901",
            auth=auth,
        )
        detail = await client.get(
            f"/api/inspector/sessions/{first_session_id}",
            auth=auth,
        )
        events = await client.get(
            f"/api/inspector/sessions/{first_session_id}/events",
            auth=auth,
        )

    assert catalog.status_code == 200
    assert catalog.headers["cache-control"] == "no-store"
    assert catalog.json()["total"] == 2
    assert {item["id"] for item in catalog.json()["items"]} == {
        first_session_id,
        second_session_id,
    }
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["id"] == first_session_id
    assert searched.json()["items"][0]["identity_subject"] == "obid:12901"
    assert searched.json()["items"][0]["turn_count"] == 1
    assert searched.json()["items"][0]["event_count"] == 1

    assert detail.status_code == 200
    assert detail.json()["session"]["id"] == first_session_id
    assert detail.json()["session"]["identity_subject"] == "obid:12901"
    assert detail.json()["session"]["workspace_name"] == "Davinci 12901 的工作区"
    assert detail.json()["context"]["session_id"] == first_session_id
    assert "session_dir" not in detail.text

    assert events.status_code == 200
    assert events.json()[0]["payload"]["text"] == "分析仪表盘"
    assert events.json()[0]["payload"]["headers"]["Authorization"] == "[REDACTED]"
    assert events.json()[0]["payload"]["input_tokens"] == 321
    assert events.json()[0]["payload"]["sessionToken"] == "[REDACTED]"
    assert "must-not-leak" not in events.text


@pytest.mark.asyncio
async def test_inspector_workspaces_endpoint_lists_distinct_workspaces_with_session_counts(
    settings_factory,
) -> None:
    async with inspector_client(settings_factory) as (client, _app):
        first_session_id = await create_obid_session(client, "23301")
        second_session_id = await create_obid_session(client, "23302")

        auth = ("inspector", PASSWORD)
        first_detail = await client.get(
            f"/api/inspector/sessions/{first_session_id}", auth=auth
        )
        second_detail = await client.get(
            f"/api/inspector/sessions/{second_session_id}", auth=auth
        )
        response = await client.get("/api/inspector/workspaces", auth=auth)

    first_workspace_id = first_detail.json()["session"]["workspace_id"]
    second_workspace_id = second_detail.json()["session"]["workspace_id"]
    assert first_workspace_id != second_workspace_id

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert {item["workspace_id"] for item in items} == {
        first_workspace_id,
        second_workspace_id,
    }
    for item in items:
        assert set(item) == {"workspace_id", "workspace_name", "session_count"}
    names = [item["workspace_name"] for item in items]
    assert names == sorted(names)
    by_id = {item["workspace_id"]: item for item in items}
    assert by_id[first_workspace_id]["session_count"] == 1
    assert by_id[second_workspace_id]["session_count"] == 1


@pytest.mark.asyncio
async def test_inspector_sessions_can_be_filtered_by_workspace_id(
    settings_factory,
) -> None:
    async with inspector_client(settings_factory) as (client, _app):
        first_session_id = await create_obid_session(client, "23401")
        second_session_id = await create_obid_session(client, "23402")

        auth = ("inspector", PASSWORD)
        first_detail = await client.get(
            f"/api/inspector/sessions/{first_session_id}", auth=auth
        )
        first_workspace_id = first_detail.json()["session"]["workspace_id"]

        filtered = await client.get(
            f"/api/inspector/sessions?workspace_id={first_workspace_id}",
            auth=auth,
        )

    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    filtered_ids = {item["id"] for item in filtered.json()["items"]}
    assert filtered_ids == {first_session_id}
    assert second_session_id not in filtered_ids


@pytest.mark.asyncio
async def test_inspector_timestamps_are_returned_with_utc_offset(
    settings_factory,
) -> None:
    async with inspector_client(settings_factory) as (client, app):
        session_id = await create_obid_session(client, "23201")

        from app.db.models import TurnEventRecord, TurnRecord

        turn_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        async with app.state.services.database.session() as db:
            db.add(
                TurnRecord(
                    id=turn_id,
                    session_id=session_id,
                    client_request_id="inspector-tz-test",
                    status="completed",
                    input_text="时区测试",
                    started_at=now,
                    completed_at=now,
                    created_at=now,
                )
            )
            await db.flush()
            db.add(
                TurnEventRecord(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    turn_id=turn_id,
                    sequence=1,
                    event_type="message.user",
                    role="user",
                    payload_json=json.dumps({"text": "时区测试"}),
                    created_at=now,
                )
            )
            await db.commit()

        auth = ("inspector", PASSWORD)
        catalog = await client.get("/api/inspector/sessions?limit=20", auth=auth)
        detail = await client.get(f"/api/inspector/sessions/{session_id}", auth=auth)
        events = await client.get(
            f"/api/inspector/sessions/{session_id}/events", auth=auth
        )

    def assert_offset_aware(value: str) -> None:
        assert datetime.fromisoformat(value).tzinfo is not None

    catalog_item = next(
        item for item in catalog.json()["items"] if item["id"] == session_id
    )
    assert_offset_aware(catalog_item["created_at"])
    assert_offset_aware(catalog_item["updated_at"])

    assert_offset_aware(detail.json()["session"]["created_at"])
    assert_offset_aware(detail.json()["session"]["updated_at"])
    assert_offset_aware(detail.json()["context"]["created_at"])
    assert_offset_aware(detail.json()["context"]["updated_at"])

    assert events.json()
    for event_item in events.json():
        assert_offset_aware(event_item["created_at"])


@pytest.mark.asyncio
async def test_inspector_has_no_mutation_routes(settings_factory) -> None:
    async with inspector_client(settings_factory) as (client, _app):
        response = await client.post(
            "/api/inspector/sessions",
            auth=("inspector", PASSWORD),
            json={"title": "must not be created"},
        )

    assert response.status_code in {404, 405}


def _transcript_line(**overrides) -> str:
    entry = {"type": "assistant", "timestamp": "2026-08-26T07:00:01+00:00"}
    entry.update(overrides)
    return json.dumps(entry, ensure_ascii=False)


@pytest.mark.asyncio
async def test_inspector_export_merges_transcript_and_degrades_without_it(
    settings_factory,
) -> None:
    from app.db.models import SessionRecord, TurnEventRecord, TurnRecord

    base = datetime(2026, 8, 26, 7, 0, 0, tzinfo=UTC)
    first_turn = str(uuid.uuid4())
    second_turn = str(uuid.uuid4())

    def event(turn_id: str, sequence: int, event_type: str, payload: dict, offset: int):
        return TurnEventRecord(
            id=str(uuid.uuid4()),
            session_id=session_id,
            turn_id=turn_id,
            sequence=sequence,
            event_type=event_type,
            role="user" if event_type == "message.user" else "tool",
            payload_json=json.dumps(payload),
            created_at=base.replace(second=offset),
        )

    async with inspector_client(settings_factory) as (client, app):
        session_id = await create_obid_session(client, "31401")
        async with app.state.services.database.session() as db:
            for turn_id, text in ((first_turn, "查空间"), (second_turn, "换个问题")):
                db.add(
                    TurnRecord(
                        id=turn_id,
                        session_id=session_id,
                        client_request_id=f"export-{turn_id[:8]}",
                        status="completed",
                        input_text=text,
                        started_at=base,
                        completed_at=base,
                        created_at=base,
                    )
                )
            await db.flush()
            db.add(event(first_turn, 1, "message.user", {"text": "查空间"}, 0))
            db.add(
                event(
                    first_turn,
                    2,
                    "tool.started",
                    {
                        "tool_use_id": "toolu_export_1",
                        "name": "Skill",
                        "input_preview": '{"skill": "manage-sp\n...[truncated]',
                    },
                    2,
                )
            )
            db.add(
                event(
                    first_turn,
                    3,
                    "tool.completed",
                    {
                        "tool_use_id": "toolu_export_1",
                        "name": "tool",
                        "is_error": False,
                        "output_preview": "Launching skill\n...[truncated]",
                        "duration_ms": 31,
                    },
                    3,
                )
            )
            db.add(event(second_turn, 1, "message.user", {"text": "换个问题"}, 9))
            await db.commit()
            record = await db.get(SessionRecord, session_id)
            session_path = app.state.services.sessions.session_path(record)

        without_transcript = await client.get(
            f"/api/inspector/sessions/{session_id}/export"
        )

        transcript_dir = session_path / "claude-config" / "projects" / "workspace"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / "claude-session.jsonl").write_text(
            "\n".join(
                [
                    _transcript_line(
                        message={
                            "role": "assistant",
                            "model": "deepseek-v4-pro-0813",
                            "content": [
                                {
                                    "type": "thinking",
                                    "thinking": "先确认用户到底有哪些空间",
                                },
                                {
                                    "type": "tool_use",
                                    "id": "toolu_export_1",
                                    "name": "Skill",
                                    "input": {
                                        "skill": "manage-space",
                                        "args": "查询当前用户拥有的空间",
                                        "cookie": "must-not-leak",
                                    },
                                },
                            ],
                        }
                    ),
                    _transcript_line(
                        type="user",
                        timestamp="2026-08-26T07:00:03+00:00",
                        message={
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_export_1",
                                    "content": "Launching skill: manage-space",
                                }
                            ],
                        },
                    ),
                    json.dumps({"type": "ai-title", "aiTitle": "忽略我"}),
                ]
            ),
            encoding="utf-8",
        )

        markdown = await client.get(f"/api/inspector/sessions/{session_id}/export")
        as_json = await client.get(
            f"/api/inspector/sessions/{session_id}/export?format=json"
        )
        scoped = await client.get(
            f"/api/inspector/sessions/{session_id}/export?turn={first_turn}"
        )

    # No Basic credentials were sent: curl from a local agent must work.
    assert without_transcript.status_code == 200
    assert "磁盘 transcript：**不可用**" in without_transcript.text
    assert "先确认用户到底有哪些空间" not in without_transcript.text

    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert (
        f'filename="session-{session_id[:8]}.md"'
        in (markdown.headers["content-disposition"])
    )
    assert "先确认用户到底有哪些空间" in markdown.text
    assert "查询当前用户拥有的空间" in markdown.text
    assert "[REDACTED]" in markdown.text
    assert "must-not-leak" not in markdown.text
    # The database stores "tool" for completions; the transcript restores the name.
    assert "工具完成 · `Skill`（31 ms）" in markdown.text

    bundle = as_json.json()
    assert bundle["sources"]["transcript"]["available"] is True
    assert bundle["sources"]["transcript"]["thinking_blocks"] == 1
    assert bundle["tool_surface"]["allowed_tools"]
    thinking = [
        item
        for turn in bundle["turns"]
        for item in turn["items"]
        if item["event_type"] == "assistant.thinking"
    ]
    assert thinking[0]["turn_id"] == first_turn

    assert scoped.status_code == 200
    assert "换个问题" not in scoped.text
    assert "先确认用户到底有哪些空间" in scoped.text


@pytest.mark.asyncio
async def test_inspector_export_survives_a_corrupt_transcript(settings_factory) -> None:
    from app.db.models import SessionRecord

    async with inspector_client(settings_factory) as (client, app):
        session_id = await create_obid_session(client, "31402")
        async with app.state.services.database.session() as db:
            record = await db.get(SessionRecord, session_id)
            session_path = app.state.services.sessions.session_path(record)

        transcript_dir = session_path / "claude-config" / "projects" / "workspace"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        good = _transcript_line(
            message={
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "仍然读得到我"}],
            }
        ).encode("utf-8")
        # Tool output is not guaranteed to be valid UTF-8; one bad byte must not
        # cost the whole export.
        (transcript_dir / "claude-session.jsonl").write_bytes(
            b"\xff\xfe not utf-8 at all\n" + good + b"\n{ broken json\n"
        )

        response = await client.get(f"/api/inspector/sessions/{session_id}/export")

    assert response.status_code == 200
    assert "仍然读得到我" in response.text


@pytest.mark.asyncio
async def test_inspector_export_reports_its_own_failure_in_the_downloaded_file(
    settings_factory, monkeypatch
) -> None:
    from app.inspector import routes as inspector_routes

    def explode(bundle):
        raise RuntimeError("renderer blew up")

    async with inspector_client(settings_factory) as (client, _app):
        session_id = await create_obid_session(client, "31403")
        monkeypatch.setattr(inspector_routes, "render_markdown", explode)
        response = await client.get(f"/api/inspector/sessions/{session_id}/export")

    # A 500 would be discarded by the browser, leaving nobody able to see why.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "导出失败" in response.text
    assert "RuntimeError: renderer blew up" in response.text
    assert "Traceback" in response.text
