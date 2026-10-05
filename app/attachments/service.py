import codecs
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiofiles
from fastapi import UploadFile
from sqlalchemy import delete, func, select

from app.config import Settings
from app.db.base import Database
from app.db.models import AttachmentRecord, SessionRecord
from app.errors import AppError
from app.sessions.locks import SessionLockRegistry

TEXT_MIME_BY_EXTENSION = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".xml": "application/xml",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".jsx": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".java": "text/x-java-source",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".sql": "application/sql",
    ".sh": "application/x-sh",
    ".toml": "application/toml",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".html": "text/html",
    ".css": "text/css",
}

MAGIC_TYPES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"%PDF-", "application/pdf", ".pdf"),
)


class AttachmentService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        locks: SessionLockRegistry | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.data_dir = settings.app_data_dir.resolve()
        self.locks = locks or SessionLockRegistry()

    async def upload(
        self, session_id: str, files: list[UploadFile]
    ) -> list[AttachmentRecord]:
        try:
            if not files or len(files) > self.settings.max_files_per_turn:
                raise AppError(
                    "attachment_invalid",
                    f"Upload between 1 and {self.settings.max_files_per_turn} files.",
                )
            async with self.locks.acquire(session_id):
                session = await self._get_session(session_id)
                async with self.database.session() as db:
                    pending_count = int(
                        (
                            await db.scalar(
                                select(func.count())
                                .select_from(AttachmentRecord)
                                .where(
                                    AttachmentRecord.session_id == session_id,
                                    AttachmentRecord.status == "pending",
                                )
                            )
                        )
                        or 0
                    )
                if pending_count + len(files) > self.settings.max_files_per_turn:
                    raise AppError(
                        "attachment_invalid",
                        f"At most {self.settings.max_files_per_turn} pending "
                        "attachments are allowed per session.",
                    )

                attachment_dir = self._attachment_dir(session)
                attachment_dir.mkdir(parents=True, exist_ok=True)
                records: list[AttachmentRecord] = []
                created_paths: list[Path] = []
                try:
                    for file in files:
                        record, path = await self._stage_file(
                            session, attachment_dir, file
                        )
                        records.append(record)
                        created_paths.append(path)
                    async with self.database.session() as db:
                        db.add_all(records)
                        await db.commit()
                        for record in records:
                            await db.refresh(record)
                except Exception:
                    for path in created_paths:
                        path.unlink(missing_ok=True)
                    for temp_path in attachment_dir.glob(".upload-*.tmp"):
                        temp_path.unlink(missing_ok=True)
                    raise
                return records
        finally:
            for file in files:
                await file.close()

    async def list_pending(self, session_id: str) -> list[AttachmentRecord]:
        await self._get_session(session_id)
        async with self.database.session() as db:
            return list(
                (
                    await db.scalars(
                        select(AttachmentRecord)
                        .where(
                            AttachmentRecord.session_id == session_id,
                            AttachmentRecord.status == "pending",
                        )
                        .order_by(
                            AttachmentRecord.created_at,
                            AttachmentRecord.id,
                        )
                    )
                ).all()
            )

    async def get(self, attachment_id: str) -> AttachmentRecord:
        async with self.database.session() as db:
            record = await db.get(AttachmentRecord, attachment_id)
            if record is None:
                raise AppError(
                    "attachment_not_found", "Attachment not found.", 404
                )
            return record

    async def delete(self, attachment_id: str) -> None:
        record = await self.get(attachment_id)
        async with self.locks.acquire(record.session_id):
            path = self.resolve_path(record)
            async with self.database.session() as db:
                result = await db.execute(
                    delete(AttachmentRecord).where(
                        AttachmentRecord.id == attachment_id,
                        AttachmentRecord.status == "pending",
                    )
                )
                if result.rowcount != 1:
                    attached = await db.get(AttachmentRecord, attachment_id)
                    if attached is None:
                        raise AppError(
                            "attachment_not_found", "Attachment not found.", 404
                        )
                    raise AppError(
                        "attachment_bound",
                        "A bound attachment cannot be deleted.",
                        409,
                    )
                await db.commit()
            path.unlink(missing_ok=True)

    async def cleanup_pending(self, max_age_hours: int = 24) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        async with self.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(AttachmentRecord).where(
                            AttachmentRecord.status == "pending",
                            AttachmentRecord.created_at < cutoff,
                        )
                    )
                ).all()
            )
            for record in records:
                self.resolve_path(record).unlink(missing_ok=True)
            if records:
                await db.execute(
                    delete(AttachmentRecord).where(
                        AttachmentRecord.id.in_([record.id for record in records])
                    )
                )
                await db.commit()
            return len(records)

    def resolve_path(self, record: AttachmentRecord) -> Path:
        path = (self.data_dir / record.relative_path).resolve()
        if not path.is_relative_to(self.data_dir):
            raise AppError("internal_error", "Invalid stored attachment path.", 500)
        return path

    async def _stage_file(
        self,
        session: SessionRecord,
        attachment_dir: Path,
        file: UploadFile,
    ) -> tuple[AttachmentRecord, Path]:
        attachment_id = str(uuid.uuid4())
        temp_path = attachment_dir / f".upload-{attachment_id}.tmp"
        original_filename = _safe_display_name(file.filename)
        suffix = Path(original_filename).suffix.lower()
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()
        decoder = codecs.getincrementaldecoder("utf-8")()
        utf8_valid = True

        try:
            async with aiofiles.open(temp_path, "wb") as output:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > self.settings.max_upload_size_bytes:
                        raise AppError(
                            "attachment_too_large",
                            f"File exceeds {self.settings.max_upload_size_mb} MiB.",
                            413,
                        )
                    if len(prefix) < 32:
                        prefix.extend(chunk[: 32 - len(prefix)])
                    if utf8_valid:
                        try:
                            decoder.decode(chunk)
                        except UnicodeDecodeError:
                            utf8_valid = False
                    digest.update(chunk)
                    await output.write(chunk)
            if size == 0:
                raise AppError("attachment_invalid", "Empty files are not supported.")
            if utf8_valid:
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    utf8_valid = False
            mime_type, stored_suffix = _detect_type(bytes(prefix), suffix, utf8_valid)
            final_path = attachment_dir / f"{attachment_id}{stored_suffix}"
            os.replace(temp_path, final_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        relative_path = final_path.relative_to(self.data_dir).as_posix()
        record = AttachmentRecord(
            id=attachment_id,
            session_id=session.id,
            status="pending",
            original_filename=original_filename,
            stored_filename=final_path.name,
            mime_type=mime_type,
            size_bytes=size,
            sha256=digest.hexdigest(),
            relative_path=relative_path,
            created_at=datetime.now(UTC),
        )
        return record, final_path

    async def _get_session(self, session_id: str) -> SessionRecord:
        async with self.database.session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                raise AppError("session_not_found", "Session not found.", 404)
            return record

    def _attachment_dir(self, session: SessionRecord) -> Path:
        session_dir = (self.data_dir / session.session_dir).resolve()
        sessions_root = (self.data_dir / "sessions").resolve()
        if not session_dir.is_relative_to(sessions_root):
            raise AppError("internal_error", "Invalid stored session path.", 500)
        return session_dir / "workspace" / "attachments"


def _safe_display_name(filename: str | None) -> str:
    normalized = (filename or "attachment").replace("\\", "/")
    name = Path(normalized).name.strip()
    return name[:255] or "attachment"


def _detect_type(prefix: bytes, suffix: str, utf8_valid: bool) -> tuple[str, str]:
    if prefix.startswith((b"\x7fELF", b"MZ")) or prefix[:4] in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
    }:
        raise AppError("attachment_invalid", "Executable files are not supported.")
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp", ".webp"
    for magic, mime_type, stored_suffix in MAGIC_TYPES:
        if prefix.startswith(magic):
            return mime_type, stored_suffix
    if utf8_valid and b"\x00" not in prefix and suffix in TEXT_MIME_BY_EXTENSION:
        return TEXT_MIME_BY_EXTENSION[suffix], suffix
    raise AppError(
        "attachment_invalid",
        "Unsupported or unrecognized file type.",
    )
