from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig
from opensandbox.exceptions import SandboxException
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.sandboxes import (
    PVC,
    Credential,
    CredentialBinding,
    CredentialBindingMutationSet,
    CredentialMutationSet,
    CredentialProxyConfig,
    NetworkPolicy,
    NetworkRule,
    Volume,
)

from app.sandbox.contracts import (
    CancelResult,
    CommandNotFound,
    CredentialProxyUnavailable,
    SandboxNotFound,
    SandboxUnavailable,
)
from app.sandbox.credentials import (
    MODEL_BINDING_NAME,
    MODEL_CREDENTIAL_NAME,
    ModelCredential,
)
from app.sandbox.models import (
    CommandHandle,
    CommandLifecycle,
    CommandObservation,
    FrameBatch,
    SandboxHandle,
    SandboxLifecycle,
    SandboxObservation,
    SessionSandboxSpec,
    memory_volume_name,
    session_volume_name,
)

logger = logging.getLogger(__name__)


class OpenSandboxAdapter:
    """Translate the official SDK into the application-owned sandbox port."""

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        ready_timeout_seconds: int,
        request_timeout_seconds: int = 30,
        command_timeout_seconds: int = 900,
    ) -> None:
        parsed = urlsplit(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("OpenSandbox API URL must be an absolute HTTP URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("OpenSandbox API URL must not contain path or query")
        if not api_key.strip():
            raise ValueError("OpenSandbox API key cannot be blank")
        self._connection = ConnectionConfig(
            api_key=api_key.strip(),
            domain=parsed.netloc,
            protocol=parsed.scheme,
            request_timeout=timedelta(seconds=request_timeout_seconds),
            # Workers run in a different container/network namespace from
            # Docker-backed sandboxes. Route execd and egress traffic through
            # the OpenSandbox control plane instead of host-published ports.
            use_server_proxy=True,
        )
        self._ready_timeout = timedelta(seconds=ready_timeout_seconds)
        self._command_timeout = timedelta(seconds=command_timeout_seconds)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(protocol={self._connection.protocol!r}, "
            f"domain={self._connection.domain!r})"
        )

    async def create_session_sandbox(self, spec: SessionSandboxSpec) -> SandboxHandle:
        session_name = session_volume_name(spec.session_key)
        memory_name = memory_volume_name(spec.memory_scope_key, "v1")
        try:
            sandbox = await Sandbox.create(
                spec.runner_image,
                timeout=timedelta(seconds=spec.timeout_seconds),
                ready_timeout=self._ready_timeout,
                metadata={
                    "workspace-agent.session": spec.session_key,
                    "workspace-agent.generation": str(spec.generation),
                },
                network_policy=NetworkPolicy(
                    defaultAction="deny",
                    egress=[
                        NetworkRule(action="allow", target=host)
                        for host in spec.allowed_hosts
                    ],
                ),
                credential_proxy=CredentialProxyConfig(
                    enabled=spec.credential_proxy_required
                ),
                **(
                    {
                        "env": {
                            "ANTHROPIC_BASE_URL": spec.model_config.base_url,
                            "ANTHROPIC_API_KEY": (
                                spec.model_config.api_key_placeholder
                            ),
                            "WORKSPACES_ROOT": "/session/workspace",
                            "APP_DATA_DIR": "/session/runtime-data",
                        }
                    }
                    if spec.model_config is not None
                    else {}
                ),
                entrypoint=["tail", "-f", "/dev/null"],
                volumes=[
                    Volume(
                        name=session_name,
                        pvc=PVC(
                            claimName=session_name,
                            createIfNotExists=True,
                            deleteOnSandboxTermination=False,
                        ),
                        mountPath="/session",
                        readOnly=False,
                    ),
                    Volume(
                        name=memory_name,
                        pvc=PVC(
                            claimName=memory_name,
                            createIfNotExists=True,
                            deleteOnSandboxTermination=False,
                        ),
                        mountPath="/memory",
                        readOnly=False,
                    ),
                ],
                connection_config=self._connection,
            )
            info = await sandbox.get_info()
        except Exception as exc:
            raise self._translate_error(exc, "create") from exc
        return SandboxHandle(
            sandbox_id=sandbox.id,
            generation=spec.generation,
            created_at=getattr(info, "created_at", None) or datetime.now(UTC),
            image_reference=spec.runner_image,
            session_volume_name=session_name,
            memory_volume_name=memory_name,
        )

    async def inspect_sandbox(self, sandbox_id: str) -> SandboxObservation:
        try:
            sandbox = await self._connect(sandbox_id)
            info = await sandbox.get_info()
        except Exception as exc:
            raise self._translate_error(exc, "inspect") from exc
        raw_state = str(info.status.state).lower()
        status = {
            "pending": SandboxLifecycle.PROVISIONING,
            "running": SandboxLifecycle.READY,
            "paused": SandboxLifecycle.IDLE,
            "terminated": SandboxLifecycle.TERMINATED,
        }.get(raw_state, SandboxLifecycle.RECOVERY_REQUIRED)
        return SandboxObservation(
            status=status,
            observed_at=datetime.now(UTC),
            detail_code=getattr(info.status, "reason", None),
        )

    async def ensure_model_credential(
        self,
        handle: SandboxHandle,
        credential: ModelCredential,
    ) -> None:
        sdk_credential = Credential(
            name=MODEL_CREDENTIAL_NAME,
            source={"value": credential.api_key.get_secret_value()},
        )
        sdk_binding = CredentialBinding(
            name=MODEL_BINDING_NAME,
            match={
                "schemes": ["https"],
                "hosts": [credential.endpoint.host],
                "methods": ["GET", "POST"],
                "paths": [credential.endpoint.binding_path],
            },
            auth={
                "type": "apiKey",
                "name": "x-api-key",
                "credential": MODEL_CREDENTIAL_NAME,
            },
        )
        try:
            sandbox = await self._connect(handle.sandbox_id)
            vault = sandbox.credential_vault
            for attempt in range(2):
                try:
                    state = await vault.get()
                except SandboxException as exc:
                    if self._status_code(exc) != 404:
                        raise
                    try:
                        await vault.create(
                            credentials=[sdk_credential],
                            bindings=[sdk_binding],
                        )
                        return
                    except SandboxException as create_error:
                        if self._status_code(create_error) == 409 and attempt == 0:
                            continue
                        raise
                try:
                    await vault.patch(
                        expected_revision=state.revision,
                        credentials=CredentialMutationSet(replace=[sdk_credential]),
                        bindings=CredentialBindingMutationSet(replace=[sdk_binding]),
                    )
                    return
                except SandboxException as patch_error:
                    if self._status_code(patch_error) == 409 and attempt == 0:
                        continue
                    raise
        except SandboxException as exc:
            raise self._translate_credential_error(
                exc,
                sandbox_generation=handle.generation,
            ) from None

    @classmethod
    def _translate_credential_error(
        cls,
        error: BaseException,
        *,
        sandbox_generation: int,
    ) -> CredentialProxyUnavailable:
        logger.warning(
            "Credential Vault operation failed",
            extra={
                "status_code": cls._status_code(error),
                "sandbox_generation": sandbox_generation,
            },
        )
        return CredentialProxyUnavailable("Sandbox credential proxy is unavailable.")

    @staticmethod
    def _status_code(error: BaseException) -> int | None:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status_code = getattr(current, "status_code", None)
            if isinstance(status_code, int):
                return status_code
            current = current.__cause__ or current.__context__
        return None

    async def write_request(self, handle: SandboxHandle, request_bytes: bytes) -> str:
        path = "/session/control/request.json"
        try:
            sandbox = await self._connect(handle.sandbox_id)
            # OpenSandbox's wire model represents Unix modes as octal digits,
            # so 0600 must be passed as ``600`` rather than decimal ``384``.
            await sandbox.files.write_file(path, request_bytes, mode=600)
        except Exception as exc:
            raise self._translate_error(exc, "write_request") from exc
        return path

    async def sync_workspace(self, handle: SandboxHandle, source_dir: Path) -> None:
        root = source_dir.resolve(strict=True)
        if not root.is_dir() or source_dir.is_symlink():
            raise ValueError("Session workspace must be a real directory")
        try:
            sandbox = await self._connect(handle.sandbox_id)
            for source in sorted(root.rglob("*")):
                if source.is_symlink():
                    raise ValueError("Session workspace cannot contain symbolic links")
                if not source.is_file():
                    continue
                relative = source.relative_to(root).as_posix()
                await sandbox.files.write_file(
                    f"/session/workspace/{relative}",
                    source.read_bytes(),
                    mode=600,
                )
        except ValueError:
            raise
        except Exception as exc:
            raise self._translate_error(exc, "sync_workspace") from exc

    async def run_turn(self, handle: SandboxHandle, request_path: str) -> CommandHandle:
        if request_path != "/session/control/request.json":
            raise ValueError("Runner request path is fixed")
        try:
            sandbox = await self._connect(handle.sandbox_id)
            execution = await sandbox.commands.run(
                "python -m app.runner.main --request /session/control/request.json",
                opts=RunCommandOpts(
                    background=True,
                    working_directory="/session/workspace",
                    timeout=self._command_timeout,
                ),
            )
        except Exception as exc:
            raise self._translate_error(exc, "run") from exc
        if not execution.id:
            raise SandboxUnavailable("OpenSandbox did not return a command identity.")
        return CommandHandle(
            sandbox_id=handle.sandbox_id,
            command_session_id=execution.id,
            execution_id=execution.id,
        )

    async def read_frames(
        self, command: CommandHandle, cursor: str | None
    ) -> FrameBatch:
        try:
            numeric_cursor = int(cursor) if cursor is not None else None
        except ValueError as exc:
            raise ValueError("OpenSandbox log cursor must be numeric") from exc
        try:
            sandbox = await self._connect(command.sandbox_id)
            logs = await sandbox.commands.get_background_command_logs(
                command.execution_id, cursor=numeric_cursor
            )
            observation = await sandbox.commands.get_command_status(
                command.execution_id
            )
        except Exception as exc:
            raise self._translate_error(exc, "logs") from exc
        return FrameBatch(
            lines=tuple(logs.content.splitlines()),
            next_cursor=str(logs.cursor) if logs.cursor is not None else cursor,
            complete=observation.running is False,
        )

    async def inspect_command(self, command: CommandHandle) -> CommandObservation:
        try:
            sandbox = await self._connect(command.sandbox_id)
            status = await sandbox.commands.get_command_status(command.execution_id)
        except Exception as exc:
            raise self._translate_error(exc, "command_status") from exc
        if status.running is True:
            lifecycle = CommandLifecycle.RUNNING
        elif status.exit_code == 0:
            lifecycle = CommandLifecycle.SUCCEEDED
        elif status.exit_code is not None:
            lifecycle = CommandLifecycle.FAILED
        else:
            lifecycle = CommandLifecycle.UNKNOWN
        return CommandObservation(status=lifecycle, exit_code=status.exit_code)

    async def cancel_command(self, command: CommandHandle) -> CancelResult:
        try:
            sandbox = await self._connect(command.sandbox_id)
            await sandbox.commands.interrupt(command.execution_id)
        except Exception as exc:
            raise self._translate_error(exc, "cancel") from exc
        return CancelResult(accepted=True, terminal=False)

    async def renew_sandbox(self, sandbox_id: str, timeout_seconds: int) -> None:
        try:
            sandbox = await self._connect(sandbox_id)
            await sandbox.renew(timedelta(seconds=timeout_seconds))
        except Exception as exc:
            raise self._translate_error(exc, "renew") from exc

    async def destroy_sandbox(self, sandbox_id: str) -> None:
        try:
            sandbox = await self._connect(sandbox_id)
            await sandbox.kill()
        except Exception as exc:
            raise self._translate_error(exc, "destroy") from exc

    async def _connect(self, sandbox_id: str):
        return await Sandbox.connect(
            sandbox_id,
            connection_config=self._connection,
            connect_timeout=self._ready_timeout,
        )

    @staticmethod
    def _translate_error(exc: Exception, operation: str) -> Exception:
        name = type(exc).__name__.lower()
        if "credential" in name:
            return CredentialProxyUnavailable(
                f"OpenSandbox credential proxy failed during {operation}."
            )
        if "notfound" in name or "not_found" in name or "404" in name:
            if "command" in name:
                return CommandNotFound("OpenSandbox command was not found.")
            return SandboxNotFound("OpenSandbox sandbox was not found.")
        return SandboxUnavailable(f"OpenSandbox is unavailable during {operation}.")
