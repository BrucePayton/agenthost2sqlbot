import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RuntimeAttachment:
    id: str
    original_filename: str
    mime_type: str
    path: Path


@dataclass(frozen=True, slots=True)
class RuntimeFrontendTool:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False
    frontend_round_trip_ms: int | None = None
    origin: Literal["model", "program"] = "model"


@dataclass(frozen=True, slots=True)
class RuntimeContextItem:
    description: str
    value: str


@dataclass(slots=True)
class RuntimeRequest:
    platform_session_id: str
    claude_session_id: str | None
    cwd: Path
    claude_config_dir: Path
    memory_scope_key: str
    memory_dir: Path
    text: str
    attachments: tuple[RuntimeAttachment, ...]
    file_references: tuple[str, ...]
    workspace_snapshot: dict[str, Any]
    frontend_tools: tuple[RuntimeFrontendTool, ...] = ()
    page_state: dict[str, Any] = field(default_factory=dict)
    tool_results: tuple[RuntimeToolResult, ...] = ()
    context_items: tuple[RuntimeContextItem, ...] = ()
    run_id: str | None = None
    model: str | None = None
    effort: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: str
    payload: dict[str, Any]
    role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": self.payload, "role": self.role}


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    status: Literal["completed", "cancelled", "failed"]
    claude_session_id: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_event(self) -> RuntimeEvent:
        payload: dict[str, Any] = {"status": self.status}
        if self.claude_session_id is not None:
            payload["claude_session_id"] = self.claude_session_id
        if self.duration_ms is not None:
            payload["duration_ms"] = self.duration_ms
        payload.update(self.metadata)
        return RuntimeEvent(type="runtime.result", payload=payload)


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    protocol_version: str
    supports_resume: bool
    supports_interrupt: bool
    supports_auto_memory: bool
    supports_mcp: bool
    supports_skills: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeCancelled(Exception):
    pass


@runtime_checkable
class AgentRuntimePort(Protocol):
    @property
    def capabilities(self) -> RuntimeCapabilities: ...

    def run(
        self,
        request: RuntimeRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[RuntimeEvent]: ...
