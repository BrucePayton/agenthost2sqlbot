import argparse
import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select, update


async def _token(client: httpx.AsyncClient, authority: str, subject: str) -> str:
    response = await client.get(
        f"{authority}/token",
        params={"issuer": authority, "subject": subject},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text}"
        ) from exc


async def run(args: argparse.Namespace) -> None:
    from app.db.base import Database
    from app.db.models import (
        TurnAttemptRecord,
        TurnRecord,
        WorkspaceMembershipProjectionRecord,
        WorkspaceRecord,
    )
    from app.turns.notifications import PostgresTurnNotifier
    from app.turns.repository import TurnRepository

    database = Database(args.database_url)
    await database.initialize()
    async with database.session() as session:
        if await session.get(WorkspaceRecord, "example") is None:
            session.add(
                WorkspaceRecord(
                    id="example",
                    name="Example Workspace",
                    kind="team",
                    config_json="{}",
                )
            )
            await session.commit()

    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        owner_token = await _token(client, args.authority, "phase1-owner")
        outsider_token = await _token(client, args.authority, "phase1-outsider")
        owner_headers = {"Authorization": f"Bearer {owner_token}"}
        outsider_headers = {"Authorization": f"Bearer {outsider_token}"}

        workspace = await client.get(
            f"{args.replica_a}/api/workspaces/example", headers=owner_headers
        )
        _raise_for_status(workspace)
        first_session_response = await client.post(
            f"{args.replica_a}/api/workspaces/example/sessions",
            headers=owner_headers,
        )
        _raise_for_status(first_session_response)
        first_session = first_session_response.json()["id"]
        payload = {
            "message": "queued only",
            "attachment_ids": [],
            "file_references": [],
            "client_request_id": "same-request",
        }
        first, duplicate = await asyncio.gather(
            client.post(
                f"{args.replica_a}/api/sessions/{first_session}/turns",
                headers=owner_headers,
                json=payload,
            ),
            client.post(
                f"{args.replica_b}/api/sessions/{first_session}/turns",
                headers=owner_headers,
                json=payload,
            ),
        )
        assert first.status_code == duplicate.status_code == 202
        assert first.json()["turn_id"] == duplicate.json()["turn_id"]

        second_session_response = await client.post(
            f"{args.replica_a}/api/workspaces/example/sessions",
            headers=owner_headers,
        )
        second_session_response.raise_for_status()
        second_session = second_session_response.json()["id"]
        competing = await asyncio.gather(
            client.post(
                f"{args.replica_a}/api/sessions/{second_session}/turns",
                headers=owner_headers,
                json={**payload, "client_request_id": "request-a"},
            ),
            client.post(
                f"{args.replica_b}/api/sessions/{second_session}/turns",
                headers=owner_headers,
                json={**payload, "client_request_id": "request-b"},
            ),
        )
        assert sorted(response.status_code for response in competing) == [202, 409]

        accepted_second = next(
            response for response in competing if response.status_code == 202
        )
        queued_turn_ids = {first.json()["turn_id"], accepted_second.json()["turn_id"]}
        async with database.session() as session:
            queued_turns = list(
                (
                    await session.scalars(
                        select(TurnRecord).where(TurnRecord.id.in_(queued_turn_ids))
                    )
                ).all()
            )
            attempt_count = len(
                list(
                    (
                        await session.scalars(
                            select(TurnAttemptRecord).where(
                                TurnAttemptRecord.turn_id.in_(queued_turn_ids)
                            )
                        )
                    ).all()
                )
            )
        assert {turn.status for turn in queued_turns} == {"queued"}
        assert attempt_count == 0

        attachment_response = await client.post(
            f"{args.replica_a}/api/sessions/{first_session}/attachments",
            headers=owner_headers,
            files={"files": ("private.txt", b"private", "text/plain")},
        )
        _raise_for_status(attachment_response)
        attachment_id = attachment_response.json()[0]["id"]

        private_urls = [
            f"/api/sessions/{first_session}",
            f"/api/sessions/{first_session}/messages",
            f"/api/sessions/{first_session}/skills",
            f"/api/sessions/{first_session}/files",
            f"/api/sessions/{first_session}/attachments",
            f"/api/attachments/{attachment_id}/content",
            f"/api/turns/{first.json()['turn_id']}",
            f"/api/turns/{first.json()['turn_id']}/events",
        ]
        for private_url in private_urls:
            private = await client.get(
                f"{args.replica_b}{private_url}", headers=outsider_headers
            )
            assert private.status_code == 404, (private_url, private.text)

        turn_id = first.json()["turn_id"]
        stream_task = asyncio.create_task(
            _read_one_sse(
                client,
                f"{args.replica_a}/api/turns/{turn_id}/events",
                owner_headers,
                last_event_id="1",
            )
        )
        await asyncio.sleep(0.2)
        repository = TurnRepository(database)
        event = await repository.append_event(
            turn_id, "turn.progress", "system", {"phase": "cross-replica"}
        )
        await PostgresTurnNotifier(args.database_url).notify(turn_id, event.sequence)
        received = await asyncio.wait_for(stream_task, timeout=5)
        assert "event: turn.progress" in received

        async with database.session() as session:
            await session.execute(
                update(WorkspaceMembershipProjectionRecord)
                .where(WorkspaceMembershipProjectionRecord.user_id.is_not(None))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        revoked = await client.post(f"{args.authority}/revoke/phase1-owner")
        revoked.raise_for_status()
        denied = await client.get(
            f"{args.replica_a}/api/sessions/{first_session}", headers=owner_headers
        )
        assert denied.status_code == 404

    await database.dispose()


async def _read_one_sse(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    last_event_id: str,
) -> str:
    async with client.stream(
        "GET", url, headers={**headers, "Last-Event-ID": last_event_id}
    ) as response:
        response.raise_for_status()
        chunk = ""
        async for line in response.aiter_lines():
            chunk += line + "\n"
            if line == "" and "event: heartbeat" not in chunk:
                return chunk
            if line == "":
                chunk = ""
    raise RuntimeError("SSE stream closed before an event arrived")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--authority", required=True)
    parser.add_argument("--replica-a", required=True)
    parser.add_argument("--replica-b", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
