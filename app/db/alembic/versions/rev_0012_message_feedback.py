"""Persist message feedback; revisions remain on the existing session and turn."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add one vote per actor and message with cascading ownership references."""
    op.create_table(
        "message_feedback",
        sa.Column(
            "actor_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rating", sa.String(4), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating IN ('up', 'down')", name="ck_message_feedback_rating"
        ),
    )
    op.create_index("ix_message_feedback_message", "message_feedback", ["message_id"])


def downgrade() -> None:
    """Remove only the feedback records introduced by this revision."""
    op.drop_table("message_feedback")
