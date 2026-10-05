import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from app.agui.bridge import FrontendToolBridgeRegistry
from app.agui.deferred_tools import DeferredFrontendToolStore
from app.agui.snapshot_artifacts import SnapshotArtifactStore
from app.agui.tool_ledger import ToolLedgerStore
from app.api.dependencies import AppServices
from app.attachments.service import AttachmentService
from app.auth.access import WorkspaceAccessService
from app.auth.davinci_passthrough import (
    DavinciObIdIdentityProvider,
    DavinciPassthroughIdentityProvider,
)
from app.auth.identity_repository import IdentityRepository
from app.auth.membership import MembershipProjectionService
from app.auth.obid import ObIdIdentityProvider
from app.auth.oidc import OidcIdentityProvider
from app.auth.provider import IdentityProvider, MockIdentityProvider
from app.auth.space_client import HttpSpaceMembershipAuthority
from app.config import Settings
from app.data_mcp.service import DataAgentService
from app.db.base import Database
from app.instance_lock import service_instance_lock
from app.instructions.service import InstructionService
from app.memory.locks import MemoryScopeLockRegistry
from app.memory.scopes import MemoryScopeService
from app.runtime.claude import ClaudeAgentRuntime
from app.runtime.contracts import AgentRuntimePort
from app.sandbox.heartbeat import (
    WorkerAvailabilityService,
    WorkerCompatibility,
    WorkerHeartbeatRepository,
)
from app.sessions.locks import SessionLockRegistry
from app.sessions.service import SessionService
from app.skills.access import PlatformSkillAccessService
from app.skills.artifacts import FilesystemSkillArtifactStore
from app.skills.migration import (
    LegacySkillArtifactMigrator,
    SkillArtifactGarbageCollector,
)
from app.skills.repository import SkillRepository
from app.skills.service import SkillBootstrapService, SkillService
from app.sqlbot.client import SQLBotClient
from app.turns.broker import EventBroker
from app.turns.dispatcher import (
    ExecutionDisabledDispatcher,
    LocalInlineExecutionDispatcher,
    OpenSandboxQueuedDispatcher,
)
from app.turns.notifications import InProcessTurnNotifier, PostgresTurnNotifier
from app.turns.repository import TurnRepository
from app.turns.service import TurnService
from app.turns.sse_store import TurnEventStream
from app.workspaces.provisioner import PersonalWorkspaceProvisioner
from app.workspaces.registry import WorkspaceRegistry
from app.workspaces.repository import WorkspaceRepository
from app.workspaces.resolver import WorkspaceTemplateResolver
from app.workspaces.sync import WorkspaceSyncService

logger = logging.getLogger(__name__)


def build_app_services(
    settings: Settings,
    runtime: AgentRuntimePort | None = None,
    identity_provider: IdentityProvider | None = None,
    frontend_tool_bridges: FrontendToolBridgeRegistry | None = None,
    davinci_auth_client: httpx.AsyncClient | None = None,
) -> AppServices:
    database = Database(settings.resolved_database_url)
    workspace_environ = dict(os.environ)
    if settings.claude_skills_root is not None:
        workspace_environ["CLAUDE_SKILLS_ROOT"] = str(settings.claude_skills_root)
    workspaces = WorkspaceRegistry(
        settings.workspaces_root,
        settings.claude_model,
        workspace_environ,
        allow_loopback_http_mcp=settings.app_env in {"development", "test"},
    )
    if identity_provider is not None:
        resolved_identity_provider = identity_provider
    elif settings.identity_mode == "oidc":
        resolved_identity_provider = OidcIdentityProvider(
            settings, IdentityRepository(database)
        )
    elif settings.identity_mode == "obid":
        if settings.davinci_local_integration and settings.davinci_api_base_url:
            client = davinci_auth_client or httpx.AsyncClient(
                base_url=str(settings.davinci_api_base_url),
                timeout=10.0,
                # The local Davinci gateway is a loopback hop. Inheriting
                # HTTP(S)_PROXY can send browser credentials to a desktop
                # proxy and turns a healthy local route into a 502.
                trust_env=False,
            )
            resolved_identity_provider = DavinciObIdIdentityProvider(
                IdentityRepository(database),
                client,
                session_ttl_seconds=settings.davinci_session_ttl_seconds,
                owns_client=davinci_auth_client is None,
            )
        else:
            resolved_identity_provider = ObIdIdentityProvider(
                IdentityRepository(database)
            )
    elif settings.identity_mode == "davinci_passthrough":
        client = davinci_auth_client or httpx.AsyncClient(
            base_url=str(settings.davinci_api_base_url),
            timeout=10.0,
        )
        resolved_identity_provider = DavinciPassthroughIdentityProvider(
            IdentityRepository(database),
            client,
            current_user_path=settings.davinci_current_user_path,
            session_ttl_seconds=settings.davinci_session_ttl_seconds,
            owns_client=davinci_auth_client is None,
        )
    else:
        resolved_identity_provider = MockIdentityProvider(
            settings.mock_user_id,
            settings.mock_user_subject,
            settings.mock_user_display_name,
        )
    skill_limits = settings.skill_bundle_limits
    workspace_sync = WorkspaceSyncService(database, settings)
    workspace_repository = WorkspaceRepository(database)
    workspace_provisioner = PersonalWorkspaceProvisioner(
        database, settings.personal_workspace_template_id
    )
    workspace_templates = WorkspaceTemplateResolver(workspaces, skill_limits)
    membership_projections = None
    if (
        settings.identity_mode == "oidc"
        and settings.space_authority_url is not None
        and settings.space_authority_token is not None
    ):
        membership_projections = MembershipProjectionService(
            database,
            HttpSpaceMembershipAuthority(settings),
            ttl_seconds=settings.membership_projection_ttl_seconds,
        )
    workspace_access = WorkspaceAccessService(database, membership_projections)
    instructions = InstructionService(
        database,
        workspace_templates,
        max_bytes=settings.max_instructions_bytes,
    )
    lifecycle_locks = SessionLockRegistry()
    skill_artifacts = FilesystemSkillArtifactStore(
        settings.resolved_skill_artifact_root
    )
    skill_repository = SkillRepository(database, skill_artifacts, skill_limits)
    platform_skill_access = PlatformSkillAccessService(database)
    skills = SkillService(
        skill_repository,
        workspace_access,
        platform_skill_access,
        skill_limits,
    )
    skill_artifact_migrator = LegacySkillArtifactMigrator(
        database, skill_artifacts, skill_limits
    )
    skill_artifact_garbage_collector = SkillArtifactGarbageCollector(
        database, skill_artifacts
    )
    sessions = SessionService(
        database,
        workspaces,
        settings.app_data_dir,
        skills=skills,
        locks=lifecycle_locks,
        workspace_repository=workspace_repository,
        workspace_templates=workspace_templates,
        instructions=instructions,
    )
    attachments = AttachmentService(database, settings, locks=lifecycle_locks)
    skill_bootstrap = SkillBootstrapService(database, skills)
    memory_scopes = MemoryScopeService(settings.app_data_dir)
    memory_locks = MemoryScopeLockRegistry()
    resolved_frontend_tool_bridges = (
        frontend_tool_bridges or FrontendToolBridgeRegistry()
    )
    deferred_frontend_tools = DeferredFrontendToolStore()
    tool_ledger = ToolLedgerStore()
    snapshot_artifacts = SnapshotArtifactStore()
    data_agents = DataAgentService(database, settings, SQLBotClient(settings))
    resolved_runtime = runtime
    if resolved_runtime is None and settings.app_runtime_mode == "local_inline":
        resolved_runtime = ClaudeAgentRuntime(
            settings,
            frontend_tool_bridges=resolved_frontend_tool_bridges,
            deferred_frontend_tools=deferred_frontend_tools,
            tool_ledger=tool_ledger,
            snapshot_artifacts=snapshot_artifacts,
            data_agent_service=data_agents,
            mcp_credential_provider=(
                resolved_identity_provider
                if isinstance(
                    resolved_identity_provider,
                    (
                        DavinciObIdIdentityProvider,
                        DavinciPassthroughIdentityProvider,
                    ),
                )
                else None
            ),
        )
    broker = EventBroker()
    if database.engine.dialect.name == "postgresql":
        notifier = PostgresTurnNotifier(settings.resolved_database_url)
    else:
        notifier = InProcessTurnNotifier(broker)
    turn_repository = TurnRepository(database)
    deferred_frontend_tools.bind_history_loader(turn_repository.frontend_tool_recovery)
    execution_availability = None
    if settings.app_runtime_mode == "opensandbox_docker":
        execution_availability = WorkerAvailabilityService(
            WorkerHeartbeatRepository(database),
            WorkerCompatibility(
                runtime_cohort=settings.app_runtime_cohort,
                protocol_version=settings.app_runtime_protocol_version,
                runner_runtime=settings.opensandbox_runner_runtime,
                image_digest=settings.app_runtime_image_digest,
            ),
            stale_seconds=settings.worker_heartbeat_stale_seconds,
        )
    turns = TurnService(
        database=database,
        sessions=sessions,
        attachments=attachments,
        runtime=resolved_runtime,
        locks=lifecycle_locks,
        memory_scopes=memory_scopes,
        memory_locks=memory_locks,
        broker=broker,
        timeout_seconds=settings.turn_timeout_seconds,
        repository=turn_repository,
        notifier=notifier,
        execution_availability=execution_availability,
    )
    if settings.app_runtime_mode == "execution_disabled":
        dispatcher = ExecutionDisabledDispatcher()
    elif settings.app_runtime_mode == "opensandbox_docker":
        dispatcher = OpenSandboxQueuedDispatcher(
            turn_repository,
            poll_seconds=settings.opensandbox_worker_poll_seconds,
        )
    else:
        dispatcher = LocalInlineExecutionDispatcher(turns.execute_turn)
    turns.bind_dispatcher(dispatcher)
    return AppServices(
        settings=settings,
        database=database,
        data_agents=data_agents,
        workspaces=workspaces,
        workspace_sync=workspace_sync,
        workspace_repository=workspace_repository,
        workspace_provisioner=workspace_provisioner,
        workspace_templates=workspace_templates,
        workspace_access=workspace_access,
        instructions=instructions,
        identity_provider=resolved_identity_provider,
        sessions=sessions,
        attachments=attachments,
        skill_artifacts=skill_artifacts,
        platform_skill_access=platform_skill_access,
        skills=skills,
        skill_artifact_migrator=skill_artifact_migrator,
        skill_artifact_garbage_collector=skill_artifact_garbage_collector,
        skill_bootstrap=skill_bootstrap,
        memory_scopes=memory_scopes,
        runtime=resolved_runtime,
        execution_availability=execution_availability,
        broker=broker,
        dispatcher=dispatcher,
        notifier=notifier,
        event_stream=TurnEventStream(turn_repository, notifier),
        turns=turns,
        frontend_tool_bridges=resolved_frontend_tool_bridges,
        deferred_frontend_tools=deferred_frontend_tools,
        snapshot_artifacts=snapshot_artifacts,
    )


@asynccontextmanager
async def initialize_app_services(services: AppServices) -> AsyncIterator[None]:
    settings = services.settings
    if getattr(settings, "security_marker", None) is not None:
        logger.warning(
            settings.security_marker,
            extra={
                "identity_mode": settings.identity_mode,
                "deployment_constraint": "single_instance",
            },
        )
    settings.app_data_dir.mkdir(parents=True, exist_ok=True)
    with service_instance_lock(settings.app_data_dir):
        try:
            services.memory_scopes.initialize()
            await asyncio.to_thread(services.skill_artifacts.initialize)
            await services.database.initialize()
            await services.skill_artifact_migrator.run()
            await services.skill_artifact_garbage_collector.run(datetime.now(UTC))
            entries = services.workspaces.scan()
            bootstrap_identity = (
                await services.identity_provider.resolve_bootstrap_identity()
            )
            if bootstrap_identity is not None:
                await services.workspace_sync.sync(entries, bootstrap_identity)
                await services.platform_skill_access.bootstrap_subjects(
                    bootstrap_identity, settings.skill_admin_subjects
                )
                await services.sessions.claim_legacy_sessions(
                    bootstrap_identity.user_id
                )
            report = await services.skill_bootstrap.run(
                entries,
                bootstrap_identity,
                settings.mock_personal_workspace_id,
                settings.claude_skills_root,
            )
            logger.info(
                "Skill bootstrap completed",
                extra={"result": report.summary()},
            )
            if getattr(settings, "app_runtime_mode", "local_inline") == "local_inline":
                await services.database.interrupt_stale_turns()
            await services.attachments.cleanup_pending()
            yield
        finally:
            await services.turns.shutdown()
            data_agents = getattr(services, "data_agents", None)
            if data_agents is not None:
                await data_agents.aclose()
            await services.frontend_tool_bridges.shutdown()
            close_identity_provider = getattr(
                services.identity_provider, "aclose", None
            )
            if close_identity_provider is not None:
                await close_identity_provider()
            await services.database.dispose()
