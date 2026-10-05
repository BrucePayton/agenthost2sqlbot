from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class DataAgentRecord(Base):
    __tablename__ = "data_agents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','disabled')",
            name="ck_data_agents_status",
        ),
        Index("ix_data_agents_creator_updated", "created_by", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    sqlbot_assistant_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sqlbot_sync_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending"
    )
    sqlbot_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DataAgentDatasetRecord(Base):
    __tablename__ = "data_agent_datasets"
    __table_args__ = (
        UniqueConstraint("agent_id", "dataset_ref", name="uq_data_agent_dataset_ref"),
        Index("ix_data_agent_datasets_agent", "agent_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False
    )
    dataset_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    virtual_table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    datasource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    base_sql: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DataAgentAskSessionRecord(Base):
    __tablename__ = "data_agent_ask_sessions"
    __table_args__ = (
        UniqueConstraint("host_session_key", name="uq_data_agent_host_session"),
        Index("ix_data_agent_ask_session_agent", "agent_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False
    )
    user_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    host_session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sqlbot_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DataAgentTicketRecord(Base):
    __tablename__ = "data_agent_tickets"
    __table_args__ = (
        Index("ix_data_agent_tickets_expiry", "expires_at"),
        Index("ix_data_agent_tickets_session", "host_session_key"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False
    )
    user_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    host_session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    projection_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    callback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DataAgentResultRecord(Base):
    __tablename__ = "data_agent_results"
    __table_args__ = (
        UniqueConstraint("sqlbot_record_id", name="uq_data_agent_sqlbot_record"),
        Index("ix_data_agent_results_session", "host_session_key", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False
    )
    user_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    host_session_key: Mapped[str] = mapped_column(String(128), nullable=False)
    sqlbot_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sqlbot_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    logical_sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    columns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    rows_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    chart_hint_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    fields_used_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DataAgentAuditRecord(Base):
    __tablename__ = "data_agent_audit"
    __table_args__ = (Index("ix_data_agent_audit_agent", "agent_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("data_agents.id", ondelete="SET NULL"), nullable=True
    )
    user_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_workspace_updated", "workspace_id", "updated_at"),
        Index(
            "ix_sessions_creator_workspace_updated",
            "created_by",
            "workspace_id",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    config_snapshot_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("config_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    claude_session_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    title_source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    workspace_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_dir: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class TurnRecord(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "client_request_id", name="uq_turn_session_request"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    effect_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="none"
    )
    finalization_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    warning_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    execution_barrier_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uncached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_input_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    total_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_api_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frontend_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_search_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_set_changes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    catalog_digest_changes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence", name="uq_message_turn_sequence"),
        Index("ix_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("turns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class MessageFeedbackRecord(Base):
    """One current rating per actor and question, anchored to its complete reply."""
    __tablename__ = "question_feedback"
    __table_args__ = (
        CheckConstraint("rating IN ('up', 'down')", name="ck_question_feedback_rating"),
        Index("ix_question_feedback_message", "message_id"),
    )
    actor_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="CASCADE"), primary_key=True
    )
    rating: Mapped[str] = mapped_column(String(4), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AttachmentRecord(Base):
    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_session_status", "session_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("turns.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(100), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_subject: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("kind IN ('personal','team')", name="ck_workspaces_kind"),
        CheckConstraint(
            "kind != 'personal' OR (owner_user_id IS NOT NULL AND template_id IS NOT NULL)",
            name="ck_workspaces_personal_identity",
        ),
        Index(
            "uq_workspaces_personal_owner",
            "owner_user_id",
            unique=True,
            sqlite_where=text("kind = 'personal'"),
            postgresql_where=text("kind = 'personal'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    template_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class WorkspaceMemberRecord(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner','admin','member')", name="ck_workspace_members_role"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class IdentityMappingRecord(Base):
    __tablename__ = "identity_mappings"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class WorkspaceMembershipProjectionRecord(Base):
    __tablename__ = "workspace_membership_projections"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner','admin','member')",
            name="ck_membership_projections_role",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConfigSnapshotRecord(Base):
    __tablename__ = "config_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class TurnAttemptRecord(Base):
    __tablename__ = "turn_attempts"
    __table_args__ = (
        UniqueConstraint("turn_id", "attempt_number", name="uq_turn_attempt_number"),
        UniqueConstraint("execution_nonce", name="uq_turn_attempt_execution_nonce"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_cohort: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sandbox_generation: Mapped[int | None] = mapped_column(Integer)
    sandbox_id: Mapped[str | None] = mapped_column(String(128))
    command_session_id: Mapped[str | None] = mapped_column(String(128))
    command_execution_id: Mapped[str | None] = mapped_column(String(128))


class SessionSandboxRecord(Base):
    __tablename__ = "session_sandboxes"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="ck_session_sandboxes_generation"),
        CheckConstraint("version >= 1", name="ck_session_sandboxes_version"),
        UniqueConstraint("sandbox_id", name="uq_session_sandboxes_sandbox_id"),
        Index("ix_session_sandboxes_status_idle", "status", "idle_since"),
    )

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(128))
    session_volume_name: Mapped[str] = mapped_column(String(63), nullable=False)
    memory_volume_name: Mapped[str] = mapped_column(String(63), nullable=False)
    memory_scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    runtime_cohort: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_image: Mapped[str] = mapped_column(String(255), nullable=False)
    image_digest: Mapped[str | None] = mapped_column(String(71))
    active_turn_id: Mapped[str | None] = mapped_column(String(36))
    active_attempt_id: Mapped[str | None] = mapped_column(String(36))
    active_command_session_id: Mapped[str | None] = mapped_column(String(128))
    active_command_execution_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idle_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    recovery_reason: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class MemoryScopeLeaseRecord(Base):
    __tablename__ = "memory_scope_leases"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_memory_scope_leases_version"),
        UniqueConstraint("lease_token", name="uq_memory_scope_leases_token"),
        Index("ix_memory_scope_leases_expires", "expires_at"),
    )

    scope_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_turn_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_attempt_id: Mapped[str | None] = mapped_column(String(36))
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class WorkerHeartbeatRecord(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ready','unavailable')",
            name="ck_worker_heartbeats_status",
        ),
        Index(
            "ix_worker_heartbeats_compatibility_seen",
            "runtime_cohort",
            "protocol_version",
            "runner_runtime",
            "image_digest",
            "last_seen_at",
        ),
    )

    instance_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    runtime_cohort: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    runner_runtime: Mapped[str] = mapped_column(String(16), nullable=False)
    image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TurnEventRecord(Base):
    __tablename__ = "turn_events"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence", name="uq_turn_event_sequence"),
        Index("ix_turn_events_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("turns.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SkillRecord(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'global' AND workspace_id IS NULL AND enabled = TRUE) OR "
            "(scope = 'workspace' AND workspace_id IS NOT NULL)",
            name="ck_skills_scope_workspace",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="workspace")
    workspace_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bundle_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("skill_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


Index(
    "uq_skills_workspace_active_name",
    SkillRecord.workspace_id,
    SkillRecord.normalized_name,
    unique=True,
    sqlite_where=(SkillRecord.archived_at.is_(None))
    & (SkillRecord.scope == "workspace"),
    postgresql_where=(SkillRecord.archived_at.is_(None))
    & (SkillRecord.scope == "workspace"),
)

Index(
    "uq_skills_global_active_name",
    SkillRecord.normalized_name,
    unique=True,
    sqlite_where=(SkillRecord.archived_at.is_(None)) & (SkillRecord.scope == "global"),
    postgresql_where=(SkillRecord.archived_at.is_(None))
    & (SkillRecord.scope == "global"),
)


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('validating','ready','failed')",
            name="ck_skill_versions_status",
        ),
        CheckConstraint("version_no >= 1", name="ck_skill_versions_version_no"),
        CheckConstraint("size_bytes >= 0", name="ck_skill_versions_size_bytes"),
        UniqueConstraint(
            "skill_id", "version_no", name="uq_skill_versions_skill_version"
        ),
        UniqueConstraint(
            "skill_id", "bundle_hash", name="uq_skill_versions_skill_hash"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(512), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class WorkspaceGlobalSkillSettingRecord(Base):
    __tablename__ = "workspace_global_skill_settings"

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class WorkspaceInstructionRecord(Base):
    """用户覆盖的 CLAUDE.md。无行即用默认，删行即还原默认。

    一个用户只有一个 personal workspace（uq_workspaces_personal_owner 强制），
    所以按 workspace 存就是按用户隔离。
    """

    __tablename__ = "workspace_instructions"

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_workspace_instructions_size"),
    )


class PlatformRoleBindingRecord(Base):
    __tablename__ = "platform_role_bindings"
    __table_args__ = (
        CheckConstraint("role = 'skill_admin'", name="ck_platform_role_bindings_role"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    granted_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class SkillNameLockRecord(Base):
    __tablename__ = "skill_name_locks"

    normalized_name: Mapped[str] = mapped_column(String(128), primary_key=True)


class SkillFileRecord(Base):
    __tablename__ = "skill_files"

    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    content_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(71), nullable=False)


class UserPreferenceRecord(Base):
    """One JSON document of UI choices (panel size, launcher position) per user."""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    preferences_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class AppMetadataRecord(Base):
    __tablename__ = "app_metadata"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
