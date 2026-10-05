from datetime import UTC, datetime

import pytest

from tests.test_turns import build_turn_services


class FakeSandboxPort:
    def __init__(self, database) -> None:
        self.database = database
        self.started_after_barrier = False
        self.created = 0
        self.destroyed = []
        self.request_bytes = None
        self.inspected = 0
        self.fail_after_barrier = False
        self.cancel_turn_id = None
        self.cancel_injected = False
        self.reconcile_running = False
        self.operations = []
        self.fail_credential = False
        self.fail_destroy = False
        self.credential_statuses = []

    async def create_session_sandbox(self, spec):
        from app.sandbox.models import SandboxHandle

        self.created += 1
        self.spec = spec
        self.operations.append("create")
        return SandboxHandle(
            sandbox_id="sandbox-1",
            generation=spec.generation,
            created_at=datetime.now(UTC),
            image_reference=spec.runner_image,
            session_volume_name="wa-session-test",
            memory_volume_name="wa-memory-test",
        )

    async def inspect_sandbox(self, sandbox_id):
        from app.sandbox.models import SandboxLifecycle, SandboxObservation

        self.inspected += 1
        self.operations.append("inspect")
        return SandboxObservation(
            status=SandboxLifecycle.READY, observed_at=datetime.now(UTC)
        )

    async def ensure_model_credential(self, handle, credential):
        from sqlalchemy import select

        from app.db.models import SessionSandboxRecord
        from app.sandbox.contracts import CredentialProxyUnavailable

        async with self.database.session() as session:
            record = await session.scalar(select(SessionSandboxRecord))
            self.credential_statuses.append(record.status if record else None)
        self.operations.append("credential")
        if self.fail_credential:
            raise CredentialProxyUnavailable("secret provider response")

    async def write_request(self, handle, request_bytes):
        self.operations.append("write_request")
        self.request_bytes = request_bytes
        return "/session/control/request.json"

    async def sync_workspace(self, handle, source_dir):
        self.operations.append("sync_workspace")
        self.synced_workspace = source_dir

    async def run_turn(self, handle, request_path):
        from sqlalchemy import select

        from app.db.models import TurnAttemptRecord
        from app.sandbox.models import CommandHandle

        self.operations.append("run_turn")
        async with self.database.session() as session:
            attempt = await session.scalar(select(TurnAttemptRecord))
            self.started_after_barrier = (
                attempt is not None
                and attempt.sandbox_id == handle.sandbox_id
                and attempt.execution_nonce is not None
            )
        if self.fail_after_barrier:
            raise RuntimeError("lost command creation response")
        return CommandHandle(
            sandbox_id=handle.sandbox_id,
            command_session_id="execution-1",
            execution_id="execution-1",
        )

    async def read_frames(self, command, cursor):
        if self.cancel_turn_id and not self.cancel_injected:
            from sqlalchemy import update

            from app.db.models import TurnRecord
            from app.sandbox.models import FrameBatch

            async with self.database.session() as session:
                await session.execute(
                    update(TurnRecord)
                    .where(TurnRecord.id == self.cancel_turn_id)
                    .values(cancel_requested_at=datetime.now(UTC))
                )
                await session.commit()
            self.cancel_injected = True
            return FrameBatch(lines=(), next_cursor=cursor, complete=False)
        from app.runner.protocol import RunnerFrame
        from app.sandbox.models import FrameBatch

        frames = (
            RunnerFrame(
                sequence=1,
                kind="assistant_delta",
                payload={"event_type": "message.assistant.delta", "text": "done"},
                role="assistant",
            ).to_line(),
            RunnerFrame(
                sequence=2,
                kind="usage",
                payload={
                    "event_type": "usage.updated",
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "cost_usd": 0,
                },
                role="system",
            ).to_line(),
            RunnerFrame(
                sequence=3,
                kind="terminal",
                payload={
                    "event_type": "runtime.result",
                    "status": "completed",
                    "claude_session_id": "claude-session-1",
                },
            ).to_line(),
        )
        return FrameBatch(lines=frames, next_cursor="3", complete=True)

    async def inspect_command(self, command):
        from app.sandbox.models import CommandLifecycle, CommandObservation

        if self.reconcile_running:
            return CommandObservation(status=CommandLifecycle.RUNNING, exit_code=None)
        if self.cancel_turn_id:
            return CommandObservation(status=CommandLifecycle.CANCELLED, exit_code=130)
        raise AssertionError("complete log batch does not need another status read")

    async def cancel_command(self, command):
        from app.sandbox.models import CancelResult

        if self.cancel_turn_id:
            return CancelResult(accepted=True, terminal=False)
        raise AssertionError("command was not cancelled")

    async def renew_sandbox(self, sandbox_id, timeout_seconds):
        return None

    async def destroy_sandbox(self, sandbox_id):
        from app.sandbox.contracts import SandboxUnavailable

        self.destroyed.append(sandbox_id)
        if self.fail_destroy:
            raise SandboxUnavailable("synthetic destroy failure")


class FakeCredentialProvider:
    def __init__(self) -> None:
        from pydantic import SecretStr

        from app.sandbox.credentials import ModelCredential, parse_model_endpoint

        self.scopes = []
        self.credential = ModelCredential(
            endpoint=parse_model_endpoint(
                "https://api.anthropic.com",
                ("api.anthropic.com",),
            ),
            api_key=SecretStr("phase2a-real-canary"),
        )

    async def resolve(self, scope):
        self.scopes.append(scope)
        return self.credential


@pytest.mark.asyncio
async def test_restart_reconciles_persisted_command_without_replaying(
    settings_factory, monkeypatch
) -> None:
    import asyncio

    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "restart-request",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    async def simulate_process_exit(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(sandbox, "read_frames", simulate_process_exit)
    with pytest.raises(asyncio.CancelledError):
        await worker.execute_one()
    assert sandbox.created == 1

    monkeypatch.undo()
    sandbox.reconcile_running = True
    reconciled = await worker.reconcile_active()

    assert reconciled == 1
    assert sandbox.created == 1
    completed = await turns.get(turn.id)
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_worker_crosses_barrier_before_start_and_finalizes_events(
    settings_factory,
) -> None:
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "worker-request",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    executed = await worker.execute_one()

    assert executed == turn.id
    assert sandbox.started_after_barrier is True
    assert sandbox.created == 1
    assert sandbox.request_bytes is not None
    completed = await turns.get(turn.id)
    assert completed.status == "completed"
    events = await turns.list_events(turn.id)
    assert [event.event_type for event in events][-3:] == [
        "message.assistant.delta",
        "usage.updated",
        "turn.completed",
    ]
    await database.dispose()


@pytest.mark.asyncio
async def test_worker_warm_reuses_then_reaps_idle_sandbox(
    settings_factory,
) -> None:
    from app.sandbox.credentials import CredentialScope
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    sandbox = FakeSandboxPort(database)
    provider = FakeCredentialProvider()
    repository = SandboxRepository(database)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=repository,
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=provider,
    )

    for request_id in ("first", "second"):
        await turns.repository.create_queued(
            session.id,
            request_id,
            {"text": request_id, "attachments": [], "file_references": []},
            input_text=request_id,
        )
        await worker.execute_one()

    assert sandbox.created == 1
    assert sandbox.inspected == 1
    assert sandbox.operations.count("credential") == 2
    assert sandbox.credential_statuses == ["provisioning", "idle"]
    assert sandbox.operations.index("credential") < sandbox.operations.index(
        "write_request"
    )
    assert sandbox.operations.index("sync_workspace") < sandbox.operations.index(
        "write_request"
    )
    assert sandbox.synced_workspace == _sessions.session_path(session) / "workspace"
    assert b"phase2a-real-canary" not in sandbox.request_bytes
    assert sandbox.spec.model_config.api_key_placeholder == (
        "opensandbox-vault-placeholder"
    )
    expected_scope = CredentialScope(
        workspace_id=session.workspace_id,
        user_id=session.created_by,
        session_id=session.id,
    )
    assert provider.scopes == [expected_scope, expected_scope]

    reaped = await worker.reap_idle(now=datetime.now(UTC), idle_ttl_seconds=0)
    assert reaped == 1
    assert sandbox.destroyed == ["sandbox-1"]

    await turns.repository.create_queued(
        session.id,
        "third",
        {"text": "third", "attachments": [], "file_references": []},
        input_text="third",
    )
    await worker.execute_one()
    assert sandbox.created == 2
    assert sandbox.spec.generation == 2
    await database.dispose()


@pytest.mark.asyncio
async def test_post_barrier_ambiguity_requires_recovery_without_replay(
    settings_factory,
) -> None:
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "ambiguous",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    sandbox.fail_after_barrier = True
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    with pytest.raises(RuntimeError, match="lost command"):
        await worker.execute_one()
    assert (await turns.get(turn.id)).status == "recovery_required"
    assert sandbox.created == 1
    assert await worker.execute_one() is None
    assert sandbox.created == 1
    await database.dispose()


@pytest.mark.asyncio
async def test_cancel_requested_before_barrier_never_creates_sandbox(
    settings_factory,
) -> None:
    from sqlalchemy import update

    from app.db.models import TurnRecord
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "cancelled",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    async with database.session() as db:
        await db.execute(
            update(TurnRecord)
            .where(TurnRecord.id == turn.id)
            .values(cancel_requested_at=datetime.now(UTC))
        )
        await db.commit()
    sandbox = FakeSandboxPort(database)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    await worker.execute_one()
    assert (await turns.get(turn.id)).status == "cancelled_before_execution"
    assert sandbox.created == 0
    await database.dispose()


@pytest.mark.asyncio
async def test_cancel_requested_while_running_interrupts_command_and_retains_sandbox(
    settings_factory,
) -> None:
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "cancel-running",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    sandbox.cancel_turn_id = turn.id
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    await worker.execute_one()
    assert (await turns.get(turn.id)).status == "cancelled"
    assert sandbox.destroyed == []
    await database.dispose()


@pytest.mark.asyncio
async def test_worker_does_not_start_command_when_barrier_commit_fails(
    settings_factory, monkeypatch
) -> None:
    from app.errors import AppError
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    await turns.repository.create_queued(
        session.id,
        "worker-request",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)

    async def fail_transition(*_args, **_kwargs):
        raise AppError("barrier_failed", "barrier failed", 409)

    monkeypatch.setattr(turns.repository, "transition", fail_transition)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    with pytest.raises(AppError, match="barrier failed"):
        await worker.execute_one()
    assert sandbox.started_after_barrier is False
    await database.dispose()


@pytest.mark.asyncio
async def test_fake_runner_bypasses_model_credentials(settings_factory) -> None:
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "fake-runner",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        runner_runtime="fake",
        allowed_hosts=(),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=None,
    )

    assert await worker.execute_one() == turn.id
    assert "credential" not in sandbox.operations
    assert sandbox.spec.allowed_hosts == ()
    assert sandbox.spec.model_config is None
    await database.dispose()


@pytest.mark.asyncio
async def test_new_sandbox_credential_failure_closes_turn_and_reaps(
    settings_factory,
) -> None:
    from sqlalchemy import select

    from app.db.models import SessionSandboxRecord
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "credential-failure",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    sandbox.fail_credential = True
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    assert await worker.execute_one() == turn.id

    failed = await turns.get(turn.id)
    assert failed.status == "failed_before_execution"
    assert failed.error_code == "credential_proxy_unavailable"
    assert "phase2a-real-canary" not in (failed.error_message or "")
    assert "write_request" not in sandbox.operations
    assert "run_turn" not in sandbox.operations
    assert sandbox.destroyed == ["sandbox-1"]
    async with database.session() as db:
        authority = await db.scalar(select(SessionSandboxRecord))
    assert authority.status == "terminated"
    await database.dispose()


@pytest.mark.asyncio
async def test_warm_sandbox_credential_failure_refreshes_then_reaps(
    settings_factory,
) -> None:
    from sqlalchemy import select

    from app.db.models import SessionSandboxRecord
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    sandbox = FakeSandboxPort(database)
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )
    await turns.repository.create_queued(
        session.id,
        "warm-success",
        {"text": "first", "attachments": [], "file_references": []},
        input_text="first",
    )
    await worker.execute_one()
    turn = await turns.repository.create_queued(
        session.id,
        "warm-failure",
        {"text": "second", "attachments": [], "file_references": []},
        input_text="second",
    )
    sandbox.fail_credential = True

    assert await worker.execute_one() == turn.id

    assert sandbox.created == 1
    assert sandbox.inspected == 1
    assert sandbox.operations.count("credential") == 2
    assert sandbox.destroyed == ["sandbox-1"]
    assert (await turns.get(turn.id)).status == "failed_before_execution"
    async with database.session() as db:
        authority = await db.scalar(select(SessionSandboxRecord))
    assert authority.status == "terminated"
    await database.dispose()


@pytest.mark.asyncio
async def test_credential_cleanup_failure_requires_sandbox_reconciliation(
    settings_factory,
) -> None:
    from sqlalchemy import select

    from app.db.models import SessionSandboxRecord
    from app.sandbox.repository import SandboxRepository
    from app.sandbox.worker import OpenSandboxExecutionWorker

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    await turns.shutdown()
    turn = await turns.repository.create_queued(
        session.id,
        "cleanup-failure",
        {"text": "hello", "attachments": [], "file_references": []},
        input_text="hello",
    )
    sandbox = FakeSandboxPort(database)
    sandbox.fail_credential = True
    sandbox.fail_destroy = True
    worker = OpenSandboxExecutionWorker(
        database=database,
        turns=turns,
        repository=SandboxRepository(database),
        sandbox=sandbox,
        runtime_cohort="phase2a",
        runner_image="runner@sha256:" + "a" * 64,
        allowed_hosts=("api.anthropic.com",),
        sandbox_timeout_seconds=900,
        memory_lease_seconds=60,
        credential_provider=FakeCredentialProvider(),
    )

    assert await worker.execute_one() == turn.id

    assert (await turns.get(turn.id)).status == "failed_before_execution"
    assert "write_request" not in sandbox.operations
    async with database.session() as db:
        authority = await db.scalar(select(SessionSandboxRecord))
    assert authority.status == "recovery_required"
    assert authority.recovery_reason == "credential_proxy_cleanup_failed"
    assert authority.last_error == "Sandbox state requires reconciliation."
    await database.dispose()
