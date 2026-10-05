import os
import struct
import zlib
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CLAUDE_TESTS") != "1",
    reason="Set RUN_LIVE_CLAUDE_TESTS=1 to call the configured Claude proxy.",
)


@pytest.mark.asyncio
async def test_real_claude_session_resume_and_image_input(tmp_path) -> None:
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
    workspace_root = workspaces_root / "smoke-test"
    workspace_root.mkdir(parents=True)
    (workspace_root / "workspace.yaml").write_text(
        """version: 1
id: smoke-test
name: Smoke Test
description: Isolated live Claude runtime smoke test.
skills: []
allowed_tools: [Read, Write, Edit, Glob, Grep]
mcp_servers: {}
""",
        encoding="utf-8",
    )
    (workspace_root / "CLAUDE.md").write_text(
        "Follow the user's instructions and reply briefly.\n",
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
        settings.workspaces_root, settings.claude_model, registry_environ
    )
    entries = registry.scan()
    workspace = next(entry for entry in entries if entry.available)
    database = Database(settings.resolved_database_url)
    await database.initialize()
    identity = IdentityContext("live-user", "live-user", "Live User")
    await WorkspaceSyncService(database, settings).sync(entries, identity)
    sessions = SessionService(database, registry, settings.app_data_dir)
    attachments = AttachmentService(database, settings)
    memory_scopes = MemoryScopeService(settings.app_data_dir)
    memory_scopes.initialize()
    turns = TurnService(
        database=database,
        sessions=sessions,
        attachments=attachments,
        runtime=ClaudeAgentRuntime(settings),
        locks=SessionLockRegistry(),
        memory_scopes=memory_scopes,
        memory_locks=MemoryScopeLockRegistry(),
        broker=EventBroker(),
        timeout_seconds=180,
    )
    turns.bind_dispatcher(LocalInlineExecutionDispatcher(turns.execute_turn))
    session = await sessions.create(workspace.id, identity)

    first = await turns.start(
        session.id,
        "Remember the marker WORKSPACE-MVP-731 and reply briefly.",
        [],
        "live-first",
    )
    await turns.wait(first.id)
    first_session = await sessions.get(session.id)
    assert (await turns.get(first.id)).status == "completed"
    assert first_session.claude_session_id

    second = await turns.start(
        session.id,
        "What marker did I ask you to remember?",
        [],
        "live-second",
    )
    await turns.wait(second.id)
    second_session = await sessions.get(session.id)
    assert (await turns.get(second.id)).status == "completed"
    assert second_session.claude_session_id == first_session.claude_session_id

    png = _png_image(16, 16)
    image = (
        await attachments.upload(
            session.id,
            [UploadFile(BytesIO(png), filename="pixel.png")],
        )
    )[0]
    third = await turns.start(
        session.id,
        "Acknowledge that an image was attached.",
        [image.id],
        "live-image",
    )
    await turns.wait(third.id)
    assert (await turns.get(third.id)).status == "completed"

    await turns.shutdown()
    await database.dispose()


def _png_image(width: int, height: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + (b"\x2f\x6f\x9f" * width) for _ in range(height))
    return (
        signature
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines))
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)
