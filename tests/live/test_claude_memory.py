import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CLAUDE_TESTS") != "1",
    reason="Set RUN_LIVE_CLAUDE_TESTS=1 to call the configured Claude proxy.",
)


@pytest.mark.asyncio
async def test_real_claude_auto_memory_crosses_sessions(tmp_path) -> None:
    from app.attachments.service import AttachmentService
    from app.auth.models import IdentityContext
    from app.config import Settings
    from app.db.base import Database
    from app.memory.locks import MemoryScopeLockRegistry
    from app.memory.scopes import MemoryScopeService
    from app.runtime.claude import ClaudeAgentRuntime
    from app.sessions.locks import SessionLockRegistry
    from app.sessions.service import SessionService
    from app.turns.broker import EventBroker
    from app.turns.dispatcher import LocalInlineExecutionDispatcher
    from app.turns.service import TurnService
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.sync import WorkspaceSyncService

    workspaces_root = tmp_path / "workspaces"
    workspace_root = workspaces_root / "memory-test"
    workspace_root.mkdir(parents=True)
    (workspace_root / "workspace.yaml").write_text(
        """version: 1
id: memory-test
name: Memory Test
description: Isolated live Auto Memory acceptance test.
skills: []
allowed_tools: [Read, Write, Edit, Glob, Grep]
mcp_servers: {}
""",
        encoding="utf-8",
    )
    (workspace_root / "CLAUDE.md").write_text(
        "Use Auto Memory when the user explicitly asks you to save or recall a marker.\n",
        encoding="utf-8",
    )
    base_settings = Settings(workspaces_root=workspaces_root)
    settings = base_settings.model_copy(
        update={
            "app_data_dir": tmp_path / "data",
            "database_url": f"sqlite+aiosqlite:///{tmp_path / 'data/app.db'}",
            "turn_timeout_seconds": 180,
        }
    )
    registry_environ = dict(os.environ)
    if settings.claude_skills_root is not None:
        registry_environ["CLAUDE_SKILLS_ROOT"] = str(settings.claude_skills_root)
    registry = WorkspaceRegistry(
        settings.workspaces_root,
        settings.claude_model,
        registry_environ,
    )
    database = Database(settings.resolved_database_url)
    await database.initialize()
    entries = registry.scan()
    workspace = next(entry for entry in entries if entry.available)
    identity = IdentityContext("live-user", "live-user", "Live User")
    await WorkspaceSyncService(database, settings).sync(entries, identity)
    memory_scopes = MemoryScopeService(settings.app_data_dir)
    memory_scopes.initialize()
    memory_locks = MemoryScopeLockRegistry()
    sessions = SessionService(database, registry, settings.app_data_dir)
    attachments = AttachmentService(database, settings)
    turns = TurnService(
        database=database,
        sessions=sessions,
        attachments=attachments,
        runtime=ClaudeAgentRuntime(settings),
        locks=SessionLockRegistry(),
        memory_scopes=memory_scopes,
        memory_locks=memory_locks,
        broker=EventBroker(),
        timeout_seconds=180,
    )
    turns.bind_dispatcher(LocalInlineExecutionDispatcher(turns.execute_turn))

    try:
        marker = f"WORKSPACE-MEMORY-{uuid.uuid4().hex}"
        first_session = await sessions.create(workspace.id, identity)
        first_turn = await turns.start(
            first_session.id,
            f"请把精确标记 {marker} 写入 Auto Memory 的 MEMORY.md，"
            "完成后只回复 SAVED。",
            [],
            "memory-write",
        )
        await turns.wait(first_turn.id)
        assert (await turns.get(first_turn.id)).status == "completed"

        scope = memory_scopes.resolve(identity.user_id, workspace.id)
        memory_files = list(scope.directory.rglob("*.md"))
        assert memory_files
        assert any(marker in path.read_text(encoding="utf-8") for path in memory_files)

        second_session = await sessions.create(workspace.id, identity)
        second_turn = await turns.start(
            second_session.id,
            "读取 Auto Memory，只回复之前保存的 WORKSPACE-MEMORY 标记。",
            [],
            "memory-read",
        )
        await turns.wait(second_turn.id)
        assert (await turns.get(second_turn.id)).status == "completed"

        second_messages = await sessions.list_messages(second_session.id)
        assistant_payloads = [
            message.payload_json
            for message in second_messages
            if message.event_type == "message.assistant.completed"
        ]
        assert marker in "\n".join(assistant_payloads)
        assert (
            sessions.session_path(first_session) / "claude-config"
            != sessions.session_path(second_session) / "claude-config"
        )
        assert memory_scopes.resolve(
            first_session.created_by, first_session.workspace_id
        ) == memory_scopes.resolve(
            second_session.created_by, second_session.workspace_id
        )
    finally:
        await turns.shutdown()
        await database.dispose()
