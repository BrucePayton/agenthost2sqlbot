import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ENV_NAME_PATTERN = r"^[A-Z_][A-Z0-9_]*$"


class McpHttpServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http"]
    url_env: str = Field(pattern=ENV_NAME_PATTERN)
    authorization_env: str | None = Field(
        default=None, pattern=ENV_NAME_PATTERN
    )
    authorization_source: Literal["davinci_session"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_authorization(self) -> "McpHttpServerManifest":
        if self.authorization_env and self.authorization_source:
            raise ValueError(
                "authorization_env and authorization_source are mutually exclusive"
            )
        return self


class McpSseServerManifest(BaseModel):
    """Legacy MCP HTTP+SSE transport used by SQLBot's FastAPI MCP server."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sse"]
    url_env: str = Field(pattern=ENV_NAME_PATTERN)
    authorization_env: str | None = Field(
        default=None, pattern=ENV_NAME_PATTERN
    )


class McpStdioServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"]
    command: str = Field(min_length=1)
    entrypoint_env: str = Field(pattern=ENV_NAME_PATTERN)
    args: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        command = value.strip()
        if not command:
            raise ValueError("command cannot be blank")
        path = Path(command)
        if not path.is_absolute() and ("/" in command or "\\" in command):
            raise ValueError("command must be an executable name or absolute path")
        return command

    @field_validator("args")
    @classmethod
    def validate_args(cls, value: list[str]) -> list[str]:
        normalized = [argument.strip() for argument in value]
        if any(not argument for argument in normalized):
            raise ValueError("args cannot contain blank items")
        return normalized

    @field_validator("env_vars")
    @classmethod
    def validate_env_vars(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("env_vars must be unique")
        if any(not re.fullmatch(ENV_NAME_PATTERN, name) for name in value):
            raise ValueError("env_vars must contain environment variable names")
        return value


McpServerManifest = Annotated[
    McpHttpServerManifest | McpSseServerManifest | McpStdioServerManifest,
    Field(discriminator="type"),
]


class WorkspaceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    model: str | None = None
    skills_root_env: str | None = Field(
        default=None, pattern=ENV_NAME_PATTERN
    )
    skills: list[str]
    allowed_tools: list[str]
    mcp_servers: dict[str, McpServerManifest]

    @field_validator("name", "description", "model")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("value cannot be blank")
        return stripped

    @field_validator("skills", "allowed_tools")
    @classmethod
    def unique_nonempty_items(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("items cannot be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("items must be unique")
        return normalized

    @field_validator("skills")
    @classmethod
    def validate_skill_names(cls, value: list[str]) -> list[str]:
        if any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item)
            for item in value
        ):
            raise ValueError("each Skill must use a simple directory name")
        return value


@dataclass(frozen=True)
class WorkspaceEntry:
    id: str
    directory: Path
    available: bool
    name: str
    description: str
    manifest: WorkspaceManifest | None
    validation_errors: tuple[str, ...]
    snapshot_json: str | None = None
    snapshot_hash: str | None = None
    skills_source_root: Path | None = None
    link_skills: bool = False
