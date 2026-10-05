from dataclasses import dataclass
from enum import StrEnum


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True)
class IdentityContext:
    user_id: str
    external_subject: str
    display_name: str
    issuer: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class WorkspaceMembership:
    workspace_id: str
    user_id: str
    role: WorkspaceRole
