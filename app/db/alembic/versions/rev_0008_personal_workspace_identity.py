"""Add personal Workspace owner and template identity.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

from app.db.alembic.versions.rev_0002_workspace_skills import (
    _create_personal_workspace_guards,
    _drop_personal_workspace_guards,
)

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    personal_ids = list(
        bind.execute(sa.text("SELECT id FROM workspaces WHERE kind='personal'"))
        .scalars()
        .all()
    )
    owner_by_workspace: dict[str, str] = {}
    for workspace_id in personal_ids:
        owners = list(
            bind.execute(
                sa.text(
                    "SELECT user_id FROM workspace_members "
                    "WHERE workspace_id=:workspace_id AND role='owner'"
                ),
                {"workspace_id": workspace_id},
            )
            .scalars()
            .all()
        )
        if len(owners) != 1:
            raise RuntimeError(
                f"personal workspace {workspace_id!r} must have exactly one owner"
            )
        owner_by_workspace[workspace_id] = owners[0]

    _drop_personal_workspace_guards()
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.add_column(
            sa.Column("owner_user_id", sa.String(36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("template_id", sa.String(128), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_workspaces_owner",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    for workspace_id in personal_ids:
        bind.execute(
            sa.text(
                "UPDATE workspaces SET owner_user_id=:owner, template_id=id "
                "WHERE id=:workspace_id"
            ),
            {
                "owner": owner_by_workspace[workspace_id],
                "workspace_id": workspace_id,
            },
        )

    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.create_check_constraint(
            "ck_workspaces_personal_identity",
            "kind != 'personal' OR "
            "(owner_user_id IS NOT NULL AND template_id IS NOT NULL)",
        )
    op.create_index(
        "uq_workspaces_personal_owner",
        "workspaces",
        ["owner_user_id"],
        unique=True,
        sqlite_where=sa.text("kind = 'personal'"),
        postgresql_where=sa.text("kind = 'personal'"),
    )
    _create_personal_workspace_guards()
    _create_postgres_personal_workspace_guards()


def downgrade() -> None:
    _drop_postgres_personal_workspace_guards()
    op.drop_index("uq_workspaces_personal_owner", table_name="workspaces")
    _drop_personal_workspace_guards()
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_constraint(
            "ck_workspaces_personal_identity", type_="check"
        )
        batch_op.drop_constraint("fk_workspaces_owner", type_="foreignkey")
        batch_op.drop_column("template_id")
        batch_op.drop_column("owner_user_id")
    _create_personal_workspace_guards()


def _create_postgres_personal_workspace_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_personal_workspace_membership()
        RETURNS trigger AS $$
        DECLARE
            target_workspace_id text;
            target_workspace_ids text[];
            expected_owner_id text;
            member_count integer;
            matching_owner_count integer;
        BEGIN
            IF TG_TABLE_NAME = 'workspaces' THEN
                IF TG_OP = 'INSERT' THEN
                    target_workspace_ids := ARRAY[NEW.id];
                ELSIF TG_OP = 'DELETE' THEN
                    target_workspace_ids := ARRAY[OLD.id];
                ELSE
                    target_workspace_ids := ARRAY[OLD.id, NEW.id];
                END IF;
            ELSE
                IF TG_OP = 'INSERT' THEN
                    target_workspace_ids := ARRAY[NEW.workspace_id];
                ELSIF TG_OP = 'DELETE' THEN
                    target_workspace_ids := ARRAY[OLD.workspace_id];
                ELSE
                    target_workspace_ids := ARRAY[
                        OLD.workspace_id, NEW.workspace_id
                    ];
                END IF;
            END IF;
            FOREACH target_workspace_id IN ARRAY target_workspace_ids LOOP
                SELECT owner_user_id INTO expected_owner_id
                FROM workspaces
                WHERE id = target_workspace_id AND kind = 'personal';
                CONTINUE WHEN expected_owner_id IS NULL;
                SELECT COUNT(*), COUNT(*) FILTER (
                    WHERE user_id = expected_owner_id AND role = 'owner'
                )
                INTO member_count, matching_owner_count
                FROM workspace_members
                WHERE workspace_id = target_workspace_id;
                IF member_count != 1 OR matching_owner_count != 1 THEN
                    RAISE EXCEPTION 'personal workspace requires exactly its declared owner';
                END IF;
            END LOOP;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("workspaces", "workspace_members"):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER trg_{table}_personal_membership
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_personal_workspace_membership()
            """
        )


def _drop_postgres_personal_workspace_guards() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("workspace_members", "workspaces"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_personal_membership ON {table}"
        )
    op.execute("DROP FUNCTION IF EXISTS enforce_personal_workspace_membership()")
