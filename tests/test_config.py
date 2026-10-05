import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_required_shared_environment_variables_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.config import Settings

    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "WORKSPACES_ROOT"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    error_text = str(exc_info.value)
    assert "ANTHROPIC_BASE_URL" in error_text
    assert "WORKSPACES_ROOT" in error_text
    assert "ANTHROPIC_API_KEY" not in error_text


def test_settings_redact_secret_and_derive_database_url(settings_factory) -> None:
    settings = settings_factory()

    assert "top-secret-test-key" not in repr(settings)
    assert settings.anthropic_api_key.get_secret_value() == "top-secret-test-key"
    assert settings.resolved_database_url.endswith("/data/app.db")
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000


def test_settings_accept_and_redact_anthropic_auth_token(settings_factory) -> None:
    settings = settings_factory(
        anthropic_api_key=None,
        anthropic_auth_token=" bearer-secret ",
    )

    assert settings.anthropic_api_key is None
    assert settings.anthropic_auth_token.get_secret_value() == "bearer-secret"
    assert "bearer-secret" not in repr(settings)
    assert settings.redacted_summary()["anthropic_auth_token"] == "**********"


def test_claude_thinking_and_idle_timeout_bounds(settings_factory) -> None:
    settings = settings_factory(
        claude_thinking_budget_tokens=4096,
        claude_stream_idle_timeout_ms=420000,
    )
    assert settings.claude_thinking_budget_tokens == 4096
    assert settings.claude_stream_idle_timeout_ms == 420000

    with pytest.raises(ValidationError):
        settings_factory(claude_thinking_budget_tokens=64001)
    with pytest.raises(ValidationError):
        settings_factory(claude_stream_idle_timeout_ms=299999)
    assert settings_factory().subscription_thinking_budget_tokens == 2048
    with pytest.raises(ValidationError):
        settings_factory(subscription_thinking_budget_tokens=512)


def test_claude_selectable_models_splits_strips_and_drops_empties(
    settings_factory,
) -> None:
    settings = settings_factory(claude_selectable_models="a, b ,,c")
    assert settings.claude_selectable_models == ("a", "b", "c")


def test_cli_path_is_the_only_frontend_tool_runtime_setting(
    settings_factory, tmp_path: Path
) -> None:
    fake_cli = tmp_path / "claude"
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o755)

    defaults = settings_factory()
    assert defaults.claude_cli_path is None
    assert not hasattr(defaults, "claude_tool_search")
    assert not hasattr(defaults, "claude_always_load_tools")

    settings = settings_factory(
        claude_tool_search=" AUTO:5 ",
        claude_always_load_tools="page.get_context, dashboard.publish ,,",
        claude_cli_path=str(fake_cli),
    )
    assert not hasattr(settings, "claude_tool_search")
    assert not hasattr(settings, "claude_always_load_tools")
    assert settings.claude_cli_path == fake_cli.resolve()


def test_skill_admin_subjects_trim_drop_empty_and_reject_duplicates(
    settings_factory,
) -> None:
    settings = settings_factory(
        skill_admin_subjects='[" bootstrap-admin ", "", "second-admin"]'
    )
    assert settings.skill_admin_subjects == ("bootstrap-admin", "second-admin")

    with pytest.raises(ValidationError, match="APP_SKILL_ADMIN_SUBJECTS"):
        settings_factory(
            skill_admin_subjects=("bootstrap-admin", " bootstrap-admin ")
        )


def test_claude_default_effort_rejects_values_outside_allowed_levels(
    settings_factory,
) -> None:
    with pytest.raises(ValidationError):
        settings_factory(claude_default_effort="bogus")


def test_uat_obid_accepts_single_instance_sqlite_without_database_url(
    settings_factory,
) -> None:
    settings = settings_factory(
        app_env="uat",
        identity_mode="obid",
        app_runtime_mode="local_inline",
    )

    assert settings.resolved_database_url.endswith("/data/app.db")
    assert settings.deployment_constraint == "single_instance"
    assert settings.security_marker == "UAT_OBID_UNVERIFIED"


def test_uat_obid_accepts_single_instance_postgresql(settings_factory) -> None:
    settings = settings_factory(
        app_env="uat",
        identity_mode="obid",
        app_runtime_mode="local_inline",
        database_url="postgresql+asyncpg://agent:test@localhost/agent",
    )

    assert settings.deployment_constraint == "single_instance"
    assert settings.security_marker == "UAT_OBID_UNVERIFIED"
    assert settings.redacted_summary()["identity_mode"] == "obid"


def test_production_rejects_obid_identity(settings_factory) -> None:
    with pytest.raises(ValidationError, match="production requires OIDC"):
        settings_factory(
            app_env="production",
            identity_mode="obid",
            app_runtime_mode="execution_disabled",
            database_url="postgresql+asyncpg://agent:test@localhost/agent",
            space_authority_url="https://spaces.example.test",
            space_authority_token="token",
        )


def test_skill_artifact_storage_defaults_to_filesystem_with_sqlite(
    settings_factory,
) -> None:
    settings = settings_factory()

    assert settings.resolved_database_url.endswith("/data/app.db")
    assert settings.skill_artifact_backend == "filesystem"
    assert (
        settings.resolved_skill_artifact_root
        == settings.app_data_dir / "skill-artifacts"
    )
    assert settings.redacted_summary()["skill_artifact_backend"] == "filesystem"


def test_skill_artifact_storage_accepts_explicit_child_root(settings_factory) -> None:
    default_settings = settings_factory()
    root = default_settings.app_data_dir / "artifacts"
    settings = settings_factory(skill_artifact_root=root)

    assert settings.resolved_skill_artifact_root == root


def test_skill_artifact_storage_rejects_unsupported_backend(settings_factory) -> None:
    with pytest.raises(ValidationError, match="skill_artifact_backend"):
        settings_factory(skill_artifact_backend="s3")


@pytest.mark.parametrize(
    "relative_root",
    [
        ".",
        "../outside",
        "sessions/artifacts",
        "memory/artifacts",
        "workspaces/artifacts",
    ],
)
def test_skill_artifact_storage_rejects_unsafe_roots(
    settings_factory, relative_root: str
) -> None:
    default_settings = settings_factory()
    root = default_settings.app_data_dir / relative_root
    with pytest.raises(ValidationError, match="SKILL_ARTIFACT_ROOT"):
        settings_factory(skill_artifact_root=root)


def test_workspaces_root_must_be_an_absolute_real_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.config import Settings

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("WORKSPACES_ROOT", "relative/workspaces")

    with pytest.raises(ValidationError, match="absolute"):
        Settings(_env_file=None)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-workspaces"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv("WORKSPACES_ROOT", str(link))

    with pytest.raises(ValidationError, match="symbolic link"):
        Settings(_env_file=None)


def test_personal_workspace_template_id_is_normalized_and_validated(
    settings_factory,
) -> None:
    assert (
        settings_factory(personal_workspace_template_id=" example ")
        .personal_workspace_template_id
        == "example"
    )
    with pytest.raises(ValidationError, match="APP_PERSONAL_WORKSPACE_TEMPLATE_ID"):
        settings_factory(personal_workspace_template_id="Not/Valid")

def test_mock_workspace_roles_example_survives_shell_source_and_settings_parse(
    tmp_path: Path,
) -> None:
    example = Path(__file__).parents[1] / ".env.example"
    roles_line = next(
        line
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.startswith("MOCK_WORKSPACE_ROLES=")
    )
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    env_file = tmp_path / "shell-source.env"
    env_file.write_text(
        "\n".join(
            (
                "ANTHROPIC_BASE_URL=https://proxy.example.test",
                "ANTHROPIC_API_KEY=secret",
                f"WORKSPACES_ROOT={workspaces}",
                roles_line,
            )
        ),
        encoding="utf-8",
    )
    script = (
        "import json; from app.config import Settings; "
        "print(json.dumps(Settings(_env_file=None).mock_workspace_roles, sort_keys=True))"
    )

    completed = subprocess.run(
        [
            "/bin/sh",
            "-c",
            'set -a; . "$1"; set +a; "$2" -c "$3"',
            "shell-source",
            str(env_file),
            sys.executable,
            script,
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {"example": "owner"}
