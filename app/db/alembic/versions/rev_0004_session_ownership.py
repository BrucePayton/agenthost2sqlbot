"""Add private Session creator ownership.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

LEGACY_SESSION_OWNER_ID = "legacy-session-owner"


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.add_column(sa.Column("created_by", sa.String(36), nullable=True))

    bind = op.get_bind()
    session_count = bind.execute(sa.text("SELECT count(*) FROM sessions")).scalar_one()
    if session_count:
        existing = bind.execute(
            sa.text("SELECT 1 FROM users WHERE id = :id"),
            {"id": LEGACY_SESSION_OWNER_ID},
        ).first()
        if existing is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO users (
                        id, external_subject, display_name, provider,
                        created_at, updated_at
                    ) VALUES (
                        :id, :subject, 'Legacy Session Owner', 'migration',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": LEGACY_SESSION_OWNER_ID,
                    "subject": "migration:legacy-session-owner",
                },
            )
        bind.execute(
            sa.text("UPDATE sessions SET created_by = :id WHERE created_by IS NULL"),
            {"id": LEGACY_SESSION_OWNER_ID},
        )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column("created_by", nullable=False)
        batch_op.create_foreign_key(
            "fk_sessions_creator",
            "users",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_sessions_creator_workspace_updated",
        "sessions",
        ["created_by", "workspace_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_creator_workspace_updated", table_name="sessions")
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("fk_sessions_creator", type_="foreignkey")
        batch_op.drop_column("created_by")
    op.get_bind().execute(
        sa.text("DELETE FROM users WHERE id = :id"),
        {"id": LEGACY_SESSION_OWNER_ID},
    )
