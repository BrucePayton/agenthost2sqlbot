from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tests.test_workspaces import write_workspace


def test_build_app_services_constructs_one_dependency_graph(settings_factory) -> None:
    from app.bootstrap import build_app_services
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    runtime = FakeAgentRuntime()

    services = build_app_services(settings, runtime=runtime)

    assert services.settings is settings
    assert services.runtime is runtime
    assert services.sessions.database is services.database
    assert services.attachments.database is services.database
    assert services.turns.database is services.database
    assert services.sessions.locks is services.attachments.locks
    assert services.attachments.locks is services.turns.locks
    assert services.turns._require_dispatcher() is services.dispatcher
    assert services.skills.repository.artifacts.root == (
        settings.resolved_skill_artifact_root
    )
    assert services.skill_artifacts is services.skills.repository.artifacts
    assert services.skills.platform_access is services.platform_skill_access
    assert services.skill_artifact_migrator.artifacts is services.skill_artifacts
    assert (
        services.skill_artifact_garbage_collector.artifacts
        is services.skill_artifacts
    )


def test_build_app_services_selects_obid_identity_provider(settings_factory) -> None:
    from app.auth.obid import ObIdIdentityProvider
    from app.bootstrap import build_app_services
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        app_env="uat",
        identity_mode="obid",
        database_url="postgresql+asyncpg://agent:test@localhost/agent",
    )

    services = build_app_services(settings, runtime=FakeAgentRuntime())

    assert isinstance(services.identity_provider, ObIdIdentityProvider)


def test_local_obid_runtime_receives_davinci_mcp_credentials(
    settings_factory,
) -> None:
    from app.bootstrap import build_app_services

    settings = settings_factory(
        identity_mode="obid",
        davinci_local_integration=True,
        davinci_api_base_url="http://127.0.0.1:5003",
    )

    services = build_app_services(settings)

    assert services.runtime.mcp_credential_provider is services.identity_provider
    assert services.identity_provider.client._trust_env is False


def test_development_services_enable_loopback_http_mcp(
    settings_factory, monkeypatch
) -> None:
    from app.bootstrap import build_app_services
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(app_env="development")
    write_workspace(
        settings.workspaces_root,
        "actual",
        mcp="{davinci_data: {type: http, url_env: DAVINCI_DATA_MCP_URL}}",
    )
    monkeypatch.setenv(
        "DAVINCI_DATA_MCP_URL",
        "http://127.0.0.1:8000/mcp",
    )

    services = build_app_services(settings, runtime=FakeAgentRuntime())
    entry = services.workspaces.scan()[0]

    assert entry.available is True


def test_uat_services_keep_loopback_http_mcp_disabled(
    settings_factory, monkeypatch
) -> None:
    from app.bootstrap import build_app_services
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        app_env="uat",
        identity_mode="obid",
        database_url="postgresql+asyncpg://agent:test@localhost/agent",
    )
    write_workspace(
        settings.workspaces_root,
        "actual",
        mcp="{davinci_data: {type: http, url_env: DAVINCI_DATA_MCP_URL}}",
    )
    monkeypatch.setenv(
        "DAVINCI_DATA_MCP_URL",
        "http://127.0.0.1:8000/mcp",
    )

    services = build_app_services(settings, runtime=FakeAgentRuntime())
    entry = services.workspaces.scan()[0]

    assert entry.available is False


@pytest.mark.asyncio
async def test_application_initializes_artifact_store_and_migrates_before_skill_bootstrap(
    monkeypatch, tmp_path
) -> None:
    from app.bootstrap import initialize_app_services

    calls: list[str] = []

    @contextmanager
    def fake_instance_lock(_app_data_dir):
        calls.append("lock.enter")
        try:
            yield
        finally:
            calls.append("lock.exit")

    class AsyncStep:
        def __init__(self, name: str, result=None) -> None:
            self.name = name
            self.result = result

        async def __call__(self, *_args, **_kwargs):
            calls.append(self.name)
            return self.result

    report = SimpleNamespace(summary=lambda: {"imported": 0})
    identity = SimpleNamespace(user_id="owner")
    services = SimpleNamespace(
        settings=SimpleNamespace(
            app_data_dir=tmp_path / "data",
            mock_personal_workspace_id="personal",
            claude_skills_root=None,
            skill_admin_subjects=("owner",),
        ),
        memory_scopes=SimpleNamespace(
            initialize=lambda: calls.append("memory.initialize")
        ),
        skill_artifacts=SimpleNamespace(
            initialize=lambda: calls.append("artifacts.initialize")
        ),
        database=SimpleNamespace(
            initialize=AsyncStep("database.initialize"),
            interrupt_stale_turns=AsyncStep("database.interrupt_stale_turns"),
            dispose=AsyncStep("database.dispose"),
        ),
        workspaces=SimpleNamespace(scan=lambda: calls.append("workspaces.scan") or []),
        identity_provider=SimpleNamespace(
            resolve_bootstrap_identity=AsyncStep("identity.resolve", identity)
        ),
        workspace_sync=SimpleNamespace(sync=AsyncStep("workspace.sync")),
        platform_skill_access=SimpleNamespace(
            bootstrap_subjects=AsyncStep("skills.admin_bootstrap")
        ),
        sessions=SimpleNamespace(
            claim_legacy_sessions=AsyncStep("sessions.claim_legacy")
        ),
        skill_artifact_migrator=SimpleNamespace(
            run=AsyncStep("skills.migrate")
        ),
        skill_artifact_garbage_collector=SimpleNamespace(
            run=AsyncStep("skills.gc")
        ),
        skill_bootstrap=SimpleNamespace(run=AsyncStep("skills.bootstrap", report)),
        attachments=SimpleNamespace(cleanup_pending=AsyncStep("attachments.cleanup")),
        turns=SimpleNamespace(shutdown=AsyncStep("turns.shutdown")),
        frontend_tool_bridges=SimpleNamespace(
            shutdown=AsyncStep("frontend_tool_bridges.shutdown")
        ),
    )
    monkeypatch.setattr("app.bootstrap.service_instance_lock", fake_instance_lock)

    async with initialize_app_services(services):
        calls.append("app.running")

    assert calls == [
        "lock.enter",
        "memory.initialize",
        "artifacts.initialize",
        "database.initialize",
        "skills.migrate",
        "skills.gc",
        "workspaces.scan",
        "identity.resolve",
        "workspace.sync",
        "skills.admin_bootstrap",
        "sessions.claim_legacy",
        "skills.bootstrap",
        "database.interrupt_stale_turns",
        "attachments.cleanup",
        "app.running",
        "turns.shutdown",
        "frontend_tool_bridges.shutdown",
        "database.dispose",
        "lock.exit",
    ]


@pytest.mark.asyncio
async def test_uat_obid_startup_logs_unverified_marker(
    monkeypatch, tmp_path, caplog
) -> None:
    from app.bootstrap import initialize_app_services

    calls: list[str] = []

    @contextmanager
    def fake_instance_lock(_app_data_dir):
        yield

    async def step(name: str, result=None):
        calls.append(name)
        return result

    async def bootstrap_skills(_entries, identity, *_args):
        assert identity is None
        return await step(
            "skills.bootstrap", SimpleNamespace(summary=lambda: {"created": 0})
        )

    services = SimpleNamespace(
        settings=SimpleNamespace(
            app_data_dir=tmp_path / "data",
            app_env="uat",
            identity_mode="obid",
            app_runtime_mode="local_inline",
            security_marker="UAT_OBID_UNVERIFIED",
            mock_personal_workspace_id="personal",
            claude_skills_root=None,
        ),
        memory_scopes=SimpleNamespace(initialize=lambda: None),
        skill_artifacts=SimpleNamespace(initialize=lambda: None),
        database=SimpleNamespace(
            initialize=lambda: step("database.initialize"),
            interrupt_stale_turns=lambda: step("database.interrupt"),
            dispose=lambda: step("database.dispose"),
        ),
        workspaces=SimpleNamespace(scan=list),
        skill_artifact_migrator=SimpleNamespace(
            run=lambda: step("skills.migrate")
        ),
        skill_artifact_garbage_collector=SimpleNamespace(
            run=lambda _now: step("skills.gc")
        ),
        identity_provider=SimpleNamespace(
            resolve_bootstrap_identity=lambda: step("identity.resolve", None)
        ),
        skill_bootstrap=SimpleNamespace(run=bootstrap_skills),
        attachments=SimpleNamespace(cleanup_pending=lambda: step("attachments")),
        turns=SimpleNamespace(shutdown=lambda: step("turns.shutdown")),
        frontend_tool_bridges=SimpleNamespace(
            shutdown=lambda: step("bridges.shutdown")
        ),
    )
    monkeypatch.setattr("app.bootstrap.service_instance_lock", fake_instance_lock)

    with caplog.at_level("WARNING", logger="app.bootstrap"):
        async with initialize_app_services(services):
            pass

    assert "UAT_OBID_UNVERIFIED" in caplog.text
    assert "skills.bootstrap" in calls


@pytest.mark.asyncio
async def test_initialize_app_services_cleans_up_after_partial_failure(
    monkeypatch, tmp_path
) -> None:
    from app.bootstrap import initialize_app_services

    calls: list[str] = []

    @contextmanager
    def fake_instance_lock(_app_data_dir):
        yield

    async def record(name: str) -> None:
        calls.append(name)

    async def fail_sync(*_args) -> None:
        calls.append("workspace.sync")
        raise RuntimeError("sync failed")

    services = SimpleNamespace(
        settings=SimpleNamespace(
            app_data_dir=tmp_path / "data",
            mock_personal_workspace_id="personal",
            claude_skills_root=None,
        ),
        memory_scopes=SimpleNamespace(initialize=lambda: calls.append("memory")),
        skill_artifacts=SimpleNamespace(
            initialize=lambda: calls.append("artifacts.initialize")
        ),
        database=SimpleNamespace(
            initialize=lambda: record("database.initialize"),
            dispose=lambda: record("database.dispose"),
        ),
        workspaces=SimpleNamespace(scan=list),
        skill_artifact_migrator=SimpleNamespace(
            run=lambda: record("skills.migrate")
        ),
        skill_artifact_garbage_collector=SimpleNamespace(
            run=lambda _now: record("skills.gc")
        ),
        identity_provider=SimpleNamespace(
            resolve_bootstrap_identity=lambda: _identity(calls)
        ),
        workspace_sync=SimpleNamespace(sync=fail_sync),
        turns=SimpleNamespace(shutdown=lambda: record("turns.shutdown")),
        frontend_tool_bridges=SimpleNamespace(
            shutdown=lambda: record("frontend_tool_bridges.shutdown")
        ),
    )
    monkeypatch.setattr("app.bootstrap.service_instance_lock", fake_instance_lock)

    with pytest.raises(RuntimeError, match="sync failed"):
        async with initialize_app_services(services):
            pytest.fail("initialization should not yield")

    assert calls == [
        "memory",
        "artifacts.initialize",
        "database.initialize",
        "skills.migrate",
        "skills.gc",
        "identity",
        "workspace.sync",
        "turns.shutdown",
        "frontend_tool_bridges.shutdown",
        "database.dispose",
    ]


async def _identity(calls: list[str]):
    calls.append("identity")
    return SimpleNamespace(user_id="owner")
