import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_compose_exposes_management_only_on_loopback_and_keeps_socket_server_only() -> None:
    compose = yaml.safe_load(
        (ROOT / "deploy/opensandbox/compose.yaml").read_text(encoding="utf-8")
    )
    service = compose["services"]["opensandbox-server"]

    assert service["image"] == "opensandbox/server:v0.2.2"
    assert service["ports"] == [
        "127.0.0.1:${OPENSANDBOX_PORT:-8080}:8080"
    ]
    assert service["environment"]["OPENSANDBOX_SERVER_API_KEY"].startswith("${")
    assert service["privileged"] is False
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert "ALL" in service["cap_drop"]
    assert service["read_only"] is True
    assert service["mem_limit"] == "1g"
    assert any(
        "/var/run/docker.sock:/var/run/docker.sock" in item
        for item in service["volumes"]
    )
    assert all(
        "/var/run/docker.sock" not in str(item)
        for name, definition in compose["services"].items()
        if name != "opensandbox-server"
        for item in definition.get("volumes", [])
    )
    assert "worker-secret" not in str(compose)


def test_server_config_pins_compatible_components_and_credential_vault_egress() -> None:
    config = tomllib.loads(
        (ROOT / "deploy/opensandbox/server.toml").read_text(encoding="utf-8")
    )

    assert config["server"]["host"] == "0.0.0.0"
    # Leave eip unset so use_server_proxy endpoints reflect the management
    # address used by each client (Compose DNS for workers, loopback for tests).
    assert not config["server"].get("eip")
    assert "api_key" not in config["server"]
    assert config["runtime"] == {
        "type": "docker",
        "execd_image": "opensandbox/execd:v1.0.21",
    }
    assert config["egress"] == {
        "image": "opensandbox/egress:v1.1.4",
        "mode": "dns+nft",
    }
    assert config["docker"]["network_mode"] == "bridge"
    assert config["docker"]["no_new_privileges"] is True
    assert config["docker"]["pids_limit"] <= 1024
    assert "NET_ADMIN" in config["docker"]["drop_capabilities"]
    assert config["store"]["path"].startswith("/var/lib/opensandbox/")


def test_runner_dockerfile_uses_locked_install_and_numeric_non_root_user() -> None:
    dockerfile = (
        ROOT / "deploy/docker/Dockerfile.opensandbox-runner"
    ).read_text(encoding="utf-8")

    assert "uv sync --frozen --no-dev" in dockerfile
    assert "python:3.12.10-slim-bookworm@sha256:" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "COPY . /app" not in dockerfile
    assert "ANTHROPIC_API_KEY" not in dockerfile
    assert "ANTHROPIC_BASE_URL" not in dockerfile
    assert "opensandbox-vault-placeholder" not in dockerfile
    assert "OPENSANDBOX_API_KEY" not in dockerfile
