from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig

from app.runner.protocol import RunnerFrame
from app.sandbox.models import SandboxHandle
from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

RUN_LIVE = os.getenv("RUN_LIVE_DOCKER_WEB_CLAUDE") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Set RUN_LIVE_DOCKER_WEB_CLAUDE=1 for the packaged Claude gate.",
)

REQUIRED_ENVIRONMENT = (
    "DOCKER_WEB_URL",
    "DOCKER_WEB_PROJECT",
    "DOCKER_WEB_POSTGRES_URL",
    "DOCKER_WEB_OPENSANDBOX_URL",
    "OPENSANDBOX_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "DOCKER_WEB_MODEL",
)


def live_environment() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.getenv(name)]
    if missing:
        pytest.fail("Missing packaged Claude gate configuration: " + ", ".join(missing))
    return {name: os.environ[name] for name in REQUIRED_ENVIRONMENT}


async def wait_for_turn(
    client: httpx.AsyncClient, turn_id: str, *, timeout_seconds: float = 360
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/turns/{turn_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
            "recovery_required",
        }:
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"packaged Claude turn did not finish: {last!r}")


async def execute_turn(
    client: httpx.AsyncClient,
    session_id: str,
    message: str,
    request_id: str,
) -> dict:
    response = await client.post(
        f"/api/sessions/{session_id}/turns",
        json={
            "message": message,
            "attachment_ids": [],
            "file_references": [],
            "client_request_id": request_id,
        },
    )
    assert response.status_code == 202, response.text
    turn = await wait_for_turn(client, response.json()["turn_id"])
    assert turn["status"] == "completed", (
        turn.get("error_code"),
        turn.get("error_message"),
    )
    return turn


async def assistant_text(
    client: httpx.AsyncClient, session_id: str, turn_id: str
) -> str:
    response = await client.get(f"/api/sessions/{session_id}/messages")
    response.raise_for_status()
    return "\n".join(
        str(message["payload"].get("text", ""))
        for message in response.json()
        if message["turn_id"] == turn_id
        and message["event_type"]
        in {"message.assistant.delta", "message.assistant.completed"}
    )


async def assert_nonzero_usage(
    client: httpx.AsyncClient, session_id: str, turn: dict
) -> None:
    response = await client.get(f"/api/sessions/{session_id}/messages")
    response.raise_for_status()
    usage_events = [
        message["payload"]
        for message in response.json()
        if message["turn_id"] == turn["id"]
        and message["event_type"] == "usage.updated"
    ]
    assert (turn.get("input_tokens") or 0) > 0, usage_events
    assert (turn.get("output_tokens") or 0) > 0, usage_events


def sandbox_connection(environment: dict[str, str]) -> ConnectionConfig:
    parsed = urlsplit(environment["DOCKER_WEB_OPENSANDBOX_URL"])
    assert parsed.scheme in {"http", "https"} and parsed.netloc
    return ConnectionConfig(
        api_key=environment["OPENSANDBOX_API_KEY"],
        domain=parsed.netloc,
        protocol=parsed.scheme,
        request_timeout=timedelta(seconds=60),
        use_server_proxy=True,
    )


async def load_execution_records(
    database_url: str, session_ids: tuple[str, str]
) -> tuple[list[dict], list[dict], str]:
    connection = await asyncpg.connect(database_url)
    try:
        sandboxes = await connection.fetch(
            """
            SELECT session_id, generation, sandbox_id, session_volume_name,
                   memory_volume_name, runner_image, created_at
              FROM session_sandboxes
             WHERE session_id = ANY($1::varchar[])
             ORDER BY session_id
            """,
            list(session_ids),
        )
        attempts = await connection.fetch(
            """
            SELECT t.session_id, a.sandbox_id, a.command_execution_id
              FROM turn_attempts AS a
              JOIN turns AS t ON t.id = a.turn_id
             WHERE t.session_id = ANY($1::varchar[])
               AND a.command_execution_id IS NOT NULL
             ORDER BY a.assigned_at
            """,
            list(session_ids),
        )
        persisted = await connection.fetchval(
            """
            SELECT concat_ws(E'\n',
                coalesce(string_agg(t.input_text, E'\n'), ''),
                coalesce((SELECT string_agg(m.payload_json, E'\n')
                            FROM messages AS m
                           WHERE m.session_id = ANY($1::varchar[])), '')
            )
              FROM turns AS t
             WHERE t.session_id = ANY($1::varchar[])
            """,
            list(session_ids),
        )
    finally:
        await connection.close()
    return (
        [dict(record) for record in sandboxes],
        [dict(record) for record in attempts],
        str(persisted or ""),
    )


def inspect_api_boundary(project: str) -> tuple[str, str]:
    container = f"{project}-api-1"
    inspected = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
        check=True,
        capture_output=True,
        text=True,
    )
    values = json.loads(inspected.stdout)
    assert all(not value.startswith("ANTHROPIC_API_KEY=") for value in values)
    assert all(not value.startswith("OPENSANDBOX_API_KEY=") for value in values)
    image = subprocess.run(
        ["docker", "inspect", "--format", "{{.Image}}", container],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert image.startswith("sha256:")
    return container, image


def assert_volume_has_no_secret(image: str, volume: str, secret: str) -> None:
    scanner = """
import pathlib, sys
needle = sys.stdin.buffer.read()
for path in pathlib.Path('/scan').rglob('*'):
    if path.is_file():
        try:
            if needle in path.read_bytes():
                raise SystemExit(1)
        except OSError:
            pass
raise SystemExit(0)
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--read-only",
            "--network",
            "none",
            "--entrypoint",
            "python",
            "--mount",
            f"type=volume,src={volume},dst=/scan,readonly",
            image,
            "-c",
            scanner,
        ],
        input=secret,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"credential canary found in volume {volume}"


def docker_logs(name: str) -> str:
    result = subprocess.run(
        ["docker", "logs", "--tail", "1000", name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout + result.stderr


async def collect_failed_frames(
    adapter: OpenSandboxAdapter, handle: SandboxHandle
) -> tuple[list[RunnerFrame], int | None]:
    command = await adapter.run_turn(handle, "/session/control/request.json")
    cursor = None
    frames: list[RunnerFrame] = []
    deadline = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < deadline:
        batch = await adapter.read_frames(command, cursor)
        cursor = batch.next_cursor
        frames.extend(RunnerFrame.model_validate_json(line) for line in batch.lines)
        if batch.complete:
            observation = await adapter.inspect_command(command)
            return frames, observation.exit_code
        await asyncio.sleep(0.25)
    pytest.fail("Timed out waiting for the Credential Vault fail-closed replay")


@pytest.mark.asyncio
async def test_packaged_claude_skill_resume_memory_and_secret_boundary() -> None:
    environment = live_environment()
    run_id = uuid.uuid4().hex
    skill_name = f"packaged-live-{run_id[:12]}"
    skill_marker = f"SKILL-{run_id}"
    memory_marker = f"MEMORY-{run_id}"
    api_key = environment["ANTHROPIC_API_KEY"]

    async with httpx.AsyncClient(
        base_url=environment["DOCKER_WEB_URL"], timeout=30
    ) as client:
        health = await client.get("/api/health")
        health.raise_for_status()
        assert health.json()["status"] == "ready"

        skill = await client.post(
            "/api/workspaces/example/skills",
            json={
                "content": f"""---
name: {skill_name}
description: Use for the packaged Docker Web live gate marker.
---
When invoked, reply with this exact marker and no other text: {skill_marker}
"""
            },
        )
        assert skill.status_code == 201, skill.text
        created_skill = skill.json()
        assert created_skill["enabled"] is False
        enabled_skill = await client.patch(
            f"/api/skills/{created_skill['id']}",
            json={
                "expected_hash": created_skill["bundle_hash"],
                "enabled": True,
            },
        )
        enabled_skill.raise_for_status()
        assert enabled_skill.json()["enabled"] is True

        first_session_response = await client.post(
            "/api/workspaces/example/sessions"
        )
        first_session_response.raise_for_status()
        first_session = first_session_response.json()
        skill_turn = await execute_turn(
            client,
            first_session["id"],
            f"/{skill_name} Return only the exact managed Skill marker.",
            f"live-skill-{run_id[:16]}",
        )
        assert skill_marker in await assistant_text(
            client, first_session["id"], skill_turn["id"]
        )
        await assert_nonzero_usage(client, first_session["id"], skill_turn)
        resumed_before = (await client.get(f"/api/sessions/{first_session['id']}")).json()
        assert resumed_before["claude_session_id"]

        memory_turn = await execute_turn(
            client,
            first_session["id"],
            f"Use Write to create /memory/MEMORY.md containing the exact marker "
            f"{memory_marker}. Use Read to verify it, then reply only SAVED.",
            f"live-memory-write-{run_id[:16]}",
        )
        assert "SAVED" in (
            await assistant_text(client, first_session["id"], memory_turn["id"])
        ).upper()
        await assert_nonzero_usage(client, first_session["id"], memory_turn)
        resumed_after = (await client.get(f"/api/sessions/{first_session['id']}")).json()
        assert resumed_after["claude_session_id"] == resumed_before["claude_session_id"]

        second_session_response = await client.post(
            "/api/workspaces/example/sessions"
        )
        second_session_response.raise_for_status()
        second_session = second_session_response.json()
        recall_turn = await execute_turn(
            client,
            second_session["id"],
            "Read Auto Memory and reply only with the previously saved MEMORY marker.",
            f"live-memory-read-{run_id[:16]}",
        )
        assert memory_marker in await assistant_text(
            client, second_session["id"], recall_turn["id"]
        )
        await assert_nonzero_usage(client, second_session["id"], recall_turn)

    session_ids = (first_session["id"], second_session["id"])
    records, attempts, persisted_events = await load_execution_records(
        environment["DOCKER_WEB_POSTGRES_URL"], session_ids
    )
    assert len(records) == 2
    assert attempts
    assert api_key not in persisted_events

    _api_container, application_image = inspect_api_boundary(
        environment["DOCKER_WEB_PROJECT"]
    )
    assert_volume_has_no_secret(
        application_image,
        f"{environment['DOCKER_WEB_PROJECT']}_app-data",
        api_key,
    )

    connection = sandbox_connection(environment)
    by_session = {record["session_id"]: record for record in records}
    for record in records:
        assert record["sandbox_id"]
        sandbox = await Sandbox.connect(
            record["sandbox_id"], connection_config=connection
        )
        environment_probe = await sandbox.commands.run(
            "printf '%s\\n' \"${ANTHROPIC_API_KEY:-}\""
        )
        assert environment_probe.exit_code == 0
        assert environment_probe.text.strip() == "opensandbox-vault-placeholder"
        request_text = await sandbox.files.read_file("/session/control/request.json")
        assert api_key not in request_text
        assert api_key not in docker_logs(f"sandbox-{record['sandbox_id']}")
        assert api_key not in docker_logs(f"sandbox-egress-{record['sandbox_id']}")
        assert_volume_has_no_secret(
            application_image, record["session_volume_name"], api_key
        )
        assert_volume_has_no_secret(
            application_image, record["memory_volume_name"], api_key
        )

    for attempt in attempts:
        sandbox = await Sandbox.connect(
            attempt["sandbox_id"], connection_config=connection
        )
        logs = await sandbox.commands.get_background_command_logs(
            attempt["command_execution_id"]
        )
        assert api_key not in logs.content

    second_record = by_session[second_session["id"]]
    sandbox = await Sandbox.connect(
        second_record["sandbox_id"], connection_config=connection
    )
    await sandbox.credential_vault.delete()
    adapter = OpenSandboxAdapter(
        api_url=environment["DOCKER_WEB_OPENSANDBOX_URL"],
        api_key=environment["OPENSANDBOX_API_KEY"],
        ready_timeout_seconds=120,
        command_timeout_seconds=180,
    )
    handle = SandboxHandle(
        sandbox_id=second_record["sandbox_id"],
        generation=second_record["generation"],
        created_at=second_record["created_at"],
        image_reference=second_record["runner_image"],
        session_volume_name=second_record["session_volume_name"],
        memory_volume_name=second_record["memory_volume_name"],
    )
    frames, exit_code = await collect_failed_frames(adapter, handle)
    terminal = [frame for frame in frames if frame.kind == "terminal"]
    assert exit_code not in {None, 0}
    assert terminal and terminal[-1].payload.get("status") == "failed"
    assert api_key not in "\n".join(frame.to_line() for frame in frames)
    post_delete_environment = await sandbox.commands.run(
        "printf '%s\\n' \"${ANTHROPIC_API_KEY:-}\""
    )
    assert post_delete_environment.text.strip() == "opensandbox-vault-placeholder"
