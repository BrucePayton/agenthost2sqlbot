from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MeOut(BaseModel):
    user_id: str
    external_subject: str
    display_name: str


PanelSize = Literal["small", "medium", "large", "fullscreen"]


class LauncherPositionIn(BaseModel):
    """Launcher button centre as a fraction of the viewport, so it ports across screens."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class PreferencesIn(BaseModel):
    """Partial update: only the keys present are changed; an explicit null clears a key."""

    model_config = ConfigDict(extra="forbid")

    panelSize: PanelSize | None = None
    launcher: LauncherPositionIn | None = None


class PreferencesOut(BaseModel):
    panelSize: PanelSize | None = None
    launcher: LauncherPositionIn | None = None


class WorkspaceOut(BaseModel):
    id: str
    name: str
    description: str
    available: bool
    model: str | None
    skill_count: int
    mcp_server_count: int
    validation_errors: list[str]
    kind: str
    role: str
    can_manage_skills: bool
    can_manage_global_skills: bool
    personal_memory_enabled: bool


class InstructionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    expected_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class InstructionsReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class InstructionsDefaultOut(BaseModel):
    """没有用户覆盖时实际会用到的那一份（seed > 模板 > 内置兜底）。"""

    content: str
    source: Literal["custom", "seed", "template", "builtin"]
    content_hash: str
    size_bytes: int


class InstructionsOut(InstructionsDefaultOut):
    """当前实际生效的那一份；审计两列只在 `source == "custom"` 时有值。"""

    max_bytes: int
    updated_at: datetime | None = None
    updated_by_name: str | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    claude_session_id: str | None
    title: str
    title_source: str
    status: str
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamp_with_timezone(cls, value: datetime) -> datetime:
        """SQLite drops tzinfo from UTC storage; include it on the API boundary."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class SessionContextOut(BaseModel):
    session_id: str
    workspace_id: str
    claude_session_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    workspace_snapshot_hash: str
    workspace_snapshot: dict[str, Any]


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class SessionSkillOut(BaseModel):
    name: str
    description: str


class SessionSkillsOut(BaseModel):
    items: list[SessionSkillOut]


class SessionFileOut(BaseModel):
    path: str
    name: str
    size_bytes: int


class SessionFilesOut(BaseModel):
    items: list[SessionFileOut]
    truncated: bool


class AttachmentOut(BaseModel):
    id: str
    session_id: str
    turn_id: str | None
    status: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    content_url: str
    created_at: datetime


class TurnCreate(BaseModel):
    message: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    file_references: list[str] = Field(default_factory=list, max_length=20)
    client_request_id: str = Field(min_length=1, max_length=64)


class TurnAccepted(BaseModel):
    turn_id: str
    status: str
    events_url: str


class TurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    status: str
    error_code: str | None
    error_message: str | None
    input_tokens: int | None
    uncached_input_tokens: int | None
    cache_read_input_tokens: int | None
    cache_creation_input_tokens: int | None
    total_input_tokens: int | None
    output_tokens: int | None
    model_api_turns: int | None
    frontend_tool_calls: int | None
    tool_search_calls: int | None
    tool_set_changes: int | None
    catalog_digest_changes: int | None
    cost_usd: Decimal | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class MessageEventOut(BaseModel):
    id: str
    session_id: str
    turn_id: str
    sequence: int
    event_type: str
    role: str | None
    payload: dict[str, Any]
    created_at: datetime


class MessageFeedbackIn(BaseModel):
    """Validate optional details for the selected vote direction."""
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"] | None
    reasons: list[str] = Field(default_factory=list, max_length=20)
    comment: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_reasons(self) -> "MessageFeedbackIn":
        """Reject mismatched details and preserve first-seen reason order."""
        allowed = {
            "up": {"accurate", "understood", "completed", "efficient", "clear"},
            "down": {"inaccurate", "misunderstood", "incomplete", "slow", "poorPresentation", "tooManySteps"},
            None: set(),
        }
        if any(reason not in allowed[self.rating] for reason in self.reasons):
            raise ValueError("Reasons must match the selected rating.")
        if self.rating is None and (self.reasons or self.comment):
            raise ValueError("Cancelling feedback requires empty reasons and comment.")
        self.reasons = list(dict.fromkeys(self.reasons))
        return self
