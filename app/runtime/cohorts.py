import re
from dataclasses import dataclass
from importlib.metadata import distribution, version
from typing import TYPE_CHECKING, Any

from packaging.requirements import Requirement
from packaging.version import Version

from app.runtime.contracts import AgentRuntimePort, RuntimeCapabilities

if TYPE_CHECKING:
    from app.config import Settings


IMMUTABLE_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class RuntimeCohort:
    name: str
    image_digest: str
    sdk_version: str
    cli_version: str
    mcp_sdk_version: str
    protocol_version: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("runtime cohort name cannot be empty")
        if self.image_digest != "local" and not IMMUTABLE_IMAGE_DIGEST.fullmatch(
            self.image_digest
        ):
            raise ValueError("runtime image digest must be local or sha256:<digest>")


@dataclass(frozen=True, slots=True)
class RuntimeCohortReport:
    mode: str
    cohort: RuntimeCohort
    capabilities: RuntimeCapabilities
    mcp_python_sdk_v2: bool
    opensandbox: dict[str, str] | None = None

    def to_health_dict(self) -> dict[str, Any]:
        supported = [
            name
            for name, enabled in (
                ("resume", self.capabilities.supports_resume),
                ("interrupt", self.capabilities.supports_interrupt),
                ("auto_memory", self.capabilities.supports_auto_memory),
                ("mcp", self.capabilities.supports_mcp),
                ("skills", self.capabilities.supports_skills),
            )
            if enabled
        ]
        health = {
            "mode": self.mode,
            "cohort": self.cohort.name,
            "image_digest": self.cohort.image_digest,
            "protocol_version": self.cohort.protocol_version,
            "capabilities": supported,
            "dependencies": {
                "claude_agent_sdk": self.cohort.sdk_version,
                "claude_cli": self.cohort.cli_version,
                "mcp_python_sdk": self.cohort.mcp_sdk_version,
                "mcp_python_sdk_v2": self.mcp_python_sdk_v2,
            },
        }
        if self.opensandbox is not None:
            health["opensandbox"] = self.opensandbox
        return health


def runtime_capabilities(protocol_version: str) -> RuntimeCapabilities:
    return RuntimeCapabilities(
        protocol_version=protocol_version,
        supports_resume=True,
        supports_interrupt=True,
        supports_auto_memory=True,
        supports_mcp=True,
        supports_skills=True,
    )


def probe_runtime_cohort(
    runtime: AgentRuntimePort | None,
    settings: "Settings",
) -> RuntimeCohortReport:
    capabilities = (
        runtime.capabilities
        if runtime is not None
        else runtime_capabilities(settings.app_runtime_protocol_version)
    )
    if capabilities.protocol_version != settings.app_runtime_protocol_version:
        raise ValueError("runtime protocol does not match APP_RUNTIME_PROTOCOL_VERSION")

    sdk_distribution = distribution("claude-agent-sdk")
    mcp_requirement = next(
        Requirement(requirement)
        for requirement in sdk_distribution.requires or []
        if Requirement(requirement).name == "mcp"
    )
    return RuntimeCohortReport(
        mode=settings.app_runtime_mode,
        cohort=RuntimeCohort(
            name=settings.app_runtime_cohort,
            image_digest=settings.app_runtime_image_digest,
            sdk_version=sdk_distribution.version,
            cli_version="bundled-with-sdk",
            mcp_sdk_version=version("mcp"),
            protocol_version=settings.app_runtime_protocol_version,
        ),
        capabilities=capabilities,
        mcp_python_sdk_v2=Version("2.0.0") in mcp_requirement.specifier,
        opensandbox=(
            {
                "sdk": settings.opensandbox_sdk_version,
                "server": settings.opensandbox_server_version,
                "execd": settings.opensandbox_execd_version,
                "egress": settings.opensandbox_egress_version,
                "backend": "docker",
            }
            if settings.app_runtime_mode == "opensandbox_docker"
            else None
        ),
    )
