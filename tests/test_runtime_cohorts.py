from dataclasses import replace

import pytest


def test_runtime_cohort_rejects_blank_names_and_mutable_images() -> None:
    from app.runtime.cohorts import RuntimeCohort

    with pytest.raises(ValueError, match="name"):
        RuntimeCohort(
            name=" ",
            image_digest="local",
            sdk_version="0.2.128",
            cli_version="bundled",
            mcp_sdk_version="1.29.0",
            protocol_version="1",
        )

    with pytest.raises(ValueError, match="image digest"):
        RuntimeCohort(
            name="candidate",
            image_digest="runner:latest",
            sdk_version="0.2.128",
            cli_version="bundled",
            mcp_sdk_version="1.29.0",
            protocol_version="1",
        )


def test_probe_reports_installed_dependencies_without_secrets(settings_factory) -> None:
    from importlib.metadata import version

    from app.runtime.cohorts import probe_runtime_cohort
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    report = probe_runtime_cohort(FakeAgentRuntime(), settings)

    assert report.cohort.sdk_version == version("claude-agent-sdk")
    assert report.cohort.mcp_sdk_version == version("mcp")
    assert report.mcp_python_sdk_v2 is False
    assert report.to_health_dict() == {
        "mode": "local_inline",
        "cohort": "local",
        "image_digest": "local",
        "protocol_version": "1",
        "capabilities": [
            "resume",
            "interrupt",
            "auto_memory",
            "mcp",
            "skills",
        ],
        "dependencies": {
            "claude_agent_sdk": version("claude-agent-sdk"),
            "claude_cli": "bundled-with-sdk",
            "mcp_python_sdk": version("mcp"),
            "mcp_python_sdk_v2": False,
        },
    }
    serialized = str(report.to_health_dict())
    assert settings.anthropic_api_key.get_secret_value() not in serialized
    assert str(settings.anthropic_base_url) not in serialized
    assert str(settings.app_data_dir) not in serialized


def test_probe_uses_declared_capabilities_without_runtime(settings_factory) -> None:
    from app.runtime.cohorts import probe_runtime_cohort

    settings = settings_factory(app_runtime_mode="execution_disabled")

    report = probe_runtime_cohort(None, settings)

    assert report.capabilities.protocol_version == "1"
    assert report.capabilities.supports_resume is True
    assert report.capabilities.supports_auto_memory is True


def test_probe_rejects_runtime_protocol_mismatch(settings_factory) -> None:
    from app.runtime.cohorts import probe_runtime_cohort
    from app.runtime.contracts import RuntimeCapabilities
    from app.runtime.fake import FakeAgentRuntime

    class IncompatibleRuntime(FakeAgentRuntime):
        @property
        def capabilities(self) -> RuntimeCapabilities:
            return replace(super().capabilities, protocol_version="2")

    with pytest.raises(ValueError, match="protocol"):
        probe_runtime_cohort(IncompatibleRuntime(), settings_factory())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"app_runtime_cohort": " "}, "APP_RUNTIME_COHORT"),
        ({"app_runtime_protocol_version": " "}, "APP_RUNTIME_PROTOCOL_VERSION"),
        ({"app_runtime_image_digest": "runner:latest"}, "image digest"),
    ],
)
def test_runtime_settings_reject_invalid_cohort_values(
    settings_factory, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        settings_factory(**overrides)


def test_production_requires_execution_disabled_until_runner_exists(
    settings_factory,
) -> None:
    from pydantic import ValidationError

    production = {
        "app_env": "production",
        "database_url": "postgresql+asyncpg://workspace:workspace@db/workspace",
        "identity_mode": "oidc",
        "oidc_issuer": "https://identity.example.test",
        "oidc_audience": "workspace-agent",
        "oidc_jwks_uri": "https://identity.example.test/jwks",
        "space_authority_url": "https://spaces.example.test",
        "space_authority_token": "space-secret",
    }
    with pytest.raises(ValidationError, match="execution_disabled"):
        settings_factory(**production, app_runtime_mode="local_inline")

    settings = settings_factory(**production, app_runtime_mode="execution_disabled")
    assert settings.app_runtime_mode == "execution_disabled"
