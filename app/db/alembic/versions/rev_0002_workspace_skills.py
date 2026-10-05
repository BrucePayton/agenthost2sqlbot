"""Add workspace, user, and Skill persistence.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-24
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_subject", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('personal','team')", name="ck_workspaces_kind"),
    )
    op.execute(
        """
        INSERT INTO workspaces (id, name, kind, config_json, created_at, updated_at)
        SELECT DISTINCT workspace_id, workspace_id, 'team', '{}', CURRENT_TIMESTAMP,
               CURRENT_TIMESTAMP
        FROM sessions
        """
    )
    op.create_table(
        "workspace_members",
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','admin','member')", name="ck_workspace_members_role"
        ),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(64),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("bundle_hash", sa.String(71), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_skills_workspace_id", "skills", ["workspace_id"])
    op.create_index(
        "uq_skills_workspace_active_name",
        "skills",
        ["workspace_id", "name"],
        unique=True,
        sqlite_where=sa.text("archived_at IS NULL"),
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_table(
        "skill_files",
        sa.Column(
            "skill_id",
            sa.String(36),
            sa.ForeignKey("skills.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("path", sa.String(512), primary_key=True),
        sa.Column("content_blob", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(71), nullable=False),
    )
    op.create_table(
        "app_metadata",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_personal_workspace_guards()
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.create_foreign_key(
            "fk_sessions_workspace",
            "workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint("fk_sessions_workspace", type_="foreignkey")
    _drop_personal_workspace_guards()
    op.drop_table("app_metadata")
    op.drop_table("skill_files")
    op.drop_index("uq_skills_workspace_active_name", table_name="skills")
    op.drop_index("ix_skills_workspace_id", table_name="skills")
    op.drop_table("skills")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("users")


def _create_personal_workspace_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute(
        """
        CREATE TRIGGER trg_workspaces_personal_insert
        BEFORE INSERT ON workspaces
        FOR EACH ROW
        WHEN NEW.kind = 'personal'
        BEGIN
            SELECT RAISE(
                ABORT,
                'create personal workspace as team, add its owner, then promote it'
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_members_personal_insert
        BEFORE INSERT ON workspace_members
        FOR EACH ROW
        WHEN (SELECT kind FROM workspaces WHERE id = NEW.workspace_id) = 'personal'
          AND (
              NEW.role != 'owner'
              OR EXISTS (
                  SELECT 1 FROM workspace_members
                  WHERE workspace_id = NEW.workspace_id
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'personal workspace requires exactly one owner');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_members_personal_update
        BEFORE UPDATE OF workspace_id, user_id, role ON workspace_members
        FOR EACH ROW
        WHEN (SELECT kind FROM workspaces WHERE id = OLD.workspace_id) = 'personal'
          OR (SELECT kind FROM workspaces WHERE id = NEW.workspace_id) = 'personal'
        BEGIN
            SELECT RAISE(ABORT, 'personal workspace membership is immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_members_personal_delete
        BEFORE DELETE ON workspace_members
        FOR EACH ROW
        WHEN (SELECT kind FROM workspaces WHERE id = OLD.workspace_id) = 'personal'
        BEGIN
            SELECT RAISE(ABORT, 'personal workspace owner cannot be removed');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspaces_personal_kind
        BEFORE UPDATE OF kind ON workspaces
        FOR EACH ROW
        WHEN NEW.kind = 'personal'
          AND (
              (SELECT COUNT(*) FROM workspace_members WHERE workspace_id = NEW.id) != 1
              OR (
                  SELECT COUNT(*) FROM workspace_members
                  WHERE workspace_id = NEW.id AND role = 'owner'
              ) != 1
          )
        BEGIN
            SELECT RAISE(ABORT, 'personal workspace requires exactly one owner');
        END
        """
    )


def _drop_personal_workspace_guards() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for name in (
        "trg_workspaces_personal_kind",
        "trg_workspaces_personal_insert",
        "trg_workspace_members_personal_delete",
        "trg_workspace_members_personal_update",
        "trg_workspace_members_personal_insert",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
