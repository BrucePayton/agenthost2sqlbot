from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillEnabledIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    enabled: bool


class SkillArchive(BaseModel):
    expected_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class GlobalSkillSettingIn(BaseModel):
    enabled: bool


class SkillFileOut(BaseModel):
    path: str
    mime_type: str
    size_bytes: int
    sha256: str


class SkillOut(BaseModel):
    id: str
    scope: Literal["global", "workspace"]
    workspace_id: str | None
    name: str
    description: str
    enabled: bool
    version_id: str
    version_no: int
    bundle_hash: str
    origin: dict[str, object]
    updated_at: datetime


class SkillCatalogOut(BaseModel):
    global_: list[SkillOut] = Field(serialization_alias="global")
    personal: list[SkillOut]
    effective_count: int
    changes_apply_to: Literal["new_sessions"] = "new_sessions"


class SkillImportOut(BaseModel):
    status: Literal["created", "overwritten", "renamed", "already_imported"]
    skill: SkillOut


class SkillDetailOut(SkillOut):
    content: str
    files: list[SkillFileOut]
