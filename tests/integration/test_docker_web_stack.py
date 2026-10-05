from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_STACK = os.getenv("RUN_DOCKER_WEB_STACK") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_STACK,
    reason="RUN_DOCKER_WEB_STACK=1 is required for the Docker Web gate",
)


def required_environment() -> dict[str, str]:
    names = (
        "DOCKER_WEB_URL",
        "DOCKER_WEB_PROJECT",
        "DOCKER_WEB_RUNTIME_DIR",
        "DOCKER_WEB_TEST_STATE",
    )
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise AssertionError("missing required environment: " + ", ".join(missing))
    return {name: os.environ[name] for name in names}


def compose_command(environment: dict[str, str], *args: str) -> list[str]:
    runtime_dir = Path(environment["DOCKER_WEB_RUNTIME_DIR"])
    return [
        "docker",
        "compose",
        "--project-name",
        environment["DOCKER_WEB_PROJECT"],
        "--env-file",
        str(runtime_dir / "compose.env"),
        "-f",
        str(ROOT / "deploy/docker-web/compose.yaml"),
        "-f",
        str(ROOT / "deploy/docker-web/compose.gate.yaml"),
        *args,
    ]


def run_checked(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {' '.join(command)}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


async def wait_for_health(
    client: httpx.AsyncClient, expected: str, *, timeout_seconds: float = 30
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        try:
            response = await client.get("/api/health")
            if response.status_code == 200:
                last = response.json()
                if last.get("status") == expected:
                    return last
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise AssertionError(f"health did not become {expected}: {last!r}")


async def wait_for_turn(
    client: httpx.AsyncClient, turn_id: str, *, timeout_seconds: float = 180
) -> dict:
    # A clean Docker host may need to pull OpenSandbox's execd and egress
    # images before the first session runner can become healthy.
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/turns/{turn_id}")
        response.raise_for_status()
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return last
        await asyncio.sleep(0.25)
    raise AssertionError(f"turn did not finish: {last!r}")


def assert_fake_response(messages: list[dict]) -> None:
    assert any(
        message["event_type"] == "message.assistant.completed"
        and "Fake response" in message["payload"].get("text", "")
        for message in messages
    )


def inspect_session_ports(environment: dict[str, str]) -> bool:
    query = "SELECT sandbox_id FROM session_sandboxes WHERE sandbox_id IS NOT NULL;"
    output = run_checked(
        compose_command(
            environment,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "workspace",
            "-d",
            "workspace",
            "-Atqc",
            query,
        )
    )
    sandbox_ids = [line.strip() for line in output.splitlines() if line.strip()]
    assert sandbox_ids
    observed_runner = False
    observed_wildcard = False
    for sandbox_id in sandbox_ids:
        for name in (f"sandbox-{sandbox_id}", f"sandbox-egress-{sandbox_id}"):
            result = subprocess.run(
                (
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .NetworkSettings.Ports}}",
                    name,
                ),
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                continue
            if name == f"sandbox-{sandbox_id}":
                observed_runner = True
            ports = json.loads(result.stdout)
            for bindings in ports.values():
                for binding in bindings or []:
                    host_port = int(binding["HostPort"])
                    assert 40000 <= host_port <= 60000
                    observed_wildcard |= binding.get("HostIp") == "0.0.0.0"
    assert observed_runner
    return observed_wildcard


@pytest.mark.asyncio
async def test_fake_stack_recovers_worker_and_persists_history() -> None:
    environment = required_environment()
    launcher_env = {
        "DOCKER_WEB_PROJECT": environment["DOCKER_WEB_PROJECT"],
        "DOCKER_WEB_RUNTIME_DIR": environment["DOCKER_WEB_RUNTIME_DIR"],
        "DOCKER_WEB_GATE": "1",
    }
    if os.getenv("DOCKER_WEB_CONFIG_FILE"):
        launcher_env["DOCKER_WEB_CONFIG_FILE"] = os.environ["DOCKER_WEB_CONFIG_FILE"]

    async with httpx.AsyncClient(
        base_url=environment["DOCKER_WEB_URL"], timeout=10
    ) as client:
        health = await wait_for_health(client, "ready")
        assert health["execution"]["worker"] == "available"

        created = await client.post("/api/workspaces/example/sessions")
        created.raise_for_status()
        session = created.json()
        first = await client.post(
            f"/api/sessions/{session['id']}/turns",
            json={
                "message": "fake gate",
                "attachment_ids": [],
                "file_references": [],
                "client_request_id": "phase2a2-fake-turn",
            },
        )
        assert first.status_code == 202
        first_turn = await wait_for_turn(client, first.json()["turn_id"])
        assert first_turn["status"] == "completed"
        messages = (await client.get(f"/api/sessions/{session['id']}/messages")).json()
        assert_fake_response(messages)

        run_checked(compose_command(environment, "stop", "worker"))
        degraded = await wait_for_health(client, "degraded")
        assert degraded["execution"]["worker"] == "unavailable"
        recovery_payload = {
            "message": "recovery gate",
            "attachment_ids": [],
            "file_references": [],
            "client_request_id": "phase2a2-recovery-turn",
        }
        rejected = await client.post(
            f"/api/sessions/{session['id']}/turns", json=recovery_payload
        )
        assert rejected.status_code == 503
        assert rejected.json()["error"]["code"] == "execution_unavailable"

        run_checked(compose_command(environment, "up", "-d", "worker"))
        recovered = await wait_for_health(client, "ready")
        assert recovered["execution"]["worker"] == "available"
        retried = await client.post(
            f"/api/sessions/{session['id']}/turns", json=recovery_payload
        )
        assert retried.status_code == 202
        recovered_turn = await wait_for_turn(client, retried.json()["turn_id"])
        assert recovered_turn["status"] == "completed"

    state_path = Path(environment["DOCKER_WEB_TEST_STATE"])
    state_path.write_text(
        json.dumps(
            {"session_id": session["id"], "turn_id": first_turn["id"]},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    run_checked(["bash", str(ROOT / "scripts/docker-web.sh"), "down"], env=launcher_env)
    startup_output = run_checked(
        ["bash", str(ROOT / "scripts/docker-web.sh"), "up", "--fake"],
        env=launcher_env,
    )
    assert "OpenSandbox v0.2.2 publishes dynamic Runner ports" in startup_output

    async with httpx.AsyncClient(
        base_url=environment["DOCKER_WEB_URL"], timeout=10
    ) as client:
        await wait_for_health(client, "ready")
        sessions = (await client.get("/api/workspaces/example/sessions")).json()
        assert session["id"] in {item["id"] for item in sessions}
        persisted_messages = (
            await client.get(f"/api/sessions/{session['id']}/messages")
        ).json()
        assert_fake_response(persisted_messages)

    wildcard = inspect_session_ports(environment)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["observed_wildcard_dynamic_port"] = wildcard
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
