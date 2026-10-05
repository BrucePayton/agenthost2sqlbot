"""Add scoped Skills, immutable versions, settings, and platform roles.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


WORKSPACE_INDEX = "uq_skills_workspace_active_name"
GLOBAL_INDEX = "uq_skills_global_active_name"


def upgrade() -> None:
    op.drop_index(WORKSPACE_INDEX, table_name="skills")

    with op.batch_alter_table("skills") as batch_op:
        batch_op.alter_column(
            "workspace_id",
            existing_type=sa.String(64),
            nullable=True,
        )
        batch_op.alter_column(
            "created_by",
            existing_type=sa.String(36),
            nullable=True,
        )
        batch_op.add_column(sa.Column("scope", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("normalized_name", sa.String(128), nullable=True))
        batch_op.add_column(
            sa.Column("current_version_id", sa.String(36), nullable=True)
        )

    op.execute(
        sa.text("UPDATE skills SET scope = 'workspace', normalized_name = lower(name)")
    )

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("bundle_hash", sa.String(71), nullable=False),
        sa.Column("artifact_key", sa.String(512), nullable=False),
        sa.Column("artifact_sha256", sa.String(71), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('validating','ready','failed')",
            name="ck_skill_versions_status",
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_skill_versions_version_no"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_skill_versions_size_bytes"),
        sa.UniqueConstraint(
            "skill_id", "version_no", name="uq_skill_versions_skill_version"
        ),
        sa.UniqueConstraint(
            "skill_id", "bundle_hash", name="uq_skill_versions_skill_hash"
        ),
    )

    with op.batch_alter_table("skills") as batch_op:
        batch_op.create_foreign_key(
            "fk_skills_current_version",
            "skill_versions",
            ["current_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_table(
        "workspace_global_skill_settings",
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "platform_role_bindings",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(32), primary_key=True),
        sa.Column(
            "granted_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role = 'skill_admin'", name="ck_platform_role_bindings_role"
        ),
    )
    op.create_table(
        "skill_name_locks",
        sa.Column("normalized_name", sa.String(128), primary_key=True),
    )

    with op.batch_alter_table("skills") as batch_op:
        batch_op.alter_column(
            "scope",
            existing_type=sa.String(16),
            nullable=False,
        )
        batch_op.alter_column(
            "normalized_name",
            existing_type=sa.String(128),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_skills_scope_workspace",
            "(scope = 'global' AND workspace_id IS NULL AND enabled = TRUE) OR "
            "(scope = 'workspace' AND workspace_id IS NOT NULL)",
        )

    op.create_index(
        GLOBAL_INDEX,
        "skills",
        ["normalized_name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL AND scope = 'global'"),
        postgresql_where=sa.text("archived_at IS NULL AND scope = 'global'"),
    )
    op.create_index(
        WORKSPACE_INDEX,
        "skills",
        ["workspace_id", "normalized_name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL AND scope = 'workspace'"),
        postgresql_where=sa.text("archived_at IS NULL AND scope = 'workspace'"),
    )


def downgrade() -> None:
    global_skill = (
        op.get_bind()
        .execute(sa.text("SELECT id FROM skills WHERE scope = 'global' LIMIT 1"))
        .first()
    )
    if global_skill is not None:
        raise RuntimeError("Cannot downgrade while global Skills exist.")

    op.drop_index(WORKSPACE_INDEX, table_name="skills")
    op.drop_index(GLOBAL_INDEX, table_name="skills")
    op.drop_table("workspace_global_skill_settings")
    op.drop_table("platform_role_bindings")
    op.drop_table("skill_name_locks")

    with op.batch_alter_table("skills") as batch_op:
        batch_op.drop_constraint("ck_skills_scope_workspace", type_="check")
        batch_op.drop_constraint("fk_skills_current_version", type_="foreignkey")
        batch_op.drop_column("current_version_id")
        batch_op.drop_column("normalized_name")
        batch_op.drop_column("scope")
        batch_op.alter_column(
            "workspace_id",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch_op.alter_column(
            "created_by",
            existing_type=sa.String(36),
            nullable=False,
        )

    op.drop_table("skill_versions")
    op.create_index(
        WORKSPACE_INDEX,
        "skills",
        [sa.text("workspace_id"), sa.text("lower(name)")],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
        postgresql_where=sa.text("archived_at IS NULL"),
    )
