import asyncio
import os
import shlex
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OPENSANDBOX_DOCKER") != "1",
    reason="RUN_OPENSANDBOX_DOCKER=1 is required for the real Docker gate",
)


@pytest.mark.asyncio
async def test_real_opensandbox_runs_fake_runner_and_retains_session_volume(
    tmp_path,
) -> None:
    from opensandbox import Sandbox

    from app.runner.protocol import RunnerRequest
    from app.sandbox.contracts import SandboxError
    from app.sandbox.models import SessionSandboxSpec
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    image = os.environ["OPENSANDBOX_RUNNER_IMAGE"]
    api_key = os.environ["OPENSANDBOX_API_KEY"]
    prefix = f"phase2a-{uuid.uuid4().hex}"
    adapter = OpenSandboxAdapter(
        api_url=os.getenv("OPENSANDBOX_API_URL", "http://127.0.0.1:8080"),
        api_key=api_key,
        ready_timeout_seconds=90,
        request_timeout_seconds=120,
    )

    def spec(generation: int) -> SessionSandboxSpec:
        return SessionSandboxSpec(
            session_key=prefix,
            memory_scope_key=f"memory-{prefix}",
            generation=generation,
            runner_image=image,
            allowed_hosts=(),
            credential_proxy_required=False,
            timeout_seconds=300,
            model_config=None,
        )

    handles = []
    volume_names: set[str] = set()
    try:
        first = await adapter.create_session_sandbox(spec(1))
        handles.append(first)
        volume_names.update((first.session_volume_name, first.memory_volume_name))
        host_workspace = tmp_path / "workspace"
        (host_workspace / ".claude/skills/gate").mkdir(parents=True)
        (host_workspace / ".claude/skills/gate/SKILL.md").write_text(
            "managed skill", encoding="utf-8"
        )
        await adapter.sync_workspace(first, host_workspace)
        request = RunnerRequest(
            protocol_version="1",
            runtime_kind="fake",
            user_id="gate-user",
            workspace_id="gate-workspace",
            platform_session_id=prefix,
            turn_id="turn-1",
            attempt_id="attempt-1",
            generation=1,
            memory_scope_key=f"memory-{prefix}",
            text="hello",
            workspace_snapshot={},
        )
        path = await adapter.write_request(first, request.to_bytes())
        command = await adapter.run_turn(first, path)
        cursor = None
        lines: list[str] = []
        for _ in range(120):
            batch = await adapter.read_frames(command, cursor)
            cursor = batch.next_cursor
            lines.extend(batch.lines)
            if batch.complete:
                break
            await asyncio.sleep(0.25)
        assert batch.complete is True
        assert any('"kind":"terminal"' in line for line in lines)
        assert any("Fake response" in line for line in lines)

        connected = await Sandbox.connect(
            first.sandbox_id, connection_config=adapter._connection
        )
        assert (
            await connected.files.read_file(
                "/session/workspace/.claude/skills/gate/SKILL.md"
            )
            == "managed skill"
        )
        await connected.files.write_file(
            "/session/workspace/retained.txt", "retained", mode=600
        )
        security = await connected.commands.run(
            "test ! -e /var/run/docker.sock && "
            'test -z "${DATABASE_URL:-}" && '
            'test -z "${OPENSANDBOX_API_KEY:-}" && '
            'test -z "${ANTHROPIC_API_KEY:-}" && '
            'test -z "${ANTHROPIC_BASE_URL:-}" && '
            "! python -c 'import socket; "
            'socket.create_connection(("example.com", 443), 1)\''
        )
        assert security.exit_code == 0
        await adapter.destroy_sandbox(first.sandbox_id)
        handles.clear()

        second = await adapter.create_session_sandbox(spec(2))
        handles.append(second)
        assert second.session_volume_name == first.session_volume_name
        assert second.memory_volume_name == first.memory_volume_name
        connected = await Sandbox.connect(
            second.sandbox_id, connection_config=adapter._connection
        )
        assert (
            await connected.files.read_file("/session/workspace/retained.txt")
            == "retained"
        )
    finally:
        for handle in handles:
            try:
                await adapter.destroy_sandbox(handle.sandbox_id)
            except SandboxError:
                pass
        if os.getenv("KEEP_OPENSANDBOX_TEST_VOLUMES") != "1":
            for volume in volume_names:
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    "volume",
                    "rm",
                    volume,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await process.wait()


@pytest.mark.asyncio
async def test_real_credential_vault_injects_synthetic_key() -> None:
    if os.getenv("RUN_OPENSANDBOX_CREDENTIAL_VAULT") != "1":
        pytest.skip("RUN_OPENSANDBOX_CREDENTIAL_VAULT=1 is required")
    base_url = os.getenv("OPENSANDBOX_CREDENTIAL_TEST_BASE_URL")
    if not base_url:
        pytest.skip("OPENSANDBOX_CREDENTIAL_TEST_BASE_URL is required")

    from opensandbox import Sandbox
    from pydantic import SecretStr

    from app.sandbox.contracts import SandboxError
    from app.sandbox.credentials import ModelCredential, parse_model_endpoint
    from app.sandbox.models import RunnerModelConfig, SessionSandboxSpec
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    image = os.environ["OPENSANDBOX_RUNNER_IMAGE"]
    prefix = f"phase2a1-vault-{uuid.uuid4().hex}"
    canary = f"phase2a1-{uuid.uuid4().hex}-{uuid.uuid4().hex}"
    endpoint_host = parse_model_endpoint(base_url, (_hostname(base_url),)).host
    endpoint = parse_model_endpoint(base_url, (endpoint_host,))
    adapter = OpenSandboxAdapter(
        api_url=os.getenv("OPENSANDBOX_API_URL", "http://127.0.0.1:8080"),
        api_key=os.environ["OPENSANDBOX_API_KEY"],
        ready_timeout_seconds=90,
        request_timeout_seconds=120,
    )
    spec = SessionSandboxSpec(
        session_key=prefix,
        memory_scope_key=f"memory-{prefix}",
        generation=1,
        runner_image=image,
        allowed_hosts=(endpoint.host,),
        credential_proxy_required=True,
        timeout_seconds=300,
        model_config=RunnerModelConfig(base_url=endpoint.base_url),
    )
    handle = None
    volume_names: set[str] = set()
    try:
        handle = await adapter.create_session_sandbox(spec)
        volume_names.update((handle.session_volume_name, handle.memory_volume_name))
        await adapter.ensure_model_credential(
            handle,
            ModelCredential(endpoint=endpoint, api_key=SecretStr(canary)),
        )
        connected = await Sandbox.connect(
            handle.sandbox_id,
            connection_config=adapter._connection,
        )
        probe_path = endpoint.binding_path.removesuffix("*") + "probe"
        probe_url = f"https://{endpoint.host}{probe_path}"
        command = _credential_probe_command(probe_url)

        injected = await connected.commands.run(command)
        assert injected.exit_code == 0
        assert _constant_time_equal(injected.text.strip(), canary), (
            "Credential Vault did not inject the synthetic credential"
        )

        safe_environment = await connected.commands.run(
            "printf '%s\\n' \"${ANTHROPIC_API_KEY:-}\""
        )
        assert safe_environment.exit_code == 0
        assert safe_environment.text.strip() == "opensandbox-vault-placeholder"
        assert not _constant_time_equal(safe_environment.text.strip(), canary)

        await connected.credential_vault.delete()
        without_vault = await connected.commands.run(command)
        assert without_vault.exit_code != 0 or not _constant_time_equal(
            without_vault.text.strip(), canary
        )
    finally:
        if handle is not None:
            try:
                await adapter.destroy_sandbox(handle.sandbox_id)
            except SandboxError:
                pass
        if os.getenv("KEEP_OPENSANDBOX_TEST_VOLUMES") != "1":
            for volume in volume_names:
                process = await asyncio.create_subprocess_exec(
                    "docker",
                    "volume",
                    "rm",
                    volume,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await process.wait()


def _hostname(base_url: str) -> str:
    from urllib.parse import urlsplit

    host = urlsplit(base_url).hostname
    if host is None:
        raise ValueError("credential test endpoint must include a hostname")
    return host


def _credential_probe_command(probe_url: str) -> str:
    script = (
        "import httpx; "
        f"response=httpx.get({probe_url!r}, "
        "headers={'x-api-key':'opensandbox-vault-placeholder'}, timeout=20); "
        "response.raise_for_status(); print(response.text, end='')"
    )
    return f"python -c {shlex.quote(script)}"


def _constant_time_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode(), right.encode())
