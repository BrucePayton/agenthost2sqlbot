"""把一个 workspace 模板物化成一次 session 的工作目录。"""

from __future__ import annotations

import hashlib
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import yaml

from app.errors import AppError
from app.skills.artifacts import artifact_key_matches_bundle
from app.skills.bundle import (
    SkillBundleLimits,
    build_bundle,
    load_bundle_from_directory,
)
from app.skills.models import ResolvedSkillBundle
from app.workspaces.models import WorkspaceEntry

if TYPE_CHECKING:
    # 运行时不导入：app.sessions 的包初始化会拉起 SessionService，而后者又导入
    # 本模块，先导入 materializer 的那条路径会成环。
    from app.sessions.snapshot import SessionSnapshot

DEFAULT_CLAUDE_MD = """# Workspace Agent Rules

- Work only inside this session workspace.
- Treat files in `attachments/` as user-provided inputs.
- Write generated artifacts to `outputs/`.
- Never expose credentials or environment variables.
"""


@dataclass(frozen=True)
class MaterializedWorkspace:
    session_dir: Path
    workspace_dir: Path
    claude_config_dir: Path
    snapshot_json: str
    snapshot_hash: str
    relative_session_dir: str


def materialize_session_workspace(
    entry: WorkspaceEntry,
    session_id: str,
    data_dir: Path,
    snapshot: SessionSnapshot,
    skills: tuple[ResolvedSkillBundle, ...],
    *,
    limits: SkillBundleLimits | None = None,
    instructions: str | None = None,
) -> MaterializedWorkspace:
    if not entry.available or entry.manifest is None:
        raise AppError("workspace_invalid", "Workspace configuration is invalid.", 409)
    if entry.snapshot_json is None or entry.snapshot_hash is None:
        raise AppError("workspace_invalid", "Workspace snapshot is unavailable.", 409)

    sessions_root = data_dir / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    final_dir = sessions_root / session_id
    if final_dir.exists():
        raise AppError("session_exists", "Session directory already exists.", 409)
    temp_dir = sessions_root / f".{session_id}.tmp-{uuid.uuid4().hex}"
    workspace_dir = temp_dir / "workspace"
    claude_config_dir = temp_dir / "claude-config"

    try:
        workspace_dir.mkdir(parents=True)
        claude_config_dir.mkdir()
        (workspace_dir / "attachments").mkdir()
        (workspace_dir / "outputs").mkdir()

        source_instructions = entry.directory / "CLAUDE.md"
        if source_instructions.exists():
            _reject_symlink(source_instructions)
            # Session instructions are editable even when the template is read-only.
            shutil.copyfile(source_instructions, workspace_dir / "CLAUDE.md")
        else:
            (workspace_dir / "CLAUDE.md").write_text(
                DEFAULT_CLAUDE_MD, encoding="utf-8"
            )

        seed_dir = entry.directory / "seed"
        if seed_dir.exists():
            _reject_tree_symlinks(seed_dir)
            shutil.copytree(seed_dir, workspace_dir, dirs_exist_ok=True)

        if instructions is not None:
            # 必须在 seed 之后：seed 里若也有 CLAUDE.md，copytree 会盖掉先写的。
            instruction_file = workspace_dir / "CLAUDE.md"
            instruction_file.chmod(instruction_file.stat().st_mode | stat.S_IWUSR)
            instruction_file.write_text(instructions, encoding="utf-8")

        skill_target = workspace_dir / ".claude" / "skills"
        if skill_target.is_dir() and not skill_target.is_symlink():
            shutil.rmtree(skill_target)
        elif skill_target.exists() or skill_target.is_symlink():
            skill_target.unlink()
        skill_target.mkdir(parents=True)

        for resolved in sorted(
            skills,
            key=lambda item: (item.skill.name.casefold(), item.skill.id),
        ):
            _validate_resolved_skill(resolved, limits)
            bundle = resolved.bundle
            skill_dir = skill_target / bundle.name
            skill_dir.mkdir()
            skill_content = bundle.content.encode("utf-8")
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_bytes(skill_content)
            if skill_file.read_bytes() != skill_content:
                raise _skill_materialization_error()
            for file in bundle.files:
                target = skill_dir.joinpath(*PurePosixPath(file.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(file.content)
                written_hash = (
                    "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
                )
                if written_hash != file.sha256:
                    raise _skill_materialization_error()
            try:
                written_bundle = load_bundle_from_directory(skill_dir, limits)
            except AppError as exc:
                raise _artifact_corrupt() from exc
            if written_bundle.bundle_hash != resolved.skill.version.bundle_hash:
                raise _artifact_corrupt()

        (workspace_dir / "workspace.snapshot.yaml").write_text(
            yaml.safe_dump(snapshot.data, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        temp_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return MaterializedWorkspace(
        session_dir=final_dir,
        workspace_dir=final_dir / "workspace",
        claude_config_dir=final_dir / "claude-config",
        snapshot_json=snapshot.json,
        snapshot_hash=snapshot.sha256,
        relative_session_dir=f"sessions/{session_id}",
    )


def _skill_materialization_error() -> AppError:
    return AppError(
        "skill_materialization_failed",
        "Skill materialization failed.",
        500,
    )


def _validate_resolved_skill(
    resolved: ResolvedSkillBundle, limits: SkillBundleLimits | None
) -> None:
    bundle = resolved.bundle
    try:
        reconstructed = build_bundle(
            bundle.content.encode("utf-8"),
            ((file.path, file.content) for file in bundle.files),
            limits,
        )
    except AppError as exc:
        raise _artifact_corrupt() from exc
    bundle_hash = reconstructed.bundle_hash
    version = resolved.skill.version
    if (
        resolved.skill.name != reconstructed.name
        or resolved.skill.description != reconstructed.description
        or bundle.bundle_hash != bundle_hash
        or version.bundle_hash != bundle_hash
        or not artifact_key_matches_bundle(version.artifact_key, bundle_hash)
    ):
        raise _artifact_corrupt()


def _artifact_corrupt() -> AppError:
    return AppError(
        "skill_artifact_corrupt", "Skill artifact integrity check failed.", 500
    )


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise AppError(
            "workspace_invalid",
            f"Workspace path {path.name!r} must not be a symbolic link.",
            409,
        )


def _reject_tree_symlinks(root: Path) -> None:
    _reject_symlink(root)
    for path in root.rglob("*"):
        _reject_symlink(path)
