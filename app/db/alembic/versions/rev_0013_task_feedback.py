"""Create task feedback without modifying or deleting legacy message ratings."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Copy each actor's latest task vote, preserving every original legacy row."""
    op.create_table(
        "task_feedback",
        sa.Column(
            "actor_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.String(4), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating IN ('up', 'down')", name="ck_task_feedback_rating"),
    )
    op.create_index("ix_task_feedback_message", "task_feedback", ["message_id"])
    # Preserve the first evaluation day while the latest vote supplies its choice and details.
    op.execute("""
        INSERT INTO task_feedback
            (actor_id, session_id, message_id, rating, reasons_json, comment, created_at, updated_at)
        SELECT actor_id, session_id, message_id, rating, reasons_json, comment, first_created_at, updated_at
        FROM (
            SELECT f.*, m.session_id,
                MIN(f.created_at) OVER (PARTITION BY f.actor_id, m.session_id) AS first_created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY f.actor_id, m.session_id
                    ORDER BY f.updated_at DESC, f.message_id DESC
                ) AS vote_rank
            FROM message_feedback f JOIN messages m ON m.id = f.message_id
        ) AS ranked WHERE vote_rank = 1
    """)


def downgrade() -> None:
    """Require an explicit data rollback instead of silently discarding task feedback."""
    raise RuntimeError(
        "Restore a verified database backup to downgrade task feedback without data loss."
    )
