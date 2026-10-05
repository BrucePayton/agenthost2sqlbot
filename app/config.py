import ipaddress
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from app.skills.bundle import SkillBundleLimits

CLAUDE_EFFORT_LEVELS: frozenset[str] = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    anthropic_base_url: AnyHttpUrl = Field(validation_alias="ANTHROPIC_BASE_URL")
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        repr=False,
    )
    anthropic_auth_token: SecretStr | None = Field(
        default=None,
        validation_alias="ANTHROPIC_AUTH_TOKEN",
        repr=False,
    )
    workspaces_root: Path = Field(validation_alias="WORKSPACES_ROOT")

    app_env: Literal["development", "test", "uat", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    session_inspector_enabled: bool = Field(
        default=False,
        validation_alias="APP_SESSION_INSPECTOR_ENABLED",
    )
    session_inspector_username: str = Field(
        default="inspector",
        min_length=1,
        max_length=64,
        validation_alias="APP_SESSION_INSPECTOR_USERNAME",
    )
    session_inspector_password: SecretStr | None = Field(
        default=None,
        validation_alias="APP_SESSION_INSPECTOR_PASSWORD",
        repr=False,
    )
    identity_mode: Literal["mock", "obid", "davinci_passthrough", "oidc"] = Field(
        default="mock", validation_alias="APP_IDENTITY_MODE"
    )
    skill_admin_subjects: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias="APP_SKILL_ADMIN_SUBJECTS"
    )
    oidc_issuer: str | None = Field(default=None, validation_alias="APP_OIDC_ISSUER")
    oidc_audience: str | None = Field(
        default=None, validation_alias="APP_OIDC_AUDIENCE"
    )
    oidc_jwks_uri: AnyHttpUrl | None = Field(
        default=None, validation_alias="APP_OIDC_JWKS_URI"
    )
    oidc_jwks_ttl_seconds: int = Field(
        default=300, ge=1, le=86400, validation_alias="APP_OIDC_JWKS_TTL_SECONDS"
    )
    oidc_clock_skew_seconds: int = Field(
        default=30, ge=0, le=300, validation_alias="APP_OIDC_CLOCK_SKEW_SECONDS"
    )
    oidc_connect_timeout_seconds: float = Field(
        default=2.0, gt=0, le=30, validation_alias="APP_OIDC_CONNECT_TIMEOUT_SECONDS"
    )
    oidc_read_timeout_seconds: float = Field(
        default=5.0, gt=0, le=60, validation_alias="APP_OIDC_READ_TIMEOUT_SECONDS"
    )
    space_authority_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="APP_SPACE_AUTHORITY_URL"
    )
    space_authority_token: SecretStr | None = Field(
        default=None, validation_alias="APP_SPACE_AUTHORITY_TOKEN", repr=False
    )
    membership_projection_ttl_seconds: int = Field(
        default=60,
        ge=5,
        le=3600,
        validation_alias="APP_MEMBERSHIP_PROJECTION_TTL_SECONDS",
    )
    space_connect_timeout_seconds: float = Field(
        default=2.0, gt=0, le=30, validation_alias="APP_SPACE_CONNECT_TIMEOUT_SECONDS"
    )
    space_read_timeout_seconds: float = Field(
        default=5.0, gt=0, le=60, validation_alias="APP_SPACE_READ_TIMEOUT_SECONDS"
    )

    claude_model: str = Field(
        default="claude-sonnet-4-6", validation_alias="CLAUDE_MODEL"
    )
    claude_selectable_models: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        validation_alias="CLAUDE_SELECTABLE_MODELS",
    )
    claude_default_effort: str = Field(
        default="medium",
        validation_alias="CLAUDE_DEFAULT_EFFORT",
    )
    tool_repeat_limit: int = Field(
        default=2,
        ge=1,
        le=10,
        validation_alias="APP_TOOL_REPEAT_LIMIT",
    )
    # 一条用户请求内默认允许 9 次组件配置写入，支持连续创建多张图表；
    # 修改已有组件和报错后的修正也计入同一预算。
    tool_write_budget: int = Field(
        default=9,
        ge=1,
        le=20,
        validation_alias="APP_TOOL_WRITE_BUDGET",
    )
    # 一条用户请求内允许的 dryRun 探针次数。探针不写盘，但每次只该验证一个
    # 变量；再多就是在替用户挑口径，该汇报了。
    tool_probe_budget: int = Field(
        default=2,
        ge=1,
        le=10,
        validation_alias="APP_TOOL_PROBE_BUDGET",
    )
    # 一条用户请求内 catalog 检索到第几次直接拒绝。找字段本来就要搜几次，
    # locate-data 的硬上限是 4 次；第 5 次说明模型已经越界。软提醒实测只会
    # 引发「这算不算逐词搜索」的逐句辩论（session 8f14f309 三条提醒各换来一
    # 段长思考），所以改为 deny，并在拒绝消息里给出路：合并成一个问题问用户。
    # 用户新指令会重置计数（start_user_turn），提问后预算自然恢复。
    catalog_search_deny_after: int = Field(
        default=5,
        ge=1,
        le=50,
        validation_alias=AliasChoices(
            "APP_CATALOG_SEARCH_DENY_AFTER", "APP_CATALOG_SEARCH_WARN_AFTER"
        ),
    )
    claude_thinking_budget_tokens: int | None = Field(
        default=None,
        ge=0,
        le=64000,
        validation_alias="CLAUDE_THINKING_BUDGET_TOKENS",
    )
    # Subscription configuration is bounded metadata work. An explicit global
    # thinking setting still wins; this default does not affect other domains.
    subscription_thinking_budget_tokens: int = Field(
        default=2048,
        ge=1024,
        le=64000,
        validation_alias="APP_SUBSCRIPTION_THINKING_BUDGET_TOKENS",
    )
    subscription_receipt_thinking_budget_tokens: int = Field(
        # After a receipt, use the verified configuration directly. Operators
        # can opt into further thinking; 0 means disabled, not an empty budget.
        default=0,
        ge=0,
        le=64000,
        validation_alias="APP_SUBSCRIPTION_RECEIPT_THINKING_BUDGET_TOKENS",
    )
    subscription_model_decision_timeout_seconds: float = Field(
        default=180, ge=1, le=600,
        validation_alias="APP_SUBSCRIPTION_MODEL_DECISION_TIMEOUT_SECONDS",
    )
    subscription_model_step_timeout_seconds: float = Field(
        # Compatibility name: bounds response inactivity, not total model work.
        # Slow but live thinking/text/tool arguments must not fail at 60 seconds.
        default=60,
        ge=1,
        le=300,
        validation_alias="APP_SUBSCRIPTION_MODEL_STEP_TIMEOUT_SECONDS",
    )
    dashboard_layout_receipt_thinking_budget_tokens: int = Field(
        default=2048,
        ge=1024,
        le=64000,
        validation_alias="APP_DASHBOARD_LAYOUT_RECEIPT_THINKING_BUDGET_TOKENS",
    )
    claude_stream_idle_timeout_ms: int | None = Field(
        default=None,
        ge=300000,
        le=1800000,
        validation_alias="CLAUDE_STREAM_IDLE_TIMEOUT_MS",
    )
    claude_cli_path: Path | None = Field(
        default=None,
        validation_alias="CLAUDE_CLI_PATH",
    )
    claude_skills_root: Path | None = Field(
        default=None, validation_alias="CLAUDE_SKILLS_ROOT"
    )
    app_host: str = Field(default="127.0.0.1", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    app_data_dir: Path = Field(default=Path("./data"), validation_alias="APP_DATA_DIR")
    skill_artifact_backend: Literal["filesystem"] = Field(
        default="filesystem", validation_alias="SKILL_ARTIFACT_BACKEND"
    )
    skill_artifact_root: Path | None = Field(
        default=None, validation_alias="SKILL_ARTIFACT_ROOT"
    )
    app_runtime_mode: Literal[
        "local_inline", "execution_disabled", "opensandbox_docker"
    ] = Field(default="local_inline", validation_alias="APP_RUNTIME_MODE")
    app_runtime_cohort: str = Field(
        default="local", validation_alias="APP_RUNTIME_COHORT"
    )
    app_runtime_image_digest: str = Field(
        default="local", validation_alias="APP_RUNTIME_IMAGE_DIGEST"
    )
    app_runtime_protocol_version: str = Field(
        default="1", validation_alias="APP_RUNTIME_PROTOCOL_VERSION"
    )
    opensandbox_api_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="OPENSANDBOX_API_URL"
    )
    opensandbox_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENSANDBOX_API_KEY", repr=False
    )
    opensandbox_idle_ttl_seconds: int = Field(
        default=300, ge=30, le=86400, validation_alias="OPENSANDBOX_IDLE_TTL_SECONDS"
    )
    opensandbox_sandbox_timeout_seconds: int = Field(
        default=14400,
        ge=60,
        le=86400,
        validation_alias="OPENSANDBOX_SANDBOX_TIMEOUT_SECONDS",
    )
    opensandbox_ready_timeout_seconds: int = Field(
        default=120,
        ge=1,
        le=1800,
        validation_alias="OPENSANDBOX_READY_TIMEOUT_SECONDS",
    )
    opensandbox_worker_poll_seconds: float = Field(
        default=1.0,
        gt=0,
        le=60,
        validation_alias="OPENSANDBOX_WORKER_POLL_SECONDS",
    )
    worker_heartbeat_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        validation_alias="WORKER_HEARTBEAT_INTERVAL_SECONDS",
    )
    worker_heartbeat_stale_seconds: float = Field(
        default=15.0,
        ge=3.0,
        le=300.0,
        validation_alias="WORKER_HEARTBEAT_STALE_SECONDS",
    )
    opensandbox_worker_concurrency: int = Field(
        default=4, ge=1, le=128, validation_alias="OPENSANDBOX_WORKER_CONCURRENCY"
    )
    opensandbox_runner_image: str = Field(
        default="workspace-agent-runner@sha256:" + "0" * 64,
        validation_alias="OPENSANDBOX_RUNNER_IMAGE",
    )
    opensandbox_runner_runtime: Literal["claude", "fake"] = Field(
        default="claude", validation_alias="OPENSANDBOX_RUNNER_RUNTIME"
    )
    opensandbox_allowed_hosts: tuple[str, ...] = Field(
        default=("api.anthropic.com",),
        validation_alias="OPENSANDBOX_ALLOWED_HOSTS",
    )
    opensandbox_sdk_version: str = Field(default="0.1.15", frozen=True)
    opensandbox_server_version: str = Field(default="0.2.2", frozen=True)
    opensandbox_execd_version: str = Field(default="1.0.21", frozen=True)
    opensandbox_egress_version: str = Field(default="1.1.4", frozen=True)
    database_url: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
        repr=False,
    )
    max_upload_size_mb: int = Field(
        default=20, ge=1, le=1024, validation_alias="MAX_UPLOAD_SIZE_MB"
    )
    max_files_per_turn: int = Field(
        default=5, ge=1, le=100, validation_alias="MAX_FILES_PER_TURN"
    )
    max_skill_file_size_mb: int = Field(
        default=10, ge=1, le=1024, validation_alias="MAX_SKILL_FILE_SIZE_MB"
    )
    max_skill_bundle_size_mb: int = Field(
        default=50, ge=1, le=1024, validation_alias="MAX_SKILL_BUNDLE_SIZE_MB"
    )
    max_skill_files: int = Field(
        default=200, ge=1, le=10000, validation_alias="MAX_SKILL_FILES"
    )
    # CLAUDE.md 每个请求都进上下文，用户版本必须有上限；模板现在约 4.3 KB。
    max_instructions_bytes: int = Field(
        default=16384,
        ge=1024,
        le=65536,
        validation_alias="MAX_INSTRUCTIONS_BYTES",
    )
    turn_timeout_seconds: int = Field(
        default=900, ge=1, le=86400, validation_alias="TURN_TIMEOUT_SECONDS"
    )
    sse_heartbeat_seconds: int = Field(
        default=15, ge=1, le=300, validation_alias="SSE_HEARTBEAT_SECONDS"
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    mock_user_id: str = Field(default="mock-user", validation_alias="MOCK_USER_ID")
    mock_user_subject: str = Field(
        default="mock-user", validation_alias="MOCK_USER_SUBJECT"
    )
    mock_user_display_name: str = Field(
        default="Mock User", validation_alias="MOCK_USER_DISPLAY_NAME"
    )
    mock_personal_workspace_id: str = Field(
        default="example", validation_alias="MOCK_PERSONAL_WORKSPACE_ID"
    )
    personal_workspace_template_id: str = Field(
        default="example",
        validation_alias="APP_PERSONAL_WORKSPACE_TEMPLATE_ID",
    )
    mock_workspace_roles: dict[str, str] = Field(
        default_factory=dict, validation_alias="MOCK_WORKSPACE_ROLES"
    )
    davinci_local_integration: bool = Field(
        default=False, validation_alias="DAVINCI_LOCAL_INTEGRATION"
    )
    davinci_local_public_origin: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="DAVINCI_LOCAL_PUBLIC_ORIGIN",
    )
    davinci_local_parent_origins: tuple[str, ...] = Field(
        default=("http://local.aihuishou.com:5002",),
        validation_alias="DAVINCI_LOCAL_PARENT_ORIGINS",
    )
    davinci_api_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="DAVINCI_API_BASE_URL",
    )
    davinci_current_user_path: str = Field(
        default="/api/v3/users/currentUser",
        validation_alias="DAVINCI_CURRENT_USER_PATH",
    )
    davinci_session_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        validation_alias="DAVINCI_SESSION_TTL_SECONDS",
    )
    data_agent_enabled: bool = Field(
        default=False, validation_alias="DATA_AGENT_ENABLED"
    )
    data_agent_provider: Literal["knowledge_mysql", "starrocks_poc"] = Field(
        default="knowledge_mysql", validation_alias="DATA_AGENT_PROVIDER"
    )
    data_agent_starrocks_manifest: Path | None = Field(
        default=None, validation_alias="DATA_AGENT_STARROCKS_MANIFEST"
    )
    data_agent_sqlbot_source_ids: dict[str, int] = Field(
        default_factory=dict, validation_alias="DATA_AGENT_SQLBOT_SOURCE_IDS"
    )
    data_agent_sqlbot_catalog_enabled: bool = Field(
        default=False, validation_alias="DATA_AGENT_SQLBOT_CATALOG_ENABLED"
    )
    data_agent_starrocks_metadata_source: Literal["manifest", "service2"] = Field(
        default="manifest", validation_alias="DATA_AGENT_STARROCKS_METADATA_SOURCE"
    )
    sqlbot_catalog_base_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="SQLBOT_CATALOG_BASE_URL"
    )
    sqlbot_catalog_admin_account: str = Field(
        default="admin", validation_alias="SQLBOT_CATALOG_ADMIN_ACCOUNT"
    )
    sqlbot_catalog_admin_password: SecretStr | None = Field(
        default=None, validation_alias="SQLBOT_CATALOG_ADMIN_PASSWORD", repr=False
    )
    starrocks_host: str | None = Field(default=None, validation_alias="STARROCKS_HOST")
    starrocks_port: int = Field(default=9030, ge=1, le=65535, validation_alias="STARROCKS_PORT")
    starrocks_user: str | None = Field(default=None, validation_alias="STARROCKS_USER")
    starrocks_password: SecretStr | None = Field(default=None, validation_alias="STARROCKS_PASSWORD", repr=False)
    sqlbot_custom_model: str = Field(default="", validation_alias="SQLBOT_CUSTOM_MODEL")
    data_agent_browser_origin: AnyHttpUrl | None = Field(default=None, validation_alias="DATA_AGENT_BROWSER_ORIGIN")
    data_agent_subject: str = Field(
        default="159358", validation_alias="DATA_AGENT_SUBJECT"
    )
    data_agent_public_base_url: AnyHttpUrl = Field(
        default="http://127.0.0.1:18080",
        validation_alias="DATA_AGENT_PUBLIC_BASE_URL",
    )
    data_agent_ticket_ttl_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        validation_alias="DATA_AGENT_TICKET_TTL_SECONDS",
    )
    data_agent_result_row_limit: int = Field(
        default=200,
        ge=1,
        le=1000,
        validation_alias="DATA_AGENT_RESULT_ROW_LIMIT",
    )
    data_agent_cache_row_limit: int = Field(
        default=1000,
        ge=1,
        le=5000,
        validation_alias="DATA_AGENT_CACHE_ROW_LIMIT",
    )
    data_agent_result_ttl_seconds: int = Field(
        default=600,
        ge=30,
        le=86400,
        validation_alias="DATA_AGENT_RESULT_TTL_SECONDS",
    )
    data_agent_max_ticket_callbacks: int = Field(
        default=2,
        ge=1,
        le=4,
        validation_alias="DATA_AGENT_MAX_TICKET_CALLBACKS",
    )
    data_agent_callback_cidrs: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias="DATA_AGENT_CALLBACK_CIDRS"
    )
    asset_mcp_url: AnyHttpUrl = Field(
        default="https://asset-search-mcp-office.aihuishou.com/mcp",
        validation_alias="ASSET_MCP_URL",
    )
    asset_mcp_timeout_seconds: float = Field(
        default=15,
        ge=1,
        le=60,
        validation_alias="ASSET_MCP_TIMEOUT_SECONDS",
    )
    data_agent_runtime_asset_ref: str | None = Field(
        default=None,
        validation_alias="DATA_AGENT_RUNTIME_ASSET_REF",
    )
    data_agent_allow_unverified_local_dataset: bool = Field(
        default=False,
        validation_alias="DATA_AGENT_ALLOW_UNVERIFIED_LOCAL_DATASET",
    )
    sqlbot_base_url: AnyHttpUrl = Field(
        default="http://127.0.0.1:18000",
        validation_alias="SQLBOT_BASE_URL",
    )
    sqlbot_admin_account: str = Field(
        default="admin", validation_alias="SQLBOT_ADMIN_ACCOUNT"
    )
    sqlbot_admin_password: SecretStr | None = Field(
        default=None,
        validation_alias="SQLBOT_ADMIN_PASSWORD",
        repr=False,
    )
    sqlbot_secret_key: SecretStr | None = Field(
        default=None,
        validation_alias="SQLBOT_SECRET_KEY",
        repr=False,
    )
    sqlbot_request_timeout_seconds: float = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias="SQLBOT_REQUEST_TIMEOUT_SECONDS",
    )
    mysql_host: str | None = Field(default=None, validation_alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, ge=1, le=65535, validation_alias="MYSQL_PORT")
    mysql_database: str = Field(default="knowledge", validation_alias="MYSQL_DATABASE")
    mysql_readonly_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MYSQL_READONLY_USER", "MYSQL_USER"),
    )
    mysql_readonly_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MYSQL_READONLY_PASSWORD", "MYSQL_PASSWORD"),
        repr=False,
    )

    @field_validator("anthropic_api_key", "anthropic_auth_token")
    @classmethod
    def validate_anthropic_credential(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is None:
            return None
        if not value.get_secret_value().strip():
            raise ValueError("Anthropic credential cannot be empty")
        return SecretStr(value.get_secret_value().strip())

    @field_validator("workspaces_root")
    @classmethod
    def validate_workspaces_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("WORKSPACES_ROOT must be an absolute path")
        if value.is_symlink():
            raise ValueError("WORKSPACES_ROOT must not be a symbolic link")
        if not value.exists() or not value.is_dir():
            raise ValueError("WORKSPACES_ROOT must be an existing directory")
        return value.resolve()

    @field_validator("app_data_dir")
    @classmethod
    def resolve_data_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("claude_skills_root")
    @classmethod
    def validate_claude_skills_root(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("CLAUDE_SKILLS_ROOT must be an absolute path")
        if expanded.is_symlink() or not expanded.is_dir():
            raise ValueError("CLAUDE_SKILLS_ROOT must be a real directory")
        return expanded.resolve()

    @field_validator("claude_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("CLAUDE_MODEL cannot be empty")
        return value

    @field_validator("claude_selectable_models", mode="before")
    @classmethod
    def _split_selectable_models(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("data_agent_callback_cidrs", mode="before")
    @classmethod
    def _normalize_data_agent_callback_cidrs(cls, value: Any) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            value = tuple(item.strip() for item in value.split(",") if item.strip())
        if not isinstance(value, (list, tuple)):
            raise TypeError("DATA_AGENT_CALLBACK_CIDRS must be comma-separated CIDRs")
        normalized = tuple(
            str(ipaddress.ip_network(str(item), strict=False)) for item in value
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("DATA_AGENT_CALLBACK_CIDRS contains duplicates")
        return normalized

    @field_validator("skill_admin_subjects", mode="before")
    @classmethod
    def _normalize_skill_admin_subjects(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "APP_SKILL_ADMIN_SUBJECTS must be a JSON string array"
                ) from exc
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(subject, str) for subject in value
        ):
            raise ValueError("APP_SKILL_ADMIN_SUBJECTS must contain strings")
        normalized = tuple(subject.strip() for subject in value if subject.strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError("APP_SKILL_ADMIN_SUBJECTS contains duplicates")
        return normalized

    @field_validator("claude_default_effort")
    @classmethod
    def _validate_default_effort(cls, value: str) -> str:
        if value not in CLAUDE_EFFORT_LEVELS:
            raise ValueError(
                f"CLAUDE_DEFAULT_EFFORT must be one of {sorted(CLAUDE_EFFORT_LEVELS)}"
            )
        return value

    @field_validator("claude_cli_path")
    @classmethod
    def _validate_cli_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        expanded = value.expanduser()
        if not expanded.is_file():
            raise ValueError("CLAUDE_CLI_PATH must point to the claude CLI binary")
        return expanded.resolve()

    @field_validator("personal_workspace_template_id")
    @classmethod
    def validate_personal_workspace_template_id(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", normalized) is None:
            raise ValueError("APP_PERSONAL_WORKSPACE_TEMPLATE_ID is invalid")
        return normalized

    @field_validator("oidc_issuer")
    @classmethod
    def validate_oidc_issuer(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("APP_OIDC_ISSUER must be an absolute HTTP URL")
        if parsed.query or parsed.fragment:
            raise ValueError("APP_OIDC_ISSUER must not contain query or fragment")
        return normalized

    @field_validator("app_runtime_cohort")
    @classmethod
    def validate_runtime_cohort(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("APP_RUNTIME_COHORT cannot be empty")
        return value

    @field_validator("app_runtime_protocol_version")
    @classmethod
    def validate_runtime_protocol_version(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("APP_RUNTIME_PROTOCOL_VERSION cannot be empty")
        return value

    @field_validator("app_runtime_image_digest")
    @classmethod
    def validate_runtime_image_digest(cls, value: str) -> str:
        value = value.strip()
        if value != "local" and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("runtime image digest must be local or sha256:<digest>")
        return value

    @field_validator("opensandbox_runner_image")
    @classmethod
    def validate_opensandbox_runner_image(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", normalized) is None:
            raise ValueError("OPENSANDBOX_RUNNER_IMAGE must use a sha256 digest")
        return normalized

    @field_validator("opensandbox_allowed_hosts")
    @classmethod
    def validate_opensandbox_allowed_hosts(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        from app.sandbox.models import validate_allowed_host

        try:
            return tuple(dict.fromkeys(validate_allowed_host(host) for host in value))
        except ValueError as exc:
            raise ValueError(f"invalid OpenSandbox allowlist: {exc}") from exc

    @field_validator("mock_workspace_roles")
    @classmethod
    def validate_workspace_roles(cls, value: dict[str, str]) -> dict[str, str]:
        valid_roles = {"owner", "admin", "member"}
        normalized = {
            workspace_id: role.strip().lower() for workspace_id, role in value.items()
        }
        if any(role not in valid_roles for role in normalized.values()):
            raise ValueError("MOCK_WORKSPACE_ROLES must use owner, admin, or member")
        return normalized

    @model_validator(mode="after")
    def validate_personal_workspace_role(self) -> "Settings":
        if (
            self.worker_heartbeat_stale_seconds
            < self.worker_heartbeat_interval_seconds * 2
        ):
            raise ValueError(
                "WORKER_HEARTBEAT_STALE_SECONDS must be at least twice "
                "WORKER_HEARTBEAT_INTERVAL_SECONDS"
            )
        role = self.mock_workspace_roles.get(self.mock_personal_workspace_id)
        if role is not None and role != "owner":
            raise ValueError("the personal Workspace role must be owner")
        inspector_password = (
            self.session_inspector_password.get_secret_value()
            if self.session_inspector_password is not None
            else ""
        )
        if self.session_inspector_enabled and len(inspector_password) < 24:
            raise ValueError(
                "APP_SESSION_INSPECTOR_PASSWORD must contain at least 24 "
                "characters when APP_SESSION_INSPECTOR_ENABLED=true"
            )
        if (
            self.identity_mode == "davinci_passthrough"
            and self.davinci_api_base_url is None
        ):
            raise ValueError(
                "Davinci passthrough identity requires DAVINCI_API_BASE_URL"
            )
        database_url = (self.database_url or "").strip()
        if (
            self.app_env == "uat"
            and self.identity_mode == "obid"
            and self.app_runtime_mode != "local_inline"
        ):
            raise ValueError("UAT OBID requires local_inline")
        if self.app_env == "production" and not database_url.startswith(
            "postgresql+asyncpg://"
        ):
            raise ValueError(
                "APP_ENV=production requires a PostgreSQL "
                "postgresql+asyncpg DATABASE_URL"
            )
        if self.app_env == "production" and self.identity_mode != "oidc":
            raise ValueError("APP_ENV=production requires OIDC identity mode")
        if (
            self.app_env == "production"
            and self.app_runtime_mode != "execution_disabled"
        ):
            raise ValueError(
                "Phase 1 production requires APP_RUNTIME_MODE=execution_disabled"
            )
        if self.app_runtime_mode == "opensandbox_docker":
            if not database_url.startswith("postgresql+asyncpg://"):
                raise ValueError(
                    "OpenSandbox Docker runtime requires a PostgreSQL DATABASE_URL"
                )
            if self.opensandbox_api_url is None:
                raise ValueError("OpenSandbox Docker runtime requires an API URL")
        if (
            self.app_runtime_mode == "opensandbox_docker"
            and self.opensandbox_runner_runtime == "claude"
        ):
            from app.sandbox.credentials import parse_model_endpoint

            parse_model_endpoint(
                str(self.anthropic_base_url),
                self.opensandbox_allowed_hosts,
            )
        if self.identity_mode == "oidc" and not all(
            (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_uri)
        ):
            raise ValueError(
                "OIDC identity mode requires issuer, audience, and JWKS URI"
            )
        if self.app_env == "production" and not all(
            (self.space_authority_url, self.space_authority_token)
        ):
            raise ValueError(
                "APP_ENV=production requires the Space membership authority"
            )
        artifact_root = self.resolved_skill_artifact_root
        if artifact_root == self.app_data_dir or not artifact_root.is_relative_to(
            self.app_data_dir
        ):
            raise ValueError(
                "SKILL_ARTIFACT_ROOT must be a child directory of APP_DATA_DIR"
            )
        relative_artifact_root = artifact_root.relative_to(self.app_data_dir)
        if relative_artifact_root.parts[0] in {"sessions", "memory", "workspaces"}:
            raise ValueError(
                "SKILL_ARTIFACT_ROOT cannot be under sessions, memory, or workspaces"
            )
        self.database_url = database_url or None
        return self

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.app_data_dir / 'app.db'}"

    @property
    def deployment_constraint(self) -> str:
        return "single_instance" if self.app_env == "uat" else "configured"

    @property
    def security_marker(self) -> str | None:
        if self.app_env == "uat" and self.identity_mode == "obid":
            return "UAT_OBID_UNVERIFIED"
        return None

    @property
    def resolved_skill_artifact_root(self) -> Path:
        return (
            self.skill_artifact_root or self.app_data_dir / "skill-artifacts"
        ).resolve()

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_skill_file_size_bytes(self) -> int:
        return self.max_skill_file_size_mb * 1024 * 1024

    @property
    def max_skill_bundle_size_bytes(self) -> int:
        return self.max_skill_bundle_size_mb * 1024 * 1024

    @property
    def skill_bundle_limits(self) -> "SkillBundleLimits":
        from app.skills.bundle import SkillBundleLimits

        return SkillBundleLimits(
            max_file_bytes=self.max_skill_file_size_bytes,
            max_total_bytes=self.max_skill_bundle_size_bytes,
            max_files=self.max_skill_files,
        )

    def redacted_summary(self) -> dict[str, Any]:
        return {
            "anthropic_base_url": str(self.anthropic_base_url),
            "anthropic_api_key": (
                "**********" if self.anthropic_api_key is not None else None
            ),
            "anthropic_auth_token": (
                "**********" if self.anthropic_auth_token is not None else None
            ),
            "workspaces_root": str(self.workspaces_root),
            "app_data_dir": str(self.app_data_dir),
            "skill_artifact_backend": self.skill_artifact_backend,
            "skill_artifact_root": str(self.resolved_skill_artifact_root),
            "app_host": self.app_host,
            "app_port": self.app_port,
            "app_env": self.app_env,
            "session_inspector_enabled": self.session_inspector_enabled,
            "identity_mode": self.identity_mode,
            "deployment_constraint": self.deployment_constraint,
            "database_dialect": self.resolved_database_url.split(":", 1)[0],
            "runtime_mode": self.app_runtime_mode,
            "opensandbox_server_version": self.opensandbox_server_version,
            "data_agent_enabled": self.data_agent_enabled,
            "data_agent_subject": self.data_agent_subject,
            "data_agent_public_base_url": str(self.data_agent_public_base_url),
            "asset_mcp_url": str(self.asset_mcp_url),
            "data_agent_runtime_asset_mapping": (
                "configured" if self.data_agent_runtime_asset_ref else "missing"
            ),
            "data_agent_callback_source_restricted": bool(
                self.data_agent_callback_cidrs
            ),
            "sqlbot_base_url": str(self.sqlbot_base_url),
            "mysql_host": self.mysql_host,
            "mysql_database": self.mysql_database,
            "mysql_runtime_credentials": (
                "configured"
                if self.mysql_readonly_user and self.mysql_readonly_password
                else "missing"
            ),
        }
