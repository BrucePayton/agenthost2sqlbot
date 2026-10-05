import pytest
from pydantic import ValidationError


def opensandbox_claude_settings(settings_factory, **overrides):
    values = {
        "app_runtime_mode": "opensandbox_docker",
        "database_url": "postgresql+asyncpg://workspace:workspace@db/workspace",
        "opensandbox_api_url": "http://127.0.0.1:8080",
        "opensandbox_api_key": "sandbox-secret",
        "opensandbox_runner_runtime": "claude",
        "anthropic_base_url": "https://proxy.example.test/apps/anthropic",
        "opensandbox_allowed_hosts": ("proxy.example.test",),
    }
    return settings_factory(**{**values, **overrides})


def test_development_opensandbox_mode_requires_authenticated_postgres_profile(
    settings_factory,
) -> None:
    common = {
        "app_runtime_mode": "opensandbox_docker",
        "database_url": "postgresql+asyncpg://workspace:workspace@db/workspace",
        "opensandbox_api_url": "http://127.0.0.1:8080",
        "opensandbox_api_key": "sandbox-secret",
        "anthropic_base_url": "https://proxy.example.test/apps/anthropic",
        "opensandbox_allowed_hosts": ("proxy.example.test",),
    }

    settings = settings_factory(**common)

    assert settings.app_runtime_mode == "opensandbox_docker"
    assert settings.opensandbox_idle_ttl_seconds == 300
    assert settings.opensandbox_sdk_version == "0.1.15"
    assert settings.opensandbox_server_version == "0.2.2"
    assert settings.opensandbox_execd_version == "1.0.21"
    assert settings.opensandbox_egress_version == "1.1.4"
    assert settings.opensandbox_api_key.get_secret_value() == "sandbox-secret"
    assert "sandbox-secret" not in repr(settings)
    assert "sandbox-secret" not in str(settings.redacted_summary())

    with pytest.raises(ValidationError, match="PostgreSQL"):
        settings_factory(**{**common, "database_url": None})
    api_settings = settings_factory(**{**common, "opensandbox_api_key": None})
    assert api_settings.opensandbox_api_key is None


def test_opensandbox_claude_requires_safe_allowlisted_model_endpoint(
    settings_factory,
) -> None:
    settings = opensandbox_claude_settings(settings_factory)
    assert settings.opensandbox_runner_runtime == "claude"

    with pytest.raises(ValidationError, match="model endpoint"):
        opensandbox_claude_settings(
            settings_factory,
            anthropic_base_url="http://proxy.example.test/apps/anthropic",
        )
    with pytest.raises(ValidationError, match="allowlist"):
        opensandbox_claude_settings(
            settings_factory,
            opensandbox_allowed_hosts=("different.example.test",),
        )


def test_opensandbox_fake_does_not_require_model_host(settings_factory) -> None:
    settings = opensandbox_claude_settings(
        settings_factory,
        opensandbox_runner_runtime="fake",
        anthropic_base_url="http://proxy.example.test",
        opensandbox_allowed_hosts=(),
    )

    assert settings.opensandbox_runner_runtime == "fake"


@pytest.mark.parametrize(
    "host",
    [
        "https://api.anthropic.com/path",
        "api.anthropic.com:443/path",
        "127.0.0.1",
        "10.1.2.3",
        "metadata.google.internal",
    ],
)
def test_opensandbox_network_allowlist_rejects_urls_paths_and_private_hosts(
    settings_factory, host: str
) -> None:
    with pytest.raises(ValidationError, match="allowlist"):
        settings_factory(opensandbox_allowed_hosts=(host,))


def test_production_rejects_docker_and_inline_runtime_modes(settings_factory) -> None:
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
    for mode in ("local_inline", "opensandbox_docker"):
        with pytest.raises(ValidationError, match="execution_disabled"):
            settings_factory(**production, app_runtime_mode=mode)
