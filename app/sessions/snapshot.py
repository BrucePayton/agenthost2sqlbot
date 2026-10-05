from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.errors import AppError
from app.skills.models import ResolvedSkillBundle
from app.workspaces.models import WorkspaceEntry

if TYPE_CHECKING:
    # 运行时不导入：instructions 模块会反向导入 materializer，导入即成环。
    from app.instructions.service import WorkspaceInstructions


@dataclass(frozen=True)
class SessionSnapshot:
    data: dict[str, Any]
    json: str
    sha256: str


def build_session_snapshot(
    entry: WorkspaceEntry,
    skills: tuple[ResolvedSkillBundle, ...],
    *,
    instructions: WorkspaceInstructions | None = None,
) -> SessionSnapshot:
    data = json.loads(entry.snapshot_json or "{}")
    data["schema_version"] = 4
    data.pop("skills_root_env", None)
    data["skills"] = [
        {
            "id": resolved.skill.id,
            "scope": resolved.skill.scope,
            "version_id": resolved.skill.version.id,
            "version_no": resolved.skill.version.version_no,
            "name": resolved.skill.name,
            "description": resolved.skill.description,
            "bundle_hash": resolved.skill.version.bundle_hash,
            "artifact_key": resolved.skill.version.artifact_key,
            "files": _manifest_files(resolved),
        }
        for resolved in sorted(
            skills,
            key=lambda item: (item.skill.name.casefold(), item.skill.id),
        )
    ]
    if instructions is not None:
        # 只记 hash 与字节数：足以回答「这个 session 用的是哪版指令」，
        # 又不会让快照跟着指令全文一起膨胀。
        data["instructions"] = {
            "sha256": hashlib.sha256(instructions.content.encode("utf-8")).hexdigest(),
            "size_bytes": instructions.size_bytes,
            "source": instructions.source,
            "drifted": bool(getattr(instructions, "drifted_from_template", False)),
        }
    raw = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return SessionSnapshot(
        data=data,
        json=raw,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )


def _manifest_files(resolved: ResolvedSkillBundle) -> list[dict[str, object]]:
    bundle = resolved.bundle
    expected = [
        {
            "path": "SKILL.md",
            "sha256": _sha256(bundle.content.encode("utf-8")),
            "size_bytes": len(bundle.content.encode("utf-8")),
        },
        *[
            {
                "path": file.path,
                "sha256": file.sha256,
                "size_bytes": file.size_bytes,
            }
            for file in bundle.files
        ],
    ]
    expected.sort(key=lambda item: str(item["path"]))
    try:
        manifest = json.loads(resolved.skill.version.manifest_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _artifact_corrupt() from exc
    if manifest != {"files": expected}:
        raise _artifact_corrupt()
    return expected


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _artifact_corrupt() -> AppError:
    return AppError(
        "skill_artifact_corrupt", "Skill artifact integrity check failed.", 500
    )
