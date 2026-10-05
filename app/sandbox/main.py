from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime

from app.api.dependencies import AppServices
from app.bootstrap import build_app_services
from app.config import Settings
from app.db.base import Database
from app.sandbox.credentials import PlatformModelCredentialProvider
from app.sandbox.health import OpenSandboxHealthProbe
from app.sandbox.heartbeat import (
    WorkerCompatibility,
    WorkerHeartbeatPublisher,
    WorkerHeartbeatRepository,
    WorkerIdentity,
)
from app.sandbox.opensandbox_adapter import OpenSandboxAdapter
from app.sandbox.repository import SandboxRepository
from app.sandbox.worker import OpenSandboxExecutionWorker

logger = logging.getLogger(__name__)


def build_worker_heartbeat_publisher(
    settings: Settings,
    database: Database,
) -> WorkerHeartbeatPublisher:
    if settings.opensandbox_api_url is None:
        raise RuntimeError("OpenSandbox Worker requires OPENSANDBOX_API_URL")
    compatibility = WorkerCompatibility(
        runtime_cohort=settings.app_runtime_cohort,
        protocol_version=settings.app_runtime_protocol_version,
        runner_runtime=settings.opensandbox_runner_runtime,
        image_digest=settings.app_runtime_image_digest,
    )
    return WorkerHeartbeatPublisher(
        WorkerHeartbeatRepository(database),
        WorkerIdentity(str(uuid.uuid4()), compatibility),
        OpenSandboxHealthProbe(str(settings.opensandbox_api_url)),
        interval_seconds=settings.worker_heartbeat_interval_seconds,
    )


def build_execution_worker(
    settings: Settings,
) -> tuple[AppServices, OpenSandboxExecutionWorker]:
    if settings.app_runtime_mode != "opensandbox_docker":
        raise RuntimeError("OpenSandbox Worker requires opensandbox_docker mode")
    if settings.opensandbox_api_url is None:
        raise RuntimeError("OpenSandbox Worker requires OPENSANDBOX_API_URL")
    if settings.opensandbox_api_key is None or not (
        settings.opensandbox_api_key.get_secret_value().strip()
    ):
        raise RuntimeError("OpenSandbox Worker requires OPENSANDBOX_API_KEY")
    services = build_app_services(settings)
    adapter = OpenSandboxAdapter(
        api_url=str(settings.opensandbox_api_url),
        api_key=settings.opensandbox_api_key.get_secret_value(),
        ready_timeout_seconds=settings.opensandbox_ready_timeout_seconds,
        command_timeout_seconds=settings.turn_timeout_seconds,
    )
    if settings.opensandbox_runner_runtime == "claude":
        if settings.anthropic_api_key is None:
            raise RuntimeError("Claude Worker requires ANTHROPIC_API_KEY")
        credential_provider = PlatformModelCredentialProvider(
            str(settings.anthropic_base_url),
            settings.anthropic_api_key,
            settings.opensandbox_allowed_hosts,
        )
    else:
        credential_provider = None
    worker = OpenSandboxExecutionWorker(
        database=services.database,
        turns=services.turns,
        repository=SandboxRepository(services.database),
        sandbox=adapter,
        runtime_cohort=settings.app_runtime_cohort,
        runner_image=settings.opensandbox_runner_image,
        runner_runtime=settings.opensandbox_runner_runtime,
        allowed_hosts=settings.opensandbox_allowed_hosts,
        sandbox_timeout_seconds=settings.opensandbox_sandbox_timeout_seconds,
        memory_lease_seconds=max(60, settings.turn_timeout_seconds + 60),
        credential_provider=credential_provider,
    )
    return services, worker


async def run_worker(settings: Settings) -> None:
    services, worker = build_execution_worker(settings)
    publisher = build_worker_heartbeat_publisher(settings, services.database)
    services.memory_scopes.initialize()
    await services.database.initialize()
    services.workspaces.scan()
    try:
        deadline = (
            asyncio.get_running_loop().time()
            + settings.opensandbox_ready_timeout_seconds
        )
        while (await publisher.publish_once()).status != "ready":
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("OpenSandbox backend did not become ready")
            await asyncio.sleep(
                min(settings.worker_heartbeat_interval_seconds, remaining)
            )
        await worker.reconcile_active()

        async def run_slot(*, reap: bool) -> None:
            while True:
                executed = await worker.execute_one()
                if reap:
                    await worker.reap_idle(
                        now=datetime.now(UTC),
                        idle_ttl_seconds=settings.opensandbox_idle_ttl_seconds,
                    )
                if executed is None:
                    await asyncio.sleep(settings.opensandbox_worker_poll_seconds)

        async with asyncio.TaskGroup() as group:
            group.create_task(publisher.run())
            for index in range(settings.opensandbox_worker_concurrency):
                group.create_task(run_slot(reap=index == 0))
    finally:
        with suppress(Exception):
            await publisher.mark_unavailable()
        with suppress(Exception):
            await services.turns.shutdown()
        await services.database.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(Settings()))


if __name__ == "__main__":
    main()
