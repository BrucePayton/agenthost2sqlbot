"""Exercise feedback through the real API, database and session ownership boundary."""

import pytest

from tests.test_api import (
    api_client,
    foreign_resource_client,  # noqa: F401 - imported pytest fixture
)


async def completed_reply(client):
    """Persist an actual assistant reply using the fake runtime."""
    session = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
    response = await client.post(
        f"/api/sessions/{session}/turns",
        json={
            "message": "hello",
            "client_request_id": "feedback-test",
            "attachment_ids": [],
        },
    )
    assert response.status_code == 202, response.text
    turn = response.json()["turn_id"]
    await client.get(f"/api/turns/{turn}/events")
    messages = (await client.get(f"/api/sessions/{session}/messages")).json()
    assistant = next(
        m for m in messages if m["event_type"] == "message.assistant.completed"
    )
    user = next(m for m in messages if m["event_type"] == "message.user")
    return session, assistant["id"], user["id"]


@pytest.mark.asyncio
async def test_session_api_includes_utc_timezone(settings_factory):
    """Database round trips must not turn UTC into browser-local wall time."""
    from datetime import datetime

    async with api_client(settings_factory) as client:
        session, _, _ = await completed_reply(client)
        record = (await client.get(f"/api/sessions/{session}")).json()
        for key in ("created_at", "updated_at"):
            assert datetime.fromisoformat(record[key]).utcoffset() is not None


@pytest.mark.asyncio
async def test_tool_continuation_has_one_question_vote_and_rejects_pending(settings_factory):
    """Real AG-UI tool receipts move the reply anchor without creating a second vote."""
    import uuid

    from tests.test_agui_api import _client, _native_body

    async with _client(settings_factory()) as (client, app):
        session = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        origin, continuation = str(uuid.uuid4()), str(uuid.uuid4())
        assert (await client.post("/api/ag-ui", json=_native_body(session, origin))).status_code == 200
        messages = (await client.get(f"/api/sessions/{session}/messages")).json()
        first = next(m["id"] for m in messages if m["event_type"] == "message.assistant.completed")
        await app.state.services.turns.repository.append_event(
            origin, "frontend_tool.deferred", "assistant",
            {"tool_use_id": "feedback-call", "name": "dashboard.get_structure", "arguments": {}, "origin_run_id": origin},
        )
        url = f"/api/sessions/{session}/messages/{first}/feedback"
        assert (await client.put(url, json={"rating": "up"})).status_code == 422
        body = _native_body(session, continuation)
        body["messages"] = [{"id": "receipt", "role": "tool", "toolCallId": "feedback-call", "content": '{"status":"success","data":{},"issues":[]}'}]
        assert (await client.post("/api/ag-ui", json=body)).status_code == 200
        messages = (await client.get(f"/api/sessions/{session}/messages")).json()
        last = next(m["id"] for m in reversed(messages) if m["event_type"] == "message.assistant.completed")
        saved = await client.put(url, json={"rating": "up"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["messageId"] == last
        assert saved.json()["taskId"] == origin
        changed = await client.put(f"/api/sessions/{session}/messages/{last}/feedback", json={"rating": "down"})
        assert changed.status_code == 200
        votes = (await client.get(f"/api/sessions/{session}/feedback")).json()
        assert len(votes) == 1 and votes[0]["rating"] == "down"


@pytest.mark.asyncio
async def test_vote_details_switch_cancel_and_restart(settings_factory):
    async with api_client(settings_factory) as client:
        session, message, user = await completed_reply(client)
        url = f"/api/sessions/{session}/messages/{message}/feedback"
        first = await client.put(url, json={"rating": "up"})
        assert first.status_code == 200, first.text
        detailed = {
            "rating": "up",
            "reasons": ["accurate", "clear", "accurate"],
            "comment": "有帮助🙂",
        }
        response = await client.put(url, json=detailed)
        assert response.json()["reasons"] == ["accurate", "clear"]
        assert response.json()["comment"] == "有帮助🙂"
        again = await client.put(url, json=detailed)
        assert again.status_code == 200
        listed = (await client.get(f"/api/sessions/{session}/feedback")).json()
        assert len(listed) == 1
        assert listed[0]["messageId"] == message
        assert (
            await client.put(
                f"/api/sessions/{session}/messages/{user}/feedback",
                json={"rating": "up"},
            )
        ).status_code == 422
        other = (await client.post("/api/workspaces/actual/sessions")).json()["id"]
        assert (
            await client.put(
                f"/api/sessions/{other}/messages/{message}/feedback",
                json={"rating": "up"},
            )
        ).status_code == 404
    async with api_client(settings_factory) as client:
        listed = (await client.get(f"/api/sessions/{session}/feedback")).json()
        assert listed[0]["comment"] == "有帮助🙂"
        switched = (await client.put(url, json={"rating": "down"})).json()
        assert switched["reasons"] == [] and switched["comment"] == ""
        for _ in range(2):
            cancelled = await client.put(url, json={"rating": None})
            assert cancelled.status_code == 200 and cancelled.json()["rating"] is None
        assert (await client.get(f"/api/sessions/{session}/feedback")).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"rating": "up", "reasons": ["slow"]},
        {"rating": "down", "reasons": ["unknown"]},
        {"rating": None, "comment": "not empty"},
        {"rating": "up", "comment": "🙂" * 1001},
        {"rating": "invalid"},
        {"rating": "up", "actor_id": "someone"},
    ],
)
async def test_feedback_validates_payload(settings_factory, payload):
    async with api_client(settings_factory) as client:
        session, message, _ = await completed_reply(client)
        response = await client.put(
            f"/api/sessions/{session}/messages/{message}/feedback", json=payload
        )
        assert response.status_code == 422
        assert (await client.get(f"/api/sessions/{session}/feedback")).json() == []


@pytest.mark.asyncio
async def test_feedback_does_not_leak_foreign_sessions(foreign_resource_client):  # noqa: F811
    for method, path, payload in [
        ("GET", "/api/sessions/foreign/feedback", None),
        ("PUT", "/api/sessions/foreign/messages/any/feedback", {"rating": "up"}),
    ]:
        response = await foreign_resource_client.request(method, path, json=payload)
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_inspector_feedback_requires_existing_inspector_access(settings_factory):
    """Anonymous legacy exports omit new comments; authenticated exports link turns."""
    from tests.test_session_inspector import (
        PASSWORD,
        create_obid_session,
        inspector_client,
    )

    async with inspector_client(settings_factory) as (client, _app):
        session = await create_obid_session(client, "feedback-owner")
        headers = {"X-Davinci-ObId": "feedback-owner"}
        accepted = await client.post(
            f"/api/sessions/{session}/turns",
            headers=headers,
            json={"message": "hello", "client_request_id": "feedback-export"},
        )
        turn = accepted.json()["turn_id"]
        await client.get(f"/api/turns/{turn}/events", headers=headers)
        messages = (
            await client.get(f"/api/sessions/{session}/messages", headers=headers)
        ).json()
        message = next(
            m["id"]
            for m in messages
            if m["event_type"] == "message.assistant.completed"
        )
        await client.put(
            f"/api/sessions/{session}/messages/{message}/feedback",
            headers=headers,
            json={"rating": "down", "reasons": ["slow"], "comment": "等待太久"},
        )
        url = f"/api/inspector/sessions/{session}/export"
        public = await client.get(url, params={"format": "json"})
        assert "feedback" not in public.json()
        exported = (
            await client.get(
                url, params={"format": "json"}, auth=("inspector", PASSWORD)
            )
        ).json()
        assert exported["feedback"][0]["messageId"] == message
        assert exported["feedback"][0]["turnId"] == turn
        assert exported["feedback"][0]["comment"] == "等待太久"
        markdown = await client.get(url, auth=("inspector", PASSWORD))
        assert "等待太久" in markdown.text and message in markdown.text
        assert exported["context"]["workspace_snapshot_hash"]


@pytest.mark.asyncio
async def test_questions_have_independent_votes_in_one_session(settings_factory):
    """Separate questions retain separate votes and cancellation is scoped to a question."""
    async with api_client(settings_factory) as client:
        session, first_message, _ = await completed_reply(client)
        old_url = f"/api/sessions/{session}/messages/{first_message}/feedback"
        first = (await client.put(old_url, json={"rating": "up"})).json()
        accepted = await client.post(
            f"/api/sessions/{session}/turns",
            json={"message": "2", "client_request_id": "clarification-answer"},
        )
        turn = accepted.json()["turn_id"]
        await client.get(f"/api/turns/{turn}/events")
        messages = (await client.get(f"/api/sessions/{session}/messages")).json()
        last = next(
            m["id"]
            for m in reversed(messages)
            if m["event_type"] == "message.assistant.completed"
        )
        assert last != first_message
        updated = (
            await client.put(
                f"/api/sessions/{session}/messages/{last}/feedback",
                json={"rating": "down", "reasons": ["slow"], "comment": "任务太慢"},
            )
        ).json()
        listed = (await client.get(f"/api/sessions/{session}/feedback")).json()
        assert len(listed) == 2
        assert {row["messageId"] for row in listed} == {first_message, last}
        assert updated["taskId"] == turn
        assert updated["taskId"] != first["taskId"]
        assert updated["rating"] == "down"
        await client.put(old_url, json={"rating": None})
        remaining = (await client.get(f"/api/sessions/{session}/feedback")).json()
        assert len(remaining) == 1 and remaining[0]["messageId"] == last


@pytest.mark.asyncio
async def test_concurrent_votes_remain_one_per_task(settings_factory):
    """Database uniqueness also holds when two tabs submit a task vote together."""
    import asyncio

    async with api_client(settings_factory) as client:
        session, message, _ = await completed_reply(client)
        url = f"/api/sessions/{session}/messages/{message}/feedback"
        results = await asyncio.gather(
            client.put(url, json={"rating": "up"}),
            client.put(url, json={"rating": "down"}),
        )
        assert all(result.status_code == 200 for result in results)
        votes = (await client.get(f"/api/sessions/{session}/feedback")).json()
        assert len(votes) == 1 and votes[0]["messageId"] == message
