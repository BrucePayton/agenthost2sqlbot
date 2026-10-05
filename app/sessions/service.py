import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from app.auth.access import WorkspaceAccessService
from app.auth.models import IdentityContext
from app.db.base import Database
from app.db.models import SessionRecord, TurnEventRecord, UserRecord
from app.errors import AppError
from app.instructions.service import InstructionService
from app.sessions.catalog import (
    FileSearchResult,
    SkillCatalogItem,
    list_skills,
    search_files,
    validate_file_references,
)
from app.sessions.locks import SessionLockRegistry
from app.sessions.snapshot import build_session_snapshot
from app.skills.repository import SkillRepository
from app.skills.service import SkillService
from app.workspaces.materializer import materialize_session_workspace
from app.workspaces.registry import WorkspaceRegistry
from app.workspaces.repository import WorkspaceRepository
from app.workspaces.resolver import WorkspaceTemplateResolver

SESSION_STATUSES = {"idle", "running", "error", "interrupted"}
LEGACY_SESSION_OWNER_ID = "legacy-session-owner"


class SessionService:
    def __init__(
        self,
        database: Database,
        registry: WorkspaceRegistry,
        data_dir: Path,
        *,
        skills: SkillService | None = None,
        locks: SessionLockRegistry | None = None,
        workspace_repository: WorkspaceRepository | None = None,
        workspace_templates: WorkspaceTemplateResolver | None = None,
        instructions: InstructionService | None = None,
    ) -> None:
        self.database = database
        self.registry = registry
        self.data_dir = data_dir.resolve()
        self.skills = skills or SkillService(
            SkillRepository(database), WorkspaceAccessService(database)
        )
        self.locks = locks or SessionLockRegistry()
        self.workspace_repository = workspace_repository or WorkspaceRepository(
            database
        )
        self.workspace_templates = workspace_templates or WorkspaceTemplateResolver(
            registry, self.skills.limits
        )
        self.instructions = instructions or InstructionService(
            database, self.workspace_templates
        )

    async def create(
        self, workspace_id: str, identity: IdentityContext
    ) -> SessionRecord:
        workspace = await self.workspace_repository.get(workspace_id)
        if workspace is None:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        entry = self.workspace_templates.resolve(workspace)
        resolved_skills = await self.skills.resolve_effective_skills(
            workspace_id, identity
        )
        session_id = str(uuid.uuid4())
        # 快照记下这次用的是哪版指令，物化写的是同一份内容。
        resolved_instructions = await self.instructions.get(workspace, identity)
        snapshot = build_session_snapshot(
            entry, resolved_skills, instructions=resolved_instructions
        )
        # Materialize the exact bytes already recorded in the Session snapshot.
        instructions = resolved_instructions.content
        materialized = materialize_session_workspace(
            entry,
            session_id,
            self.data_dir,
            snapshot,
            resolved_skills,
            limits=self.skills.limits,
            instructions=instructions,
        )
        now = datetime.now(UTC)
        record = SessionRecord(
            id=session_id,
            workspace_id=workspace_id,
            created_by=identity.user_id,
            title="新会话",
            title_source="auto",
            status="idle",
            workspace_snapshot_json=snapshot.json,
            workspace_snapshot_hash=snapshot.sha256,
            session_dir=materialized.relative_session_dir,
            created_at=now,
            updated_at=now,
        )
        committed = False
        try:
            async with self.database.session() as db:
                db.add(record)
                await db.commit()
                committed = True
        except Exception:
            if not committed:
                shutil.rmtree(materialized.session_dir, ignore_errors=True)
            raise
        return record

    async def list_for_workspace(
        self,
        workspace_id: str,
        created_by: str,
        limit: int = 200,
    ) -> list[SessionRecord]:
        async with self.database.session() as db:
            return list(
                (
                    await db.scalars(
                        select(SessionRecord)
                        .where(
                            SessionRecord.workspace_id == workspace_id,
                            SessionRecord.created_by == created_by,
                        )
                        .order_by(SessionRecord.updated_at.desc())
                        .limit(limit)
                    )
                ).all()
            )

    async def get(self, session_id: str) -> SessionRecord:
        async with self.database.session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                raise AppError("session_not_found", "Session not found.", 404)
            return record

    async def require_current_skill_snapshot(self, session: SessionRecord) -> None:
        """Reject a blank session whose frozen Skills changed before its first turn."""
        snapshot = json.loads(session.workspace_snapshot_json)
        # Older snapshots predate managed Skill identities and cannot be compared.
        if snapshot.get("schema_version", 1) < 3:
            return
        workspace = await self.workspace_repository.get(session.workspace_id)
        current = (
            await self.skills.repository.list_effective_versions(session.workspace_id)
            if workspace is not None and workspace.kind == "personal" else ()
        )
        pinned = {
            (skill["id"], skill.get("version_id"), skill.get("bundle_hash"))
            for skill in snapshot.get("skills", [])
        }
        effective = {(skill.id, skill.version.id, skill.bundle_hash) for skill in current}
        if pinned != effective:
            raise AppError(
                "session_skills_changed",
                "Skill 配置已变化，请新建会话后重新发送；本次消息尚未执行。",
                409,
            )

    async def claim_legacy_sessions(self, created_by: str) -> int:
        async with self.database.session() as db:
            result = await db.execute(
                update(SessionRecord)
                .where(SessionRecord.created_by == LEGACY_SESSION_OWNER_ID)
                .values(created_by=created_by)
            )
            claimed = int(result.rowcount or 0)
            if claimed:
                legacy = await db.get(UserRecord, LEGACY_SESSION_OWNER_ID)
                if legacy is not None:
                    await db.delete(legacy)
            await db.commit()
        return claimed

    async def list_messages(
        self, session_id: str, limit: int = 10_000
    ) -> list[TurnEventRecord]:
        await self.get(session_id)
        async with self.database.session() as db:
            records = list(
                (
                    await db.scalars(
                        select(TurnEventRecord)
                        .where(TurnEventRecord.session_id == session_id)
                        .order_by(TurnEventRecord.created_at, TurnEventRecord.sequence)
                        .limit(limit + 1)
                    )
                ).all()
            )
        if len(records) > limit:
            raise AppError(
                "history_too_large",
                "This session contains too many events to display.",
                413,
            )
        return records

    async def list_skills(self, session_id: str) -> tuple[SkillCatalogItem, ...]:
        record = await self.get(session_id)
        return await run_in_threadpool(
            self._list_skills_for_record,
            record,
        )

    async def search_files(self, session_id: str, query: str) -> FileSearchResult:
        record = await self.get(session_id)
        return await run_in_threadpool(
            self._search_files_for_record,
            record,
            query,
        )

    def _list_skills_for_record(
        self, record: SessionRecord
    ) -> tuple[SkillCatalogItem, ...]:
        return list_skills(
            self._lexical_workspace_path(record),
            json.loads(record.workspace_snapshot_json),
        )

    def _search_files_for_record(
        self, record: SessionRecord, query: str
    ) -> FileSearchResult:
        return search_files(
            self._lexical_workspace_path(record),
            json.loads(record.workspace_snapshot_json),
            query,
        )

    def validate_file_references_for_record(
        self, record: SessionRecord, references: list[str] | tuple[str, ...]
    ) -> tuple[str, ...]:
        return validate_file_references(
            self._lexical_workspace_path(record),
            json.loads(record.workspace_snapshot_json),
            references,
        )

    def _lexical_workspace_path(self, record: SessionRecord) -> Path:
        # Validate stored containment without hiding Session-owned symlinks
        # from catalog and reference boundaries.
        self.session_path(record)
        return self.data_dir / record.session_dir / "workspace"

    async def rename(self, session_id: str, title: str) -> SessionRecord:
        normalized = title.strip()
        if not 1 <= len(normalized) <= 120:
            raise AppError(
                "invalid_request", "Session title must contain 1 to 120 characters."
            )
        return await self._update(
            session_id,
            title=normalized,
            title_source="user",
            updated_at=datetime.now(UTC),
        )

    async def set_auto_title(self, session_id: str, message: str) -> SessionRecord:
        async with self.database.session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                raise AppError("session_not_found", "Session not found.", 404)
            if record.title_source == "auto" and record.title == "新会话":
                record.title = _auto_title(message)
                record.updated_at = datetime.now(UTC)
                await db.commit()
                await db.refresh(record)
            return record

    async def set_status(
        self, session_id: str, status: str, error_code: str | None = None
    ) -> SessionRecord:
        if status not in SESSION_STATUSES:
            raise ValueError(f"Unsupported session status: {status}")
        return await self._update(
            session_id,
            status=status,
            last_error_code=error_code,
            updated_at=datetime.now(UTC),
        )

    async def delete(self, session_id: str) -> None:
        async with self.locks.acquire(session_id):
            record = await self.get(session_id)
            if record.status == "running":
                raise AppError(
                    "session_busy", "A running session cannot be deleted.", 409
                )
            session_path = self.session_path(record)
            trash_path = session_path.with_name(
                f".{session_path.name}.deleting-{uuid.uuid4().hex}"
            )
            if session_path.exists():
                session_path.rename(trash_path)
            try:
                async with self.database.session() as db:
                    attached = await db.get(SessionRecord, session_id)
                    if attached is None:
                        raise AppError("session_not_found", "Session not found.", 404)
                    await db.delete(attached)
                    await db.commit()
            except Exception:
                if trash_path.exists() and not session_path.exists():
                    trash_path.rename(session_path)
                raise
            shutil.rmtree(trash_path, ignore_errors=True)

    def session_path(self, record: SessionRecord) -> Path:
        path = (self.data_dir / record.session_dir).resolve()
        sessions_root = (self.data_dir / "sessions").resolve()
        if not path.is_relative_to(sessions_root):
            raise AppError("internal_error", "Invalid stored session path.", 500)
        return path

    async def _update(self, session_id: str, **values) -> SessionRecord:
        async with self.database.session() as db:
            record = await db.get(SessionRecord, session_id)
            if record is None:
                raise AppError("session_not_found", "Session not found.", 404)
            for name, value in values.items():
                setattr(record, name, value)
            await db.commit()
            await db.refresh(record)
            return record


def _auto_title(message: str) -> str:
    normalized = " ".join(message.split()).strip()
    if not normalized:
        return "附件会话"
    if len(normalized) <= 30:
        return normalized
    candidate = normalized[:30].rstrip()
    if " " in candidate:
        candidate = candidate.rsplit(" ", 1)[0]
    return candidate or normalized[:30]
