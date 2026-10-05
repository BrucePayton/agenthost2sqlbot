import hashlib
import io
import lzma
import mimetypes
import os
import re
import stat
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import yaml

from app.errors import AppError
from app.skills.models import SkillBundle, SkillBundleFile

SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HASH_VERSION = b"skill-bundle-v1"
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class SkillBundleLimits:
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    max_files: int = 200


@dataclass(frozen=True)
class UploadedSkillDirectory:
    source_name: str
    bundle: SkillBundle


def invalid_bundle(message: str) -> AppError:
    return AppError("invalid_skill_bundle", message, status_code=422)


def bundle_too_large(message: str) -> AppError:
    return AppError("skill_bundle_too_large", message, status_code=413)


def parse_skill_markdown(raw: bytes) -> tuple[str, str, str]:
    if not isinstance(raw, bytes):
        raise invalid_bundle("SKILL.md content must be bytes.")
    if not raw.startswith(b"---\n"):
        raise invalid_bundle("SKILL.md must start with YAML frontmatter.")
    closing = raw.find(b"\n---\n", 4)
    if closing < 0 or len(raw[4:closing]) > 64 * 1024:
        raise invalid_bundle("SKILL.md frontmatter is missing or too large.")
    try:
        text = raw.decode("utf-8")
        metadata = yaml.safe_load(raw[4:closing].decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise invalid_bundle("SKILL.md frontmatter is invalid.") from exc
    name = metadata.get("name") if isinstance(metadata, dict) else None
    description = metadata.get("description") if isinstance(metadata, dict) else None
    if not isinstance(name, str) or not SKILL_NAME_RE.fullmatch(name.strip()):
        raise invalid_bundle("Skill name is invalid.")
    if not isinstance(description, str) or not description.strip():
        raise invalid_bundle("Skill description is required.")
    return name.strip(), description.strip(), text


def build_bundle(
    skill_markdown: bytes,
    files: Iterable[tuple[str, bytes]],
    limits: SkillBundleLimits | None = None,
) -> SkillBundle:
    limits = limits or SkillBundleLimits()
    _validate_limits(limits)
    if not isinstance(skill_markdown, bytes):
        raise invalid_bundle("SKILL.md content must be bytes.")
    total_bytes = len(skill_markdown)
    if total_bytes > limits.max_total_bytes:
        raise bundle_too_large("Skill bundle exceeds the total size limit.")

    normalized_files: list[tuple[str, bytes]] = []
    paths: set[str] = set()
    for supporting_file_count, entry in enumerate(files, start=1):
        if supporting_file_count > limits.max_files:
            raise bundle_too_large("Skill bundle exceeds the file count limit.")
        try:
            path, content = entry
        except (TypeError, ValueError) as exc:
            raise invalid_bundle("Skill bundle file entry is invalid.") from exc
        normalized_path = _validate_relative_path(path)
        if normalized_path == "SKILL.md":
            raise invalid_bundle("SKILL.md must be the root bundle document only.")
        if normalized_path in paths:
            raise invalid_bundle("Skill bundle contains duplicate file paths.")
        if _has_path_conflict(normalized_path, paths):
            raise invalid_bundle("Skill bundle contains conflicting file paths.")
        if not isinstance(content, bytes):
            raise invalid_bundle("Skill bundle file content must be bytes.")
        if len(content) > limits.max_file_bytes:
            raise bundle_too_large("Skill bundle file exceeds the per-file size limit.")
        total_bytes += len(content)
        if total_bytes > limits.max_total_bytes:
            raise bundle_too_large("Skill bundle exceeds the total size limit.")
        paths.add(normalized_path)
        normalized_files.append((normalized_path, content))

    name, description, text = parse_skill_markdown(skill_markdown)
    bundle_files = tuple(
        SkillBundleFile(
            path=path,
            content=content,
            mime_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
            size_bytes=len(content),
            sha256=_prefixed_sha256(content),
        )
        for path, content in sorted(normalized_files)
    )
    return SkillBundle(
        name=name,
        description=description,
        content=text,
        bundle_hash=_bundle_hash(skill_markdown, name, description, bundle_files),
        files=bundle_files,
    )


def load_bundle_from_archive(
    raw: bytes, limits: SkillBundleLimits | None = None
) -> SkillBundle:
    limits = limits or SkillBundleLimits()
    _validate_limits(limits)
    if not isinstance(raw, bytes):
        raise invalid_bundle("Skill archive content must be bytes.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            _validate_archive_infos(infos, limits)
            contents: dict[str, bytes] = {}
            actual_total = 0
            for info in infos:
                path = _validate_relative_path(info.orig_filename)
                content = _read_limited(
                    archive.open(info),
                    limits,
                    actual_total,
                    path,
                    enforce_file_limit=path != "SKILL.md",
                )
                actual_total += len(content)
                contents[path] = content
    except AppError:
        raise
    except (
        EOFError,
        lzma.LZMAError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zlib.error,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ) as exc:
        raise invalid_bundle("Skill archive is invalid.") from exc

    return build_bundle(contents.pop("SKILL.md"), contents.items(), limits=limits)


def load_bundle_from_directory(
    root: str | Path, limits: SkillBundleLimits | None = None
) -> SkillBundle:
    limits = limits or SkillBundleLimits()
    _validate_limits(limits)
    root_fd = _open_anchored_root_directory(Path(root))
    contents: dict[str, bytes] = {}
    declared_total = 0
    actual_total = 0
    supporting_file_count = 0

    def walk(directory_fd: int, relative_directory: Path) -> None:
        nonlocal actual_total, declared_total, supporting_file_count
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise invalid_bundle("Skill bundle directory cannot be read.") from exc
        for name in names:
            relative_path = relative_directory / name
            normalized_path = _validate_relative_path(relative_path.as_posix())
            try:
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise invalid_bundle("Skill bundle entry cannot be inspected.") from exc
            if stat.S_ISLNK(entry_stat.st_mode):
                raise invalid_bundle("Skill bundle cannot contain symbolic links.")
            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd = _open_child_directory(directory_fd, name)
                try:
                    walk(child_fd, relative_path)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise invalid_bundle("Skill bundle can contain regular files only.")

            is_root_skill = normalized_path == "SKILL.md"
            if not is_root_skill:
                supporting_file_count += 1
                if supporting_file_count > limits.max_files:
                    raise bundle_too_large("Skill bundle exceeds the file count limit.")
            content, declared_size = _read_directory_file(
                directory_fd,
                name,
                limits,
                actual_total,
                enforce_file_limit=not is_root_skill,
            )
            declared_total += declared_size
            if declared_total > limits.max_total_bytes:
                raise bundle_too_large("Skill bundle exceeds the total size limit.")
            actual_total += len(content)
            contents[normalized_path] = content

    try:
        walk(root_fd, Path())
    finally:
        os.close(root_fd)
    if "SKILL.md" not in contents:
        raise invalid_bundle("Skill bundle must contain exactly one root SKILL.md.")
    return build_bundle(contents.pop("SKILL.md"), contents.items(), limits=limits)


def load_bundle_from_uploaded_files(
    files: Iterable[tuple[str, bytes]],
    limits: SkillBundleLimits | None = None,
) -> UploadedSkillDirectory:
    limits = limits or SkillBundleLimits()
    _validate_limits(limits)
    source_name: str | None = None
    contents: dict[str, bytes] = {}
    paths: set[str] = set()
    total_bytes = 0
    supporting_files = 0

    for position, entry in enumerate(files, start=1):
        if position > limits.max_files + 1:
            raise bundle_too_large("Skill bundle exceeds the file count limit.")
        try:
            browser_path, content = entry
        except (TypeError, ValueError) as exc:
            raise invalid_bundle("Skill bundle file entry is invalid.") from exc
        normalized = _validate_relative_path(browser_path)
        parts = normalized.split("/")
        if len(parts) < 2:
            raise invalid_bundle("Select one Skill directory, not individual files.")
        if source_name is None:
            source_name = parts[0]
        elif parts[0] != source_name:
            raise invalid_bundle("Skill upload must contain exactly one directory root.")

        relative_path = _validate_relative_path("/".join(parts[1:]))
        if relative_path in paths or _has_path_conflict(relative_path, paths):
            raise invalid_bundle(
                "Skill bundle contains duplicate or conflicting file paths."
            )
        if not isinstance(content, bytes):
            raise invalid_bundle("Skill bundle file content must be bytes.")
        if relative_path != "SKILL.md":
            supporting_files += 1
            if supporting_files > limits.max_files:
                raise bundle_too_large("Skill bundle exceeds the file count limit.")
            if len(content) > limits.max_file_bytes:
                raise bundle_too_large(
                    f"{relative_path} exceeds the per-file size limit."
                )
        total_bytes += len(content)
        if total_bytes > limits.max_total_bytes:
            raise bundle_too_large("Skill bundle exceeds the total size limit.")
        paths.add(relative_path)
        contents[relative_path] = content

    if source_name is None or "SKILL.md" not in contents:
        raise invalid_bundle("Skill bundle must contain exactly one root SKILL.md.")
    bundle = build_bundle(contents.pop("SKILL.md"), contents.items(), limits)
    return UploadedSkillDirectory(source_name=source_name, bundle=bundle)


def rename_bundle(
    bundle: SkillBundle,
    target_name: str,
    limits: SkillBundleLimits | None = None,
) -> SkillBundle:
    normalized_name = target_name.strip() if isinstance(target_name, str) else ""
    if not SKILL_NAME_RE.fullmatch(normalized_name):
        raise invalid_bundle("Skill name is invalid.")
    raw = bundle.content.encode("utf-8")
    closing = raw.find(b"\n---\n", 4)
    try:
        metadata = yaml.safe_load(raw[4:closing].decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise invalid_bundle("SKILL.md frontmatter is invalid.") from exc
    if not isinstance(metadata, dict):
        raise invalid_bundle("SKILL.md frontmatter is invalid.")
    metadata["name"] = normalized_name
    frontmatter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).encode("utf-8")
    rewritten = (
        b"---\n"
        + frontmatter
        + b"---\n"
        + raw[closing + len(b"\n---\n") :]
    )
    return build_bundle(
        rewritten,
        [(item.path, item.content) for item in bundle.files],
        limits,
    )


def _validate_limits(limits: SkillBundleLimits) -> None:
    if (
        limits.max_file_bytes < 0
        or limits.max_total_bytes < 0
        or limits.max_files < 0
    ):
        raise ValueError("Skill bundle limits must be non-negative.")


def _validate_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise invalid_bundle("Skill bundle file path is invalid.")
    if "\x00" in path or "\\" in path or path.startswith("/"):
        raise invalid_bundle("Skill bundle file path is invalid.")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise invalid_bundle("Skill bundle file path is invalid.")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise invalid_bundle("Skill bundle file path is invalid.") from exc
    return path


def _bundle_hash(
    skill_markdown: bytes,
    name: str,
    description: str,
    files: tuple[SkillBundleFile, ...],
) -> str:
    digest = hashlib.sha256()
    _hash_field(digest, b"version", HASH_VERSION)
    _hash_field(digest, b"name", name.encode("utf-8"))
    _hash_field(digest, b"description", description.encode("utf-8"))
    _hash_field(digest, b"skill_markdown", skill_markdown)
    _hash_field(digest, b"supporting_file_count", len(files).to_bytes(8, "big"))
    for bundle_file in files:
        _hash_field(digest, b"file_path", bundle_file.path.encode("utf-8"))
        _hash_field(digest, b"file_length", bundle_file.size_bytes.to_bytes(8, "big"))
        _hash_field(
            digest,
            b"file_sha256",
            hashlib.sha256(bundle_file.content).digest(),
        )
        _hash_field(digest, b"file_content", bundle_file.content)
    return f"sha256:{digest.hexdigest()}"


def _hash_field(digest: object, tag: bytes, value: bytes) -> None:
    digest.update(len(tag).to_bytes(4, "big"))
    digest.update(tag)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _prefixed_sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _validate_archive_infos(infos: list[zipfile.ZipInfo], limits: SkillBundleLimits) -> None:
    if not infos:
        raise invalid_bundle("Skill archive is empty.")
    total_declared = 0
    supporting_file_count = 0
    skill_markdown_count = 0
    seen_paths: set[str] = set()
    for info in infos:
        path = _validate_relative_path(info.orig_filename)
        if path in seen_paths:
            raise invalid_bundle("Skill archive contains duplicate file paths.")
        if _has_path_conflict(path, seen_paths):
            raise invalid_bundle("Skill archive contains conflicting file paths.")
        seen_paths.add(path)
        if info.flag_bits & 0x1:
            raise invalid_bundle("Encrypted skill archives are not supported.")
        dos_attributes = info.external_attr & 0x3F
        if dos_attributes & (0x08 | 0x10):
            raise invalid_bundle("Skill archive can contain regular files only.")
        unix_file_type = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir() or unix_file_type not in {0, stat.S_IFREG}:
            raise invalid_bundle("Skill archive can contain regular files only.")
        if info.file_size < 0:
            raise invalid_bundle("Skill archive file size is invalid.")
        total_declared += info.file_size
        if total_declared > limits.max_total_bytes:
            raise bundle_too_large("Skill bundle exceeds the total size limit.")
        if path == "SKILL.md":
            skill_markdown_count += 1
        else:
            supporting_file_count += 1
            if supporting_file_count > limits.max_files:
                raise bundle_too_large("Skill bundle exceeds the file count limit.")
            if info.file_size > limits.max_file_bytes:
                raise bundle_too_large("Skill bundle file exceeds the per-file size limit.")
    if skill_markdown_count != 1:
        raise invalid_bundle("Skill archive must contain exactly one root SKILL.md.")


def _has_path_conflict(path: str, paths: set[str]) -> bool:
    prefix = f"{path}/"
    return any(path.startswith(f"{existing}/") or existing.startswith(prefix) for existing in paths)


def _read_limited(
    stream: BinaryIO,
    limits: SkillBundleLimits,
    total_before: int,
    label: str,
    *,
    enforce_file_limit: bool,
) -> bytes:
    content = bytearray()
    try:
        while True:
            total_remaining = limits.max_total_bytes - total_before - len(content)
            file_remaining = (
                limits.max_file_bytes - len(content)
                if enforce_file_limit
                else total_remaining
            )
            allowed = min(file_remaining, total_remaining)
            if allowed < 0:
                raise bundle_too_large("Skill bundle exceeds the total size limit.")
            chunk = stream.read(min(_CHUNK_SIZE, allowed + 1))
            if not chunk:
                break
            if enforce_file_limit and len(chunk) > file_remaining:
                raise bundle_too_large(f"{label} exceeds the per-file size limit.")
            if len(chunk) > total_remaining:
                raise bundle_too_large("Skill bundle exceeds the total size limit.")
            content.extend(chunk)
    finally:
        stream.close()
    return bytes(content)


def _open_anchored_root_directory(root: Path) -> int:
    if not _supports_safe_directory_operations():
        raise invalid_bundle("Safe directory loading is not supported on this platform.")
    absolute_root = root.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd: int | None = None
    try:
        directory_fd = os.open("/", flags)
        for component in absolute_root.parts[1:]:
            child_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
            if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
                raise invalid_bundle("Skill bundle root must be a real directory.")
        return directory_fd
    except AppError:
        if directory_fd is not None:
            os.close(directory_fd)
        raise
    except OSError as exc:
        if directory_fd is not None:
            os.close(directory_fd)
        raise invalid_bundle("Skill bundle root must be a real directory.") from exc


def _supports_safe_directory_operations() -> bool:
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_dir_fd", set())
        and os.stat in getattr(os, "supports_follow_symlinks", set())
        and os.listdir in getattr(os, "supports_fd", set())
    )


def _open_child_directory(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise invalid_bundle("Skill bundle directory cannot be read safely.") from exc
    try:
        is_directory = stat.S_ISDIR(os.fstat(directory_fd).st_mode)
    except OSError as exc:
        os.close(directory_fd)
        raise invalid_bundle("Skill bundle directory cannot be inspected.") from exc
    if not is_directory:
        os.close(directory_fd)
        raise invalid_bundle("Skill bundle can contain regular files only.")
    return directory_fd


def _read_directory_file(
    parent_fd: int,
    name: str,
    limits: SkillBundleLimits,
    total_before: int,
    *,
    enforce_file_limit: bool,
) -> tuple[bytes, int]:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise invalid_bundle("Skill bundle file cannot be read safely.") from exc
    with os.fdopen(descriptor, "rb") as stream:
        try:
            file_stat = os.fstat(stream.fileno())
        except OSError as exc:
            raise invalid_bundle("Skill bundle file cannot be inspected.") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise invalid_bundle("Skill bundle can contain regular files only.")
        if file_stat.st_nlink > 1:
            raise invalid_bundle("Skill bundle cannot contain hard-linked files.")
        if enforce_file_limit and file_stat.st_size > limits.max_file_bytes:
            raise bundle_too_large("Skill bundle file exceeds the per-file size limit.")
        return (
            _read_limited(
                stream,
                limits,
                total_before,
                name,
                enforce_file_limit=enforce_file_limit,
            ),
            file_stat.st_size,
        )
