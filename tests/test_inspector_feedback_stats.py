"""Verify task-grain feedback, Beijing day boundaries and Inspector-only access."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.db.models import MessageFeedbackRecord, SessionRecord, UserRecord
from tests.test_session_inspector import PASSWORD, create_obid_session, inspector_client

AUTH = ("inspector", PASSWORD)
WINDOW = {"dateFrom": "2026-09-06", "dateTo": "2026-09-07"}


async def rate_task(client, ob_id, rating):
    """Create and rate one actual fake-runtime task through its public authorized API."""
    session = await create_obid_session(client, ob_id)
    headers = {"X-Davinci-ObId": ob_id}
    response = await client.post(
        f"/api/sessions/{session}/turns",
        headers=headers,
        json={"message": "hello", "client_request_id": session},
    )
    turn = response.json()["turn_id"]
    await client.get(f"/api/turns/{turn}/events", headers=headers)
    messages = (
        await client.get(f"/api/sessions/{session}/messages", headers=headers)
    ).json()
    message = next(
        row["id"]
        for row in messages
        if row["event_type"] == "message.assistant.completed"
    )
    url = f"/api/sessions/{session}/messages/{message}/feedback"
    assert (
        await client.put(url, headers=headers, json={"rating": rating})
    ).status_code == 200
    return session, message, turn, url, headers


@pytest.mark.asyncio
async def test_stats_match_current_votes_across_dates_people_edits_and_pagination(
    settings_factory,
):
    async with inspector_client(settings_factory) as (client, app):
        first = await rate_task(client, "actor-one", "up")
        second = await rate_task(client, "actor-two", "down")
        third = await rate_task(client, "actor-one", "up")
        database = app.state.services.database
        async with database.session() as db:
            for task, created in [
                (first, "2026-09-06T15:59:59"),
                (second, "2026-09-06T16:00:00"),
                (third, "2026-09-06T16:00:01"),
            ]:
                await db.execute(
                    update(MessageFeedbackRecord)
                    .where(MessageFeedbackRecord.session_id == task[0])
                    .values(
                        created_at=datetime.fromisoformat(created).replace(tzinfo=UTC)
                    )
                )
            await db.execute(update(UserRecord).values(display_name="同名用户"))
            workspace = await db.scalar(
                select(SessionRecord.workspace_id).where(SessionRecord.id == first[0])
            )
            await db.commit()
        for group in ["day", "actor", "dayActor"]:
            response = await client.get(
                "/api/inspector/feedbackStats",
                auth=AUTH,
                params={**WINDOW, "groupBy": group, "limit": 1},
            )
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            data = response.json()
            assert data["summary"] == {
                "total": 3,
                "up": 2,
                "down": 1,
                "actorCount": 2,
                "positiveRate": 2 / 3,
            }
            assert len(data["rows"]) == 1
            assert data["totalGroups"] == (3 if group == "dayActor" else 2)
        days = (
            await client.get("/api/inspector/feedbackStats", auth=AUTH, params=WINDOW)
        ).json()["rows"]
        assert [(row["date"], row["total"]) for row in days] == [
            ("2026-09-07", 2),
            ("2026-09-06", 1),
        ]
        people = (
            await client.get(
                "/api/inspector/feedbackStats",
                auth=AUTH,
                params={**WINDOW, "groupBy": "actor", "actorQuery": "同名用户"},
            )
        ).json()["rows"]
        assert len({row["actorId"] for row in people}) == 2
        details = (
            await client.get(
                "/api/inspector/feedback",
                auth=AUTH,
                params={**WINDOW, "rating": "down"},
            )
        ).json()
        assert details["total"] == 1 and details["items"][0]["messageId"] == second[1]
        assert details["items"][0]["turnId"] == second[2]
        scoped = (
            await client.get(
                "/api/inspector/feedbackStats",
                auth=AUTH,
                params={**WINDOW, "workspaceId": workspace},
            )
        ).json()
        assert scoped["summary"]["total"] == 2
        # Switching or supplementing a vote keeps its first evaluation day.
        assert (
            await client.put(
                first[3],
                headers=first[4],
                json={
                    "rating": "down",
                    "reasons": ["slow", "incomplete"],
                    "comment": "<script>plain text</script>",
                },
            )
        ).status_code == 200
        old_day = {"dateFrom": "2026-09-06", "dateTo": "2026-09-06"}
        summary = (
            await client.get("/api/inspector/feedbackStats", auth=AUTH, params=old_day)
        ).json()["summary"]
        assert summary["total"] == summary["down"] == 1 and summary["up"] == 0
        detail = (
            await client.get(f"/api/inspector/sessions/{first[0]}", auth=AUTH)
        ).json()
        assert (
            len(detail["feedback"]) == 1
            and detail["feedback"][0]["messageId"] == first[1]
        )
        events = (
            await client.get(
                f"/api/inspector/sessions/{first[0]}/events",
                auth=AUTH,
                params={"turnId": first[2]},
            )
        ).json()
        assert any(event["id"] == first[1] for event in events)
        await client.put(first[3], headers=first[4], json={"rating": None})
        summary = (
            await client.get("/api/inspector/feedbackStats", auth=AUTH, params=old_day)
        ).json()["summary"]
        assert summary["total"] == 0 and summary["positiveRate"] is None
        async with database.session() as db:
            await db.execute(
                update(SessionRecord)
                .where(SessionRecord.id == third[0])
                .values(deleted_at=datetime.now(UTC))
            )
            await db.commit()
        summary = (
            await client.get("/api/inspector/feedbackStats", auth=AUTH, params=WINDOW)
        ).json()["summary"]
        assert summary["total"] == 1


@pytest.mark.asyncio
async def test_stats_access_empty_results_and_validation(settings_factory):
    async with inspector_client(settings_factory) as (client, _app):
        for path in ["/api/inspector/feedbackStats", "/api/inspector/feedback"]:
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(path, auth=("inspector", "wrong"))
            ).status_code == 401
            for params in [
                {"dateFrom": "bad"},
                {"dateFrom": "2026-09-08", "dateTo": "2026-09-07"},
                {"dateFrom": "2026-01-01", "dateTo": "2026-09-07"},
                {"limit": 201},
                {"offset": -1},
            ]:
                assert (
                    await client.get(path, auth=AUTH, params=params)
                ).status_code == 422
        assert (
            await client.get(
                "/api/inspector/feedbackStats", auth=AUTH, params={"groupBy": "invalid"}
            )
        ).status_code == 422
        assert (
            await client.get(
                "/api/inspector/feedback", auth=AUTH, params={"rating": "invalid"}
            )
        ).status_code == 422
        data = (await client.get("/api/inspector/feedbackStats", auth=AUTH)).json()
        assert data["rows"] == [] and data["summary"]["positiveRate"] is None
