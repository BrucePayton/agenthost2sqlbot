import asyncio

import pytest


class FakeTurnRepository:
    def __init__(self) -> None:
        self.cancelled = []
        self.status = "queued"

    async def request_cancel(self, turn_id):
        self.cancelled.append(turn_id)
        return type("Turn", (), {"status": self.status})()

    async def get_status(self, turn_id):
        return self.status


@pytest.mark.asyncio
async def test_opensandbox_dispatcher_only_queues_and_persists_cancel() -> None:
    from app.turns.dispatcher import OpenSandboxQueuedDispatcher

    repository = FakeTurnRepository()
    dispatcher = OpenSandboxQueuedDispatcher(repository, poll_seconds=0.001)

    assert dispatcher.submit("turn-1") is True
    assert await dispatcher.cancel("turn-1") is True
    assert repository.cancelled == ["turn-1"]

    waiter = asyncio.create_task(dispatcher.wait("turn-1"))
    await asyncio.sleep(0.01)
    assert waiter.done() is False
    repository.status = "completed"
    await asyncio.wait_for(waiter, timeout=0.1)
    await dispatcher.shutdown()


def test_bootstrap_selects_queue_only_dispatcher_without_api_key(
    settings_factory,
) -> None:
    from app.bootstrap import build_app_services
    from app.runtime.fake import FakeAgentRuntime
    from app.turns.dispatcher import OpenSandboxQueuedDispatcher

    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key=None,
        anthropic_base_url="https://proxy.example.test",
        opensandbox_allowed_hosts=("proxy.example.test",),
    )
    services = build_app_services(settings, runtime=FakeAgentRuntime())

    assert isinstance(services.dispatcher, OpenSandboxQueuedDispatcher)


def test_opensandbox_health_reports_component_versions_without_secrets(
    settings_factory,
) -> None:
    from app.runtime.cohorts import probe_runtime_cohort
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key="worker-secret",
        anthropic_base_url="https://proxy.example.test",
        opensandbox_allowed_hosts=("proxy.example.test",),
    )

    health = probe_runtime_cohort(FakeAgentRuntime(), settings).to_health_dict()

    assert health["opensandbox"] == {
        "sdk": "0.1.15",
        "server": "0.2.2",
        "execd": "1.0.21",
        "egress": "1.1.4",
        "backend": "docker",
    }
    assert "worker-secret" not in str(health)
    assert "127.0.0.1" not in str(health)


def test_worker_requires_api_key_even_though_api_composition_does_not(
    settings_factory,
) -> None:
    from app.sandbox.main import build_execution_worker

    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key=None,
        anthropic_base_url="https://proxy.example.test",
        opensandbox_allowed_hosts=("proxy.example.test",),
    )

    with pytest.raises(RuntimeError, match="OPENSANDBOX_API_KEY"):
        build_execution_worker(settings)
