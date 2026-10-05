"""Add OpenSandbox Docker runtime authority.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_sandboxes",
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("sandbox_id", sa.String(128), nullable=True),
        sa.Column("session_volume_name", sa.String(63), nullable=False),
        sa.Column("memory_volume_name", sa.String(63), nullable=False),
        sa.Column("memory_scope_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("runtime_cohort", sa.String(64), nullable=False),
        sa.Column("runner_image", sa.String(255), nullable=False),
        sa.Column("image_digest", sa.String(71), nullable=True),
        sa.Column("active_turn_id", sa.String(36), nullable=True),
        sa.Column("active_attempt_id", sa.String(36), nullable=True),
        sa.Column("active_command_session_id", sa.String(128), nullable=True),
        sa.Column("active_command_execution_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("recovery_reason", sa.String(128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_session_sandboxes_generation"),
        sa.CheckConstraint("version >= 1", name="ck_session_sandboxes_version"),
        sa.UniqueConstraint("sandbox_id", name="uq_session_sandboxes_sandbox_id"),
    )
    op.create_index(
        "ix_session_sandboxes_status_idle",
        "session_sandboxes",
        ["status", "idle_since"],
    )

    op.create_table(
        "memory_scope_leases",
        sa.Column("scope_key", sa.String(128), primary_key=True),
        sa.Column("owner_session_id", sa.String(36), nullable=False),
        sa.Column("owner_turn_id", sa.String(36), nullable=False),
        sa.Column("owner_attempt_id", sa.String(36), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_memory_scope_leases_version"),
        sa.UniqueConstraint("lease_token", name="uq_memory_scope_leases_token"),
    )
    op.create_index(
        "ix_memory_scope_leases_expires", "memory_scope_leases", ["expires_at"]
    )

    with op.batch_alter_table("turn_attempts") as batch_op:
        batch_op.add_column(sa.Column("sandbox_generation", sa.Integer()))
        batch_op.add_column(sa.Column("sandbox_id", sa.String(128)))
        batch_op.add_column(sa.Column("command_session_id", sa.String(128)))
        batch_op.add_column(sa.Column("command_execution_id", sa.String(128)))


def downgrade() -> None:
    with op.batch_alter_table("turn_attempts") as batch_op:
        batch_op.drop_column("command_execution_id")
        batch_op.drop_column("command_session_id")
        batch_op.drop_column("sandbox_id")
        batch_op.drop_column("sandbox_generation")
    op.drop_table("memory_scope_leases")
    op.drop_table("session_sandboxes")
