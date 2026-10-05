from datetime import UTC, datetime

import pytest


def test_volume_names_are_opaque_stable_and_scope_isolated() -> None:
    from app.sandbox.models import memory_volume_name, session_volume_name

    session = session_volume_name("Session With PII@example.com")
    same_session = session_volume_name("Session With PII@example.com")
    other_session = session_volume_name("different")
    personal = memory_volume_name("user@example.com", "finance-team")
    other_workspace = memory_volume_name("user@example.com", "other-team")

    assert session == same_session
    assert session != other_session
    assert personal != other_workspace
    assert session.startswith("wa-session-")
    assert personal.startswith("wa-memory-")
    assert "example" not in session
    assert "finance" not in personal
    assert len(session) <= 63
    assert len(personal) <= 63


def test_session_sandbox_spec_rejects_mutable_or_unsafe_execution_inputs() -> None:
    from app.sandbox.models import RunnerModelConfig, SessionSandboxSpec

    common = {
        "session_key": "session-1",
        "memory_scope_key": "memory-1",
        "generation": 1,
        "runner_image": "workspace-agent-runner@sha256:" + "a" * 64,
        "allowed_hosts": ("api.anthropic.com",),
        "credential_proxy_required": True,
        "model_config": RunnerModelConfig(base_url="https://api.anthropic.com"),
        "timeout_seconds": 900,
    }

    spec = SessionSandboxSpec(**common)
    assert spec.session_mount == "/session"
    assert spec.memory_mount == "/memory"
    assert spec.allowed_hosts == ("api.anthropic.com",)

    with pytest.raises(ValueError, match="generation"):
        SessionSandboxSpec(**{**common, "generation": 0})
    with pytest.raises(ValueError, match="digest"):
        SessionSandboxSpec(**{**common, "runner_image": "runner:latest"})
    fake_spec = SessionSandboxSpec(
        **{
            **common,
            "runner_image": "sha256:" + "b" * 64,
            "credential_proxy_required": False,
            "allowed_hosts": (),
            "model_config": None,
        }
    )
    assert fake_spec.runner_image.startswith("sha256:")
    with pytest.raises(ValueError, match="cannot allow hosts"):
        SessionSandboxSpec(
            **{
                **common,
                "credential_proxy_required": False,
                "model_config": None,
            }
        )
    with pytest.raises(ValueError, match="allowed host"):
        SessionSandboxSpec(**{**common, "allowed_hosts": ("https://bad/path",)})
    with pytest.raises(ValueError, match="model config"):
        SessionSandboxSpec(**{**common, "model_config": None})
    with pytest.raises(ValueError, match="model config"):
        SessionSandboxSpec(
            **{
                **common,
                "credential_proxy_required": False,
                "allowed_hosts": (),
            }
        )


def test_runner_model_config_rejects_non_placeholder_api_key() -> None:
    from app.sandbox.models import RunnerModelConfig

    config = RunnerModelConfig(base_url="https://api.anthropic.com")
    assert config.api_key_placeholder == "opensandbox-vault-placeholder"

    with pytest.raises(ValueError, match="fixed placeholder"):
        RunnerModelConfig(
            base_url="https://api.anthropic.com",
            api_key_placeholder="real-key-must-not-enter-runner",
        )


def test_sandbox_handle_repr_does_not_expose_runtime_identifiers() -> None:
    from app.sandbox.models import SandboxHandle

    handle = SandboxHandle(
        sandbox_id="sandbox-secret-id",
        generation=2,
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
        image_reference="runner@sha256:" + "b" * 64,
        session_volume_name="wa-session-private",
        memory_volume_name="wa-memory-private",
    )

    rendered = repr(handle)
    assert "sandbox-secret-id" not in rendered
    assert "wa-session-private" not in rendered
    assert "wa-memory-private" not in rendered
    assert handle.generation == 2
