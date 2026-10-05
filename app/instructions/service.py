"""每个 workspace 一份可编辑的 CLAUDE.md。

没有覆盖行时用默认（seed > 模板 > 内置兜底），删行即还原默认。物化 session
时由 `SessionService.create` 取覆盖内容传给 materializer。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import IdentityContext
from app.db.base import Database
from app.db.models import WorkspaceInstructionRecord, WorkspaceRecord
from app.errors import AppError
from app.workspaces.materializer import DEFAULT_CLAUDE_MD
from app.workspaces.models import WorkspaceEntry
from app.workspaces.resolver import WorkspaceTemplateResolver

InstructionSource = Literal["custom", "seed", "template", "builtin"]

DEFAULT_MAX_INSTRUCTIONS_BYTES = 16384

logger = logging.getLogger(__name__)


def instructions_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkspaceInstructions:
    content: str
    source: InstructionSource
    content_hash: str
    size_bytes: int
    updated_at: datetime | None = None
    updated_by: str | None = None
    drifted_from_template: bool = False


class InstructionService:
    def __init__(
        self,
        database: Database,
        templates: WorkspaceTemplateResolver,
        *,
        max_bytes: int = DEFAULT_MAX_INSTRUCTIONS_BYTES,
    ) -> None:
        self.database = database
        self.templates = templates
        self.max_bytes = max_bytes
        self._drift_logged: set[str] = set()

    async def get(
        self, workspace: WorkspaceRecord, identity: IdentityContext
    ) -> WorkspaceInstructions:
        """当前实际生效的那一份：有覆盖就是覆盖，没有就是默认。

        授权由路由层的 `require_personal_owner` 承担；`identity` 只是让四个
        方法的调用形状一致，读路径本身不依赖它。
        """
        record = await self._load_record(workspace.id)
        if record is None:
            return self._with_drift(
                self._resolve_default(self.templates.resolve(workspace)), workspace
            )
        return self._with_drift(_custom(record), workspace)

    def _with_drift(
        self, resolved: WorkspaceInstructions, workspace: WorkspaceRecord
    ) -> WorkspaceInstructions:
        if resolved.source != "custom":
            return resolved
        default = self._resolve_default(self.templates.resolve(workspace))
        drifted = instructions_hash(resolved.content) != instructions_hash(
            default.content
        )
        if drifted and workspace.id not in self._drift_logged:
            self._drift_logged.add(workspace.id)
            logger.warning(
                "workspace_instructions_override_drifted",
                extra={
                    "workspace_id": workspace.id,
                    "override_hash": instructions_hash(resolved.content)[:12],
                    "template_hash": instructions_hash(default.content)[:12],
                },
            )
        return replace(resolved, drifted_from_template=drifted)

    async def get_default(self, workspace: WorkspaceRecord) -> WorkspaceInstructions:
        return self._resolve_default(self.templates.resolve(workspace))

    def _resolve_default(self, entry: WorkspaceEntry) -> WorkspaceInstructions:
        """没有用户覆盖时实际会用到的那一份：seed > 模板 > 内置兜底。

        顺序照 materializer 的写入顺序倒推——seed 的 copytree 在模板 CLAUDE.md
        之后执行，所以 seed 里的那份才是最终落盘的。
        """
        seed_instructions = entry.directory / "seed" / "CLAUDE.md"
        if seed_instructions.exists():
            return _resolved(_read(seed_instructions), "seed")
        template_instructions = entry.directory / "CLAUDE.md"
        if template_instructions.exists():
            return _resolved(_read(template_instructions), "template")
        return _resolved(DEFAULT_CLAUDE_MD, "builtin")

    async def save(
        self,
        workspace: WorkspaceRecord,
        identity: IdentityContext,
        content: str,
        *,
        expected_hash: str | None,
    ) -> WorkspaceInstructions:
        size_bytes = _encoded_size(content)
        if size_bytes > self.max_bytes:
            raise AppError(
                "instructions_too_large",
                f"Instructions must be at most {self.max_bytes} bytes.",
                413,
            )
        if "\x00" in content:
            raise AppError(
                "instructions_invalid",
                "Instructions must not contain NUL bytes.",
                422,
            )
        content_hash = instructions_hash(content)
        now = datetime.now(UTC)
        values = {
            "workspace_id": workspace.id,
            "content": content,
            "content_hash": content_hash,
            "size_bytes": size_bytes,
            "updated_by": identity.user_id,
            "created_at": now,
            "updated_at": now,
        }
        async with self.database.session() as db, db.begin():
            await self._require_expected_hash(db, workspace.id, expected_hash)
            statement = (
                postgresql_insert(WorkspaceInstructionRecord)
                if db.bind is not None and db.bind.dialect.name == "postgresql"
                else sqlite_insert(WorkspaceInstructionRecord)
            )
            await db.execute(
                statement.values(**values).on_conflict_do_update(
                    index_elements=("workspace_id",),
                    set_={
                        "content": content,
                        "content_hash": content_hash,
                        "size_bytes": size_bytes,
                        "updated_by": identity.user_id,
                        "updated_at": now,
                    },
                )
            )
        record = await self._load_record(workspace.id)
        if record is None:
            raise AppError(
                "instructions_invalid", "Instructions could not be stored.", 500
            )
        return self._with_drift(_custom(record), workspace)

    async def reset(
        self,
        workspace: WorkspaceRecord,
        identity: IdentityContext,
        *,
        expected_hash: str | None,
    ) -> None:
        """删覆盖行即还原默认；没有行时按幂等成功处理。

        `identity` 与 `save` 对齐；删除不写审计列，所以这里用不到它。
        """
        async with self.database.session() as db, db.begin():
            record = await db.get(WorkspaceInstructionRecord, workspace.id)
            if record is None:
                return
            _require_hash_match(record.content_hash, expected_hash)
            await db.execute(
                delete(WorkspaceInstructionRecord).where(
                    WorkspaceInstructionRecord.workspace_id == workspace.id
                )
            )

    async def load_override_content(self, workspace_id: str) -> str | None:
        """物化 session 时取用户覆盖的全文；没有覆盖返回 None。"""
        async with self.database.session() as db:
            return await db.scalar(
                select(WorkspaceInstructionRecord.content).where(
                    WorkspaceInstructionRecord.workspace_id == workspace_id
                )
            )

    async def _load_record(
        self, workspace_id: str
    ) -> WorkspaceInstructionRecord | None:
        async with self.database.session() as db:
            return await db.get(WorkspaceInstructionRecord, workspace_id)

    async def _require_expected_hash(
        self, db: AsyncSession, workspace_id: str, expected_hash: str | None
    ) -> None:
        if expected_hash is None:
            return
        record = await db.get(WorkspaceInstructionRecord, workspace_id)
        # 没有覆盖行时任何 expected_hash 都算不匹配：调用方以为在改一份自定义
        # 内容，而它已经被还原成默认了。
        _require_hash_match(
            record.content_hash if record is not None else None, expected_hash
        )


def _require_hash_match(current_hash: str | None, expected_hash: str | None) -> None:
    if expected_hash is None or current_hash == expected_hash:
        return
    raise AppError(
        "instructions_changed",
        "Instructions changed since they were loaded.",
        409,
    )


def _custom(record: WorkspaceInstructionRecord) -> WorkspaceInstructions:
    return WorkspaceInstructions(
        content=record.content,
        source="custom",
        content_hash=record.content_hash,
        size_bytes=record.size_bytes,
        updated_at=record.updated_at,
        updated_by=record.updated_by,
    )


def _resolved(content: str, source: InstructionSource) -> WorkspaceInstructions:
    return WorkspaceInstructions(
        content=content,
        source=source,
        content_hash=instructions_hash(content),
        size_bytes=_encoded_size(content),
    )


def _encoded_size(content: str) -> int:
    try:
        return len(content.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise AppError(
            "instructions_invalid", "Instructions must be valid UTF-8.", 422
        ) from exc


def _read(path: Path) -> str:
    _reject_symlink(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(
            "instructions_invalid", "Instructions must be valid UTF-8.", 422
        ) from exc


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise AppError(
            "workspace_invalid",
            f"Workspace path {path.name!r} must not be a symbolic link.",
            409,
        )
