import asyncio
import json
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from tests.conftest import synchronize_workspaces
from tests.test_workspaces import write_workspace


async def build_turn_services(
    settings_factory,
    *,
    chunks=("hello", " world"),
    delay=0,
    fail_code=None,
    timeout=5,
    locks=None,
    runtime_usage=None,
):
    from app.attachments.service import AttachmentService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.memory.locks import MemoryScopeLockRegistry
    from app.memory.scopes import MemoryScopeService
    from app.runtime.fake import FakeAgentRuntime
    from app.sessions.locks import SessionLockRegistry
    from app.sessions.service import SessionService
    from app.turns.broker import EventBroker
    from app.turns.dispatcher import LocalInlineExecutionDispatcher
    from app.turns.service import TurnService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    if not settings.workspaces_root.joinpath("actual").exists():
        write_workspace(settings.workspaces_root, "actual")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    memory_scopes = MemoryScopeService(settings.app_data_dir)
    memory_scopes.initialize()
    lifecycle_locks = locks or SessionLockRegistry()
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        locks=lifecycle_locks,
    )
    attachments = AttachmentService(database, settings, locks=lifecycle_locks)
    runtime = FakeAgentRuntime(
        chunks=chunks,
        delay_seconds=delay,
        fail_code=fail_code,
        usage=runtime_usage,
    )
    broker = EventBroker()
    turns = TurnService(
        database=database,
        sessions=sessions,
        attachments=attachments,
        runtime=runtime,
        locks=lifecycle_locks,
        memory_scopes=memory_scopes,
        memory_locks=MemoryScopeLockRegistry(),
        broker=broker,
        timeout_seconds=timeout,
    )
    turns.bind_dispatcher(LocalInlineExecutionDispatcher(turns.execute_turn))
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    session = await sessions.create("actual", identity)
    return settings, database, session, sessions, attachments, runtime, broker, turns


class GatedSessionLockRegistry:
    def __init__(self, held_task_name: str) -> None:
        from app.sessions.locks import SessionLockRegistry

        self._registry = SessionLockRegistry()
        self.held_task_name = held_task_name
        self.held_acquired = asyncio.Event()
        self.release_held = asyncio.Event()
        self._attempted: dict[str, asyncio.Event] = {}

    async def wait_for_attempt(self, task_name: str) -> None:
        event = self._attempted.setdefault(task_name, asyncio.Event())
        await asyncio.wait_for(event.wait(), timeout=2)

    @asynccontextmanager
    async def acquire(self, session_id: str) -> AsyncIterator[None]:
        task_name = asyncio.current_task().get_name()
        self._attempted.setdefault(task_name, asyncio.Event()).set()
        async with self._registry.acquire(session_id):
            if task_name == self.held_task_name:
                self.held_acquired.set()
                await self.release_held.wait()
            yield


@pytest.mark.asyncio
async def test_turn_start_can_use_validated_agui_run_id(settings_factory) -> None:
    _settings, database, session, *_rest, turns = await build_turn_services(
        settings_factory
    )
    run_id = "9ee1d0ac-96a5-46ba-856f-7442713cbb77"

    turn = await turns.start(
        session.id,
        "hello",
        [],
        run_id,
        turn_id=run_id,
    )
    duplicate = await turns.start(
        session.id,
        "hello again",
        [],
        run_id,
        turn_id="0529f57b-aeba-46ef-8b81-63aa76ed355e",
    )

    assert turn.id == run_id
    assert duplicate.id == run_id
    await turns.wait(run_id)
    assert (await turns.get(run_id)).status == "completed"
    await turns.shutdown()
    await database.dispose()


class GatedConcurrencyRuntime:
    def __init__(self) -> None:
        self.requests = []
        self.release = asyncio.Event()
        self.active = 0
        self.max_active = 0
        self._condition = asyncio.Condition()

    async def wait_for_calls(self, count: int) -> None:
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(lambda: len(self.requests) >= count),
                timeout=1,
            )

    async def run(self, request, cancel_event):
        from app.runtime.base import RuntimeCancelled, RuntimeEvent

        if cancel_event.is_set():
            raise RuntimeCancelled()
        async with self._condition:
            self.requests.append(request)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self._condition.notify_all()
        try:
            await self.release.wait()
            yield RuntimeEvent(
                "message.assistant.completed",
                {"text": "done"},
                "assistant",
            )
            yield RuntimeEvent(
                "usage.updated",
                {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0},
                "system",
            )
            yield RuntimeEvent(
                "runtime.result",
                {
                    "claude_session_id": request.claude_session_id
                    or f"gated-{request.platform_session_id}",
                    "duration_ms": 1,
                },
            )
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_successful_turn_persists_ordered_events_and_resume_id(
    settings_factory,
) -> None:
    (
        settings,
        database,
        session,
        sessions,
        _attachments,
        runtime,
        _broker,
        turns,
    ) = await build_turn_services(
        settings_factory,
        runtime_usage={
            "input_tokens": 10,
            "uncached_input_tokens": 10,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 5,
            "total_input_tokens": 45,
            "output_tokens": 20,
            "model_api_turns": 2,
            "frontend_tool_calls": 1,
            "tool_search_calls": 0,
            "tool_set_changes": 1,
            "catalog_digest_changes": 1,
            "cost_usd": 0.001,
        },
    )

    workspace = settings.app_data_dir / session.session_dir / "workspace"
    (workspace / "report.txt").write_text("report", encoding="utf-8")
    turn = await turns.start(
        session.id,
        "Review @report.txt",
        [],
        "client-request-1",
        file_references=["report.txt"],
    )
    await turns.wait(turn.id)

    completed = await turns.get(turn.id)
    updated_session = await sessions.get(session.id)
    events = await turns.list_events(turn.id)
    request = runtime.requests[0]
    expected_scope = turns.memory_scopes.resolve(
        session.created_by,
        session.workspace_id,
    )
    assert request.memory_scope_key == expected_scope.key
    assert request.memory_dir == expected_scope.directory
    assert completed.status == "completed"
    assert completed.input_tokens == 10
    assert completed.uncached_input_tokens == 10
    assert completed.cache_read_input_tokens == 30
    assert completed.cache_creation_input_tokens == 5
    assert completed.total_input_tokens == 45
    assert completed.output_tokens == 20
    assert completed.model_api_turns == 2
    assert completed.frontend_tool_calls == 1
    assert completed.tool_search_calls == 0
    assert completed.tool_set_changes == 1
    assert completed.catalog_digest_changes == 1
    assert updated_session.status == "idle"
    assert updated_session.claude_session_id == f"fake-{session.id}"
    assert updated_session.title == "Review @report.txt"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "message.user",
        "turn.started",
        "turn.progress",
        "turn.progress",
        "turn.progress",
        "turn.progress",
        "message.assistant.delta",
        "message.assistant.delta",
        "message.assistant.completed",
        "turn.progress",
        "usage.updated",
        "turn.completed",
    ]
    progress_phases = [
        event.payload_json for event in events if event.event_type == "turn.progress"
    ]
    assert '"phase": "preparing"' in progress_phases[0]
    user_payload = json.loads(events[0].payload_json)
    assert user_payload["file_references"] == ["report.txt"]
    assert runtime.requests[0].file_references == ("report.txt",)
    assert runtime.requests[0].cwd == workspace
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_preserves_unknown_provider_cache_metrics(
    settings_factory,
) -> None:
    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(
        settings_factory,
        runtime_usage={
            "input_tokens": 10,
            "uncached_input_tokens": 10,
            "cache_read_input_tokens": 30,
            "output_tokens": 5,
        },
    )

    turn = await turns.start(session.id, "hello", [], "unknown-cache-metrics")
    await turns.wait(turn.id)

    completed = await turns.get(turn.id)
    assert completed.uncached_input_tokens == 10
    assert completed.cache_read_input_tokens == 30
    assert completed.cache_creation_input_tokens is None
    assert completed.total_input_tokens is None
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turns_in_same_memory_scope_are_serialized(settings_factory) -> None:
    from app.auth.models import IdentityContext

    (
        settings,
        database,
        first_session,
        sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    second_session = await sessions.create("actual", identity)
    runtime = GatedConcurrencyRuntime()
    turns.runtime = runtime

    first = await turns.start(first_session.id, "first", [], "same-scope-first")
    second = await turns.start(second_session.id, "second", [], "same-scope-second")
    await runtime.wait_for_calls(1)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runtime.wait_for_calls(2), timeout=0.05)
    assert runtime.max_active == 1

    runtime.release.set()
    await turns.wait(first.id)
    await turns.wait(second.id)
    assert runtime.max_active == 1
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turns_in_different_memory_scopes_remain_concurrent(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext
    from app.workspaces.sync import WorkspaceSyncService

    (
        settings,
        database,
        first_session,
        sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    write_workspace(settings.workspaces_root, "other")
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    await WorkspaceSyncService(database, settings).sync(
        sessions.registry.scan(),
        identity,
    )
    second_session = await sessions.create("other", identity)
    runtime = GatedConcurrencyRuntime()
    turns.runtime = runtime

    first = await turns.start(first_session.id, "first", [], "other-scope-first")
    second = await turns.start(second_session.id, "second", [], "other-scope-second")
    await runtime.wait_for_calls(2)
    assert runtime.max_active == 2

    runtime.release.set()
    await turns.wait(first.id)
    await turns.wait(second.id)
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_rejects_deleted_file_reference_without_creating_turn(
    settings_factory,
) -> None:
    from sqlalchemy import func, select

    from app.db.models import TurnRecord
    from app.errors import AppError

    (
        settings,
        database,
        session,
        sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    workspace = settings.app_data_dir / session.session_dir / "workspace"
    report = workspace / "report.txt"
    report.write_text("report", encoding="utf-8")
    assert sessions.validate_file_references_for_record(session, ["report.txt"]) == (
        "report.txt",
    )
    report.unlink()

    with pytest.raises(AppError) as exc_info:
        await turns.start(
            session.id,
            "Review @report.txt",
            [],
            "deleted-reference",
            file_references=["report.txt"],
        )

    assert exc_info.value.code == "file_reference_invalid"
    async with database.session() as db:
        assert await db.scalar(select(func.count(TurnRecord.id))) == 0
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_rejects_symlinked_workspace_root_without_creating_turn(
    settings_factory,
) -> None:
    from sqlalchemy import func, select

    from app.db.models import TurnRecord
    from app.errors import AppError

    (
        settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    workspace = settings.app_data_dir / session.session_dir / "workspace"
    shutil.rmtree(workspace)
    outside_workspace = settings.app_data_dir.parent / "outside-workspace"
    outside_workspace.mkdir()
    (outside_workspace / "report.txt").write_text("outside", encoding="utf-8")
    workspace.symlink_to(outside_workspace, target_is_directory=True)

    try:
        with pytest.raises(AppError) as exc_info:
            await turns.start(
                session.id,
                "Review @report.txt",
                [],
                "symlinked-workspace",
                file_references=["report.txt"],
            )
        assert exc_info.value.code == "file_reference_invalid"
        async with database.session() as db:
            assert await db.scalar(select(func.count(TurnRecord.id))) == 0
    finally:
        await turns.shutdown()
        await database.dispose()


@pytest.mark.asyncio
async def test_turn_rejects_symlinked_sessions_root_without_creating_turn(
    settings_factory,
) -> None:
    from sqlalchemy import func, select

    from app.db.models import TurnRecord
    from app.errors import AppError

    (
        settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    sessions_root = settings.app_data_dir / "sessions"
    external_sessions = settings.app_data_dir.parent / "external-sessions"
    sessions_root.rename(external_sessions)
    sessions_root.symlink_to(external_sessions, target_is_directory=True)
    workspace = external_sessions / session.id / "workspace"
    (workspace / "report.txt").write_text("outside", encoding="utf-8")

    try:
        with pytest.raises(AppError) as exc_info:
            await turns.start(
                session.id,
                "Review @report.txt",
                [],
                "symlinked-sessions-root",
                file_references=["report.txt"],
            )
        assert exc_info.value.code == "file_reference_invalid"
        async with database.session() as db:
            assert await db.scalar(select(func.count(TurnRecord.id))) == 0
    finally:
        await turns.shutdown()
        await database.dispose()


@pytest.mark.asyncio
async def test_turn_rejects_symlinked_current_session_without_creating_turn(
    settings_factory,
) -> None:
    from sqlalchemy import func, select

    from app.auth.models import IdentityContext
    from app.db.models import TurnRecord
    from app.errors import AppError

    (
        settings,
        database,
        session,
        sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    other_session = await sessions.create("actual", identity)
    sessions_root = settings.app_data_dir / "sessions"
    current_session_path = sessions_root / session.id
    other_session_path = sessions_root / other_session.id
    shutil.rmtree(current_session_path)
    current_session_path.symlink_to(other_session_path, target_is_directory=True)
    (other_session_path / "workspace/report.txt").write_text(
        "other session", encoding="utf-8"
    )

    try:
        with pytest.raises(AppError) as exc_info:
            await turns.start(
                session.id,
                "Review @report.txt",
                [],
                "symlinked-current-session",
                file_references=["report.txt"],
            )
        assert exc_info.value.code == "file_reference_invalid"
        async with database.session() as db:
            assert await db.scalar(select(func.count(TurnRecord.id))) == 0
    finally:
        await turns.shutdown()
        await database.dispose()


@pytest.mark.asyncio
async def test_runtime_request_reads_sequence_one_user_input_event(
    settings_factory,
) -> None:
    from sqlalchemy import select

    from app.db.models import MessageRecord

    (
        settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    workspace = settings.app_data_dir / session.session_dir / "workspace"
    (workspace / "first.txt").write_text("first", encoding="utf-8")
    (workspace / "later.txt").write_text("later", encoding="utf-8")
    turn = await turns.start(
        session.id,
        "Review @first.txt",
        [],
        "sequence-one-reference",
        file_references=["first.txt"],
    )
    await turns.wait(turn.id)

    async with database.session() as db:
        original = await db.scalar(
            select(MessageRecord).where(
                MessageRecord.turn_id == turn.id,
                MessageRecord.sequence == 1,
            )
        )
        assert original is not None
        original_payload = original.payload_json
        await db.delete(original)
        await db.commit()
        db.add(
            MessageRecord(
                id=str(uuid.uuid4()),
                session_id=session.id,
                turn_id=turn.id,
                sequence=100,
                event_type="message.user",
                role="user",
                payload_json=json.dumps(
                    {
                        "text": "Ignore the first input",
                        "attachments": [],
                        "file_references": ["later.txt"],
                    }
                ),
            )
        )
        await db.commit()
        db.add(
            MessageRecord(
                id=str(uuid.uuid4()),
                session_id=session.id,
                turn_id=turn.id,
                sequence=1,
                event_type="message.user",
                role="user",
                payload_json=original_payload,
            )
        )
        await db.commit()

    request = await turns._runtime_request(turn.id)
    assert request.file_references == ("first.txt",)
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_runtime_request_defaults_legacy_user_input_references_to_empty(
    settings_factory,
) -> None:
    from sqlalchemy import select

    from app.db.models import MessageRecord

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
    turn = await turns.start(session.id, "Legacy input", [], "legacy-input")
    await turns.wait(turn.id)

    async with database.session() as db:
        user_input = await db.scalar(
            select(MessageRecord).where(
                MessageRecord.turn_id == turn.id,
                MessageRecord.sequence == 1,
            )
        )
        assert user_input is not None
        payload = json.loads(user_input.payload_json)
        payload.pop("file_references")
        user_input.payload_json = json.dumps(payload)
        await db.commit()

    request = await turns._runtime_request(turn.id)
    assert request.file_references == ()
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_runtime_request_persists_native_frontend_tool_resumption_input(
    settings_factory,
) -> None:
    from app.runtime.contracts import RuntimeFrontendTool, RuntimeToolResult

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
    turn = await turns.start(
        session.id,
        "",
        [],
        "native-frontend-tool-result",
        runtime_frontend_tools=(
            RuntimeFrontendTool(
                name="dashboard.get_widget_data",
                description="Read current Widget data.",
                parameters={"type": "object", "additionalProperties": False},
            ),
        ),
        runtime_page_state={
            "schemaVersion": "davinci-page-state-v1",
            "resourceRevision": "dashboard:88:7",
        },
        runtime_tool_results=(
            RuntimeToolResult(
                tool_call_id="tool-widget-data",
                content='{"ok":true,"data":{"items":[]}}',
                is_error=False,
            ),
        ),
        runtime_metadata={
            "profile_id": "space-dashboard",
            "catalog_digest": "contract:space-dashboard",
            "tool_set_id": "contract:space-dashboard:8",
            "tool_set_changes": 1,
            "catalog_digest_changes": 1,
        },
    )
    await turns.wait(turn.id)

    request = await turns._runtime_request(turn.id)

    assert request.text == ""
    assert request.run_id == turn.id
    assert request.frontend_tools[0].name == "dashboard.get_widget_data"
    assert request.page_state["resourceRevision"] == "dashboard:88:7"
    assert request.tool_results == (
        RuntimeToolResult(
            tool_call_id="tool-widget-data",
            content='{"ok":true,"data":{"items":[]}}',
            is_error=False,
        ),
    )
    assert request.metadata["profile_id"] == "space-dashboard"
    assert request.metadata["tool_set_changes"] == 1
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_runtime_request_persists_model_and_effort_overrides(
    settings_factory,
) -> None:
    from sqlalchemy import select

    from app.db.models import MessageRecord

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
    turn = await turns.start(
        session.id,
        "Use a specific model",
        [],
        "model-effort-override",
        runtime_model="qwen3.7-flash",
        runtime_effort="low",
    )
    await turns.wait(turn.id)

    async with database.session() as db:
        user_input = await db.scalar(
            select(MessageRecord).where(
                MessageRecord.turn_id == turn.id,
                MessageRecord.sequence == 1,
            )
        )
        assert user_input is not None
        payload = json.loads(user_input.payload_json)
        assert payload["model"] == "qwen3.7-flash"
        assert payload["effort"] == "low"

    request = await turns._runtime_request(turn.id)
    assert request.model == "qwen3.7-flash"
    assert request.effort == "low"
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_start_is_idempotent_and_rejects_parallel_turn(
    settings_factory,
) -> None:
    from app.errors import AppError

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory, delay=0.05)
    first = await turns.start(session.id, "one", [], "same-request")
    duplicate = await turns.start(session.id, "one", [], "same-request")
    assert duplicate.id == first.id

    with pytest.raises(AppError) as exc_info:
        await turns.start(session.id, "two", [], "other-request")
    assert exc_info.value.code == "session_busy"

    await turns.wait(first.id)
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_unavailable_worker_rejects_only_new_turns(settings_factory) -> None:
    from app.errors import AppError

    class ToggleAvailability:
        def __init__(self) -> None:
            self.available = True

        async def require_available(self) -> None:
            if not self.available:
                raise AppError(
                    "execution_unavailable",
                    "Execution service is temporarily unavailable.",
                    503,
                )

    (
        _settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory, delay=0.05)
    gate = ToggleAvailability()
    turns.execution_availability = gate
    created = await turns.start(session.id, "hello", [], "request-1")

    gate.available = False
    repeated = await turns.start(session.id, "hello", [], "request-1")
    assert repeated.id == created.id

    with pytest.raises(AppError) as error:
        await turns.start(session.id, "new", [], "request-2")
    assert error.value.code == "execution_unavailable"
    assert error.value.status_code == 503

    await turns.wait(created.id)
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_rejects_more_than_configured_attachments(
    settings_factory,
) -> None:
    from app.errors import AppError

    (
        settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    attachment_ids = [str(uuid.uuid4()) for _ in range(settings.max_files_per_turn + 1)]

    with pytest.raises(AppError) as exc_info:
        await turns.start(session.id, "too many", attachment_ids, "request-limit")

    assert exc_info.value.code == "attachment_invalid"
    assert exc_info.value.message == "At most 5 attachments are allowed per turn."
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_binds_pending_attachment(settings_factory) -> None:
    (
        _settings,
        database,
        session,
        _sessions,
        attachments,
        runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    record = (
        await attachments.upload(
            session.id,
            [UploadFile(BytesIO(b"notes"), filename="notes.txt")],
        )
    )[0]

    turn = await turns.start(session.id, "read it", [record.id], "request-1")
    await turns.wait(turn.id)

    bound = await attachments.get(record.id)
    assert bound.status == "bound"
    assert bound.turn_id == turn.id
    assert runtime.requests[0].attachments[0].original_filename == "notes.txt"
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_binding_after_delete_reads_pending_cannot_delete_bound_attachment(
    settings_factory, monkeypatch
) -> None:
    from app.errors import AppError

    (
        _settings,
        database,
        session,
        _sessions,
        attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    record = (
        await attachments.upload(
            session.id,
            [UploadFile(BytesIO(b"notes"), filename="notes.txt")],
        )
    )[0]
    initial_delete_read = asyncio.Event()
    release_delete = asyncio.Event()
    original_get = attachments.get

    async def pause_delete_after_initial_read(attachment_id: str):
        attached = await original_get(attachment_id)
        if asyncio.current_task().get_name() == "attachment-delete":
            initial_delete_read.set()
            await release_delete.wait()
        return attached

    monkeypatch.setattr(attachments, "get", pause_delete_after_initial_read)
    delete_task = asyncio.create_task(
        attachments.delete(record.id),
        name="attachment-delete",
    )
    await asyncio.wait_for(initial_delete_read.wait(), timeout=2)

    turn = await turns.start(
        session.id,
        "bind before delete commits",
        [record.id],
        "request-bind-before-delete",
    )
    release_delete.set()
    result = (await asyncio.gather(delete_task, return_exceptions=True))[0]

    assert isinstance(result, AppError)
    assert result.code == "attachment_bound"
    bound = await original_get(record.id)
    assert bound.status == "bound"
    assert bound.turn_id == turn.id
    await turns.wait(turn.id)
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_attachment_delete_first_serializes_before_turn_start(
    settings_factory,
) -> None:
    from app.errors import AppError

    locks = GatedSessionLockRegistry("attachment-delete")
    (
        _settings,
        database,
        session,
        _sessions,
        attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory, locks=locks)
    record = (
        await attachments.upload(
            session.id,
            [UploadFile(BytesIO(b"delete first"), filename="delete-first.txt")],
        )
    )[0]
    path = attachments.resolve_path(record)

    delete_task = asyncio.create_task(
        attachments.delete(record.id),
        name="attachment-delete",
    )
    await asyncio.wait_for(locks.held_acquired.wait(), timeout=2)
    turn_task = asyncio.create_task(
        turns.start(
            session.id,
            "must not bind deleted attachment",
            [record.id],
            "request-delete-first",
        ),
        name="turn-start",
    )
    await locks.wait_for_attempt("turn-start")
    assert not turn_task.done()

    locks.release_held.set()
    await delete_task
    result = (await asyncio.gather(turn_task, return_exceptions=True))[0]

    assert isinstance(result, AppError)
    assert result.code == "attachment_invalid"
    assert not path.exists()
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_start_first_serializes_before_attachment_delete(
    settings_factory,
) -> None:
    from app.errors import AppError

    locks = GatedSessionLockRegistry("turn-start")
    (
        _settings,
        database,
        session,
        _sessions,
        attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory, locks=locks)
    record = (
        await attachments.upload(
            session.id,
            [UploadFile(BytesIO(b"turn first"), filename="turn-first.txt")],
        )
    )[0]

    turn_task = asyncio.create_task(
        turns.start(
            session.id,
            "bind before delete",
            [record.id],
            "request-turn-first",
        ),
        name="turn-start",
    )
    await asyncio.wait_for(locks.held_acquired.wait(), timeout=2)
    delete_task = asyncio.create_task(
        attachments.delete(record.id),
        name="attachment-delete",
    )
    await locks.wait_for_attempt("attachment-delete")
    assert not delete_task.done()

    locks.release_held.set()
    turn = await turn_task
    result = (await asyncio.gather(delete_task, return_exceptions=True))[0]

    assert isinstance(result, AppError)
    assert result.code == "attachment_bound"
    bound = await attachments.get(record.id)
    assert bound.status == "bound"
    assert bound.turn_id == turn.id
    await turns.wait(turn.id)
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_turn_failure_cancel_and_timeout_are_terminal(settings_factory) -> None:
    failure = await build_turn_services(
        settings_factory, fail_code="claude_unavailable"
    )
    database, session, sessions, turns = failure[1], failure[2], failure[3], failure[-1]
    failed = await turns.start(session.id, "fail", [], "request-fail")
    await turns.wait(failed.id)
    assert (await turns.get(failed.id)).status == "failed"
    assert (await sessions.get(session.id)).status == "error"
    assert (await turns.list_events(failed.id))[-1].event_type == "turn.failed"
    await turns.shutdown()
    await database.dispose()

    cancellation = await build_turn_services(settings_factory, delay=0.2)
    database, session, sessions, turns = (
        cancellation[1],
        cancellation[2],
        cancellation[3],
        cancellation[-1],
    )
    cancelled = await turns.start(session.id, "cancel", [], "request-cancel")
    await asyncio.sleep(0.02)
    await turns.cancel(cancelled.id)
    await turns.wait(cancelled.id)
    assert (await turns.get(cancelled.id)).status == "cancelled"
    assert (await sessions.get(session.id)).status == "idle"
    await turns.shutdown()
    await database.dispose()

    timed = await build_turn_services(settings_factory, delay=0.2, timeout=0.01)
    database, session, sessions, turns = timed[1], timed[2], timed[3], timed[-1]
    timeout_turn = await turns.start(session.id, "timeout", [], "request-timeout")
    await turns.wait(timeout_turn.id)
    timeout_record = await turns.get(timeout_turn.id)
    assert timeout_record.status == "failed"
    assert timeout_record.error_code == "turn_timeout"
    assert (await sessions.get(session.id)).status == "error"
    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_session_resumes_after_service_objects_are_recreated(
    settings_factory,
) -> None:
    from app.attachments.service import AttachmentService
    from app.db.base import Database
    from app.memory.locks import MemoryScopeLockRegistry
    from app.memory.scopes import MemoryScopeService
    from app.runtime.fake import FakeAgentRuntime
    from app.sessions.locks import SessionLockRegistry
    from app.sessions.service import SessionService
    from app.turns.broker import EventBroker
    from app.turns.dispatcher import LocalInlineExecutionDispatcher
    from app.turns.service import TurnService
    from app.workspaces.registry import WorkspaceRegistry

    (
        settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    first = await turns.start(session.id, "first", [], "restart-first")
    await turns.wait(first.id)
    saved_claude_id = (await _sessions.get(session.id)).claude_session_id
    await turns.shutdown()
    await database.dispose()

    restarted_database = Database(settings.resolved_database_url)
    await restarted_database.initialize()
    restarted_registry = WorkspaceRegistry(
        settings.workspaces_root, settings.claude_model, {}
    )
    restarted_registry.scan()
    restarted_sessions = SessionService(
        restarted_database, restarted_registry, settings.app_data_dir
    )
    restarted_attachments = AttachmentService(restarted_database, settings)
    restarted_runtime = FakeAgentRuntime(chunks=("resumed",))
    restarted_memory_scopes = MemoryScopeService(settings.app_data_dir)
    restarted_memory_scopes.initialize()
    restarted_turns = TurnService(
        database=restarted_database,
        sessions=restarted_sessions,
        attachments=restarted_attachments,
        runtime=restarted_runtime,
        locks=SessionLockRegistry(),
        memory_scopes=restarted_memory_scopes,
        memory_locks=MemoryScopeLockRegistry(),
        broker=EventBroker(),
        timeout_seconds=5,
    )
    restarted_turns.bind_dispatcher(
        LocalInlineExecutionDispatcher(restarted_turns.execute_turn)
    )

    second = await restarted_turns.start(session.id, "second", [], "restart-second")
    await restarted_turns.wait(second.id)

    assert (await restarted_turns.get(second.id)).status == "completed"
    assert restarted_runtime.requests[0].claude_session_id == saved_claude_id
    assert restarted_runtime.requests[0].cwd == (
        settings.app_data_dir / session.session_dir / "workspace"
    )
    await restarted_turns.shutdown()
    await restarted_database.dispose()


@pytest.mark.asyncio
async def test_sessions_isolate_messages_attachments_and_directories(
    settings_factory,
) -> None:
    from app.auth.models import IdentityContext

    (
        settings,
        database,
        first_session,
        sessions,
        attachments,
        runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    second_session = await sessions.create("actual", identity)
    first_attachment = (
        await attachments.upload(
            first_session.id,
            [UploadFile(BytesIO(b"first attachment"), filename="first.txt")],
        )
    )[0]
    second_attachment = (
        await attachments.upload(
            second_session.id,
            [UploadFile(BytesIO(b"second attachment"), filename="second.txt")],
        )
    )[0]

    first_turn = await turns.start(
        first_session.id,
        "session-one-message",
        [first_attachment.id],
        "isolation-first",
    )
    await turns.wait(first_turn.id)
    second_turn = await turns.start(
        second_session.id,
        "session-two-message",
        [second_attachment.id],
        "isolation-second",
    )
    await turns.wait(second_turn.id)

    first_messages = await sessions.list_messages(first_session.id)
    second_messages = await sessions.list_messages(second_session.id)
    assert all(message.session_id == first_session.id for message in first_messages)
    assert all(message.session_id == second_session.id for message in second_messages)
    assert any(
        "session-one-message" in message.payload_json for message in first_messages
    )
    assert not any(
        "session-two-message" in message.payload_json for message in first_messages
    )
    assert any(
        "session-two-message" in message.payload_json for message in second_messages
    )

    first_root = settings.app_data_dir / first_session.session_dir / "workspace"
    second_root = settings.app_data_dir / second_session.session_dir / "workspace"
    assert first_root != second_root
    assert attachments.resolve_path(
        await attachments.get(first_attachment.id)
    ).is_relative_to(first_root)
    assert attachments.resolve_path(
        await attachments.get(second_attachment.id)
    ).is_relative_to(second_root)
    assert {request.cwd for request in runtime.requests} == {first_root, second_root}

    first_local = first_root / "session-local.txt"
    second_local = second_root / "session-local.txt"
    first_local.write_text("first", encoding="utf-8")
    assert second_local.exists() is False

    await turns.shutdown()
    await database.dispose()


@pytest.mark.asyncio
async def test_new_message_cannot_bypass_persisted_frontend_continuation(settings_factory):
    """A fresh service/process must recover pending calls from events before starting the SDK."""
    from app.errors import AppError
    _, db, session, _, _, _, _, turns = await build_turn_services(settings_factory)
    try:
        original = await turns.start(session.id, "read page", [], "origin")
        await turns.wait(original.id)
        await turns.repository.append_event(original.id, "frontend_tool.deferred", "assistant", {"tool_use_id": "call", "name": "page.get_context", "arguments": {}, "origin_run_id": original.id})
        with pytest.raises(AppError) as caught:
            await turns.start(session.id, "next question", [], "next")
        assert caught.value.code == "TOOL_CONTINUATION_REQUIRED"
        pending = await turns.pending_frontend_calls(session.id)
        assert len(pending) == 1 and pending[0].origin_run_id == original.id
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_first_message_rejects_skill_settings_changed_after_session_creation(
    settings_factory,
):
    """A blank session cannot launch a Skill disabled after the New Session click."""
    from app.auth.models import IdentityContext
    from app.errors import AppError
    from tests.test_skill_repository import _bundle

    settings, database, _, sessions, _, runtime, _, turns = (
        await build_turn_services(settings_factory)
    )
    owner = IdentityContext(settings.mock_user_id, settings.mock_user_subject,
                            settings.mock_user_display_name)
    try:
        skill = (await sessions.skills.publish_trusted_global_bundle(
            _bundle("data-discovery"), created_by=owner.user_id,
            origin={"type": "test"},
        )).skill
        blank = await sessions.create("actual", owner)
        await sessions.skills.set_global_enabled(
            "actual", skill.id, owner, enabled=False
        )
        with pytest.raises(AppError) as rejected:
            await turns.start(blank.id, "find data", [], "stale-skills")
        assert rejected.value.code == "session_skills_changed"
        assert runtime.requests == []
        assert (await sessions.get(blank.id)).status == "idle"
        fresh = await sessions.create("actual", owner)
        turn = await turns.start(fresh.id, "find data", [], "fresh-skills")
        await turns.wait(turn.id)
        assert runtime.requests[0].workspace_snapshot["skills"] == []
    finally:
        await turns.shutdown()
        await database.dispose()


@pytest.mark.asyncio
async def test_started_conversation_keeps_pinned_skills_after_workspace_toggle(
    settings_factory,
):
    """The first-message guard must not change historical conversation semantics."""
    from app.auth.models import IdentityContext
    from tests.test_skill_repository import _bundle

    settings, database, _, sessions, _, runtime, _, turns = (
        await build_turn_services(settings_factory)
    )
    owner = IdentityContext(settings.mock_user_id, settings.mock_user_subject,
                            settings.mock_user_display_name)
    try:
        skill = (await sessions.skills.publish_trusted_global_bundle(
            _bundle("data-discovery"), created_by=owner.user_id,
            origin={"type": "test"},
        )).skill
        conversation = await sessions.create("actual", owner)
        first = await turns.start(conversation.id, "hello", [], "before-toggle")
        await turns.wait(first.id)
        await sessions.skills.set_global_enabled(
            "actual", skill.id, owner, enabled=False
        )
        second = await turns.start(conversation.id, "continue", [], "after-toggle")
        await turns.wait(second.id)
        assert [s["id"] for s in runtime.requests[1].workspace_snapshot["skills"]] == [skill.id]
    finally:
        await turns.shutdown()
        await database.dispose()
