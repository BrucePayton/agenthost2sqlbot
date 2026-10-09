import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.agui.deferred_tools import DeferredFrontendToolCall
from app.attachments.service import AttachmentService
from app.db.base import Database
from app.db.models import (
    AttachmentRecord,
    SessionRecord,
    TurnEventRecord,
    TurnRecord,
)
from app.errors import AppError
from app.memory.locks import MemoryScopeLockRegistry
from app.memory.scopes import MemoryScopeService
from app.runtime.base import (
    AgentRuntime,
    RuntimeAttachment,
    RuntimeCancelled,
    RuntimeContextItem,
    RuntimeEvent,
    RuntimeFrontendTool,
    RuntimeRequest,
    RuntimeToolResult,
)
from app.runtime.events import progress_event
from app.sandbox.heartbeat import WorkerAvailabilityService
from app.sessions.locks import SessionLockRegistry
from app.sessions.service import SessionService
from app.turns.broker import EventBroker, PersistedEvent
from app.turns.dispatcher import ExecutionDispatcher
from app.turns.notifications import InProcessTurnNotifier, TurnNotifier
from app.turns.repository import TurnRepository
from app.turns.state_machine import ACTIVE_TURN_STATES, TERMINAL_TURN_STATES

ACTIVE_TURN_STATUSES = ACTIVE_TURN_STATES
TERMINAL_TURN_STATUSES = TERMINAL_TURN_STATES


def _optional_usage_int(usage: dict, key: str) -> int | None:
    """Preserve an unavailable provider metric instead of inventing zero."""
    value = usage.get(key)
    return int(value) if value is not None else None


class TurnService:
    def __init__(
        self,
        *,
        database: Database,
        sessions: SessionService,
        attachments: AttachmentService,
        runtime: AgentRuntime | None,
        locks: SessionLockRegistry,
        memory_scopes: MemoryScopeService,
        memory_locks: MemoryScopeLockRegistry,
        broker: EventBroker,
        timeout_seconds: float,
        repository: TurnRepository | None = None,
        notifier: TurnNotifier | None = None,
        execution_availability: WorkerAvailabilityService | None = None,
    ) -> None:
        self.database = database
        self.sessions = sessions
        self.attachments = attachments
        self.runtime = runtime
        self.locks = locks
        self.memory_scopes = memory_scopes
        self.memory_locks = memory_locks
        self.broker = broker
        self.timeout_seconds = timeout_seconds
        self.repository = repository or TurnRepository(database)
        self.notifier = notifier or InProcessTurnNotifier(broker)
        self.execution_availability = execution_availability
        self._dispatcher: ExecutionDispatcher | None = None

    def bind_dispatcher(self, dispatcher: ExecutionDispatcher) -> None:
        if self._dispatcher is not None:
            raise RuntimeError("TurnService dispatcher is already bound")
        self._dispatcher = dispatcher

    def _require_dispatcher(self) -> ExecutionDispatcher:
        if self._dispatcher is None:
            raise RuntimeError("TurnService dispatcher is not bound")
        return self._dispatcher

    def _require_inline_runtime(self) -> AgentRuntime:
        if self.runtime is None:
            raise RuntimeError("inline Turn execution requires an Agent runtime")
        return self.runtime

    async def start(
        self,
        session_id: str,
        message: str,
        attachment_ids: list[str],
        client_request_id: str,
        file_references: list[str] | tuple[str, ...] = (),
        *,
        turn_id: str | None = None,
        runtime_frontend_tools: tuple[RuntimeFrontendTool, ...] = (),
        runtime_page_state: dict | None = None,
        runtime_tool_results: tuple[RuntimeToolResult, ...] = (),
        runtime_context_items: tuple[RuntimeContextItem, ...] = (),
        runtime_model: str | None = None,
        runtime_effort: str | None = None,
        runtime_metadata: dict | None = None,
    ) -> TurnRecord:
        normalized = message.strip()
        if not client_request_id.strip() or len(client_request_id) > 64:
            raise AppError("invalid_request", "client_request_id is invalid.")
        if len(attachment_ids) != len(set(attachment_ids)):
            raise AppError("attachment_invalid", "Attachment IDs must be unique.")
        max_attachments = self.attachments.settings.max_files_per_turn
        if len(attachment_ids) > max_attachments:
            raise AppError(
                "attachment_invalid",
                f"At most {max_attachments} attachments are allowed per turn.",
            )

        async with self.locks.acquire(session_id):
            async with self.database.session() as db:
                existing = await db.scalar(
                    select(TurnRecord).where(
                        TurnRecord.session_id == session_id,
                        TurnRecord.client_request_id == client_request_id,
                    )
                )
                recovery = await self.repository.frontend_tool_recovery(session_id)
                by_call = {state["call"].tool_call_id: state for state in recovery}
                if runtime_tool_results:
                    # Validate the whole batch against durable bindings before accepting a new Run.
                    ids = [result.tool_call_id for result in runtime_tool_results]
                    if len(ids) != len(set(ids)):
                        raise AppError("TOOL_RESULT_CONFLICT", "重复的工具结果不能组成一次续接。", 409)
                    for result in runtime_tool_results:
                        previous = by_call.get(result.tool_call_id)
                        if previous is None:
                            continue  # Legacy bridge calls may have no native deferred event.
                        bound = previous["continuation_run_id"]
                        old_result = previous["tool_result"]
                        if bound is not None and (bound != (turn_id or client_request_id)
                                or old_result.get("content") != result.content
                                or bool(old_result.get("is_error")) != result.is_error):
                            raise AppError("TOOL_RESULT_CONFLICT", "工具结果与已接收的续接不一致。", 409)
                elif existing is None and any(state["continuation_status"] != "completed" for state in recovery):
                    raise AppError("TOOL_CONTINUATION_REQUIRED", "上次工具操作仍待回传或核对，请先恢复上次操作。", 409)
                if existing is not None:
                    if runtime_tool_results:
                        original = (await self.repository.list_events(existing.id))[0]
                        saved = json.loads(original.payload_json).get("tool_results", [])
                        incoming = [{"tool_call_id": r.tool_call_id, "content": r.content, "is_error": r.is_error} for r in runtime_tool_results]
                        comparable = [{key: item.get(key) for key in ("tool_call_id", "content", "is_error")} for item in saved]
                        if comparable != incoming:
                            raise AppError("TOOL_RESULT_CONFLICT", "此续接 Run 已接收不同的工具结果。", 409)
                    return existing

                if self.execution_availability is not None:
                    await self.execution_availability.require_available()

                session = await db.get(SessionRecord, session_id, with_for_update=True)
                if session is None:
                    raise AppError("session_not_found", "Session not found.", 404)
                normalized_references = (
                    self.sessions.validate_file_references_for_record(
                        session, file_references
                    )
                )
                active = await db.scalar(
                    select(TurnRecord.id).where(
                        TurnRecord.session_id == session_id,
                        TurnRecord.status.in_(ACTIVE_TURN_STATUSES),
                    )
                )
                if active is not None:
                    raise AppError(
                        "session_busy",
                        "This session already has a running turn.",
                        409,
                    )

                records: list[AttachmentRecord] = []
                if attachment_ids:
                    records = list(
                        (
                            await db.scalars(
                                select(AttachmentRecord).where(
                                    AttachmentRecord.id.in_(attachment_ids)
                                )
                            )
                        ).all()
                    )
                if len(records) != len(attachment_ids) or any(
                    record.session_id != session_id or record.status != "pending"
                    for record in records
                ):
                    raise AppError(
                        "attachment_invalid",
                        "Attachments must be pending and belong to this session.",
                    )
                if not normalized and not records and not runtime_tool_results:
                    raise AppError(
                        "invalid_request", "A message or attachment is required."
                    )

                if session.claude_session_id is None:
                    previous_turn = await db.scalar(
                        select(TurnRecord.id)
                        .where(TurnRecord.session_id == session_id)
                        .limit(1)
                    )
                    if previous_turn is None:
                        # New Session can be clicked before a Skill toggle finishes.
                        # Reject before binding attachments or accepting any message.
                        await self.sessions.require_current_skill_snapshot(session)

                now = datetime.now(UTC)
                turn = TurnRecord(
                    id=turn_id or str(uuid.uuid4()),
                    session_id=session_id,
                    client_request_id=client_request_id,
                    status="queued",
                    input_text=normalized,
                    created_at=now,
                )
                db.add(turn)
                payload = {
                    "text": normalized,
                    "attachments": [
                        {
                            "id": record.id,
                            "filename": record.original_filename,
                            "mime_type": record.mime_type,
                            "size_bytes": record.size_bytes,
                        }
                        for record in records
                    ],
                    "file_references": list(normalized_references),
                    "frontend_tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        }
                        for tool in runtime_frontend_tools
                    ],
                    "page_state": dict(runtime_page_state or {}),
                    "tool_results": [
                        {
                            "tool_call_id": result.tool_call_id,
                            "content": result.content,
                            "is_error": result.is_error,
                            "frontend_round_trip_ms": result.frontend_round_trip_ms,
                            "origin": result.origin,
                        }
                        for result in runtime_tool_results
                    ],
                    "context_items": [
                        {"description": item.description, "value": item.value}
                        for item in runtime_context_items
                    ],
                    "model": runtime_model,
                    "effort": runtime_effort,
                    "runtime_metadata": dict(runtime_metadata or {}),
                }
                try:
                    await db.flush()
                    for record in records:
                        record.turn_id = turn.id
                        record.status = "bound"
                    session.status = "running"
                    session.last_error_code = None
                    session.updated_at = now
                    self.repository.add_event_records(
                        db,
                        turn,
                        sequence=1,
                        event_type="message.user",
                        role="user",
                        payload=payload,
                        created_at=now,
                    )
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    duplicate = await db.scalar(
                        select(TurnRecord).where(
                            TurnRecord.session_id == session_id,
                            TurnRecord.client_request_id == client_request_id,
                        )
                    )
                    if duplicate is not None:
                        return duplicate
                    raise AppError(
                        "session_busy",
                        "This session already has an active turn.",
                        409,
                    )
                await db.refresh(turn)

            if normalized or records:
                await self.sessions.set_auto_title(session_id, normalized)
            user_event = PersistedEvent(
                sequence=1,
                event=RuntimeEvent("message.user", payload, "user"),
            )
            await self.notifier.notify(turn.id, user_event.sequence)
            if not self._require_dispatcher().submit(turn.id):
                raise RuntimeError(f"Turn {turn.id} is already submitted")
            return turn

    async def pending_frontend_calls(self, session_id: str) -> tuple[DeferredFrontendToolCall, ...]:
        """Pending includes failed/unknown continuations until their results are resolved."""
        return tuple(state["call"] for state in await self.repository.frontend_tool_recovery(session_id)
                     if state["continuation_status"] != "completed")

    async def get(self, turn_id: str) -> TurnRecord:
        async with self.database.session() as db:
            turn = await db.get(TurnRecord, turn_id)
            if turn is None:
                raise AppError("turn_not_found", "Turn not found.", 404)
            return turn

    async def list_events(
        self, turn_id: str, after_sequence: int = 0
    ) -> list[TurnEventRecord]:
        await self.get(turn_id)
        return await self.repository.list_events(turn_id, after_sequence)

    async def cancel(self, turn_id: str) -> TurnRecord:
        turn = await self.get(turn_id)
        if turn.status in TERMINAL_TURN_STATUSES:
            return turn
        await self._require_dispatcher().cancel(turn_id)
        return turn

    async def wait(self, turn_id: str) -> TurnRecord:
        await self._require_dispatcher().wait(turn_id)
        return await self.get(turn_id)

    async def shutdown(self) -> None:
        if self._dispatcher is not None:
            await self._dispatcher.shutdown()

    async def execute_turn(self, turn_id: str, cancel_event: asyncio.Event) -> None:
        try:
            await self._mark_running(turn_id)
            await self._append_event(
                turn_id,
                progress_event("preparing", "正在准备 Session 环境"),
            )
            request = await self._runtime_request(turn_id)
            result_payload: dict | None = None
            usage_payload: dict = {}
            await self._append_event(
                turn_id,
                progress_event(
                    "waiting_memory",
                    "正在准备个人 Workspace 记忆",
                ),
            )
            async with self.memory_locks.acquire(request.memory_scope_key):
                if cancel_event.is_set():
                    raise RuntimeCancelled()
                async with asyncio.timeout(self.timeout_seconds):
                    async for event in self._require_inline_runtime().run(
                        request,
                        cancel_event,
                    ):
                        if event.type == "runtime.result":
                            result_payload = event.payload
                            continue
                        if event.type == "usage.updated":
                            usage_payload = event.payload
                        await self._append_event(turn_id, event)
            if cancel_event.is_set():
                raise RuntimeCancelled()
            if result_payload is None:
                raise AppError(
                    "claude_unavailable",
                    "Claude ended without returning a result.",
                    503,
                )
            await self._complete(turn_id, result_payload, usage_payload)
        except RuntimeCancelled:
            await self._cancelled(turn_id)
        except TimeoutError:
            cancel_event.set()
            await self._fail(
                turn_id,
                AppError(
                    "turn_timeout", "The turn exceeded its execution timeout.", 504
                ),
            )
        except AppError as exc:
            await self._fail(turn_id, exc)
        except Exception:  # noqa: BLE001 - convert unexpected runtime failures to Turn state
            await self._fail(
                turn_id,
                AppError("internal_error", "The turn failed unexpectedly.", 500),
            )

    async def _mark_running(self, turn_id: str) -> None:
        now = datetime.now(UTC)
        await self.repository.transition(
            turn_id,
            ("queued", "waiting_for_memory", "assigned"),
            "running",
            {
                "execution_nonce": str(uuid.uuid4()),
                "runtime_cohort": "local",
            },
        )
        await self._append_event(
            turn_id,
            RuntimeEvent(
                "turn.started",
                {"turn_id": turn_id, "started_at": now.isoformat()},
                "system",
            ),
        )

    async def _runtime_request(self, turn_id: str) -> RuntimeRequest:
        async with self.database.session() as db:
            turn = await db.get(TurnRecord, turn_id)
            if turn is None:
                raise AppError("turn_not_found", "Turn not found.", 404)
            session = await db.get(SessionRecord, turn.session_id)
            if session is None:
                raise AppError("session_not_found", "Session not found.", 404)
            attachments = list(
                (
                    await db.scalars(
                        select(AttachmentRecord).where(
                            AttachmentRecord.turn_id == turn_id
                        )
                    )
                ).all()
            )
            user_message = await db.scalar(
                select(TurnEventRecord).where(
                    TurnEventRecord.turn_id == turn_id,
                    TurnEventRecord.event_type == "message.user",
                    TurnEventRecord.sequence == 1,
                )
            )
            if user_message is None:
                raise AppError("internal_error", "Turn input event is missing.", 500)
            input_payload = json.loads(user_message.payload_json)
            references = self.sessions.validate_file_references_for_record(
                session, input_payload.get("file_references", [])
            )
            memory_scope = self.memory_scopes.resolve(
                session.created_by,
                session.workspace_id,
            )
            # Both inline and remote workers resume the same server-owned task checkpoint.
            checkpoint = await db.scalar(
                select(TurnEventRecord).join(TurnRecord, TurnRecord.id == TurnEventRecord.turn_id)
                .where(TurnRecord.session_id == session.id, TurnRecord.id != turn_id,
                       TurnEventRecord.event_type == "subscription.task")
                .order_by(TurnRecord.created_at.desc(), TurnEventRecord.sequence.desc()).limit(1)
            )
            runtime_metadata = dict(input_payload.get("runtime_metadata", {}))
            runtime_metadata.pop("subscription_task", None)
            # Backend/tool schemas are server-owned, never accepted from request metadata.
            runtime_metadata["data_backend"] = session.data_backend
            runtime_metadata["data_mcp_tools"] = json.loads(session.data_mcp_tools_json)

            if checkpoint is not None:
                runtime_metadata["subscription_task"] = json.loads(checkpoint.payload_json)
        session_path = self.sessions.session_path(session)
        return RuntimeRequest(
            platform_session_id=session.id,
            claude_session_id=session.claude_session_id,
            cwd=session_path / "workspace",
            claude_config_dir=session_path / "claude-config",
            memory_scope_key=memory_scope.key,
            memory_dir=memory_scope.directory,
            text=turn.input_text,
            attachments=tuple(
                RuntimeAttachment(
                    id=record.id,
                    original_filename=record.original_filename,
                    mime_type=record.mime_type,
                    path=self.attachments.resolve_path(record),
                )
                for record in attachments
            ),
            file_references=references,
            workspace_snapshot=json.loads(session.workspace_snapshot_json),
            frontend_tools=tuple(
                RuntimeFrontendTool(
                    name=str(tool["name"]),
                    description=str(tool["description"]),
                    parameters=dict(tool["parameters"]),
                )
                for tool in input_payload.get("frontend_tools", [])
            ),
            page_state=dict(input_payload.get("page_state", {})),
            tool_results=tuple(
                RuntimeToolResult(
                    tool_call_id=str(result["tool_call_id"]),
                    content=str(result["content"]),
                    is_error=bool(result.get("is_error", False)),
                    frontend_round_trip_ms=result.get("frontend_round_trip_ms"),
                    origin="program" if result.get("origin") == "program" else "model",
                )
                for result in input_payload.get("tool_results", [])
            ),
            context_items=tuple(
                RuntimeContextItem(
                    description=str(item["description"]),
                    value=str(item["value"]),
                )
                for item in input_payload.get("context_items", [])
            ),
            run_id=turn.id,
            model=input_payload.get("model"),
            effort=input_payload.get("effort"),
            metadata=runtime_metadata,
        )

    async def build_runtime_request(self, turn_id: str) -> RuntimeRequest:
        """Build the validated immutable request used by every execution backend."""
        return await self._runtime_request(turn_id)

    async def append_runtime_event(
        self, turn_id: str, event: RuntimeEvent
    ) -> PersistedEvent:
        return await self._append_event(turn_id, event)

    async def complete_runtime_turn(
        self, turn_id: str, result: dict, usage: dict
    ) -> None:
        await self._complete(turn_id, result, usage)

    async def finish_runtime_turn(
        self,
        turn_id: str,
        *,
        turn_status: str,
        session_status: str,
        error_code: str | None,
        error_message: str | None,
        event: RuntimeEvent,
        completed_at: datetime,
    ) -> None:
        await self._finish_with_status(
            turn_id,
            turn_status=turn_status,
            session_status=session_status,
            error_code=error_code,
            error_message=error_message,
            event=event,
            completed_at=completed_at,
        )

    async def _append_event(self, turn_id: str, event: RuntimeEvent) -> PersistedEvent:
        record = await self.repository.append_event(
            turn_id, event.type, event.role, event.payload
        )
        persisted = PersistedEvent(sequence=record.sequence, event=event)
        await self.notifier.notify(turn_id, persisted.sequence)
        return persisted

    async def _complete(self, turn_id: str, result: dict, usage: dict) -> None:
        now = datetime.now(UTC)
        await self.repository.transition(
            turn_id,
            ("running",),
            "finalizing",
            {"finalization_status": "in_progress"},
        )
        async with self.database.session() as db:
            turn = await db.get(TurnRecord, turn_id)
            if turn is None:
                return
            session = await db.get(SessionRecord, turn.session_id)
            if session is None:
                return
            returned_session_id = str(result["claude_session_id"])
            if (
                session.claude_session_id
                and session.claude_session_id != returned_session_id
            ):
                raise AppError(
                    "claude_resume_failed",
                    "Claude returned a different session while resuming.",
                    409,
                )
            session.claude_session_id = returned_session_id
            session.status = "idle"
            session.last_error_code = None
            session.updated_at = now
            turn.input_tokens = int(usage.get("input_tokens") or 0)
            turn.uncached_input_tokens = _optional_usage_int(
                usage, "uncached_input_tokens"
            )
            turn.cache_read_input_tokens = _optional_usage_int(
                usage, "cache_read_input_tokens"
            )
            turn.cache_creation_input_tokens = _optional_usage_int(
                usage, "cache_creation_input_tokens"
            )
            turn.total_input_tokens = _optional_usage_int(
                usage, "total_input_tokens"
            )
            turn.output_tokens = int(usage.get("output_tokens") or 0)
            turn.model_api_turns = int(usage.get("model_api_turns") or 0)
            turn.frontend_tool_calls = int(
                usage.get("frontend_tool_calls") or 0
            )
            turn.tool_search_calls = int(usage.get("tool_search_calls") or 0)
            turn.tool_set_changes = int(usage.get("tool_set_changes") or 0)
            turn.catalog_digest_changes = int(
                usage.get("catalog_digest_changes") or 0
            )
            if usage.get("cost_usd") is not None:
                turn.cost_usd = Decimal(str(usage["cost_usd"]))
            await db.commit()
        await self.repository.transition(
            turn_id,
            ("finalizing",),
            "completed",
            {"finalization_status": "complete"},
        )
        await self._append_event(
            turn_id,
            RuntimeEvent("turn.completed", {"completed_at": now.isoformat()}, "system"),
        )

    async def _cancelled(self, turn_id: str) -> None:
        now = datetime.now(UTC)
        await self._finish_with_status(
            turn_id,
            turn_status="cancelled",
            session_status="idle",
            error_code=None,
            error_message=None,
            event=RuntimeEvent(
                "turn.cancelled", {"cancelled_at": now.isoformat()}, "system"
            ),
            completed_at=now,
        )

    async def _fail(self, turn_id: str, error: AppError) -> None:
        now = datetime.now(UTC)
        await self._finish_with_status(
            turn_id,
            turn_status="failed",
            session_status="error",
            error_code=error.code,
            error_message=error.message,
            event=RuntimeEvent(
                "turn.failed",
                {"code": error.code, "message": error.message},
                "system",
            ),
            completed_at=now,
        )

    async def _finish_with_status(
        self,
        turn_id: str,
        *,
        turn_status: str,
        session_status: str,
        error_code: str | None,
        error_message: str | None,
        event: RuntimeEvent,
        completed_at: datetime,
    ) -> None:
        turn = await self.get(turn_id)
        if turn.status in TERMINAL_TURN_STATUSES:
            return
        await self.repository.transition(
            turn_id,
            (turn.status,),
            turn_status,
            {
                "error_code": error_code,
                "error_message": error_message,
            },
        )
        async with self.database.session() as db:
            turn = await db.get(TurnRecord, turn_id)
            if turn is None:
                return
            session = await db.get(SessionRecord, turn.session_id)
            if session is not None:
                session.status = session_status
                session.last_error_code = error_code
                session.updated_at = completed_at
            await db.commit()
        await self._append_event(turn_id, event)
