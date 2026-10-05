import os
from collections import deque
from dataclasses import dataclass
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from app.errors import AppError

MAX_SKILL_FRONTMATTER_BYTES = 64 * 1024
MAX_FILE_REFERENCES = 20
EXCLUDED_NAMES = {
    ".claude",
    "CLAUDE.md",
    "workspace.snapshot.yaml",
    "__pycache__",
    "node_modules",
    "cache",
}


@dataclass(frozen=True)
class SkillCatalogItem:
    name: str
    description: str


@dataclass(frozen=True)
class FileCatalogItem:
    path: str
    name: str
    size_bytes: int


@dataclass(frozen=True)
class FileSearchResult:
    items: tuple[FileCatalogItem, ...]
    truncated: bool


def list_skills(
    workspace_dir: Path, snapshot: dict[str, Any]
) -> tuple[SkillCatalogItem, ...]:
    try:
        schema_version = int(snapshot.get("schema_version", 0))
    except (TypeError, ValueError):
        return ()
    if schema_version not in {2, 3, 4}:
        return ()
    if schema_version == 2:
        items: list[SkillCatalogItem] = []
        for manifest in snapshot.get("skills", []):
            if not isinstance(manifest, dict):
                continue
            name = manifest.get("name")
            description = manifest.get("description")
            if not isinstance(name, str) or not name:
                continue
            items.append(
                SkillCatalogItem(
                    name=name,
                    description=description if isinstance(description, str) else "",
                )
            )
        return tuple(items)

    root = _resolve_workspace_root(workspace_dir)
    items = []
    for manifest in snapshot.get("skills", []):
        if not isinstance(manifest, dict):
            continue
        name = manifest.get("name")
        description = manifest.get("description")
        if not isinstance(name, str) or not name:
            continue
        pinned_description = description if isinstance(description, str) else ""
        materialized = _read_skill_metadata(
            root,
            root / ".claude" / "skills" / name / "SKILL.md",
            allow_external_links=False,
        )
        items.append(
            SkillCatalogItem(
                name=name,
                description=(
                    pinned_description
                    if materialized == (name, pinned_description.strip())
                    else ""
                ),
            )
        )
    return tuple(items)


def search_files(
    workspace_dir: Path,
    snapshot: dict[str, Any],
    query: str,
    *,
    scan_limit: int = 10_000,
    result_limit: int = 50,
) -> FileSearchResult:
    _require_read(snapshot)
    root = _resolve_workspace_root(workspace_dir)
    needle = query.casefold()
    matches: list[FileCatalogItem] = []
    scanned = 0
    truncated = False

    directories = deque([root])
    while directories and not truncated:
        current_path = directories.popleft()
        remaining = scan_limit - scanned
        if remaining <= 0:
            truncated = True
            break
        try:
            with os.scandir(current_path) as iterator:
                entries = sorted(
                    islice(iterator, remaining), key=lambda entry: entry.name
                )
        except OSError:
            truncated = True
            break
        scanned += len(entries)
        if len(entries) == remaining:
            truncated = True
        child_directories: list[Path] = []
        for entry in entries:
            candidate = Path(entry.path)
            relative = candidate.relative_to(root)
            try:
                if _is_excluded(relative) or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    child_directories.append(candidate)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                relative_text = relative.as_posix()
                if (
                    needle not in relative_text.casefold()
                    and needle not in entry.name.casefold()
                ):
                    continue
                resolved = candidate.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    truncated = True
                    break
                size_bytes = candidate.stat().st_size
            except (OSError, RuntimeError):
                truncated = True
                break
            matches.append(FileCatalogItem(relative_text, entry.name, size_bytes))
        directories.extend(child_directories)

    matches.sort(key=lambda item: (item.path.casefold(), item.path))
    if len(matches) > result_limit:
        truncated = True
    return FileSearchResult(tuple(matches[:result_limit]), truncated)


def validate_file_references(
    workspace_dir: Path,
    snapshot: dict[str, Any],
    references: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if not references:
        return ()
    _require_read(snapshot)
    if len(references) > MAX_FILE_REFERENCES or len(references) != len(set(references)):
        raise AppError(
            "file_reference_invalid",
            "File references must be unique and contain at most 20 paths.",
        )
    root = _resolve_workspace_root(workspace_dir)
    normalized: list[str] = []
    for raw in references:
        if (
            not isinstance(raw, str)
            or not raw
            or any(part in {"", ".", ".."} for part in raw.split("/"))
        ):
            raise AppError("file_reference_invalid", "A referenced file is invalid.")
        path = PurePosixPath(raw)
        if path.is_absolute() or any(
            _is_excluded_component(part) for part in path.parts
        ):
            raise AppError("file_reference_invalid", "A referenced file is invalid.")
        candidate = root.joinpath(*path.parts)
        cursor = root
        for part in path.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise AppError(
                    "file_reference_invalid", "A referenced file is invalid."
                )
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise AppError(
                "file_reference_invalid", "A referenced file no longer exists."
            ) from exc
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise AppError("file_reference_invalid", "A referenced file is invalid.")
        normalized.append(path.as_posix())
    return tuple(normalized)


def _resolve_workspace_root(workspace_dir: Path) -> Path:
    lexical_root = workspace_dir.absolute()
    # SessionService owns this three-level containment chain. Higher-level path
    # aliases can be normal OS or deployment details and are already resolved
    # before a Session workspace is materialized.
    containment_chain = (
        lexical_root,
        lexical_root.parent,
        lexical_root.parent.parent,
    )
    if any(path.is_symlink() for path in containment_chain):
        raise AppError(
            "file_reference_invalid", "The Session workspace path is invalid."
        )
    return lexical_root.resolve(strict=True)


def _require_read(snapshot: dict[str, Any]) -> None:
    allowed = [str(rule) for rule in snapshot.get("allowed_tools", [])]
    if any(
        rule == "Read" or (rule.endswith("*") and "Read".startswith(rule[:-1]))
        for rule in allowed
    ):
        return
    raise AppError(
        "file_reference_unavailable",
        "This Session does not allow the Read tool.",
        409,
    )


def _is_excluded_component(name: str) -> bool:
    return name.startswith(".") or name in EXCLUDED_NAMES


def _is_excluded(relative: Path) -> bool:
    return any(_is_excluded_component(part) for part in relative.parts)


def _read_skill_description(
    root: Path,
    skill_file: Path,
    *,
    allow_external_links: bool,
) -> str:
    metadata = _read_skill_metadata(
        root,
        skill_file,
        allow_external_links=allow_external_links,
    )
    return metadata[1] if metadata is not None else ""


def _read_skill_metadata(
    root: Path,
    skill_file: Path,
    *,
    allow_external_links: bool,
) -> tuple[str, str] | None:
    try:
        if allow_external_links:
            claude_dir = root / ".claude"
            skills_dir = claude_dir / "skills"
            if any(
                path.is_symlink() or not path.is_dir()
                for path in (claude_dir, skills_dir)
            ):
                return None
            if not skill_file.parent.is_symlink():
                return None
            readable_skill_file = skill_file.resolve(strict=True)
            if not readable_skill_file.is_file():
                return None
        else:
            cursor = root
            for part in skill_file.relative_to(root).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    return None
            readable_skill_file = skill_file
        with readable_skill_file.open("rb") as handle:
            raw = handle.read(MAX_SKILL_FRONTMATTER_BYTES + 1)
        if not raw.startswith(b"---\n"):
            return None
        closing = raw.find(b"\n---\n", 4, MAX_SKILL_FRONTMATTER_BYTES + 1)
        if (
            closing < 0
            and len(raw) <= MAX_SKILL_FRONTMATTER_BYTES
            and raw.endswith(b"\n---")
        ):
            closing = len(raw) - len(b"\n---")
        if closing < 0:
            return None
        frontmatter = yaml.safe_load(raw[4:closing].decode("utf-8"))
    except (OSError, RuntimeError, UnicodeError, yaml.YAMLError):
        return None
    if not isinstance(frontmatter, dict):
        return None
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    return name.strip(), description.strip()
