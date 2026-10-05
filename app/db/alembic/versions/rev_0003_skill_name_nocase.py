"""Make active Skill names ASCII case-insensitive per Workspace.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


INDEX_NAME = "uq_skills_workspace_active_name"


def upgrade() -> None:
    conflict = op.get_bind().execute(
        sa.text(
            """
            SELECT workspace_id, lower(name)
            FROM skills
            WHERE archived_at IS NULL
            GROUP BY workspace_id, lower(name)
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if conflict is not None:
        raise RuntimeError(
            "Cannot migrate while case-insensitive active Skill name conflicts exist."
        )

    op.drop_index(INDEX_NAME, table_name="skills")
    op.create_index(
        INDEX_NAME,
        "skills",
        [sa.text("workspace_id"), sa.text("lower(name)")],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="skills")
    op.create_index(
        INDEX_NAME,
        "skills",
        ["workspace_id", "name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
        postgresql_where=sa.text("archived_at IS NULL"),
    )
