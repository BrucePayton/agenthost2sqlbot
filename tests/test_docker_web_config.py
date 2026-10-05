from __future__ import annotations

import json
from pathlib import Path

import pytest
from dotenv import dotenv_values

APP_IMAGE = "sha256:" + "a" * 64
RUNNER_IMAGE = "sha256:" + "b" * 64
PROJECT = "workspace-agent-docker-web"


def render(tmp_path: Path, **overrides):
    from scripts.docker_web_config import render_runtime_config

    values = {
        "mode": "fake",
        "source_path": None,
        "runtime_dir": tmp_path,
        "app_image": APP_IMAGE,
        "runner_image": RUNNER_IMAGE,
        "project_name": PROJECT,
    }
    values.update(overrides)
    return render_runtime_config(**values)


def test_fake_config_omits_model_key_from_api_and_worker(tmp_path: Path) -> None:
    rendered = render(tmp_path)

    assert "ANTHROPIC_API_KEY" not in rendered.api_env.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in rendered.worker_env.read_text(encoding="utf-8")
    assert rendered.runtime_dir.stat().st_mode & 0o777 == 0o700
    for path in (
        rendered.compose_env,
        rendered.api_env,
        rendered.worker_env,
        rendered.opensandbox_env,
        rendered.marker,
    ):
        assert path.stat().st_mode & 0o777 == 0o600


def test_claude_config_requires_endpoint_key_and_exact_allowlist(
    tmp_path: Path,
) -> None:
    source = tmp_path / "operator.env"

    for content, expected in (
        ("ANTHROPIC_API_KEY=test\n", "ANTHROPIC_BASE_URL"),
        ("ANTHROPIC_BASE_URL=https://proxy.example.test\n", "ANTHROPIC_API_KEY"),
        (
            ("ANTHROPIC_BASE_URL=https://proxy.example.test\nANTHROPIC_API_KEY=test\n"),
            "OPENSANDBOX_ALLOWED_HOSTS",
        ),
        (
            (
                "ANTHROPIC_BASE_URL=https://proxy.example.test\n"
                "ANTHROPIC_API_KEY=test\n"
                "OPENSANDBOX_ALLOWED_HOSTS=api.anthropic.com\n"
            ),
            "allowlist",
        ),
    ):
        source.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match=expected):
            render(tmp_path / "runtime", mode="claude", source_path=source)


def test_claude_key_is_written_only_to_worker_file(tmp_path: Path) -> None:
    source = tmp_path / "operator.env"
    source.write_text(
        "ANTHROPIC_BASE_URL=https://proxy.example.test\n"
        "ANTHROPIC_API_KEY=synthetic-model-secret\n"
        "OPENSANDBOX_ALLOWED_HOSTS=proxy.example.test\n",
        encoding="utf-8",
    )

    rendered = render(tmp_path / "runtime", mode="claude", source_path=source)
    api = dotenv_values(rendered.api_env)
    worker = dotenv_values(rendered.worker_env)

    assert "ANTHROPIC_API_KEY" not in api
    assert worker["ANTHROPIC_API_KEY"] == "synthetic-model-secret"
    assert worker["OPENSANDBOX_RUNNER_RUNTIME"] == "claude"
    assert api["OPENSANDBOX_ALLOWED_HOSTS"] == '["proxy.example.test"]'
    assert "synthetic-model-secret" not in repr(rendered)


def test_generated_secrets_persist_across_repeated_renders(tmp_path: Path) -> None:
    first = render(tmp_path)
    first_compose = dotenv_values(first.compose_env)
    first_worker = dotenv_values(first.worker_env)
    first_server = dotenv_values(first.opensandbox_env)

    second = render(tmp_path)

    assert (
        dotenv_values(second.compose_env)["DOCKER_WEB_POSTGRES_PASSWORD"]
        == (first_compose["DOCKER_WEB_POSTGRES_PASSWORD"])
    )
    assert (
        dotenv_values(second.worker_env)["OPENSANDBOX_API_KEY"]
        == (first_worker["OPENSANDBOX_API_KEY"])
    )
    assert (
        dotenv_values(second.opensandbox_env)["OPENSANDBOX_SERVER_API_KEY"]
        == first_server["OPENSANDBOX_SERVER_API_KEY"]
    )


@pytest.mark.parametrize(
    "project_name",
    ("has space", "has/slash", "Uppercase", "", ".hidden"),
)
def test_project_name_rejects_unsafe_values(tmp_path: Path, project_name: str) -> None:
    with pytest.raises(ValueError, match="project"):
        render(tmp_path, project_name=project_name)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("app_image", "workspace-agent:latest"),
        ("app_image", "sha256:abc"),
        ("runner_image", "workspace-agent-runner:latest"),
        ("runner_image", "sha256:" + "g" * 64),
    ),
)
def test_images_require_immutable_sha256_ids(
    tmp_path: Path, field: str, value: str
) -> None:
    with pytest.raises(ValueError, match="sha256"):
        render(tmp_path, **{field: value})


def test_marker_contains_only_non_secret_ownership_identity(tmp_path: Path) -> None:
    rendered = render(tmp_path)

    assert json.loads(rendered.marker.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "project_name": PROJECT,
    }


def test_existing_marker_cannot_be_claimed_by_another_project(tmp_path: Path) -> None:
    render(tmp_path)

    with pytest.raises(ValueError, match="belongs to another project"):
        render(tmp_path, project_name="another-project")
