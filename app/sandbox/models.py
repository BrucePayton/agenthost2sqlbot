from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

SESSION_MOUNT = "/session"
MEMORY_MOUNT = "/memory"
WORKSPACE_PATH = "/session/workspace"
CONTROL_PATH = "/session/control"
MODEL_API_KEY_PLACEHOLDER = "opensandbox-vault-placeholder"

_IMAGE_DIGEST_RE = re.compile(r"^(?:[^\s@]+@)?sha256:[0-9a-f]{64}$")
_DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _opaque_name(prefix: str, *parts: str) -> str:
    if any(not part.strip() for part in parts):
        raise ValueError("volume identity parts cannot be blank")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:40]
    return f"wa-{prefix}-{digest}"


def session_volume_name(session_id: str) -> str:
    return _opaque_name("session", session_id)


def memory_volume_name(user_id: str, workspace_id: str) -> str:
    return _opaque_name("memory", user_id, workspace_id)


def validate_allowed_host(host: str) -> str:
    normalized = host.strip().lower().rstrip(".")
    if not normalized or not _DNS_HOST_RE.fullmatch(normalized):
        raise ValueError(
            "allowed host must be a DNS hostname without scheme, port, or path"
        )
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise ValueError("allowed host must not be an IP address")
    if normalized == "metadata.google.internal" or normalized.endswith(".internal"):
        raise ValueError("allowed host must not target a private metadata domain")
    return normalized


class SandboxLifecycle(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    BUSY = "busy"
    IDLE = "idle"
    REAPING = "reaping"
    TERMINATED = "terminated"
    RECOVERY_REQUIRED = "recovery_required"


class CommandLifecycle(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunnerModelConfig:
    base_url: str
    api_key_placeholder: str = MODEL_API_KEY_PLACEHOLDER

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("runner model base URL cannot be blank")
        if self.api_key_placeholder != MODEL_API_KEY_PLACEHOLDER:
            raise ValueError("runner model API key must use the fixed placeholder")


@dataclass(frozen=True)
class SessionSandboxSpec:
    session_key: str
    memory_scope_key: str
    generation: int
    runner_image: str
    allowed_hosts: tuple[str, ...]
    credential_proxy_required: bool
    timeout_seconds: int
    model_config: RunnerModelConfig | None = None
    session_mount: str = SESSION_MOUNT
    memory_mount: str = MEMORY_MOUNT

    def __post_init__(self) -> None:
        if not self.session_key.strip() or not self.memory_scope_key.strip():
            raise ValueError("sandbox scope keys cannot be blank")
        if self.generation < 1:
            raise ValueError("sandbox generation must be positive")
        if _IMAGE_DIGEST_RE.fullmatch(self.runner_image.strip()) is None:
            raise ValueError("runner image must use an immutable sha256 digest")
        if not self.credential_proxy_required and self.allowed_hosts:
            raise ValueError("sandbox without credential proxy cannot allow hosts")
        if self.credential_proxy_required and not self.allowed_hosts:
            raise ValueError("at least one allowed host is required")
        if self.credential_proxy_required and self.model_config is None:
            raise ValueError("credential proxy requires a runner model config")
        if not self.credential_proxy_required and self.model_config is not None:
            raise ValueError("runner model config requires the credential proxy")
        normalized = tuple(validate_allowed_host(host) for host in self.allowed_hosts)
        object.__setattr__(self, "allowed_hosts", normalized)
        if self.timeout_seconds < 1:
            raise ValueError("sandbox timeout must be positive")
        if self.session_mount != SESSION_MOUNT or self.memory_mount != MEMORY_MOUNT:
            raise ValueError("sandbox mount paths are fixed")


@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str = field(repr=False)
    generation: int
    created_at: datetime
    image_reference: str
    session_volume_name: str = field(repr=False)
    memory_volume_name: str = field(repr=False)


@dataclass(frozen=True)
class SandboxObservation:
    status: SandboxLifecycle
    observed_at: datetime
    detail_code: str | None = None


@dataclass(frozen=True)
class CommandHandle:
    sandbox_id: str = field(repr=False)
    command_session_id: str = field(repr=False)
    execution_id: str = field(repr=False)


@dataclass(frozen=True)
class CommandObservation:
    status: CommandLifecycle
    exit_code: int | None = None


@dataclass(frozen=True)
class FrameBatch:
    lines: tuple[str, ...]
    next_cursor: str | None
    complete: bool


@dataclass(frozen=True)
class CancelResult:
    accepted: bool
    terminal: bool
