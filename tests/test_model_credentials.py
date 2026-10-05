import pytest
from pydantic import SecretStr


@pytest.mark.parametrize(
    ("base_url", "host", "binding_path", "normalized"),
    [
        (
            "https://api.anthropic.com",
            "api.anthropic.com",
            "/v1/*",
            "https://api.anthropic.com",
        ),
        (
            "https://dashscope.aliyuncs.com/apps/anthropic/",
            "dashscope.aliyuncs.com",
            "/apps/anthropic/v1/*",
            "https://dashscope.aliyuncs.com/apps/anthropic",
        ),
        (
            "https://gateway.example.com/anthropic/v1",
            "gateway.example.com",
            "/anthropic/v1/*",
            "https://gateway.example.com/anthropic/v1",
        ),
        (
            "HTTPS://API.EXAMPLE.COM/Anthropic",
            "api.example.com",
            "/Anthropic/v1/*",
            "https://api.example.com/Anthropic",
        ),
    ],
)
def test_parse_model_endpoint_derives_exact_binding(
    base_url: str,
    host: str,
    binding_path: str,
    normalized: str,
) -> None:
    from app.sandbox.credentials import parse_model_endpoint

    endpoint = parse_model_endpoint(base_url, (host, "mcp.example.com"))

    assert endpoint.host == host
    assert endpoint.binding_path == binding_path
    assert endpoint.base_url == normalized


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/v1?key=x",
        "https://api.example.com/v1#fragment",
        "https://127.0.0.1/v1",
        "https://api.example.com:8443/v1",
        "https://api.example.com/a/../v1",
        "https://api.example.com/a%2fv1",
        "https://api.example.com/a%5Cv1",
        "https://api.example.com/a\\v1",
    ],
)
def test_parse_model_endpoint_rejects_unsafe_urls(base_url: str) -> None:
    from app.sandbox.credentials import parse_model_endpoint

    with pytest.raises(ValueError, match="model endpoint"):
        parse_model_endpoint(base_url, ("api.example.com",))


def test_parse_model_endpoint_requires_exact_allowlist_match() -> None:
    from app.sandbox.credentials import parse_model_endpoint

    with pytest.raises(ValueError, match="allowlist"):
        parse_model_endpoint(
            "https://api.example.com/v1",
            ("sub.api.example.com", "example.com"),
        )


@pytest.mark.asyncio
async def test_platform_credential_is_redacted_and_scope_aware() -> None:
    from app.sandbox.credentials import (
        CredentialScope,
        PlatformModelCredentialProvider,
    )

    provider = PlatformModelCredentialProvider(
        "https://api.example.com/anthropic",
        SecretStr("phase2a-real-canary"),
        ("api.example.com",),
    )
    scope = CredentialScope("workspace-1", "user-1", "session-1")

    credential = await provider.resolve(scope)

    assert credential.endpoint.binding_path == "/anthropic/v1/*"
    assert credential.api_key.get_secret_value() == "phase2a-real-canary"
    assert "phase2a-real-canary" not in repr(provider)
    assert "phase2a-real-canary" not in repr(credential)


@pytest.mark.parametrize(
    "scope",
    [
        ("", "user-1", "session-1"),
        ("workspace-1", " ", "session-1"),
        ("workspace-1", "user-1", ""),
    ],
)
def test_credential_scope_rejects_blank_identifiers(scope: tuple[str, str, str]) -> None:
    from app.sandbox.credentials import CredentialScope

    with pytest.raises(ValueError, match="scope"):
        CredentialScope(*scope)


def test_platform_credential_rejects_blank_api_key() -> None:
    from app.sandbox.credentials import PlatformModelCredentialProvider

    with pytest.raises(ValueError, match="API key"):
        PlatformModelCredentialProvider(
            "https://api.example.com/anthropic",
            SecretStr(" "),
            ("api.example.com",),
        )
