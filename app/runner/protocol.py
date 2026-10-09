from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.runtime.contracts import (
    RuntimeAttachment,
    RuntimeContextItem,
    RuntimeFrontendTool,
    RuntimeRequest,
    RuntimeToolResult,
)

PROTOCOL_VERSION = "1"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 1024 * 1024


class RunnerAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if (
            not value
            or candidate.is_absolute()
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise ValueError("attachment must use a safe relative path")
        return candidate.as_posix()


class RunnerFrontendTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    description: str = Field(max_length=4096)
    parameters: dict[str, Any]


class RunnerToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=1024 * 1024)
    is_error: bool = False
    frontend_round_trip_ms: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    origin: Literal["model", "program"] = "model"


class RunnerContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # 名字与长度的取舍由 build_user_message 统一裁决（超限丢弃而不是让整个 Run 失败），
    # 这里只做传输层的粗粒度上限。
    description: str = Field(max_length=4096)
    value: str = Field(max_length=1024 * 1024)


class RunnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"]
    runtime_kind: Literal["claude", "fake"] = "claude"
    user_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    platform_session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=1)
    claude_session_id: str | None = Field(default=None, max_length=256)
    memory_scope_key: str = Field(min_length=1, max_length=128)
    text: str
    attachments: tuple[RunnerAttachment, ...] = ()
    file_references: tuple[str, ...] = ()
    workspace_snapshot: dict[str, Any]
    frontend_tools: tuple[RunnerFrontendTool, ...] = ()
    page_state: dict[str, Any] = Field(default_factory=dict)
    tool_results: tuple[RunnerToolResult, ...] = ()
    context_items: tuple[RunnerContextItem, ...] = ()
    data_backend: Literal["sqlbot", "mcp"] = Field(default="sqlbot", exclude_if=lambda value: value == "sqlbot")
    data_mcp_tools: list[dict[str, Any]] = Field(default_factory=list, exclude_if=lambda value: not value)
    # Server-owned checkpoints include real SDK receipts and program state.
    subscription_task: dict[str, Any] = Field(default_factory=dict, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def validate_serialized_size(self) -> RunnerRequest:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Runner request exceeds the maximum size")
        return self

    def to_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def to_runtime_request(self) -> RuntimeRequest:
        workspace = Path("/session/workspace")
        return RuntimeRequest(
            platform_session_id=self.platform_session_id,
            claude_session_id=self.claude_session_id,
            cwd=workspace,
            claude_config_dir=Path("/session/claude-config"),
            memory_scope_key=self.memory_scope_key,
            memory_dir=Path("/memory"),
            text=self.text,
            attachments=tuple(
                RuntimeAttachment(
                    id=item.id,
                    original_filename=item.original_filename,
                    mime_type=item.mime_type,
                    path=workspace / item.relative_path,
                )
                for item in self.attachments
            ),
            file_references=tuple(self.file_references),
            workspace_snapshot=self.workspace_snapshot,
            frontend_tools=tuple(
                RuntimeFrontendTool(
                    name=tool.name,
                    description=tool.description,
                    parameters=dict(tool.parameters),
                )
                for tool in self.frontend_tools
            ),
            page_state=dict(self.page_state),
            tool_results=tuple(
                RuntimeToolResult(
                    tool_call_id=result.tool_call_id,
                    content=result.content,
                    is_error=result.is_error,
                    frontend_round_trip_ms=result.frontend_round_trip_ms,
                    origin=result.origin,
                )
                for result in self.tool_results
            ),
            context_items=tuple(
                RuntimeContextItem(
                    description=item.description,
                    value=item.value,
                )
                for item in self.context_items
            ),
            run_id=self.turn_id,
            metadata={"subscription_task": dict(self.subscription_task), "data_backend": self.data_backend,
                      "data_mcp_tools": self.data_mcp_tools},
        )


RunnerFrameKind = Literal[
    "phase",
    "assistant_delta",
    "tool_event",
    "artifact",
    "heartbeat",
    "usage",
    "terminal",
    "error",
]


class RunnerFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1"] = PROTOCOL_VERSION
    sequence: int = Field(ge=1)
    kind: RunnerFrameKind
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any]
    role: str | None = Field(default=None, max_length=32)

    def to_line(self) -> str:
        line = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(line.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ValueError("Runner frame exceeds the maximum size")
        return line


class RunnerFrameValidator:
    def __init__(self) -> None:
        self._last_sequence = 0
        self._terminal_seen = False

    def accept(self, frame: RunnerFrame) -> None:
        if frame.sequence != self._last_sequence + 1:
            raise ValueError("Runner frame sequence is not monotonic")
        if self._terminal_seen:
            raise ValueError("Runner frame appeared after terminal frame")
        if frame.kind == "terminal":
            self._terminal_seen = True
        self._last_sequence = frame.sequence
