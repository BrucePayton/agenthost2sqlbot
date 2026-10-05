from dataclasses import dataclass
from datetime import datetime
from typing import Literal

SkillScope = Literal["global", "workspace"]


@dataclass(frozen=True)
class SkillBundleFile:
    path: str
    content: bytes
    mime_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SkillBundle:
    name: str
    description: str
    content: str
    bundle_hash: str
    files: tuple[SkillBundleFile, ...]


@dataclass(frozen=True)
class SkillVersionRef:
    id: str
    version_no: int
    bundle_hash: str
    artifact_key: str
    artifact_sha256: str
    manifest_json: str
    size_bytes: int


@dataclass(frozen=True)
class ManagedSkillSummary:
    id: str
    scope: SkillScope
    workspace_id: str | None
    name: str
    description: str
    enabled: bool
    origin: dict[str, object]
    version: SkillVersionRef
    updated_at: datetime

    @property
    def bundle_hash(self) -> str:
        return self.version.bundle_hash


@dataclass(frozen=True)
class StoredSkill:
    summary: ManagedSkillSummary
    bundle: SkillBundle

    @property
    def id(self) -> str:
        return self.summary.id

    @property
    def scope(self) -> SkillScope:
        return self.summary.scope

    @property
    def workspace_id(self) -> str | None:
        return self.summary.workspace_id

    @property
    def name(self) -> str:
        return self.summary.name

    @property
    def description(self) -> str:
        return self.summary.description

    @property
    def content(self) -> str:
        return self.bundle.content

    @property
    def enabled(self) -> bool:
        return self.summary.enabled

    @property
    def bundle_hash(self) -> str:
        return self.summary.version.bundle_hash

    @property
    def origin(self) -> dict[str, object]:
        return self.summary.origin

    @property
    def files(self) -> tuple[SkillBundleFile, ...]:
        return self.bundle.files

    @property
    def updated_at(self) -> datetime:
        return self.summary.updated_at


@dataclass(frozen=True)
class SkillCatalog:
    global_skills: tuple[ManagedSkillSummary, ...]
    personal_skills: tuple[ManagedSkillSummary, ...]

    @property
    def effective_count(self) -> int:
        return sum(
            item.enabled for item in (*self.global_skills, *self.personal_skills)
        )


@dataclass(frozen=True)
class ResolvedSkillBundle:
    skill: ManagedSkillSummary
    bundle: SkillBundle
