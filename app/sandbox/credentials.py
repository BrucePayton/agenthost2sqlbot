from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import SecretStr

from app.sandbox.models import validate_allowed_host

MODEL_CREDENTIAL_NAME = "workspace-agent-model-api-key"
MODEL_BINDING_NAME = "workspace-agent-model-endpoint"

_ENCODED_SEPARATOR_RE = re.compile(r"%(?:2f|5c)", re.IGNORECASE)


@dataclass(frozen=True)
class CredentialScope:
    workspace_id: str
    user_id: str
    session_id: str

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.workspace_id, self.user_id, self.session_id)
        ):
            raise ValueError("credential scope identifiers cannot be blank")


@dataclass(frozen=True)
class ModelEndpoint:
    base_url: str
    host: str
    binding_path: str


@dataclass(frozen=True)
class ModelCredential:
    endpoint: ModelEndpoint
    api_key: SecretStr = field(repr=False)


class ModelCredentialProvider(Protocol):
    async def resolve(self, scope: CredentialScope) -> ModelCredential: ...


class PlatformModelCredentialProvider:
    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        allowed_hosts: tuple[str, ...],
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise ValueError("model API key cannot be blank")
        self._credential = ModelCredential(
            endpoint=parse_model_endpoint(base_url, allowed_hosts),
            api_key=api_key,
        )

    async def resolve(self, scope: CredentialScope) -> ModelCredential:
        # Constructing CredentialScope validates the tenant attribution inputs. The
        # platform provider intentionally shares one credential in Phase 2A.1.
        if not isinstance(scope, CredentialScope):
            raise TypeError("credential scope is required")
        return self._credential

    def __repr__(self) -> str:
        return (
            "PlatformModelCredentialProvider("
            f"endpoint={self._credential.endpoint!r}, api_key=SecretStr('**********'))"
        )


def parse_model_endpoint(
    base_url: str,
    allowed_hosts: tuple[str, ...],
) -> ModelEndpoint:
    candidate = base_url.strip()
    if not candidate or any(character.isspace() for character in candidate):
        raise ValueError("model endpoint must be a non-empty HTTPS URL")

    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("model endpoint is malformed") from exc

    if parsed.scheme.lower() != "https":
        raise ValueError("model endpoint must use HTTPS")
    if not parsed.netloc or parsed.hostname is None:
        raise ValueError("model endpoint must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("model endpoint must not include user information")
    if parsed.query or parsed.fragment:
        raise ValueError("model endpoint must not include query or fragment")
    if port not in (None, 443):
        raise ValueError("model endpoint must use canonical port 443")

    try:
        host = validate_allowed_host(parsed.hostname)
    except ValueError as exc:
        raise ValueError("model endpoint hostname is unsafe") from exc
    if "." not in host:
        raise ValueError("model endpoint hostname must be a fully-qualified domain")

    try:
        normalized_allowlist = {
            validate_allowed_host(allowed_host) for allowed_host in allowed_hosts
        }
    except ValueError as exc:
        raise ValueError("model endpoint allowlist is invalid") from exc
    if host not in normalized_allowlist:
        raise ValueError("model endpoint hostname is not in the exact allowlist")

    path = parsed.path or ""
    if "\\" in path or _ENCODED_SEPARATOR_RE.search(path):
        raise ValueError("model endpoint path contains an encoded separator")
    if path.startswith("//") or any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("model endpoint path contains an unsafe segment")

    prefix = path.rstrip("/")
    binding_path = f"{prefix}/*" if prefix.endswith("/v1") else f"{prefix}/v1/*"
    normalized_base_url = f"https://{host}{prefix}"
    return ModelEndpoint(
        base_url=normalized_base_url,
        host=host,
        binding_path=binding_path,
    )
