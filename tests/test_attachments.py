import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO

import pytest
from sqlalchemy import func, select
from starlette.datastructures import Headers, UploadFile

from tests.conftest import synchronize_workspaces
from tests.test_workspaces import write_workspace


def upload(name: str, content: bytes, content_type: str = "application/octet-stream"):
    return UploadFile(
        BytesIO(content),
        filename=name,
        headers=Headers({"content-type": content_type}),
    )


async def build_services(settings_factory):
    from app.attachments.service import AttachmentService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.sessions.service import SessionService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    session_service = SessionService(database, registry, settings.app_data_dir)
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    session = await session_service.create("actual", identity)
    attachment_service = AttachmentService(database, settings)
    return settings, database, session, attachment_service


class GatedSessionLockRegistry:
    def __init__(self, held_task_name: str) -> None:
        from app.sessions.locks import SessionLockRegistry

        self._registry = SessionLockRegistry()
        self.held_task_name = held_task_name
        self.held_acquired = asyncio.Event()
        self.release_held = asyncio.Event()
        self._attempted: dict[str, asyncio.Event] = {}

    async def wait_for_attempt(self, task_name: str) -> None:
        event = self._attempted.setdefault(task_name, asyncio.Event())
        await asyncio.wait_for(event.wait(), timeout=2)

    @asynccontextmanager
    async def acquire(self, session_id: str) -> AsyncIterator[None]:
        task_name = asyncio.current_task().get_name()
        self._attempted.setdefault(task_name, asyncio.Event()).set()
        async with self._registry.acquire(session_id):
            if task_name == self.held_task_name:
                self.held_acquired.set()
                await self.release_held.wait()
            yield


async def build_lifecycle_services(settings_factory, locks):
    from app.attachments.service import AttachmentService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.sessions.service import SessionService
    from app.workspaces.registry import WorkspaceRegistry

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        locks=locks,
    )
    attachments = AttachmentService(database, settings, locks=locks)
    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    session = await sessions.create("actual", identity)
    return settings, database, session, sessions, attachments


@pytest.mark.asyncio
async def test_upload_detects_image_pdf_and_utf8_text(settings_factory) -> None:
    _settings, database, session, service = await build_services(settings_factory)
    png = b"\x89PNG\r\n\x1a\n" + b"image"
    pdf = b"%PDF-1.7\ncontent"
    text = "hello,世界".encode()

    records = await service.upload(
        session.id,
        [
            upload("diagram.png", png, "text/plain"),
            upload("report.pdf", pdf),
            upload("notes.txt", text),
        ],
    )

    assert [record.mime_type for record in records] == [
        "image/png",
        "application/pdf",
        "text/plain",
    ]
    assert all(record.status == "pending" for record in records)
    assert all(service.resolve_path(record).is_file() for record in records)
    assert all(".." not in record.relative_path for record in records)
    await database.dispose()


@pytest.mark.asyncio
async def test_upload_rejects_executable_and_rolls_back_batch(settings_factory) -> None:
    from app.db.models import AttachmentRecord
    from app.errors import AppError

    settings, database, session, service = await build_services(settings_factory)

    with pytest.raises(AppError) as exc_info:
        await service.upload(
            session.id,
            [upload("valid.txt", b"valid"), upload("program.bin", b"\x7fELFbinary")],
        )

    assert exc_info.value.code == "attachment_invalid"
    async with database.session() as db:
        count = await db.scalar(select(func.count()).select_from(AttachmentRecord))
        assert count == 0
    attachment_dir = (
        settings.app_data_dir / session.session_dir / "workspace/attachments"
    )
    assert list(attachment_dir.iterdir()) == []
    await database.dispose()


@pytest.mark.asyncio
async def test_upload_enforces_count_and_size_limits(settings_factory) -> None:
    from app.errors import AppError

    settings, database, session, service = await build_services(settings_factory)

    too_many = [upload(f"{index}.txt", b"x") for index in range(6)]
    with pytest.raises(AppError) as count_error:
        await service.upload(session.id, too_many)
    assert count_error.value.code == "attachment_invalid"

    oversized = upload("large.txt", b"x" * (settings.max_upload_size_bytes + 1))
    with pytest.raises(AppError) as size_error:
        await service.upload(session.id, [oversized])
    assert size_error.value.code == "attachment_too_large"
    await database.dispose()


@pytest.mark.asyncio
async def test_pending_attachment_can_be_deleted(settings_factory) -> None:
    from app.errors import AppError

    _settings, database, session, service = await build_services(settings_factory)
    record = (await service.upload(session.id, [upload("notes.txt", b"hello")]))[0]
    path = service.resolve_path(record)

    await service.delete(record.id)

    assert not path.exists()
    with pytest.raises(AppError) as exc_info:
        await service.get(record.id)
    assert exc_info.value.code == "attachment_not_found"
    await database.dispose()


@pytest.mark.asyncio
async def test_upload_enforces_pending_limit_across_batches(settings_factory) -> None:
    from app.db.models import AttachmentRecord
    from app.errors import AppError

    _settings, database, session, service = await build_services(settings_factory)
    first = await service.upload(
        session.id,
        [upload(f"first-{index}.txt", b"x") for index in range(4)],
    )

    with pytest.raises(AppError) as exc_info:
        await service.upload(
            session.id,
            [upload("overflow-1.txt", b"x"), upload("overflow-2.txt", b"x")],
        )

    assert exc_info.value.code == "attachment_invalid"
    assert (
        exc_info.value.message
        == "At most 5 pending attachments are allowed per session."
    )
    async with database.session() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(AttachmentRecord)
            .where(AttachmentRecord.session_id == session.id)
        )
    assert count == 4
    assert all(service.resolve_path(record).is_file() for record in first)
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_uploads_cannot_bypass_pending_limit(settings_factory) -> None:
    from app.db.models import AttachmentRecord
    from app.errors import AppError

    _settings, database, session, service = await build_services(settings_factory)
    results = await asyncio.gather(
        service.upload(
            session.id,
            [upload(f"left-{index}.txt", b"x") for index in range(3)],
        ),
        service.upload(
            session.id,
            [upload(f"right-{index}.txt", b"x") for index in range(3)],
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, list) for result in results) == 1
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(errors) == 1
    assert errors[0].code == "attachment_invalid"
    async with database.session() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(AttachmentRecord)
            .where(AttachmentRecord.session_id == session.id)
        )
    assert count == 3
    await database.dispose()


@pytest.mark.asyncio
async def test_list_pending_returns_only_session_drafts_in_stable_order(
    settings_factory,
) -> None:
    _settings, database, session, service = await build_services(settings_factory)
    records = await service.upload(
        session.id,
        [upload("first.txt", b"first"), upload("second.txt", b"second")],
    )

    pending = await service.list_pending(session.id)

    assert [record.id for record in pending] == [record.id for record in records]
    assert all(record.session_id == session.id for record in pending)
    assert all(record.status == "pending" for record in pending)
    await database.dispose()


@pytest.mark.asyncio
async def test_upload_first_makes_session_delete_wait_then_removes_database_and_directory(
    settings_factory,
) -> None:
    from app.db.models import AttachmentRecord
    from app.errors import AppError

    locks = GatedSessionLockRegistry("attachment-upload")
    (
        _settings,
        database,
        session,
        sessions,
        attachments,
    ) = await build_lifecycle_services(settings_factory, locks)
    session_path = sessions.session_path(session)
    upload_task = asyncio.create_task(
        attachments.upload(
            session.id,
            [upload("upload-first.txt", b"upload first")],
        ),
        name="attachment-upload",
    )
    await asyncio.wait_for(locks.held_acquired.wait(), timeout=2)
    delete_task = asyncio.create_task(
        sessions.delete(session.id),
        name="session-delete",
    )
    await locks.wait_for_attempt("session-delete")
    assert not delete_task.done()

    locks.release_held.set()
    uploaded = await upload_task
    await delete_task

    assert len(uploaded) == 1
    with pytest.raises(AppError) as exc_info:
        await sessions.get(session.id)
    assert exc_info.value.code == "session_not_found"
    async with database.session() as db:
        count = await db.scalar(select(func.count()).select_from(AttachmentRecord))
    assert count == 0
    assert not session_path.exists()
    assert list(session_path.parent.glob(f".{session_path.name}.deleting-*")) == []
    await database.dispose()


@pytest.mark.asyncio
async def test_session_delete_first_makes_upload_fail_without_recreating_directory(
    settings_factory,
) -> None:
    from app.errors import AppError

    locks = GatedSessionLockRegistry("session-delete")
    (
        _settings,
        database,
        session,
        sessions,
        attachments,
    ) = await build_lifecycle_services(settings_factory, locks)
    session_path = sessions.session_path(session)
    delete_task = asyncio.create_task(
        sessions.delete(session.id),
        name="session-delete",
    )
    await asyncio.wait_for(locks.held_acquired.wait(), timeout=2)
    upload_task = asyncio.create_task(
        attachments.upload(
            session.id,
            [upload("delete-first.txt", b"delete first")],
        ),
        name="attachment-upload",
    )
    await locks.wait_for_attempt("attachment-upload")
    assert not upload_task.done()

    locks.release_held.set()
    await delete_task
    result = (await asyncio.gather(upload_task, return_exceptions=True))[0]

    assert isinstance(result, AppError)
    assert result.code == "session_not_found"
    assert not session_path.exists()
    assert list(session_path.parent.glob(f".{session_path.name}.deleting-*")) == []
    await database.dispose()
