"""Add the logical Data MCP persistence model for governed data agents."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("sqlbot_assistant_id", sa.BigInteger(), nullable=True),
        sa.Column("sqlbot_sync_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("sqlbot_sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','published','disabled')", name="ck_data_agents_status"),
    )
    op.create_index("ix_data_agents_creator_updated", "data_agents", ["created_by", "updated_at"])

    op.create_table(
        "data_agent_datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_ref", sa.String(255), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("virtual_table_name", sa.String(128), nullable=False),
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("base_sql", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "dataset_ref", name="uq_data_agent_dataset_ref"),
    )
    op.create_index("ix_data_agent_datasets_agent", "data_agent_datasets", ["agent_id"])

    op.create_table(
        "data_agent_ask_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_subject", sa.String(255), nullable=False),
        sa.Column("host_session_key", sa.String(128), nullable=False),
        sa.Column("sqlbot_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("host_session_key", name="uq_data_agent_host_session"),
    )
    op.create_index("ix_data_agent_ask_session_agent", "data_agent_ask_sessions", ["agent_id", "updated_at"])

    op.create_table(
        "data_agent_tickets",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_subject", sa.String(255), nullable=False),
        sa.Column("host_session_key", sa.String(128), nullable=False),
        sa.Column("question_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("projection_limit", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("callback_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_data_agent_tickets_expiry", "data_agent_tickets", ["expires_at"])
    op.create_index("ix_data_agent_tickets_session", "data_agent_tickets", ["host_session_key"])

    op.create_table(
        "data_agent_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("data_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_subject", sa.String(255), nullable=False),
        sa.Column("host_session_key", sa.String(128), nullable=False),
        sa.Column("sqlbot_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("sqlbot_record_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("logical_sql", sa.Text(), nullable=False, server_default=""),
        sa.Column("columns_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("rows_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("chart_hint_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fields_used_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sqlbot_record_id", name="uq_data_agent_sqlbot_record"),
    )
    op.create_index("ix_data_agent_results_session", "data_agent_results", ["host_session_key", "created_at"])

    op.create_table(
        "data_agent_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("data_agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_subject", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_data_agent_audit_agent", "data_agent_audit", ["agent_id", "created_at"])


def downgrade() -> None:
    op.drop_table("data_agent_audit")
    op.drop_table("data_agent_results")
    op.drop_table("data_agent_tickets")
    op.drop_table("data_agent_ask_sessions")
    op.drop_table("data_agent_datasets")
    op.drop_table("data_agents")
