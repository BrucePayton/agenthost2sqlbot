import hashlib
import io
import json
import os
import re
import stat
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.errors import AppError
from app.skills.bundle import SkillBundleLimits, load_bundle_from_archive
from app.skills.models import SkillBundle

_ARTIFACT_KEY_RE = re.compile(
    r"^skills/sha256/(?P<prefix>[0-9a-f]{2})/(?P<digest>[0-9a-f]{64})\.zip$"
)
_BUNDLE_HASH_RE = re.compile(r"^sha256:(?P<digest>[0-9a-f]{64})$")
_ARTIFACT_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class SkillArtifact:
    artifact_key: str
    archive_bytes: bytes
    artifact_sha256: str
    bundle_hash: str
    manifest_json: str
    size_bytes: int


@dataclass(frozen=True)
class SkillArtifactObject:
    artifact_key: str
    size_bytes: int
    modified_at: datetime


class SkillArtifactStore(Protocol):
    def initialize(self) -> None: ...

    def put(self, artifact: SkillArtifact) -> None: ...

    def read(self, artifact_key: str, *, maximum_size: int) -> bytes: ...

    def exists(self, artifact_key: str) -> bool: ...

    def delete(self, artifact_key: str) -> None: ...

    def iter_objects(self) -> tuple[SkillArtifactObject, ...]: ...


class _Utf8ZipInfo(zipfile.ZipInfo):
    def _encodeFilenameFlags(self) -> tuple[bytes, int]:
        return self.filename.encode("utf-8"), self.flag_bits | 0x800


def build_skill_artifact(bundle: SkillBundle) -> SkillArtifact:
    entries = [("SKILL.md", bundle.content.encode("utf-8"))]
    entries.extend((item.path, item.content) for item in bundle.files)
    raw = _canonical_zip(tuple(sorted(entries)))
    digest = hashlib.sha256(raw).hexdigest()
    artifact_key = _artifact_key_for_bundle_hash(bundle.bundle_hash)
    if artifact_key is None:
        raise _artifact_corrupt()
    return SkillArtifact(
        artifact_key=artifact_key,
        archive_bytes=raw,
        artifact_sha256=f"sha256:{digest}",
        bundle_hash=bundle.bundle_hash,
        manifest_json=_manifest_json(bundle),
        size_bytes=len(raw),
    )


def artifact_key_matches_bundle(artifact_key: str, bundle_hash: str) -> bool:
    expected = _artifact_key_for_bundle_hash(bundle_hash)
    return expected is not None and artifact_key == expected


def load_skill_artifact(
    raw: bytes,
    *,
    expected_artifact_sha256: str,
    expected_bundle_hash: str,
    limits: SkillBundleLimits,
) -> SkillBundle:
    if not isinstance(raw, bytes) or _sha256(raw) != expected_artifact_sha256:
        raise _artifact_corrupt()
    try:
        bundle = load_bundle_from_archive(raw, limits)
    except AppError as exc:
        raise _artifact_corrupt() from exc
    if bundle.bundle_hash != expected_bundle_hash:
        raise _artifact_corrupt()
    return bundle


class FilesystemSkillArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def initialize(self) -> None:
        root = self._root_path()
        _mkdir_real_directory(root)

    def put(self, artifact: SkillArtifact) -> None:
        self._validate_artifact(artifact)
        target = self._target(artifact.artifact_key)
        _mkdir_real_directory(target.parent)
        self._assert_real_path(target.parent)
        if target.is_symlink():
            raise _artifact_corrupt()
        if target.exists():
            self._verify_existing(target, artifact)
            return
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(artifact.archive_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, artifact_key: str, *, maximum_size: int) -> bytes:
        if maximum_size < 0:
            raise _artifact_corrupt()
        target = self._target(artifact_key)
        try:
            target_stat = os.stat(target, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise _artifact_corrupt() from exc
        except OSError as exc:
            raise _artifact_corrupt() from exc
        if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_size > maximum_size:
            raise _artifact_corrupt()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags)
        except OSError as exc:
            raise _artifact_corrupt() from exc
        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_size > maximum_size
                or opened_stat.st_size != target_stat.st_size
            ):
                raise _artifact_corrupt()
            raw = _read_descriptor(descriptor, maximum_size)
            final_stat = os.fstat(descriptor)
        except OSError as exc:
            raise _artifact_corrupt() from exc
        finally:
            os.close(descriptor)
        if len(raw) != final_stat.st_size or final_stat.st_size > maximum_size:
            raise _artifact_corrupt()
        return raw

    def exists(self, artifact_key: str) -> bool:
        target = self._target(artifact_key)
        if target.is_symlink():
            raise _artifact_corrupt()
        try:
            return target.is_file()
        except OSError as exc:
            raise _artifact_corrupt() from exc

    def delete(self, artifact_key: str) -> None:
        target = self._target(artifact_key)
        if target.is_symlink():
            raise _artifact_corrupt()
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise _artifact_corrupt() from exc

    def iter_objects(self) -> tuple[SkillArtifactObject, ...]:
        root = self._root_path()
        self._assert_real_path(root)
        if not root.exists():
            return ()
        objects: list[SkillArtifactObject] = []
        for directory, names, files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            for name in names:
                child = directory_path / name
                if child.is_symlink():
                    raise _artifact_corrupt()
            for name in files:
                child = directory_path / name
                if child.is_symlink():
                    raise _artifact_corrupt()
                relative = child.relative_to(root).as_posix()
                if name.startswith(".") and name.endswith(".tmp"):
                    continue
                if _parse_artifact_key(relative) is None:
                    continue
                try:
                    child_stat = os.stat(child, follow_symlinks=False)
                except OSError as exc:
                    raise _artifact_corrupt() from exc
                if not stat.S_ISREG(child_stat.st_mode):
                    raise _artifact_corrupt()
                objects.append(
                    SkillArtifactObject(
                        artifact_key=relative,
                        size_bytes=child_stat.st_size,
                        modified_at=datetime.fromtimestamp(child_stat.st_mtime, tz=UTC),
                    )
                )
        return tuple(sorted(objects, key=lambda item: item.artifact_key))

    def _target(self, artifact_key: str) -> Path:
        _parse_artifact_key_or_raise(artifact_key)
        root = self._root_path()
        self._assert_real_path(root)
        target = root.joinpath(*artifact_key.split("/"))
        self._assert_real_path(target.parent)
        try:
            target.parent.relative_to(root)
        except ValueError as exc:
            raise _artifact_corrupt() from exc
        return target

    def _root_path(self) -> Path:
        return self.root.expanduser().absolute()

    def _assert_real_path(self, path: Path) -> None:
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for component in absolute.parts[1:]:
            current /= component
            if not current.exists() and not current.is_symlink():
                break
            if current.is_symlink():
                raise _artifact_corrupt()
            try:
                if not current.is_dir():
                    raise _artifact_corrupt()
            except OSError as exc:
                raise _artifact_corrupt() from exc

    def _validate_artifact(self, artifact: SkillArtifact) -> None:
        match = _parse_artifact_key_or_raise(artifact.artifact_key)
        if (
            not isinstance(artifact.archive_bytes, bytes)
            or artifact.size_bytes != len(artifact.archive_bytes)
            or artifact.artifact_sha256 != _sha256(artifact.archive_bytes)
            or artifact.bundle_hash != f"sha256:{match.group('digest')}"
        ):
            raise _artifact_corrupt()

    def _verify_existing(self, target: Path, artifact: SkillArtifact) -> None:
        raw = self.read(artifact.artifact_key, maximum_size=artifact.size_bytes)
        if len(raw) != artifact.size_bytes or _sha256(raw) != artifact.artifact_sha256:
            raise _artifact_corrupt()


def _canonical_zip(entries: tuple[tuple[str, bytes], ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        archive.comment = b""
        for path, content in entries:
            info = _Utf8ZipInfo(path, date_time=_ARTIFACT_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.extra = b""
            info.comment = b""
            info.flag_bits = 0x800
            archive.writestr(
                info,
                content,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    return output.getvalue()


def _manifest_json(bundle: SkillBundle) -> str:
    files = [
        {
            "path": "SKILL.md",
            "sha256": _sha256(bundle.content.encode("utf-8")),
            "size_bytes": len(bundle.content.encode("utf-8")),
        }
    ]
    files.extend(
        {
            "path": item.path,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        }
        for item in bundle.files
    )
    return json.dumps(
        {"files": sorted(files, key=lambda item: item["path"])},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _artifact_corrupt() -> AppError:
    return AppError(
        "skill_artifact_corrupt",
        "Skill artifact integrity check failed.",
        500,
    )


def _parse_artifact_key(artifact_key: str) -> re.Match[str] | None:
    if not isinstance(artifact_key, str):
        return None
    match = _ARTIFACT_KEY_RE.fullmatch(artifact_key)
    if match is None or match.group("prefix") != match.group("digest")[:2]:
        return None
    return match


def _parse_artifact_key_or_raise(artifact_key: str) -> re.Match[str]:
    match = _parse_artifact_key(artifact_key)
    if match is None:
        raise _artifact_corrupt()
    return match


def _artifact_key_for_bundle_hash(bundle_hash: str) -> str | None:
    if not isinstance(bundle_hash, str):
        return None
    match = _BUNDLE_HASH_RE.fullmatch(bundle_hash)
    if match is None:
        return None
    digest = match.group("digest")
    return f"skills/sha256/{digest[:2]}/{digest}.zip"


def _mkdir_real_directory(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.is_symlink():
            raise _artifact_corrupt()
        try:
            current.mkdir(exist_ok=True)
        except OSError as exc:
            raise _artifact_corrupt() from exc
        if current.is_symlink() or not current.is_dir():
            raise _artifact_corrupt()


def _read_descriptor(descriptor: int, maximum_size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(_CHUNK_SIZE, maximum_size - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_size:
            raise _artifact_corrupt()
        chunks.append(chunk)
    return b"".join(chunks)


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
