"""Add compatible Worker heartbeats.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("instance_id", sa.String(36), primary_key=True),
        sa.Column("runtime_cohort", sa.String(64), nullable=False),
        sa.Column("protocol_version", sa.String(32), nullable=False),
        sa.Column("runner_runtime", sa.String(16), nullable=False),
        sa.Column("image_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('ready','unavailable')",
            name="ck_worker_heartbeats_status",
        ),
    )
    op.create_index(
        "ix_worker_heartbeats_compatibility_seen",
        "worker_heartbeats",
        [
            "runtime_cohort",
            "protocol_version",
            "runner_runtime",
            "image_digest",
            "last_seen_at",
        ],
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
