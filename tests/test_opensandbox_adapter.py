from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.sandboxes import CredentialVaultState


class FakeCredentialVault:
    def __init__(self) -> None:
        self.state = None
        self.created = []
        self.patched = []
        self.get_calls = 0
        self.patch_conflicts = 0
        self.get_error = None

    async def get(self):
        self.get_calls += 1
        if self.get_error is not None:
            raise self.get_error
        if self.state is None:
            raise SandboxApiException("missing", status_code=404)
        return self.state

    async def create(self, *, credentials, bindings):
        self.created.append((credentials, bindings))
        self.state = CredentialVaultState(revision=1, credentials=[], bindings=[])
        return self.state

    async def patch(self, **kwargs):
        self.patched.append(kwargs)
        if self.patch_conflicts:
            self.patch_conflicts -= 1
            raise SandboxApiException("conflict with secret body", status_code=409)
        self.state = CredentialVaultState(
            revision=kwargs["expected_revision"] + 1,
            credentials=[],
            bindings=[],
        )
        return self.state


class FakeFiles:
    def __init__(self) -> None:
        self.writes: list[tuple[str, bytes, int]] = []

    async def write_file(self, path, data, **kwargs):
        self.writes.append((path, data, kwargs["mode"]))


class FakeCommands:
    def __init__(self) -> None:
        self.command = None
        self.interrupted = None

    async def run(self, command, *, opts=None, handlers=None):
        self.command = (command, opts, handlers)
        return SimpleNamespace(id="execution-1")

    async def get_background_command_logs(self, execution_id, cursor=None):
        assert execution_id == "execution-1"
        return SimpleNamespace(
            content='{"kind":"phase"}\n{"kind":"terminal"}\n', cursor=7
        )

    async def get_command_status(self, execution_id):
        assert execution_id == "execution-1"
        return SimpleNamespace(running=False, exit_code=0, error=None)

    async def interrupt(self, execution_id):
        self.interrupted = execution_id


class FakeSandbox:
    id = "sandbox-1"

    def __init__(self) -> None:
        self.files = FakeFiles()
        self.commands = FakeCommands()
        self.credential_vault = FakeCredentialVault()
        self.renewed = None
        self.killed = False

    async def get_info(self):
        return SimpleNamespace(
            id=self.id,
            status=SimpleNamespace(state="RUNNING", reason=None),
            created_at=datetime(2026, 7, 31, tzinfo=UTC),
        )

    async def renew(self, timeout):
        self.renewed = timeout

    async def kill(self):
        self.killed = True


@pytest.fixture
def fake_sdk(monkeypatch):
    import app.sandbox.opensandbox_adapter as adapter_module

    sandbox = FakeSandbox()
    calls = []

    async def create(image, **kwargs):
        calls.append((image, kwargs))
        return sandbox

    async def connect(sandbox_id, **kwargs):
        assert sandbox_id == "sandbox-1"
        return sandbox

    monkeypatch.setattr(adapter_module.Sandbox, "create", create)
    monkeypatch.setattr(adapter_module.Sandbox, "connect", connect)
    return sandbox, calls


def sandbox_spec():
    from app.sandbox.models import RunnerModelConfig, SessionSandboxSpec

    return SessionSandboxSpec(
        session_key="session-opaque",
        memory_scope_key="memory-opaque",
        generation=4,
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com", "mcp.example.com"),
        credential_proxy_required=True,
        timeout_seconds=900,
        model_config=RunnerModelConfig(base_url="https://api.anthropic.com"),
    )


def model_credential():
    from pydantic import SecretStr

    from app.sandbox.credentials import ModelCredential, parse_model_endpoint

    return ModelCredential(
        endpoint=parse_model_endpoint(
            "https://dashscope.aliyuncs.com/apps/anthropic",
            ("dashscope.aliyuncs.com",),
        ),
        api_key=SecretStr("phase2a-real-canary"),
    )


@pytest.mark.asyncio
async def test_create_maps_application_spec_to_official_sdk_types(fake_sdk) -> None:
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    _sandbox, calls = fake_sdk
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    handle = await adapter.create_session_sandbox(sandbox_spec())

    assert handle.sandbox_id == "sandbox-1"
    assert handle.generation == 4
    assert len(calls) == 1
    image, kwargs = calls[0]
    assert image == "runner@sha256:" + "a" * 64
    assert kwargs["metadata"] == {
        "workspace-agent.session": "session-opaque",
        "workspace-agent.generation": "4",
    }
    assert kwargs["network_policy"].default_action == "deny"
    assert [(rule.action, rule.target) for rule in kwargs["network_policy"].egress] == [
        ("allow", "api.anthropic.com"),
        ("allow", "mcp.example.com"),
    ]
    assert kwargs["credential_proxy"].enabled is True
    assert [(volume.name, volume.mount_path) for volume in kwargs["volumes"]] == [
        (handle.session_volume_name, "/session"),
        (handle.memory_volume_name, "/memory"),
    ]
    assert [volume.pvc.claim_name for volume in kwargs["volumes"]] == [
        handle.session_volume_name,
        handle.memory_volume_name,
    ]
    assert all(
        volume.pvc.delete_on_sandbox_termination is False
        for volume in kwargs["volumes"]
    )
    assert kwargs["entrypoint"] == ["tail", "-f", "/dev/null"]
    assert kwargs["connection_config"].use_server_proxy is True
    assert kwargs["env"] == {
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        "ANTHROPIC_API_KEY": "opensandbox-vault-placeholder",
        "WORKSPACES_ROOT": "/session/workspace",
        "APP_DATA_DIR": "/session/runtime-data",
    }
    assert "top-secret-test-key" not in repr(kwargs["env"])
    assert "worker-secret" not in repr(adapter)


@pytest.mark.asyncio
async def test_create_omits_model_environment_for_fake_runner(fake_sdk) -> None:
    from app.sandbox.models import SessionSandboxSpec
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    _sandbox, calls = fake_sdk
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    await adapter.create_session_sandbox(
        SessionSandboxSpec(
            session_key="session-fake",
            memory_scope_key="memory-fake",
            generation=1,
            runner_image="runner@sha256:" + "b" * 64,
            allowed_hosts=(),
            credential_proxy_required=False,
            timeout_seconds=900,
            model_config=None,
        )
    )

    _image, kwargs = calls[0]
    assert "env" not in kwargs


@pytest.mark.asyncio
async def test_command_and_file_operations_preserve_resume_identity(
    fake_sdk, tmp_path
) -> None:
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    sandbox, _calls = fake_sdk
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    handle = await adapter.create_session_sandbox(sandbox_spec())
    workspace = tmp_path / "workspace"
    (workspace / ".claude/skills/review").mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("rules", encoding="utf-8")
    (workspace / ".claude/skills/review/SKILL.md").write_text("skill", encoding="utf-8")
    await adapter.sync_workspace(handle, workspace)
    request_path = await adapter.write_request(handle, b'{"protocol_version":"1"}')
    command = await adapter.run_turn(handle, request_path)
    frames = await adapter.read_frames(command, None)
    observation = await adapter.inspect_command(command)
    cancelled = await adapter.cancel_command(command)
    await adapter.renew_sandbox(handle.sandbox_id, 120)
    await adapter.destroy_sandbox(handle.sandbox_id)

    assert request_path == "/session/control/request.json"
    assert sandbox.files.writes == [
        ("/session/workspace/.claude/skills/review/SKILL.md", b"skill", 600),
        ("/session/workspace/CLAUDE.md", b"rules", 600),
        ("/session/control/request.json", b'{"protocol_version":"1"}', 600),
    ]
    assert "app.runner.main" in sandbox.commands.command[0]
    assert sandbox.commands.command[1].background is True
    assert sandbox.commands.command[1].working_directory == "/session/workspace"
    assert command.command_session_id == "execution-1"
    assert command.execution_id == "execution-1"
    assert frames.lines == ('{"kind":"phase"}', '{"kind":"terminal"}')
    assert frames.next_cursor == "7"
    assert observation.status == "succeeded"
    assert cancelled.accepted is True
    assert sandbox.commands.interrupted == "execution-1"
    assert sandbox.renewed.total_seconds() == 120
    assert sandbox.killed is True


@pytest.mark.asyncio
async def test_workspace_sync_rejects_symbolic_links(fake_sdk, tmp_path) -> None:
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    _sandbox, _calls = fake_sdk
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(target)
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    handle = await adapter.create_session_sandbox(sandbox_spec())

    with pytest.raises(ValueError, match="symbolic links"):
        await adapter.sync_workspace(handle, workspace)


@pytest.mark.asyncio
async def test_sdk_errors_are_stable_and_redacted(monkeypatch) -> None:
    import app.sandbox.opensandbox_adapter as adapter_module
    from app.sandbox.contracts import SandboxUnavailable
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    async def create(_image, **_kwargs):
        raise RuntimeError("request failed with Authorization: worker-secret")

    monkeypatch.setattr(adapter_module.Sandbox, "create", create)
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )

    with pytest.raises(SandboxUnavailable) as raised:
        await adapter.create_session_sandbox(sandbox_spec())
    assert raised.value.code == "sandbox_unavailable"
    assert "worker-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_credential_vault_is_created_then_refreshed_with_official_models(
    fake_sdk,
) -> None:
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    sandbox, _calls = fake_sdk
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    handle = await adapter.create_session_sandbox(sandbox_spec())
    credential = model_credential()

    await adapter.ensure_model_credential(handle, credential)
    await adapter.ensure_model_credential(handle, credential)

    created_credentials, created_bindings = sandbox.credential_vault.created[0]
    assert created_credentials[0].name == "workspace-agent-model-api-key"
    assert created_credentials[0].source.value == "phase2a-real-canary"
    assert created_bindings[0].name == "workspace-agent-model-endpoint"
    assert created_bindings[0].match.hosts == ["dashscope.aliyuncs.com"]
    assert created_bindings[0].match.schemes == ["https"]
    assert created_bindings[0].match.methods == ["GET", "POST"]
    assert created_bindings[0].match.paths == ["/apps/anthropic/v1/*"]
    assert created_bindings[0].auth.model_dump(exclude_none=True) == {
        "type": "apiKey",
        "credential": "workspace-agent-model-api-key",
        "name": "x-api-key",
    }
    patch = sandbox.credential_vault.patched[0]
    assert patch["expected_revision"] == 1
    assert patch["credentials"].replace[0].name == ("workspace-agent-model-api-key")
    assert patch["bindings"].replace[0].name == ("workspace-agent-model-endpoint")
    assert "phase2a-real-canary" not in repr(credential)
    assert "phase2a-real-canary" not in repr(adapter)


@pytest.mark.asyncio
async def test_credential_vault_retries_one_revision_conflict(fake_sdk) -> None:
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    sandbox, _calls = fake_sdk
    sandbox.credential_vault.state = CredentialVaultState(
        revision=7,
        credentials=[],
        bindings=[],
    )
    sandbox.credential_vault.patch_conflicts = 1
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    handle = await adapter.create_session_sandbox(sandbox_spec())

    await adapter.ensure_model_credential(handle, model_credential())

    assert sandbox.credential_vault.get_calls == 2
    assert len(sandbox.credential_vault.patched) == 2


@pytest.mark.asyncio
async def test_credential_vault_terminal_errors_are_stable_and_redacted(
    fake_sdk,
) -> None:
    from app.sandbox.contracts import CredentialProxyUnavailable
    from app.sandbox.opensandbox_adapter import OpenSandboxAdapter

    sandbox, _calls = fake_sdk
    sandbox.credential_vault.state = CredentialVaultState(
        revision=7,
        credentials=[],
        bindings=[],
    )
    sandbox.credential_vault.patch_conflicts = 2
    adapter = OpenSandboxAdapter(
        api_url="http://127.0.0.1:8080",
        api_key="worker-secret",
        ready_timeout_seconds=30,
    )
    handle = await adapter.create_session_sandbox(sandbox_spec())

    with pytest.raises(CredentialProxyUnavailable) as raised:
        await adapter.ensure_model_credential(handle, model_credential())

    assert raised.value.code == "credential_proxy_unavailable"
    assert str(raised.value) == "Sandbox credential proxy is unavailable."
    assert "phase2a-real-canary" not in str(raised.value)
    assert "conflict with secret body" not in str(raised.value)
