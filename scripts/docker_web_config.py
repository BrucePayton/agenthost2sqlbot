from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from dotenv import dotenv_values

from app.sandbox.credentials import parse_model_endpoint
from app.sandbox.models import validate_allowed_host

Mode = Literal["fake", "claude"]
_PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MARKER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RenderedConfig:
    runtime_dir: Path
    compose_env: Path
    api_env: Path
    worker_env: Path
    opensandbox_env: Path
    marker: Path


def _validate_project_name(value: str) -> str:
    if _PROJECT_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "project name must use lowercase letters, digits, hyphens, or underscores"
        )
    return value


def _validate_image_id(name: str, value: str) -> str:
    if _IMAGE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be an immutable sha256:<64-hex> image ID")
    return value


def _validate_scalar(name: str, value: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} cannot be blank")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{name} cannot contain control characters")
    return value.strip()


def _read_source(source_path: Path | None) -> dict[str, str]:
    if source_path is None:
        return {}
    if source_path.is_symlink() or not source_path.is_file():
        raise ValueError("operator env file must be an existing regular file")
    values = dotenv_values(source_path)
    return {
        key: _validate_scalar(key, value)
        for key, value in values.items()
        if value is not None and value.strip()
    }


def _parse_port(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name, str(default))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer port") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return port


def _parse_positive_int(
    values: Mapping[str, str], name: str, default: int, *, minimum: int = 1
) -> int:
    raw = values.get(name, str(default))
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return parsed


def _parse_allowed_hosts(raw: str) -> tuple[str, ...]:
    candidate = raw.strip()
    if candidate.startswith("["):
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("OPENSANDBOX_ALLOWED_HOSTS must be valid JSON") from exc
        if not isinstance(decoded, list) or not all(
            isinstance(item, str) for item in decoded
        ):
            raise ValueError("OPENSANDBOX_ALLOWED_HOSTS must be a string list")
        items = decoded
    else:
        items = candidate.split(",")
    try:
        normalized = tuple(
            dict.fromkeys(validate_allowed_host(item.strip()) for item in items)
        )
    except ValueError as exc:
        raise ValueError("OPENSANDBOX_ALLOWED_HOSTS contains an unsafe host") from exc
    if not normalized:
        raise ValueError("OPENSANDBOX_ALLOWED_HOSTS cannot be empty")
    return normalized


def _quote_env(value: object) -> str:
    normalized = _validate_scalar("environment value", str(value))
    return "'" + normalized.replace("'", "\\'") + "'"


def _env_text(values: Mapping[str, object]) -> str:
    return "".join(f"{key}={_quote_env(value)}\n" for key, value in values.items())


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to replace symbolic link: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(mode)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _existing_secret(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"existing {path.name} must be a regular file")
    value = dotenv_values(path).get(name)
    if value is None:
        return None
    if _SECRET_PATTERN.fullmatch(value) is None:
        raise ValueError(f"existing {name} is malformed; refusing secret rotation")
    return value


def _persistent_secret(path: Path, name: str) -> str:
    return _existing_secret(path, name) or secrets.token_hex(32)


def _prepare_runtime_dir(runtime_dir: Path) -> Path:
    expanded = runtime_dir.expanduser()
    if expanded.exists() and expanded.is_symlink():
        raise ValueError("runtime directory must not be a symbolic link")
    expanded.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = expanded.resolve()
    resolved.chmod(0o700)
    return resolved


def _validate_marker(marker: Path, project_name: str) -> None:
    if not marker.exists():
        return
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("deployment marker must be a regular file")
    try:
        current = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("deployment marker is invalid") from exc
    if current != {
        "schema_version": _MARKER_SCHEMA_VERSION,
        "project_name": project_name,
    }:
        raise ValueError("runtime directory belongs to another project")


def render_runtime_config(
    *,
    mode: Mode,
    source_path: Path | None,
    runtime_dir: Path,
    app_image: str,
    runner_image: str,
    project_name: str,
) -> RenderedConfig:
    if mode not in ("fake", "claude"):
        raise ValueError("mode must be fake or claude")
    project_name = _validate_project_name(project_name)
    app_image = _validate_image_id("application image", app_image)
    runner_image = _validate_image_id("Runner image", runner_image)
    source = _read_source(source_path)
    # Keep the operator file authoritative across restarts/re-renders. Only
    # recognized integration settings can cross into the API/Worker environment.
    from app.config import Settings

    integration_names = {
        field.validation_alias for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
        and field.validation_alias.startswith(("DATA_AGENT_", "SQLBOT_", "STARROCKS_"))
    }
    integration = {key: value for key, value in source.items() if key in integration_names}
    manifest = integration.get("DATA_AGENT_STARROCKS_MANIFEST")
    mounts = []
    if manifest:
        manifest_path = Path(manifest).expanduser()
        if not manifest_path.is_absolute() and source_path is not None:
            manifest_path = source_path.parent / manifest_path
        if not manifest_path.is_file():
            raise ValueError("DATA_AGENT_STARROCKS_MANIFEST must be an existing file on the host")
        integration["DATA_AGENT_STARROCKS_MANIFEST"] = "/app/starrocks-manifest.json"
        mounts = [{"type": "bind", "source": str(manifest_path.resolve()),
                   "target": "/app/starrocks-manifest.json", "read_only": True}]
    target = _prepare_runtime_dir(runtime_dir)
    rendered = RenderedConfig(
        runtime_dir=target,
        compose_env=target / "compose.env",
        api_env=target / "api.env",
        worker_env=target / "worker.env",
        opensandbox_env=target / "opensandbox.env",
        marker=target / "deployment.json",
    )
    _validate_marker(rendered.marker, project_name)

    if mode == "claude":
        for required in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_API_KEY",
            "OPENSANDBOX_ALLOWED_HOSTS",
        ):
            if required not in source:
                raise ValueError(f"Claude mode requires {required}")
        base_url = source["ANTHROPIC_BASE_URL"]
        model_key = source["ANTHROPIC_API_KEY"]
        allowed_hosts = _parse_allowed_hosts(source["OPENSANDBOX_ALLOWED_HOSTS"])
        parse_model_endpoint(base_url, allowed_hosts)
    else:
        base_url = source.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        model_key = None
        allowed_hosts = _parse_allowed_hosts(
            source.get("OPENSANDBOX_ALLOWED_HOSTS", "api.anthropic.com")
        )

    postgres_password = _persistent_secret(
        rendered.compose_env, "DOCKER_WEB_POSTGRES_PASSWORD"
    )
    opensandbox_key = _persistent_secret(
        rendered.opensandbox_env, "OPENSANDBOX_SERVER_API_KEY"
    )
    web_port = _parse_port(source, "DOCKER_WEB_PORT", 8765)
    postgres_port = _parse_port(source, "DOCKER_WEB_POSTGRES_PORT", 55432)
    opensandbox_port = _parse_port(source, "DOCKER_WEB_OPENSANDBOX_PORT", 58080)
    model = source.get("DOCKER_WEB_MODEL", "claude-sonnet-4-6")
    idle_ttl = _parse_positive_int(
        source, "OPENSANDBOX_IDLE_TTL_SECONDS", 300, minimum=30
    )
    sandbox_timeout = _parse_positive_int(
        source, "OPENSANDBOX_SANDBOX_TIMEOUT_SECONDS", 14400, minimum=60
    )
    concurrency = _parse_positive_int(source, "OPENSANDBOX_WORKER_CONCURRENCY", 4)
    heartbeat_interval = _parse_positive_int(
        source, "WORKER_HEARTBEAT_INTERVAL_SECONDS", 5
    )
    heartbeat_stale = _parse_positive_int(source, "WORKER_HEARTBEAT_STALE_SECONDS", 15)
    if heartbeat_stale < heartbeat_interval * 2:
        raise ValueError(
            "WORKER_HEARTBEAT_STALE_SECONDS must be at least twice the interval"
        )

    database_url = (
        "postgresql+asyncpg://workspace:"
        f"{quote(postgres_password, safe='')}@postgres:5432/workspace"
    )
    _atomic_write(
        rendered.compose_env,
        _env_text(
            {
                "COMPOSE_PROJECT_NAME": project_name,
                "DOCKER_WEB_APP_IMAGE": app_image,
                "DOCKER_WEB_RUNNER_IMAGE": runner_image,
                "DOCKER_WEB_API_ENV_FILE": rendered.api_env,
                "DOCKER_WEB_WORKER_ENV_FILE": rendered.worker_env,
                "DOCKER_WEB_OPENSANDBOX_ENV_FILE": rendered.opensandbox_env,
                "DOCKER_WEB_PORT": web_port,
                "DOCKER_WEB_POSTGRES_PORT": postgres_port,
                "DOCKER_WEB_OPENSANDBOX_PORT": opensandbox_port,
                "DOCKER_WEB_POSTGRES_PASSWORD": postgres_password,
            }
        ),
    )
    _atomic_write(
        rendered.api_env,
        _env_text(
            {
                "ANTHROPIC_BASE_URL": base_url,
                "CLAUDE_MODEL": model,
                "WORKSPACES_ROOT": "/app/workspaces",
                "APP_DATA_DIR": "/var/lib/workspace-agent",
                "APP_ENV": "development",
                "APP_IDENTITY_MODE": "mock",
                "MOCK_USER_ID": "mock-user",
                "MOCK_USER_SUBJECT": "mock-user",
                "MOCK_USER_DISPLAY_NAME": "Mock User",
                "MOCK_PERSONAL_WORKSPACE_ID": "example",
                "MOCK_WORKSPACE_ROLES": '{"example":"owner","data-question":"owner"}'
                if integration.get("DATA_AGENT_ENABLED", "").lower() in {"true", "1"}
                else '{"example":"owner"}',
                "APP_RUNTIME_MODE": "opensandbox_docker",
                "APP_RUNTIME_COHORT": "docker-web",
                "APP_RUNTIME_IMAGE_DIGEST": runner_image,
                "APP_RUNTIME_PROTOCOL_VERSION": "1",
                "DATABASE_URL": database_url,
                "OPENSANDBOX_API_URL": "http://opensandbox-server:8080",
                "OPENSANDBOX_RUNNER_RUNTIME": mode,
                "OPENSANDBOX_ALLOWED_HOSTS": json.dumps(
                    allowed_hosts, separators=(",", ":")
                ),
                "OPENSANDBOX_IDLE_TTL_SECONDS": idle_ttl,
                "OPENSANDBOX_SANDBOX_TIMEOUT_SECONDS": sandbox_timeout,
                "OPENSANDBOX_WORKER_CONCURRENCY": concurrency,
                "WORKER_HEARTBEAT_INTERVAL_SECONDS": heartbeat_interval,
                "WORKER_HEARTBEAT_STALE_SECONDS": heartbeat_stale,
                **integration,
            }
        ),
    )
    worker_values: dict[str, object] = {
        "OPENSANDBOX_API_KEY": opensandbox_key,
        "OPENSANDBOX_RUNNER_IMAGE": runner_image,
        "OPENSANDBOX_RUNNER_RUNTIME": mode,
    }
    if model_key is not None:
        worker_values["ANTHROPIC_API_KEY"] = model_key
    _atomic_write(rendered.worker_env, _env_text(worker_values))
    _atomic_write(
        target / "data-agent.compose.json",
        json.dumps({"services": {name: {"volumes": mounts}
                                 for name in ("api", "worker", "migrate")}}) + "\n",
    )
    _atomic_write(
        rendered.opensandbox_env,
        _env_text({"OPENSANDBOX_SERVER_API_KEY": opensandbox_key}),
    )
    _atomic_write(
        rendered.marker,
        json.dumps(
            {
                "schema_version": _MARKER_SCHEMA_VERSION,
                "project_name": project_name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return rendered


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render secret-separated Docker Web runtime configuration."
    )
    parser.add_argument("--mode", choices=("fake", "claude"), required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--app-image", required=True)
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--project-name", required=True)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    render_runtime_config(
        mode=args.mode,
        source_path=args.source,
        runtime_dir=args.runtime_dir,
        app_image=args.app_image,
        runner_image=args.runner_image,
        project_name=args.project_name,
    )


if __name__ == "__main__":
    main()
