import re
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.multipart import MultipartParseError, parse_options_header

from app.errors import AppError
from app.skills.bundle import (
    SkillBundleLimits,
    _validate_relative_path,
    bundle_too_large,
    invalid_bundle,
)

_MAX_MULTIPART_HEADERS = 32
_MAX_MULTIPART_HEADER_BYTES = 8 * 1024
_MAX_MULTIPART_HEADERS_BYTES = 32 * 1024
_MAX_MULTIPART_PREFILE_BYTES = 64 * 1024
_MAX_TEXT_FIELD_BYTES = 1024
_IMPORT_TEXT_FIELDS = {"on_conflict", "expected_hash", "target_name"}
_CONFLICT_POLICIES = {"fail", "overwrite", "rename"}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ArchiveUpload:
    raw: bytes
    on_conflict: Literal["fail", "overwrite", "rename"]
    expected_hash: str | None
    target_name: str | None


@dataclass(frozen=True)
class DirectoryUpload:
    files: tuple[tuple[str, bytes], ...]
    on_conflict: Literal["fail", "overwrite", "rename"]
    expected_hash: str | None
    target_name: str | None


async def read_multipart_archive(
    request: Request, maximum_size: int
) -> ArchiveUpload:
    return await _consume_multipart(
        request,
        _ArchiveMultipartCollector(maximum_size),
        invalid_message="Skill archive is invalid.",
    )


async def read_multipart_directory(
    request: Request,
    limits: SkillBundleLimits,
) -> DirectoryUpload:
    return await _consume_multipart(
        request,
        _DirectoryMultipartCollector(limits),
        invalid_message="Skill directory upload is invalid.",
    )


async def _consume_multipart(
    request: Request,
    collector: Any,
    *,
    invalid_message: str,
) -> Any:
    content_type = request.headers.get("content-type")
    if not content_type:
        raise invalid_bundle("Skill import requires multipart form data.")
    received_bytes = 0
    try:
        media_type, params = parse_options_header(content_type.encode("latin-1"))
        boundary = params[b"boundary"]
        if media_type.lower() != b"multipart/form-data" or not boundary:
            raise ValueError("invalid multipart content type")
        parser = MultipartParser(
            boundary,
            collector.callbacks,
            max_header_count=_MAX_MULTIPART_HEADERS,
            max_header_size=_MAX_MULTIPART_HEADER_BYTES,
        )
        async for chunk in request.stream():
            collector.begin_chunk(received_bytes)
            received_bytes += len(chunk)
            parser.write(chunk)
            if collector.ended:
                break
            if collector.prefile_exhausted(received_bytes):
                raise bundle_too_large("Skill multipart overhead exceeds the size limit.")
        parser.finalize()
    except AppError:
        raise
    except MultipartParseError as exc:
        if (
            str(exc).startswith("Maximum header ")
            or collector.prefile_exhausted(received_bytes)
        ):
            raise bundle_too_large(
                "Skill multipart overhead exceeds the size limit."
            ) from exc
        raise invalid_bundle(invalid_message) from exc
    except (UnicodeError, ValueError, KeyError) as exc:
        raise invalid_bundle(invalid_message) from exc
    return collector.finish()


class _DirectoryMultipartCollector:
    def __init__(self, limits: SkillBundleLimits) -> None:
        self.limits = limits
        self.files: list[tuple[str, bytes]] = []
        self.text_values: dict[str, str] = {}
        self.seen_paths: set[str] = set()
        self.current_kind: str | None = None
        self.current_name: str | None = None
        self.current_filename: str | None = None
        self.current_is_root = False
        self.current_content = bytearray()
        self.file_count = 0
        self.supporting_file_count = 0
        self.total_file_bytes = 0
        self.ended = False
        self.file_data_started = False
        self.chunk_offset = 0
        self.header_count = 0
        self.header_bytes = 0
        self.current_header_bytes = 0
        self.aggregate_header_limit = max(
            _MAX_MULTIPART_HEADERS_BYTES,
            (limits.max_files + 4) * 1024,
        )
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.callbacks = {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_end": self.on_end,
        }

    def begin_chunk(self, received_before: int) -> None:
        self.chunk_offset = received_before

    def prefile_exhausted(self, received_bytes: int) -> bool:
        return (
            not self.file_data_started
            and received_bytes > _MAX_MULTIPART_PREFILE_BYTES
        )

    def on_part_begin(self) -> None:
        self.current_kind = None
        self.current_name = None
        self.current_filename = None
        self.current_is_root = False
        self.current_content.clear()
        self.header_count = 0
        self.header_field.clear()
        self.header_value.clear()
        self.current_header_bytes = 0
        self.headers = {}

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self.current_kind is None:
            raise invalid_bundle("Skill directory multipart part is invalid.")
        if not self.file_data_started and self.chunk_offset + start > _MAX_MULTIPART_PREFILE_BYTES:
            raise bundle_too_large("Skill multipart overhead exceeds the size limit.")
        chunk = data[start:end]
        if self.current_kind == "field":
            if len(self.current_content) + len(chunk) > _MAX_TEXT_FIELD_BYTES:
                raise bundle_too_large("Skill multipart field exceeds the size limit.")
            self.current_content.extend(chunk)
            return

        self.file_data_started = True
        if (
            not self.current_is_root
            and len(self.current_content) + len(chunk) > self.limits.max_file_bytes
        ):
            raise bundle_too_large("Skill bundle file exceeds the per-file size limit.")
        if self.total_file_bytes + len(chunk) > self.limits.max_total_bytes:
            raise bundle_too_large("Skill bundle exceeds the total size limit.")
        self.current_content.extend(chunk)
        self.total_file_bytes += len(chunk)

    def on_part_end(self) -> None:
        if self.current_kind == "file" and self.current_filename is not None:
            self.files.append((self.current_filename, bytes(self.current_content)))
            return
        if self.current_kind == "field" and self.current_name is not None:
            try:
                value = self.current_content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise invalid_bundle("Skill multipart field is invalid.") from exc
            if self.current_name == "on_conflict" and value not in _CONFLICT_POLICIES:
                raise invalid_bundle("Skill import conflict policy is invalid.")
            self.text_values[self.current_name] = value
            return
        raise invalid_bundle("Skill directory multipart part is invalid.")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._add_header_bytes(end - start)
        self.header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._add_header_bytes(end - start)
        self.header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        self.header_count += 1
        if self.header_count > _MAX_MULTIPART_HEADERS:
            raise bundle_too_large("Skill multipart headers exceed the size limit.")
        field = bytes(self.header_field).lower()
        if not field or field in self.headers:
            raise invalid_bundle("Skill directory headers are invalid.")
        self.headers[field] = bytes(self.header_value)
        self.header_field.clear()
        self.header_value.clear()
        self.current_header_bytes = 0

    def _add_header_bytes(self, size: int) -> None:
        self.current_header_bytes += size
        self.header_bytes += size
        if (
            self.current_header_bytes > _MAX_MULTIPART_HEADER_BYTES
            or self.header_bytes > self.aggregate_header_limit
        ):
            raise bundle_too_large("Skill multipart headers exceed the size limit.")

    def on_headers_finished(self) -> None:
        try:
            disposition, options = parse_options_header(
                self.headers[b"content-disposition"]
            )
            name = options[b"name"].decode("utf-8")
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            raise invalid_bundle("Skill directory headers are invalid.") from exc
        if disposition.lower() != b"form-data":
            raise invalid_bundle("Skill directory headers are invalid.")

        filename_raw = options.get(b"filename")
        if filename_raw is None:
            if name not in _IMPORT_TEXT_FIELDS or name in self.text_values:
                raise invalid_bundle("Skill directory multipart field is invalid.")
            self.current_kind = "field"
            self.current_name = name
            return

        if name != "files":
            raise invalid_bundle("Skill directory files must use the files field.")
        try:
            filename = filename_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise invalid_bundle("Skill directory filename is invalid.") from exc
        normalized = _validate_relative_path(filename)
        if normalized in self.seen_paths:
            raise invalid_bundle("Skill directory contains duplicate file paths.")
        self.seen_paths.add(normalized)
        parts = normalized.split("/")
        self.current_is_root = len(parts) >= 2 and "/".join(parts[1:]) == "SKILL.md"
        self.file_count += 1
        if self.file_count > self.limits.max_files + 1:
            raise bundle_too_large("Skill bundle exceeds the file count limit.")
        if not self.current_is_root:
            self.supporting_file_count += 1
            if self.supporting_file_count > self.limits.max_files:
                raise bundle_too_large("Skill bundle exceeds the file count limit.")
        self.current_kind = "file"
        self.current_filename = normalized

    def on_end(self) -> None:
        self.ended = True

    def finish(self) -> DirectoryUpload:
        if not self.ended or not self.files:
            raise invalid_bundle("Skill directory upload is invalid.")
        on_conflict, expected_hash, target_name = _validated_control_fields(
            self.text_values
        )
        return DirectoryUpload(
            files=tuple(self.files),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
        )


class _ArchiveMultipartCollector:
    def __init__(self, maximum_size: int) -> None:
        self.maximum_size = maximum_size
        self.archive_content = bytearray()
        self.current_content = bytearray()
        self.text_values: dict[str, str] = {}
        self.file_seen = False
        self.current_kind: Literal["archive", "field"] | None = None
        self.current_name: str | None = None
        self.part_complete = False
        self.ended = False
        self.archive_data_started = False
        self.chunk_offset = 0
        self.header_count = 0
        self.header_bytes = 0
        self.current_header_bytes = 0
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.callbacks = {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_end": self.on_end,
        }

    def begin_chunk(self, received_before: int) -> None:
        self.chunk_offset = received_before

    def prefile_exhausted(self, received_bytes: int) -> bool:
        return (
            not self.archive_data_started
            and received_bytes > _MAX_MULTIPART_PREFILE_BYTES
        )

    def on_part_begin(self) -> None:
        self.current_kind = None
        self.current_name = None
        self.current_content.clear()
        self.header_field.clear()
        self.header_value.clear()
        self.current_header_bytes = 0
        self.headers = {}

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self.current_kind is None:
            raise invalid_bundle("Skill import must contain exactly one archive file.")
        chunk = data[start:end]
        if self.current_kind == "field":
            if len(self.current_content) + len(chunk) > _MAX_TEXT_FIELD_BYTES:
                raise bundle_too_large("Skill multipart field exceeds the size limit.")
            self.current_content.extend(chunk)
            return
        if not self.archive_data_started:
            if self.chunk_offset + start > _MAX_MULTIPART_PREFILE_BYTES:
                raise bundle_too_large(
                    "Skill multipart overhead exceeds the size limit."
                )
            self.archive_data_started = True
        if len(self.archive_content) + len(chunk) > self.maximum_size:
            raise bundle_too_large("Skill archive exceeds the total size limit.")
        self.archive_content.extend(chunk)

    def on_part_end(self) -> None:
        if self.current_kind == "archive":
            self.part_complete = True
            return
        if self.current_kind == "field" and self.current_name is not None:
            try:
                value = self.current_content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise invalid_bundle("Skill multipart field is invalid.") from exc
            if self.current_name == "on_conflict" and value not in _CONFLICT_POLICIES:
                raise invalid_bundle("Skill import conflict policy is invalid.")
            self.text_values[self.current_name] = value
            return
        raise invalid_bundle("Skill import must contain exactly one archive file.")

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._add_header_bytes(end - start, end)
        self.header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._add_header_bytes(end - start, end)
        self.header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        self.header_count += 1
        if self.header_count > _MAX_MULTIPART_HEADERS:
            raise bundle_too_large("Skill multipart headers exceed the size limit.")
        field = bytes(self.header_field).lower()
        if not field or field in self.headers:
            raise invalid_bundle("Skill archive headers are invalid.")
        self.headers[field] = bytes(self.header_value)
        self.header_field.clear()
        self.header_value.clear()
        self.current_header_bytes = 0

    def _add_header_bytes(self, size: int, chunk_end: int) -> None:
        self.current_header_bytes += size
        self.header_bytes += size
        if (
            self.current_header_bytes > _MAX_MULTIPART_HEADER_BYTES
            or self.header_bytes > _MAX_MULTIPART_HEADERS_BYTES
            or self.chunk_offset + chunk_end > _MAX_MULTIPART_PREFILE_BYTES
        ):
            raise bundle_too_large("Skill multipart headers exceed the size limit.")

    def on_headers_finished(self) -> None:
        try:
            disposition, options = parse_options_header(
                self.headers[b"content-disposition"]
            )
            name = options[b"name"].decode("utf-8")
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            raise invalid_bundle("Skill archive headers are invalid.") from exc
        if disposition.lower() != b"form-data":
            raise invalid_bundle("Skill archive headers are invalid.")

        filename_raw = options.get(b"filename")
        if filename_raw is None:
            if name not in _IMPORT_TEXT_FIELDS or name in self.text_values:
                raise invalid_bundle("Skill archive multipart field is invalid.")
            self.current_kind = "field"
            self.current_name = name
            return

        try:
            filename = filename_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise invalid_bundle("Skill archive filename is invalid.") from exc
        if self.file_seen or name != "archive" or not _is_allowed_archive_filename(
            filename
        ):
            raise invalid_bundle(
                "Skill import must contain exactly one .skill or .zip file."
            )
        self.file_seen = True
        self.current_kind = "archive"

    def on_end(self) -> None:
        self.ended = True

    def finish(self) -> ArchiveUpload:
        if not self.ended or not self.file_seen or not self.part_complete:
            raise invalid_bundle("Skill archive is invalid.")
        on_conflict, expected_hash, target_name = _validated_control_fields(
            self.text_values
        )
        return ArchiveUpload(
            raw=bytes(self.archive_content),
            on_conflict=on_conflict,
            expected_hash=expected_hash,
            target_name=target_name,
        )


def _validated_control_fields(
    values: dict[str, str],
) -> tuple[
    Literal["fail", "overwrite", "rename"],
    str | None,
    str | None,
]:
    on_conflict = values.get("on_conflict", "fail")
    expected_hash = values.get("expected_hash")
    target_name = values.get("target_name")
    if on_conflict == "fail" and (
        expected_hash is not None or target_name is not None
    ):
        raise invalid_bundle("Skill import conflict fields are invalid.")
    if on_conflict == "overwrite" and (
        expected_hash is None
        or not _HASH_RE.fullmatch(expected_hash)
        or target_name is not None
    ):
        raise invalid_bundle("Skill overwrite fields are invalid.")
    if on_conflict == "rename" and (
        target_name is None or expected_hash is not None
    ):
        raise invalid_bundle("Skill rename fields are invalid.")
    if on_conflict not in _CONFLICT_POLICIES:
        raise invalid_bundle("Skill import conflict policy is invalid.")
    return on_conflict, expected_hash, target_name


def _is_allowed_archive_filename(filename: str) -> bool:
    return (
        bool(filename)
        and "\x00" not in filename
        and "/" not in filename
        and "\\" not in filename
        and filename.lower().endswith((".skill", ".zip"))
    )
