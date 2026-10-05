from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.agui.bridge import FrontendToolBridgeRegistry
from app.agui.deferred_tools import DeferredFrontendToolStore
from app.agui.snapshot_artifacts import SnapshotArtifactStore
from app.attachments.service import AttachmentService
from app.auth.access import WorkspaceAccessService
from app.auth.models import IdentityContext
from app.auth.provider import IdentityProvider
from app.config import Settings
from app.data_mcp.service import DataAgentService
from app.db.base import Database
from app.errors import AppError
from app.instructions.service import InstructionService
from app.memory.scopes import MemoryScopeService
from app.runtime.contracts import AgentRuntimePort
from app.sandbox.heartbeat import WorkerAvailabilityService
from app.sessions.service import SessionService
from app.skills.access import PlatformSkillAccessService
from app.skills.artifacts import SkillArtifactStore
from app.skills.migration import (
    LegacySkillArtifactMigrator,
    SkillArtifactGarbageCollector,
)
from app.skills.service import SkillBootstrapService, SkillService
from app.turns.broker import EventBroker
from app.turns.dispatcher import ExecutionDispatcher
from app.turns.notifications import TurnNotifier
from app.turns.service import TurnService
from app.turns.sse_store import TurnEventStream
from app.workspaces.provisioner import PersonalWorkspaceProvisioner
from app.workspaces.registry import WorkspaceRegistry
from app.workspaces.repository import WorkspaceRepository
from app.workspaces.resolver import WorkspaceTemplateResolver
from app.workspaces.sync import WorkspaceSyncService


@dataclass
class AppServices:
    settings: Settings
    database: Database
    data_agents: DataAgentService
    workspaces: WorkspaceRegistry
    workspace_sync: WorkspaceSyncService
    workspace_repository: WorkspaceRepository
    workspace_provisioner: PersonalWorkspaceProvisioner
    workspace_templates: WorkspaceTemplateResolver
    workspace_access: WorkspaceAccessService
    instructions: InstructionService
    identity_provider: IdentityProvider
    sessions: SessionService
    attachments: AttachmentService
    skill_artifacts: SkillArtifactStore
    platform_skill_access: PlatformSkillAccessService
    skills: SkillService
    skill_artifact_migrator: LegacySkillArtifactMigrator
    skill_artifact_garbage_collector: SkillArtifactGarbageCollector
    skill_bootstrap: SkillBootstrapService
    memory_scopes: MemoryScopeService
    runtime: AgentRuntimePort | None
    execution_availability: WorkerAvailabilityService | None
    broker: EventBroker
    dispatcher: ExecutionDispatcher
    notifier: TurnNotifier
    event_stream: TurnEventStream
    turns: TurnService
    frontend_tool_bridges: FrontendToolBridgeRegistry
    deferred_frontend_tools: DeferredFrontendToolStore
    snapshot_artifacts: SnapshotArtifactStore


def get_services(request: Request) -> AppServices:
    return request.app.state.services


async def get_identity(request: Request) -> IdentityContext:
    try:
        return await get_services(request).identity_provider.resolve(request)
    except LookupError as exc:
        raise AppError("identity_missing", "Identity is unavailable.", 401) from exc


Identity = Annotated[IdentityContext, Depends(get_identity)]
