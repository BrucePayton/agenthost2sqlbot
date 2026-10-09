from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.base import Database
from app.db.models import SessionRecord, UserRecord
from app.runner.protocol import (
    RunnerAttachment,
    RunnerFrame,
    RunnerFrameValidator,
    RunnerRequest,
)
from app.runtime.contracts import RuntimeEvent
from app.sandbox.contracts import (
    CredentialProxyUnavailable,
    SandboxError,
    SandboxPort,
)
from app.sandbox.credentials import CredentialScope, ModelCredentialProvider
from app.sandbox.models import (
    CommandHandle,
    CommandLifecycle,
    RunnerModelConfig,
    SandboxHandle,
    SandboxLifecycle,
    SessionSandboxSpec,
)
from app.sandbox.repository import SandboxRepository
from app.turns.service import TurnService

logger = logging.getLogger(__name__)


class OpenSandboxExecutionWorker:
    def __init__(
        self,
        *,
        database: Database,
        turns: TurnService,
        repository: SandboxRepository,
        sandbox: SandboxPort,
        runtime_cohort: str,
        runner_image: str,
        allowed_hosts: tuple[str, ...],
        sandbox_timeout_seconds: int,
        memory_lease_seconds: int,
        runner_runtime: str = "claude",
        credential_provider: ModelCredentialProvider | None = None,
        data_agent_service=None,
    ) -> None:
        self.data_agent_service = data_agent_service
        self.database = database
        self.turns = turns
        self.repository = repository
        self.sandbox = sandbox
        self.runtime_cohort = runtime_cohort
        self.runner_image = runner_image
        self.runner_runtime = runner_runtime
        self.allowed_hosts = allowed_hosts
        self.sandbox_timeout_seconds = sandbox_timeout_seconds
        self.memory_lease_seconds = memory_lease_seconds
        self.credential_provider = credential_provider
        if self.runner_runtime == "claude" and self.credential_provider is None:
            raise ValueError("Claude sandbox runner requires a credential provider")
        if self.runner_runtime == "fake" and self.credential_provider is not None:
            raise ValueError("Fake sandbox runner cannot use a credential provider")

    async def execute_one(self) -> str | None:
        turn = await self.repository.claim_next_turn()
        if turn is None:
            return None
        if turn.cancel_requested_at is not None:
            await self.turns.finish_runtime_turn(
                turn.id,
                turn_status="cancelled_before_execution",
                session_status="idle",
                error_code=None,
                error_message=None,
                event=RuntimeEvent(
                    "turn.cancelled",
                    {"cancelled_at": datetime.now(UTC).isoformat()},
                    "system",
                ),
                completed_at=datetime.now(UTC),
            )
            return turn.id
        runtime_request = await self.turns.build_runtime_request(turn.id)
        attempt_id = str(uuid.uuid4())
        lease = await self.repository.acquire_memory_lease(
            scope_key=runtime_request.memory_scope_key,
            session_id=turn.session_id,
            turn_id=turn.id,
            attempt_id=attempt_id,
            now=datetime.now(UTC),
            ttl=timedelta(seconds=self.memory_lease_seconds),
        )
        if lease is None:
            await self.turns.repository.transition(
                turn.id, ("waiting_for_memory",), "queued", {}
            )
            return None
        crossed_barrier = False
        sandbox_record = None
        handle = None
        try:
            owner = await self._session_owner(runtime_request.platform_session_id)
            credential = (
                await self.credential_provider.resolve(
                    CredentialScope(
                        workspace_id=owner.workspace_id,
                        user_id=owner.created_by,
                        session_id=owner.id,
                    )
                )
                if self.credential_provider is not None
                else None
            )
            sandbox_record = await self.repository.reserve_generation(
                session_id=turn.session_id,
                memory_scope_key=runtime_request.memory_scope_key,
                runtime_cohort=self.runtime_cohort,
                runner_image=self.runner_image,
            )
            if sandbox_record.status in {"ready", "idle"} and sandbox_record.sandbox_id:
                observation = await self.sandbox.inspect_sandbox(
                    sandbox_record.sandbox_id
                )
                if observation.status not in {
                    SandboxLifecycle.READY,
                    SandboxLifecycle.IDLE,
                }:
                    raise RuntimeError("Existing sandbox is not reusable")
                handle = SandboxHandle(
                    sandbox_id=sandbox_record.sandbox_id,
                    generation=sandbox_record.generation,
                    created_at=sandbox_record.created_at,
                    image_reference=sandbox_record.runner_image,
                    session_volume_name=sandbox_record.session_volume_name,
                    memory_volume_name=sandbox_record.memory_volume_name,
                )
            else:
                handle = await self.sandbox.create_session_sandbox(
                    SessionSandboxSpec(
                        session_key=turn.session_id,
                        memory_scope_key=runtime_request.memory_scope_key,
                        generation=sandbox_record.generation,
                        runner_image=self.runner_image,
                        allowed_hosts=(
                            self.allowed_hosts
                            if self.runner_runtime == "claude"
                            else ()
                        ),
                        credential_proxy_required=self.runner_runtime == "claude",
                        timeout_seconds=self.sandbox_timeout_seconds,
                        model_config=(
                            RunnerModelConfig(base_url=credential.endpoint.base_url)
                            if credential is not None
                            else None
                        ),
                    )
                )
            if credential is not None:
                await self.sandbox.ensure_model_credential(handle, credential)
            await self.sandbox.sync_workspace(handle, runtime_request.cwd)
            if sandbox_record.status not in {"ready", "idle"}:
                sandbox_record = await self.repository.record_sandbox_ready(
                    session_id=turn.session_id,
                    generation=sandbox_record.generation,
                    expected_version=sandbox_record.version,
                    sandbox_id=handle.sandbox_id,
                    image_digest=(
                        self.runner_image.rsplit("@", 1)[-1]
                        if "@" in self.runner_image
                        else self.runner_image
                    ),
                )
            request = await self._runner_request(
                turn.id,
                attempt_id,
                sandbox_record.generation,
                runtime_request,
                owner,
            )
            request_path = await self.sandbox.write_request(handle, request.to_bytes())
            execution_nonce = str(uuid.uuid4())
            await self.turns.repository.transition(
                turn.id,
                ("waiting_for_memory", "assigned"),
                "running",
                {
                    "execution_nonce": execution_nonce,
                    "runtime_cohort": self.runtime_cohort,
                    "attempt_id": attempt_id,
                    "sandbox_generation": sandbox_record.generation,
                    "sandbox_id": handle.sandbox_id,
                },
            )
            crossed_barrier = True
            sandbox_record = await self.repository.mark_busy(
                session_id=turn.session_id,
                generation=sandbox_record.generation,
                expected_version=sandbox_record.version,
                turn_id=turn.id,
                attempt_id=attempt_id,
            )
            command = await self.sandbox.run_turn(handle, request_path)
            await self.turns.repository.bind_attempt_command(
                turn_id=turn.id,
                execution_nonce=execution_nonce,
                sandbox_generation=sandbox_record.generation,
                sandbox_id=handle.sandbox_id,
                command_session_id=command.command_session_id,
                command_execution_id=command.execution_id,
            )
            sandbox_record = await self.repository.bind_active_command(
                session_id=turn.session_id,
                generation=sandbox_record.generation,
                expected_version=sandbox_record.version,
                command_session_id=command.command_session_id,
                command_execution_id=command.execution_id,
            )
            result, usage = await self._consume_frames(
                turn.id,
                command,
                memory_scope_key=runtime_request.memory_scope_key,
                lease_token=lease.lease_token,
                sandbox_id=handle.sandbox_id,
            )
            if result.get("status") == "cancelled":
                await self.turns.finish_runtime_turn(
                    turn.id,
                    turn_status="cancelled",
                    session_status="idle",
                    error_code=None,
                    error_message=None,
                    event=RuntimeEvent(
                        "turn.cancelled",
                        {"cancelled_at": datetime.now(UTC).isoformat()},
                        "system",
                    ),
                    completed_at=datetime.now(UTC),
                )
            else:
                await self.turns.complete_runtime_turn(turn.id, result, usage)
            await self.repository.mark_idle(
                session_id=turn.session_id,
                generation=sandbox_record.generation,
                expected_version=sandbox_record.version,
            )
            return turn.id
        except CredentialProxyUnavailable:
            if crossed_barrier:
                await self._record_post_barrier_recovery(
                    turn.id, turn.session_id, sandbox_record
                )
                raise
            await self._fail_credential_preparation(
                turn_id=turn.id,
                session_id=turn.session_id,
                sandbox_record=sandbox_record,
                handle=handle,
            )
            return turn.id
        except Exception:
            if crossed_barrier:
                await self._record_post_barrier_recovery(
                    turn.id,
                    turn.session_id,
                    sandbox_record,
                )
            raise
        finally:
            await self.repository.release_memory_lease(
                runtime_request.memory_scope_key, lease.lease_token
            )

    async def reap_idle(self, *, now: datetime, idle_ttl_seconds: int) -> int:
        cutoff = now - timedelta(seconds=idle_ttl_seconds)
        candidates = await self.repository.list_idle_before(cutoff)
        reaped = 0
        for record in candidates:
            if record.sandbox_id is None:
                continue
            await self.sandbox.destroy_sandbox(record.sandbox_id)
            await self.repository.mark_reaped(
                session_id=record.session_id,
                generation=record.generation,
                expected_version=record.version,
            )
            reaped += 1
        return reaped

    async def reconcile_active(self) -> int:
        """Reattach to persisted commands without ever replaying a turn."""
        reconciled = 0
        for record in await self.repository.list_busy():
            if not (
                record.sandbox_id
                and record.active_turn_id
                and record.active_attempt_id
                and record.active_command_session_id
                and record.active_command_execution_id
            ):
                await self._mark_ambiguous(record, "missing_command_identity")
                reconciled += 1
                continue
            lease = await self.repository.acquire_memory_lease(
                scope_key=record.memory_scope_key,
                session_id=record.session_id,
                turn_id=record.active_turn_id,
                attempt_id=record.active_attempt_id,
                now=datetime.now(UTC),
                ttl=timedelta(seconds=self.memory_lease_seconds),
            )
            if lease is None:
                continue
            command = CommandHandle(
                sandbox_id=record.sandbox_id,
                command_session_id=record.active_command_session_id,
                execution_id=record.active_command_execution_id,
            )
            try:
                observation = await self.sandbox.inspect_command(command)
                if observation.status in {
                    CommandLifecycle.RUNNING,
                    CommandLifecycle.SUCCEEDED,
                }:
                    result, usage = await self._consume_frames(
                        record.active_turn_id,
                        command,
                        memory_scope_key=record.memory_scope_key,
                        lease_token=lease.lease_token,
                        sandbox_id=record.sandbox_id,
                    )
                    await self.turns.complete_runtime_turn(
                        record.active_turn_id, result, usage
                    )
                    await self.repository.mark_idle(
                        session_id=record.session_id,
                        generation=record.generation,
                        expected_version=record.version,
                    )
                elif observation.status == CommandLifecycle.CANCELLED:
                    await self.turns.finish_runtime_turn(
                        record.active_turn_id,
                        turn_status="cancelled",
                        session_status="idle",
                        error_code=None,
                        error_message=None,
                        event=RuntimeEvent(
                            "turn.cancelled",
                            {"cancelled_at": datetime.now(UTC).isoformat()},
                            "system",
                        ),
                        completed_at=datetime.now(UTC),
                    )
                    await self.repository.mark_idle(
                        session_id=record.session_id,
                        generation=record.generation,
                        expected_version=record.version,
                    )
                else:
                    await self._mark_ambiguous(record, "command_not_recoverable")
                reconciled += 1
            except Exception:
                logger.exception(
                    "Failed to reconcile sandbox command",
                    extra={"session_id": record.session_id},
                )
                await self._mark_ambiguous(record, "reconcile_failed")
                reconciled += 1
            finally:
                try:
                    await self.repository.release_memory_lease(
                        record.memory_scope_key, lease.lease_token
                    )
                except Exception:
                    logger.exception("Failed to release reconciliation lease")
        return reconciled

    async def _mark_ambiguous(self, record, reason: str) -> None:
        current = (
            await self.turns.get(record.active_turn_id)
            if record.active_turn_id
            else None
        )
        if current is not None and current.status not in {
            "completed",
            "failed",
            "cancelled",
        }:
            await self.turns.finish_runtime_turn(
                record.active_turn_id,
                turn_status="recovery_required",
                session_status="error",
                error_code="sandbox_recovery_required",
                error_message="Sandbox execution requires reconciliation.",
                event=RuntimeEvent(
                    "turn.recovery_required",
                    {"code": "sandbox_recovery_required", "reason": reason},
                    "system",
                ),
                completed_at=datetime.now(UTC),
            )
        await self.repository.mark_recovery_required(
            session_id=record.session_id,
            generation=record.generation,
            expected_version=record.version,
            reason=reason,
        )

    async def _runner_request(
        self,
        turn_id: str,
        attempt_id: str,
        generation: int,
        runtime_request,
        owner: SessionRecord,
    ) -> RunnerRequest:
        attachments = []
        for item in runtime_request.attachments:
            try:
                relative = item.path.relative_to(runtime_request.cwd)
            except ValueError:
                relative = Path("attachments") / item.path.name
            attachments.append(
                RunnerAttachment(
                    id=item.id,
                    original_filename=item.original_filename,
                    mime_type=item.mime_type,
                    relative_path=relative.as_posix(),
                )
            )
        return RunnerRequest(
            protocol_version="1",
            runtime_kind=self.runner_runtime,
            user_id=owner.created_by,
            workspace_id=owner.workspace_id,
            platform_session_id=owner.id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            generation=generation,
            claude_session_id=runtime_request.claude_session_id,
            memory_scope_key=runtime_request.memory_scope_key,
            text=runtime_request.text,
            attachments=tuple(attachments),
            file_references=runtime_request.file_references,
            workspace_snapshot=runtime_request.workspace_snapshot,
            frontend_tools=tuple(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in runtime_request.frontend_tools
            ),
            page_state=runtime_request.page_state,
            tool_results=tuple(
                {
                    "tool_call_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                    "frontend_round_trip_ms": result.frontend_round_trip_ms,
                    "origin": result.origin,
                }
                for result in runtime_request.tool_results
            ),
            context_items=tuple(
                {"description": item.description, "value": item.value}
                for item in runtime_request.context_items
            ),
            subscription_task=dict(runtime_request.metadata.get("subscription_task") or {}),
            data_backend=runtime_request.metadata.get("data_backend", "sqlbot"),
            data_mcp_tools=runtime_request.metadata.get("data_mcp_tools", []),
        )

    async def _session_owner(self, session_id: str) -> SessionRecord:
        async with self.database.session() as session:
            owner = await session.get(SessionRecord, session_id)
            if owner is None:
                raise RuntimeError("Session disappeared while preparing Runner request")
            return owner

    async def _fail_credential_preparation(
        self,
        *,
        turn_id: str,
        session_id: str,
        sandbox_record,
        handle: SandboxHandle | None,
    ) -> None:
        cleanup_failed = handle is None
        if handle is not None:
            try:
                await self.sandbox.destroy_sandbox(handle.sandbox_id)
                cleanup_failed = False
            except SandboxError as exc:
                logger.warning(
                    "Credential failure sandbox cleanup failed",
                    extra={
                        "session_id": session_id,
                        "error_type": type(exc).__name__,
                    },
                )
                cleanup_failed = True
        if sandbox_record is not None:
            try:
                if cleanup_failed:
                    await self.repository.mark_recovery_required(
                        session_id=session_id,
                        generation=sandbox_record.generation,
                        expected_version=sandbox_record.version,
                        reason="credential_proxy_cleanup_failed",
                    )
                else:
                    await self.repository.mark_reaped(
                        session_id=session_id,
                        generation=sandbox_record.generation,
                        expected_version=sandbox_record.version,
                    )
            except Exception as exc:  # noqa: BLE001 - preserve terminal Turn state
                logger.warning(
                    "Failed to persist credential cleanup state",
                    extra={
                        "session_id": session_id,
                        "error_type": type(exc).__name__,
                    },
                )
        message = "The sandbox credential proxy is unavailable."
        await self.turns.finish_runtime_turn(
            turn_id,
            turn_status="failed_before_execution",
            session_status="error",
            error_code="credential_proxy_unavailable",
            error_message=message,
            event=RuntimeEvent(
                "turn.failed",
                {
                    "code": "credential_proxy_unavailable",
                    "message": message,
                },
                "system",
            ),
            completed_at=datetime.now(UTC),
        )

    async def _record_post_barrier_recovery(
        self,
        turn_id: str,
        session_id: str,
        sandbox_record,
    ) -> None:
        current = await self.turns.get(turn_id)
        if current.status not in {"completed", "failed", "cancelled"}:
            await self.turns.finish_runtime_turn(
                turn_id,
                turn_status="recovery_required",
                session_status="error",
                error_code="sandbox_recovery_required",
                error_message="Sandbox execution requires reconciliation.",
                event=RuntimeEvent(
                    "turn.recovery_required",
                    {"code": "sandbox_recovery_required"},
                    "system",
                ),
                completed_at=datetime.now(UTC),
            )
        if sandbox_record is not None:
            try:
                await self.repository.mark_recovery_required(
                    session_id=session_id,
                    generation=sandbox_record.generation,
                    expected_version=sandbox_record.version,
                    reason="command_state_ambiguous",
                )
            except Exception:
                logger.exception(
                    "Failed to persist ambiguous sandbox state",
                    extra={"session_id": session_id},
                )

    async def _answer_data_request(self, turn_id: str, sandbox_id: str, payload: dict, *, table_request: bool = False) -> None:
        from app.data_mcp.schemas import DataAskInput
        from app.errors import AppError
        request_id = payload.get("request_id", "")
        if uuid.UUID(request_id).hex != request_id:
            raise ValueError("Invalid data request id")
        try:
            turn = await self.turns.get(turn_id)
            owner = await self._session_owner(turn.session_id)
            if owner.workspace_id != "data-question" or self.data_agent_service is None:
                raise AppError("data_agent_unavailable", "Data Agent is unavailable in this workspace.", 403)
            if table_request:
                body = await self.data_agent_service.call_table_tool(
                    host_session_key=owner.id, name=payload.get("name", ""),
                    arguments=payload.get("arguments", {}))
                await self.sandbox.write_data_response(sandbox_id, request_id, json.dumps(body, ensure_ascii=False).encode())
                return
            if getattr(owner, "data_backend", "sqlbot") != "sqlbot":
                raise AppError("data_backend_mismatch", "MCP mode cannot call SQLBot.", 403)
            question = DataAskInput.model_validate({key: value for key, value in payload.items() if key != "request_id"})
            async with self.database.session() as db:
                user = await db.get(UserRecord, owner.created_by)
                if user is None:
                    raise AppError("data_agent_identity_missing", "Session owner is unavailable.", 403)
                subject = user.external_subject
            result = await self.data_agent_service.ask(
                user_subject=subject, host_session_key=owner.id, question=question.question,
                context_mode=question.context_mode)
            body = result.model_dump(mode="json", by_alias=True, exclude={"presentation"})
        except AppError as exc:
            body = {"error": {"code": exc.code, "message": exc.message}}
        except Exception:  # noqa: BLE001 - redact Host errors before crossing the sandbox boundary.
            body = {"error": {"code": "data_ask_failed", "message": "The governed data question failed."}}
        await self.sandbox.write_data_response(sandbox_id, request_id,
            json.dumps(body, ensure_ascii=False).encode())

    async def _consume_frames(
        self,
        turn_id: str,
        command,
        *,
        memory_scope_key: str,
        lease_token: str,
        sandbox_id: str,
    ) -> tuple[dict, dict]:
        validator = RunnerFrameValidator()
        cursor = None
        result = None
        usage = {}
        loop = asyncio.get_running_loop()
        last_renewed = loop.time()
        renew_interval = max(
            1.0,
            min(
                30.0,
                self.memory_lease_seconds / 3,
                self.sandbox_timeout_seconds / 3,
            ),
        )
        data_tasks: set[asyncio.Task] = set()
        try:
            while True:
                for task in tuple(data_tasks):
                    if task.done():
                        task.result()
                        data_tasks.remove(task)
                if loop.time() - last_renewed >= renew_interval:
                    now = datetime.now(UTC)
                    await self.repository.renew_memory_lease(
                        scope_key=memory_scope_key,
                        lease_token=lease_token,
                        now=now,
                        ttl=timedelta(seconds=self.memory_lease_seconds),
                    )
                    await self.sandbox.renew_sandbox(
                        sandbox_id, self.sandbox_timeout_seconds
                    )
                    last_renewed = loop.time()
                current = await self.turns.get(turn_id)
                if current.cancel_requested_at is not None:
                    await self.sandbox.cancel_command(command)
                    observation = await self.sandbox.inspect_command(command)
                    if observation.status.value in {
                        "cancelled",
                        "failed",
                        "succeeded",
                    }:
                        return {"status": "cancelled"}, usage
                    await asyncio.sleep(0.25)
                    continue
                batch = await self.sandbox.read_frames(command, cursor)
                for line in batch.lines:
                    frame = RunnerFrame.model_validate_json(line)
                    validator.accept(frame)
                    payload = dict(frame.payload)
                    event_type = str(payload.pop("event_type", frame.kind))
                    if event_type in {"data.ask.request", "data.table.request"}:
                        if data_tasks:
                            raise ValueError("Only one data request may be pending per turn")
                        data_tasks.add(asyncio.create_task(self._answer_data_request(turn_id, sandbox_id, payload, table_request=event_type == "data.table.request")))
                        continue
                    if frame.kind == "terminal":
                        result = payload
                        continue
                    event = RuntimeEvent(event_type, payload, frame.role)
                    if frame.kind == "usage":
                        usage = payload
                    await self.turns.append_runtime_event(turn_id, event)
                cursor = batch.next_cursor
                if batch.complete:
                    break
                if not batch.lines:
                    await asyncio.sleep(0.25)
        finally:
            for task in data_tasks:
                task.cancel()
            await asyncio.gather(*data_tasks, return_exceptions=True)
        if result is None or result.get("status") != "completed":
            raise RuntimeError("Runner ended without a completed terminal frame")
        return result, usage
