"""Add durable multi-replica control-plane authority.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

ACTIVE_TURN_STATES = (
    "queued",
    "waiting_for_memory",
    "assigned",
    "running",
    "finalizing",
)


def upgrade() -> None:
    op.create_table(
        "identity_mappings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("profile_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),
    )
    op.create_index("ix_identity_mappings_user_id", "identity_mappings", ["user_id"])

    op.create_table(
        "workspace_membership_projections",
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("source_version", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','admin','member')",
            name="ck_membership_projections_role",
        ),
    )
    op.create_index(
        "ix_membership_projections_expiry",
        "workspace_membership_projections",
        ["expires_at"],
    )

    op.create_table(
        "config_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_config_snapshots_workspace_created",
        "config_snapshots",
        ["workspace_id", "created_at"],
    )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("config_snapshot_id", sa.String(36)))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("deleted_by", sa.String(36)))
        batch_op.create_foreign_key(
            "fk_sessions_config_snapshot",
            "config_snapshots",
            ["config_snapshot_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_sessions_deleted_by",
            "users",
            ["deleted_by"],
            ["id"],
            ondelete="RESTRICT",
        )

    with op.batch_alter_table("turns") as batch_op:
        batch_op.alter_column(
            "status", existing_type=sa.String(16), type_=sa.String(32), nullable=False
        )
        batch_op.add_column(
            sa.Column(
                "effect_state", sa.String(24), nullable=False, server_default="none"
            )
        )
        batch_op.add_column(
            sa.Column(
                "finalization_status",
                sa.String(32),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(sa.Column("warning_code", sa.String(64)))
        batch_op.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("execution_barrier_at", sa.DateTime(timezone=True)))

    op.create_table(
        "turn_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "turn_id",
            sa.String(36),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("execution_nonce", sa.String(64), nullable=False),
        sa.Column("runtime_cohort", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("turn_id", "attempt_number", name="uq_turn_attempt_number"),
        sa.UniqueConstraint("execution_nonce", name="uq_turn_attempt_execution_nonce"),
    )
    op.create_index("ix_turn_attempts_turn_id", "turn_attempts", ["turn_id"])

    op.create_table(
        "turn_events",
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
        sa.UniqueConstraint("turn_id", "sequence", name="uq_turn_event_sequence"),
    )
    op.create_index("ix_turn_events_turn_id", "turn_events", ["turn_id"])
    op.create_index("ix_turn_events_session_id", "turn_events", ["session_id"])
    op.create_index(
        "ix_turn_events_session_created",
        "turn_events",
        ["session_id", "created_at"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO turn_events (
                id, session_id, turn_id, sequence, event_type, role,
                payload_json, created_at
            )
            SELECT id, session_id, turn_id, sequence, event_type, role,
                   payload_json, created_at
            FROM messages
            ORDER BY created_at, id
            """
        )
    )

    if op.get_bind().dialect.name == "postgresql":
        active_values = ", ".join(f"'{state}'" for state in ACTIVE_TURN_STATES)
        op.create_index(
            "uq_turns_one_active_per_session",
            "turns",
            ["session_id"],
            unique=True,
            postgresql_where=sa.text(f"status IN ({active_values})"),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("uq_turns_one_active_per_session", table_name="turns")
    op.drop_table("turn_events")
    op.drop_table("turn_attempts")
    with op.batch_alter_table("turns") as batch_op:
        batch_op.drop_column("execution_barrier_at")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("warning_code")
        batch_op.drop_column("finalization_status")
        batch_op.drop_column("effect_state")
        batch_op.alter_column(
            "status", existing_type=sa.String(32), type_=sa.String(16), nullable=False
        )
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("fk_sessions_deleted_by", type_="foreignkey")
        batch_op.drop_constraint("fk_sessions_config_snapshot", type_="foreignkey")
        batch_op.drop_column("deleted_by")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("config_snapshot_id")
    op.drop_table("config_snapshots")
    op.drop_table("workspace_membership_projections")
    op.drop_table("identity_mappings")
