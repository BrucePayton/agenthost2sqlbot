from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.sandbox.credentials import ModelCredential
from app.sandbox.models import (
    CancelResult,
    CommandHandle,
    CommandObservation,
    FrameBatch,
    SandboxHandle,
    SandboxObservation,
    SessionSandboxSpec,
)


class SandboxError(RuntimeError):
    code = "sandbox_error"


class SandboxUnavailable(SandboxError):
    code = "sandbox_unavailable"


class SandboxNotFound(SandboxError):
    code = "sandbox_not_found"


class CommandNotFound(SandboxError):
    code = "command_not_found"


class CredentialProxyUnavailable(SandboxError):
    code = "credential_proxy_unavailable"


class SandboxPort(Protocol):
    async def create_session_sandbox(
        self, spec: SessionSandboxSpec
    ) -> SandboxHandle: ...

    async def inspect_sandbox(self, sandbox_id: str) -> SandboxObservation: ...

    async def ensure_model_credential(
        self,
        handle: SandboxHandle,
        credential: ModelCredential,
    ) -> None: ...

    async def sync_workspace(self, handle: SandboxHandle, source_dir: Path) -> None: ...

    async def write_request(
        self, handle: SandboxHandle, request_bytes: bytes
    ) -> str: ...

    async def write_data_response(self, sandbox_id: str, request_id: str, payload: bytes) -> None: ...

    async def run_turn(
        self, handle: SandboxHandle, request_path: str
    ) -> CommandHandle: ...

    async def read_frames(
        self, command: CommandHandle, cursor: str | None
    ) -> FrameBatch: ...

    async def inspect_command(self, command: CommandHandle) -> CommandObservation: ...

    async def cancel_command(self, command: CommandHandle) -> CancelResult: ...

    async def renew_sandbox(self, sandbox_id: str, timeout_seconds: int) -> None: ...

    async def destroy_sandbox(self, sandbox_id: str) -> None: ...
