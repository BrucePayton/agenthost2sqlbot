import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from app.errors import AppError
from app.workspaces.models import (
    McpHttpServerManifest,
    McpSseServerManifest,
    McpStdioServerManifest,
    WorkspaceEntry,
    WorkspaceManifest,
)


class WorkspaceRegistry:
    def __init__(
        self,
        root: Path,
        default_model: str,
        environ: Mapping[str, str] | None = None,
        *,
        allow_loopback_http_mcp: bool = False,
    ) -> None:
        self.root = root
        self.default_model = default_model
        self.environ = environ if environ is not None else os.environ
        self.allow_loopback_http_mcp = allow_loopback_http_mcp
        self._entries: dict[str, WorkspaceEntry] = {}

    def scan(self) -> list[WorkspaceEntry]:
        entries: list[WorkspaceEntry] = []
        for directory in sorted(self.root.iterdir(), key=lambda path: path.name):
            if not directory.is_dir() and not directory.is_symlink():
                continue
            entries.append(self._load(directory))

        ids: dict[str, list[int]] = {}
        for index, entry in enumerate(entries):
            manifest_id = entry.manifest.id if entry.manifest else entry.id
            ids.setdefault(manifest_id, []).append(index)
        for manifest_id, indexes in ids.items():
            if len(indexes) < 2:
                continue
            for index in indexes:
                entry = entries[index]
                entries[index] = WorkspaceEntry(
                    id=entry.id,
                    directory=entry.directory,
                    available=False,
                    name=entry.name,
                    description=entry.description,
                    manifest=entry.manifest,
                    validation_errors=(
                        *entry.validation_errors,
                        f"workspace id {manifest_id!r} is duplicated",
                    ),
                    snapshot_json=entry.snapshot_json,
                    snapshot_hash=entry.snapshot_hash,
                )

        self._entries = {entry.id: entry for entry in entries}
        return entries

    def all(self) -> list[WorkspaceEntry]:
        if not self._entries:
            return self.scan()
        return list(self._entries.values())

    def get(self, workspace_id: str, *, require_available: bool = True) -> WorkspaceEntry:
        if not self._entries:
            self.scan()
        entry = self._entries.get(workspace_id)
        if entry is None:
            raise AppError("workspace_not_found", "Workspace not found.", 404)
        if require_available and not entry.available:
            raise AppError(
                "workspace_invalid",
                "Workspace configuration is invalid.",
                409,
                {"validation_errors": list(entry.validation_errors)},
            )
        return entry

    def _load(self, directory: Path) -> WorkspaceEntry:
        errors: list[str] = []
        if directory.is_symlink():
            errors.append("workspace directory must not be a symbolic link")
        manifest_path = directory / "workspace.yaml"
        if manifest_path.is_symlink():
            errors.append("workspace.yaml must not be a symbolic link")
        if not manifest_path.is_file():
            errors.append("workspace.yaml does not exist")
            return self._invalid_entry(directory, errors)

        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest = WorkspaceManifest.model_validate(raw)
        except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
            errors.append(self._format_error(exc))
            return self._invalid_entry(directory, errors)

        if manifest.id != directory.name:
            errors.append("workspace id must match directory name")
        if manifest.model is None:
            manifest = manifest.model_copy(update={"model": self.default_model})

        skills_source_root: Path | None = directory / ".claude" / "skills"
        link_skills = manifest.skills_root_env is not None
        if manifest.skills_root_env:
            configured_root = self.environ.get(manifest.skills_root_env)
            if not configured_root:
                skills_source_root = None
            else:
                candidate = Path(configured_root).expanduser()
                if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
                    skills_source_root = None
                else:
                    skills_source_root = candidate.resolve()

        for server_name, server in manifest.mcp_servers.items():
            if isinstance(server, (McpHttpServerManifest, McpSseServerManifest)):
                self._validate_http_server(server_name, server, errors)
            elif isinstance(server, McpStdioServerManifest):
                self._validate_stdio_server(server_name, server, errors)

        snapshot_json = json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_hash = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
        return WorkspaceEntry(
            id=directory.name,
            directory=directory,
            available=not errors,
            name=manifest.name,
            description=manifest.description,
            manifest=manifest,
            validation_errors=tuple(errors),
            snapshot_json=snapshot_json,
            snapshot_hash=snapshot_hash,
            skills_source_root=skills_source_root,
            link_skills=link_skills,
        )

    def _validate_http_server(
        self,
        server_name: str,
        server: McpHttpServerManifest | McpSseServerManifest,
        errors: list[str],
    ) -> None:
        url = self.environ.get(server.url_env)
        if not url:
            errors.append(
                f"MCP server {server_name!r} requires environment variable "
                f"{server.url_env}"
            )
        else:
            if not self._is_valid_http_mcp_url(url):
                errors.append(
                    f"MCP server {server_name!r} environment variable "
                    f"{server.url_env} must contain a valid HTTPS or explicit "
                    "loopback HTTP URL"
                )

        authorization_env = server.authorization_env
        if authorization_env and not self.environ.get(authorization_env):
            errors.append(
                f"MCP server {server_name!r} requires environment variable "
                f"{authorization_env}"
            )

    def _is_valid_http_mcp_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        if parsed.scheme == "https":
            return True
        return (
            self.allow_loopback_http_mcp
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and port is not None
        )

    def _validate_stdio_server(
        self,
        server_name: str,
        server: McpStdioServerManifest,
        errors: list[str],
    ) -> None:
        command = shutil.which(
            server.command,
            path=self.environ.get("PATH", ""),
        )
        if command is None:
            errors.append(
                f"MCP server {server_name!r} command {server.command!r} "
                "is not executable"
            )

        entrypoint_value = self.environ.get(server.entrypoint_env)
        if not entrypoint_value:
            errors.append(
                f"MCP server {server_name!r} requires environment variable "
                f"{server.entrypoint_env}"
            )
        else:
            entrypoint = Path(entrypoint_value)
            if not entrypoint.is_absolute():
                errors.append(
                    f"MCP server {server_name!r} environment variable "
                    f"{server.entrypoint_env} must point to an absolute file"
                )
            elif entrypoint.is_symlink():
                errors.append(
                    f"MCP server {server_name!r} entrypoint must not be a "
                    "symbolic link"
                )
            elif not entrypoint.is_file():
                errors.append(
                    f"MCP server {server_name!r} environment variable "
                    f"{server.entrypoint_env} must point to a regular file"
                )

        for env_name in server.env_vars:
            if not self.environ.get(env_name):
                errors.append(
                    f"MCP server {server_name!r} requires environment variable "
                    f"{env_name}"
                )

    @staticmethod
    def _format_error(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                for error in exc.errors()
            )
        return str(exc)

    @staticmethod
    def _invalid_entry(directory: Path, errors: list[str]) -> WorkspaceEntry:
        return WorkspaceEntry(
            id=directory.name,
            directory=directory,
            available=False,
            name=directory.name,
            description="",
            manifest=None,
            validation_errors=tuple(errors),
        )
