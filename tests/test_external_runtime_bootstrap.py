import pytest


def test_opensandbox_api_bootstrap_does_not_require_model_key(
    settings_factory,
) -> None:
    from app.bootstrap import build_app_services

    settings = settings_factory(
        anthropic_api_key=None,
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://opensandbox-server:8080",
        opensandbox_api_key=None,
        opensandbox_runner_runtime="fake",
        opensandbox_allowed_hosts=(),
    )

    services = build_app_services(settings)

    assert services.runtime is None


def test_local_inline_rejects_missing_model_key(settings_factory) -> None:
    from app.bootstrap import build_app_services

    settings = settings_factory(anthropic_api_key=None)

    with pytest.raises(ValueError, match="local_inline requires ANTHROPIC_API_KEY"):
        build_app_services(settings)
