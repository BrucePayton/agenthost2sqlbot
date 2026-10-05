from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/docker-web.sh"
PROJECT = "workspace-agent-docker-web"


def run_launcher(
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(SCRIPT), *args),
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def marker(runtime_dir: Path, project: str = PROJECT) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "deployment.json").write_text(
        json.dumps({"schema_version": 1, "project_name": project}),
        encoding="utf-8",
    )


def render_runtime(runtime_dir: Path) -> None:
    from scripts.docker_web_config import render_runtime_config

    render_runtime_config(
        mode="fake",
        source_path=None,
        runtime_dir=runtime_dir,
        app_image="sha256:" + "a" * 64,
        runner_image="sha256:" + "b" * 64,
        project_name=PROJECT,
    )


def test_launcher_has_valid_bash_syntax_and_help() -> None:
    syntax = subprocess.run(
        ("bash", "-n", str(SCRIPT)), capture_output=True, text=True, check=False
    )
    assert syntax.returncode == 0, syntax.stderr

    result = run_launcher("--help")
    assert result.returncode == 0
    for command in ("up", "status", "logs", "restart", "down", "reset"):
        assert command in result.stdout


@pytest.mark.parametrize(
    "args",
    (("unknown",), ("logs", "database"), ("up", "--unknown"), ("reset", "--force")),
)
def test_launcher_rejects_unknown_commands_and_options(args: tuple[str, ...]) -> None:
    result = run_launcher(*args)

    assert result.returncode != 0
    assert "Usage:" in result.stderr


def test_gate_rejects_arbitrary_values(tmp_path: Path) -> None:
    result = run_launcher(
        "status",
        env={
            "DOCKER_WEB_GATE": "../override.yaml",
            "DOCKER_WEB_RUNTIME_DIR": str(tmp_path),
        },
    )

    assert result.returncode != 0
    assert "DOCKER_WEB_GATE" in result.stderr


def test_reset_requires_confirmation_when_stdin_is_not_a_tty(tmp_path: Path) -> None:
    marker(tmp_path)

    result = run_launcher("reset", env={"DOCKER_WEB_RUNTIME_DIR": str(tmp_path)})

    assert result.returncode != 0
    assert "--yes" in result.stderr


@pytest.mark.parametrize("unsafe", (Path("/"), Path.home(), ROOT))
def test_reset_refuses_unsafe_runtime_directories(unsafe: Path) -> None:
    result = run_launcher(
        "reset",
        "--yes",
        env={"DOCKER_WEB_RUNTIME_DIR": str(unsafe)},
    )

    assert result.returncode != 0
    assert "unsafe runtime directory" in result.stderr.lower()


def test_reset_requires_matching_ownership_marker(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    missing.mkdir()
    missing_result = run_launcher(
        "reset",
        "--yes",
        env={"DOCKER_WEB_RUNTIME_DIR": str(missing)},
    )
    assert missing_result.returncode != 0
    assert "ownership marker" in missing_result.stderr.lower()

    mismatch = tmp_path / "mismatch"
    marker(mismatch, project="another-project")
    mismatch_result = run_launcher(
        "reset",
        "--yes",
        env={"DOCKER_WEB_RUNTIME_DIR": str(mismatch)},
    )
    assert mismatch_result.returncode != 0
    assert "ownership marker" in mismatch_result.stderr.lower()


def test_up_defaults_to_fake_and_uses_only_fixed_compose_files(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "docker.calls"
    runtime_dir = tmp_path / "runtime"
    operator_env = tmp_path / "operator.env"
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocked_port = blocker.getsockname()[1]
    blocker.listen()
    operator_env.write_text(f"DOCKER_WEB_PORT={blocked_port}\n", encoding="utf-8")
    write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$DOCKER_RECORD"
if [[ "$1 ${2:-}" == "image inspect" ]]; then
  if [[ "$*" == *runner* ]]; then
    printf 'sha256:%064d\n' 2
  else
    printf 'sha256:%064d\n' 1
  fi
fi
""",
    )
    write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
printf '%s\n' '{"status":"ready","runtime":{"mode":"opensandbox_docker"},"execution":{"status":"ready","worker_count":1}}'
""",
    )

    release = threading.Timer(1.0, blocker.close)
    release.start()
    try:
        result = run_launcher(
            "up",
            env={
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "DOCKER_RECORD": str(record),
                "DOCKER_WEB_RUNTIME_DIR": str(runtime_dir),
                "DOCKER_WEB_CONFIG_FILE": str(operator_env),
                "DOCKER_WEB_READY_TIMEOUT_SECONDS": "2",
            },
        )
    finally:
        release.cancel()
        blocker.close()

    assert result.returncode == 0, result.stderr
    worker = dotenv_values(runtime_dir / "worker.env")
    assert worker["OPENSANDBOX_RUNNER_RUNTIME"] == "fake"
    assert "ANTHROPIC_API_KEY" not in worker
    calls = record.read_text(encoding="utf-8")
    assert str(ROOT / "deploy/docker-web/compose.yaml") in calls
    assert "compose.gate.yaml" not in calls
    assert "Local development only" in result.stdout


def test_logs_redact_generated_secrets(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    render_runtime(runtime_dir)
    secret = dotenv_values(runtime_dir / "worker.env")["OPENSANDBOX_API_KEY"]
    assert secret is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -eu
if [[ "$*" == *" logs "* ]]; then
  printf 'request failed with key=%s\n' "$TEST_SECRET"
fi
""",
    )

    result = run_launcher(
        "logs",
        "worker",
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCKER_WEB_RUNTIME_DIR": str(runtime_dir),
            "TEST_SECRET": secret,
        },
    )

    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_reset_removes_only_database_registered_sandbox_volumes(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    render_runtime(runtime_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "docker.calls"
    session_volume = "wa-session-" + "a" * 40
    memory_volume = "wa-memory-" + "b" * 40
    sandbox_id = "12345678-1234-4234-8234-123456789abc"
    write_executable(
        bin_dir / "docker",
        f"""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$DOCKER_RECORD"
if [[ "$*" == *"SELECT to_regclass"* ]]; then
  printf 't\n'
elif [[ "$*" == *"SELECT sandbox_id"* ]]; then
  printf '%s\n' '{sandbox_id}'
elif [[ "$*" == *"SELECT session_volume_name"* ]]; then
  printf '%s\n%s\n' '{session_volume}' '{memory_volume}'
fi
""",
    )

    result = run_launcher(
        "reset",
        "--yes",
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCKER_RECORD": str(record),
            "DOCKER_WEB_RUNTIME_DIR": str(runtime_dir),
            "DOCKER_WEB_READY_TIMEOUT_SECONDS": "2",
        },
    )

    assert result.returncode == 0, result.stderr
    calls = record.read_text(encoding="utf-8")
    assert f"rm -f sandbox-{sandbox_id} sandbox-egress-{sandbox_id}" in calls
    assert f"volume rm opensandbox-runtime-{sandbox_id}" in calls
    assert f"volume rm {session_volume}" in calls
    assert f"volume rm {memory_volume}" in calls
    assert "volume prune" not in calls
    assert "down -v --remove-orphans" in calls
    assert not runtime_dir.exists()
