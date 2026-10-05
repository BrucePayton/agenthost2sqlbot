import asyncio
from types import SimpleNamespace

import pytest


def _capture_worker(monkeypatch):
    import app.sandbox.main as main_module

    services = SimpleNamespace(database=object(), turns=object())

    monkeypatch.setattr(main_module, "build_app_services", lambda _settings: services)
    monkeypatch.setattr(
        main_module,
        "OpenSandboxAdapter",
        lambda **_kwargs: object(),
    )
    return main_module.build_execution_worker


def test_build_worker_injects_platform_provider_for_claude(
    settings_factory,
    monkeypatch,
) -> None:
    from app.sandbox.credentials import PlatformModelCredentialProvider

    build_execution_worker = _capture_worker(monkeypatch)
    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key="sandbox-secret",
        opensandbox_runner_runtime="claude",
        anthropic_base_url="https://proxy.example.test/apps/anthropic",
        anthropic_api_key="top-secret-test-key",
        opensandbox_allowed_hosts=("proxy.example.test",),
    )

    _services, worker = build_execution_worker(settings)

    assert isinstance(worker.credential_provider, PlatformModelCredentialProvider)
    assert "top-secret-test-key" not in repr(worker.credential_provider)


def test_build_worker_omits_provider_for_fake(settings_factory, monkeypatch) -> None:
    build_execution_worker = _capture_worker(monkeypatch)
    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key="sandbox-secret",
        opensandbox_runner_runtime="fake",
        anthropic_base_url="http://proxy.example.test",
        anthropic_api_key=None,
        opensandbox_allowed_hosts=(),
    )

    _services, worker = build_execution_worker(settings)

    assert worker.credential_provider is None


def test_build_worker_rejects_missing_model_key_for_claude(
    settings_factory,
    monkeypatch,
) -> None:
    build_execution_worker = _capture_worker(monkeypatch)
    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key="sandbox-secret",
        opensandbox_runner_runtime="claude",
        anthropic_base_url="https://proxy.example.test/apps/anthropic",
        anthropic_api_key=None,
        opensandbox_allowed_hosts=("proxy.example.test",),
    )

    with pytest.raises(RuntimeError, match="Claude Worker requires ANTHROPIC_API_KEY"):
        build_execution_worker(settings)


def test_build_worker_heartbeat_uses_exact_runtime_contract(settings_factory) -> None:
    from app.db.base import Database
    from app.sandbox.main import build_worker_heartbeat_publisher

    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        app_runtime_cohort="docker-web",
        app_runtime_protocol_version="7",
        app_runtime_image_digest="sha256:" + "d" * 64,
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://opensandbox-server:8080",
        opensandbox_runner_runtime="fake",
        opensandbox_allowed_hosts=(),
        anthropic_api_key=None,
        worker_heartbeat_interval_seconds=4,
        worker_heartbeat_stale_seconds=12,
    )

    publisher = build_worker_heartbeat_publisher(
        settings,
        Database("sqlite+aiosqlite:///:memory:"),
    )

    assert publisher.identity.compatibility.runtime_cohort == "docker-web"
    assert publisher.identity.compatibility.protocol_version == "7"
    assert publisher.identity.compatibility.runner_runtime == "fake"
    assert publisher.identity.compatibility.image_digest == "sha256:" + "d" * 64
    assert publisher.interval_seconds == 4


@pytest.mark.asyncio
async def test_heartbeat_failure_stops_worker_slots(
    settings_factory,
    monkeypatch,
) -> None:
    from app.sandbox import main as main_module

    calls: list[str] = []

    class FakeDatabase:
        async def initialize(self) -> None:
            calls.append("database.initialize")

        async def dispose(self) -> None:
            calls.append("database.dispose")

    class FakeTurns:
        async def shutdown(self) -> None:
            calls.append("turns.shutdown")

    class FakeWorker:
        async def reconcile_active(self) -> None:
            calls.append("worker.reconcile")

        async def execute_one(self):
            calls.append("worker.execute")
            try:
                await asyncio.Event().wait()
            finally:
                calls.append("worker.cancelled")

        async def reap_idle(self, **_kwargs) -> None:
            calls.append("worker.reap")

    class FailingPublisher:
        async def publish_once(self):
            calls.append("heartbeat.ready")
            return SimpleNamespace(status="ready")

        async def run(self) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("heartbeat failed")

        async def mark_unavailable(self) -> None:
            calls.append("heartbeat.unavailable")

    database = FakeDatabase()
    services = SimpleNamespace(
        database=database,
        turns=FakeTurns(),
        memory_scopes=SimpleNamespace(
            initialize=lambda: calls.append("memory.initialize")
        ),
        workspaces=SimpleNamespace(
            scan=lambda: calls.append("workspaces.scan")
        ),
    )
    worker = FakeWorker()
    publisher = FailingPublisher()
    monkeypatch.setattr(
        main_module,
        "build_execution_worker",
        lambda _settings: (services, worker),
    )
    monkeypatch.setattr(
        main_module,
        "build_worker_heartbeat_publisher",
        lambda _settings, _database: publisher,
    )
    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        app_runtime_image_digest="sha256:" + "e" * 64,
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://opensandbox-server:8080",
        opensandbox_api_key="sandbox-secret",
        opensandbox_runner_runtime="fake",
        opensandbox_allowed_hosts=(),
        anthropic_api_key=None,
        opensandbox_worker_concurrency=1,
    )

    with pytest.raises(ExceptionGroup) as error:
        await asyncio.wait_for(main_module.run_worker(settings), timeout=1)

    assert any(
        isinstance(item, RuntimeError) and str(item) == "heartbeat failed"
        for item in error.value.exceptions
    )

    assert calls == [
        "memory.initialize",
        "database.initialize",
        "workspaces.scan",
        "heartbeat.ready",
        "worker.reconcile",
        "worker.execute",
        "worker.cancelled",
        "heartbeat.unavailable",
        "turns.shutdown",
        "database.dispose",
    ]
