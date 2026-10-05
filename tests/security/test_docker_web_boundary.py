from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from app.workspaces.registry import WorkspaceRegistry

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "deploy/docker-web/compose.yaml"
DOCKERFILE = ROOT / "deploy/docker/Dockerfile.web"
RUNNER_DOCKERFILE = ROOT / "deploy/docker/Dockerfile.opensandbox-runner"
GATE_SCRIPT = ROOT / "scripts/verify-phase-2a2.sh"


def test_runtime_imports_are_declared_as_production_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert any(
        dependency.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0]
        == "packaging"
        for dependency in project["dependencies"]
    )


def test_runtime_dependencies_support_centos_7_python_312() -> None:
    """A default installation must include native dependencies with UAT-compatible wheels."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    supported = SpecifierSet(project["project"]["requires-python"])
    assert "3.12.7" in supported
    assert "3.13.3" not in supported
    dependencies = {
        requirement.name: requirement
        for requirement in map(Requirement, project["project"]["dependencies"])
    }
    # Extras and version markers previously made ordinary deployments omit the solver.
    assert dependencies["ortools"].marker is None
    assert "asyncio" in dependencies["sqlalchemy"].extras
    packages = {package["name"]: package for package in lock["package"]}
    for name in ("asyncpg", "greenlet", "ortools", "numpy", "pandas"):
        assert any(
            "-cp312-cp312-" in wheel["url"]
            and (
                "manylinux2014_x86_64" in wheel["url"]
                or "manylinux_2_17_x86_64" in wheel["url"]
            )
            for wheel in packages[name]["wheels"]
        ), name


def _compose() -> dict[str, object]:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_api_and_worker_share_image_but_not_secrets() -> None:
    compose = _compose()
    services = compose["services"]
    api = services["api"]
    worker = services["worker"]

    expected_image = "${DOCKER_WEB_APP_IMAGE:?set DOCKER_WEB_APP_IMAGE}"
    assert api["image"] == worker["image"] == expected_image
    assert "DOCKER_WEB_WORKER_ENV_FILE" not in str(api)
    assert "DOCKER_WEB_WORKER_ENV_FILE" in str(worker)
    assert api["ports"] == ["127.0.0.1:${DOCKER_WEB_PORT:-8765}:8000"]


def test_compose_preserves_container_and_host_security_boundaries() -> None:
    compose = _compose()
    services = compose["services"]

    for name, service in services.items():
        assert service.get("privileged", False) is False, name
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), (name, port)

    socket_services = {
        name
        for name, service in services.items()
        if any(
            "/var/run/docker.sock" in str(volume)
            for volume in service.get("volumes", [])
        )
    }
    assert socket_services == {"opensandbox-server"}

    for name in ("api", "worker", "migrate"):
        service = services[name]
        assert service["read_only"] is True
        assert any(str(item).startswith("/tmp") for item in service["tmpfs"])
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert "ALL" in service["cap_drop"]

    assert services["api"]["init"] is True
    assert services["worker"]["init"] is True
    assert "ports" not in services["postgres"]
    assert "ports" not in services["opensandbox-server"]


def test_application_image_is_locked_non_root_and_credential_free() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "python:3.12.10-slim-bookworm@sha256:" in dockerfile
    assert "uv==0.7.6" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "COPY contracts /app/contracts" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "COPY . /app" not in dockerfile
    assert "ANTHROPIC_API_KEY" not in dockerfile
    assert "OPENSANDBOX_API_KEY" not in dockerfile


def test_images_cache_locked_dependencies_before_copying_application_code() -> None:
    for path in (DOCKERFILE, RUNNER_DOCKERFILE):
        dockerfile = path.read_text(encoding="utf-8")
        dependency_sync = "uv sync --frozen --no-dev --no-install-project"
        assert "id=workspace-agent-uv" in dockerfile
        assert dependency_sync in dockerfile
        assert dockerfile.index(dependency_sync) < dockerfile.index("COPY app /app/app")
        assert dockerfile.count("uv sync --frozen --no-dev") == 2
        project_sync = "uv sync --frozen --no-dev --no-editable"
        assert project_sync in dockerfile
        assert dockerfile.index("COPY app /app/app") < dockerfile.index(project_sync)


def test_container_workspace_is_valid_and_has_no_host_integrations() -> None:
    root = ROOT / "deploy/docker-web/workspaces"
    entries = WorkspaceRegistry(root, "claude-sonnet-4-6", environ={}).scan()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.available is True
    assert entry.validation_errors == ()
    assert entry.manifest is not None
    assert entry.manifest.skills == []
    assert entry.manifest.skills_root_env is None
    assert entry.manifest.mcp_servers == {}


def test_rendered_compose_keeps_api_environment_credential_free(tmp_path: Path) -> None:
    api_env = tmp_path / "api.env"
    worker_env = tmp_path / "worker.env"
    opensandbox_env = tmp_path / "opensandbox.env"
    compose_env = tmp_path / "compose.env"
    api_env.write_text(
        "\n".join(
            (
                "ANTHROPIC_BASE_URL=https://proxy.example.test",
                "WORKSPACES_ROOT=/app/workspaces",
                "APP_DATA_DIR=/var/lib/workspace-agent",
                "APP_RUNTIME_MODE=opensandbox_docker",
                "APP_RUNTIME_COHORT=test",
                "APP_RUNTIME_IMAGE_DIGEST=sha256:" + "1" * 64,
                "OPENSANDBOX_API_URL=http://opensandbox-server:8080",
                "DATABASE_URL=postgresql+asyncpg://workspace:test@postgres:5432/workspace",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    worker_env.write_text(
        "ANTHROPIC_API_KEY=test-model-key\nOPENSANDBOX_API_KEY=test-sandbox-key\n",
        encoding="utf-8",
    )
    opensandbox_env.write_text(
        "OPENSANDBOX_SERVER_API_KEY=test-server-key\n", encoding="utf-8"
    )
    compose_env.write_text(
        "\n".join(
            (
                "DOCKER_WEB_APP_IMAGE=workspace-agent:test",
                "DOCKER_WEB_RUNNER_IMAGE=workspace-agent-runner@sha256:" + "2" * 64,
                f"DOCKER_WEB_API_ENV_FILE={api_env}",
                f"DOCKER_WEB_WORKER_ENV_FILE={worker_env}",
                f"DOCKER_WEB_OPENSANDBOX_ENV_FILE={opensandbox_env}",
                "DOCKER_WEB_POSTGRES_PASSWORD=test",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        (
            "docker",
            "compose",
            "--env-file",
            str(compose_env),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "yaml",
        ),
        cwd=ROOT,
        env={**os.environ, "COMPOSE_PROJECT_NAME": "workspace-agent-boundary-test"},
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = yaml.safe_load(result.stdout)
    api_environment = rendered["services"]["api"]["environment"]
    worker_environment = rendered["services"]["worker"]["environment"]

    assert "ANTHROPIC_API_KEY" not in api_environment
    assert "OPENSANDBOX_API_KEY" not in api_environment
    assert worker_environment["ANTHROPIC_API_KEY"] == "test-model-key"
    assert worker_environment["OPENSANDBOX_API_KEY"] == "test-sandbox-key"


def test_gate_uses_an_isolated_project_and_exact_cleanup() -> None:
    gate = GATE_SCRIPT.read_text(encoding="utf-8")

    assert "mktemp -d" in gate
    assert "DOCKER_WEB_GATE=1" in gate
    assert "RUN_DOCKER_WEB_STACK=1" in gate
    assert "docker volume prune" not in gate
    assert "reset --yes" in gate
    assert "Docker Web reset failed during Gate cleanup" in gate
    assert "label=com.docker.compose.project=$PROJECT" in gate
    assert "tests/integration/test_docker_web_stack.py" in gate
    assert 'RUN_LIVE_DOCKER_WEB_CLAUDE:-0' in gate
    assert "tests/live/test_docker_web_claude.py" in gate
    assert "up --claude" in gate


def test_reset_tolerates_sandbox_volumes_already_removed_by_opensandbox() -> None:
    launcher = (ROOT / "scripts/docker-web.sh").read_text(encoding="utf-8")

    assert 'docker volume rm "$volume" >/dev/null 2>&1 || true' in launcher
