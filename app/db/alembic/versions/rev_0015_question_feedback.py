"""Preserve session votes while introducing independent question evaluations."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Copy each existing vote onto its anchored question without changing the old table."""
    table = op.create_table(
        "question_feedback",
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("question_id", sa.String(36), sa.ForeignKey("turns.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.String(4), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating IN ('up', 'down')", name="ck_question_feedback_rating"),
    )
    op.create_index("ix_question_feedback_message", "question_feedback", ["message_id"])
    connection = op.get_bind()
    legacy = sa.Table("task_feedback", sa.MetaData(), autoload_with=connection)
    # Read only sessions which have a saved vote. Keep the migration independent of app code.
    sessions = connection.execute(sa.select(legacy.c.session_id).distinct()).scalars().all()
    for session_id in sessions:
        messages = connection.execute(sa.text("""
            SELECT m.id, m.turn_id, m.event_type, m.payload_json
            FROM messages m JOIN turns t ON t.id = m.turn_id
            WHERE m.session_id = :session_id
            ORDER BY t.created_at, t.id, m.sequence
        """), {"session_id": session_id}).mappings().all()
        roots = {}
        current = None
        for message in messages:
            payload = json.loads(message["payload_json"])
            if message["event_type"] == "message.user" and (
                (payload.get("text") or "").strip() or payload.get("attachments")
                or payload.get("file_references") or current is None
            ):
                current = message["turn_id"]
            roots[message["id"]] = current or message["turn_id"]
        for vote in connection.execute(sa.select(legacy).where(legacy.c.session_id == session_id)).mappings():
            connection.execute(table.insert().values(**dict(vote), question_id=roots[vote["message_id"]]))


def downgrade() -> None:
    """Question votes cannot be collapsed back into sessions without losing evaluations."""
    raise RuntimeError("Restore a verified database backup to downgrade question feedback without data loss.")
