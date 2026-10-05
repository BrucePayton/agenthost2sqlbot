"""Create the frozen pre-workspace database schema.

Revision ID: 0001
Revises:
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _legacy_tables(metadata: sa.MetaData) -> tuple[sa.Table, ...]:
    sessions = sa.Table(
        "sessions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("claude_session_id", sa.String(128), unique=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("title_source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("workspace_snapshot_json", sa.Text(), nullable=False),
        sa.Column("workspace_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("session_dir", sa.String(255), nullable=False, unique=True),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index("ix_sessions_workspace_id", sessions.c.workspace_id)
    sa.Index("ix_sessions_status", sessions.c.status)
    sa.Index("ix_sessions_updated_at", sessions.c.updated_at)
    sa.Index(
        "ix_sessions_workspace_updated", sessions.c.workspace_id, sessions.c.updated_at
    )

    turns = sa.Table(
        "turns",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_request_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(12, 6)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "session_id", "client_request_id", name="uq_turn_session_request"
        ),
    )
    sa.Index("ix_turns_session_id", turns.c.session_id)
    sa.Index("ix_turns_status", turns.c.status)

    messages = sa.Table(
        "messages",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16)),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", "sequence", name="uq_message_turn_sequence"),
    )
    sa.Index("ix_messages_session_id", messages.c.session_id)
    sa.Index("ix_messages_turn_id", messages.c.turn_id)
    sa.Index("ix_messages_session_created", messages.c.session_id, messages.c.created_at)

    attachments = sa.Table(
        "attachments",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_filename", sa.String(100), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("relative_path", sa.String(512), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Index("ix_attachments_session_id", attachments.c.session_id)
    sa.Index("ix_attachments_turn_id", attachments.c.turn_id)
    sa.Index("ix_attachments_session_status", attachments.c.session_id, attachments.c.status)

    return sessions, turns, messages, attachments


def create_legacy_schema(bind: sa.Connection) -> None:
    for table in _legacy_tables(sa.MetaData()):
        table.create(bind, checkfirst=True)


def upgrade() -> None:
    create_legacy_schema(op.get_bind())


def downgrade() -> None:
    for table in reversed(_legacy_tables(sa.MetaData())):
        table.drop(op.get_bind(), checkfirst=True)
