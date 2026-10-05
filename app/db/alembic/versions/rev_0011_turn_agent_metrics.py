"""Add cache and stable-catalog metrics to Turns.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("turns") as batch_op:
        batch_op.add_column(sa.Column("uncached_input_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("cache_read_input_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("cache_creation_input_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("total_input_tokens", sa.Integer()))
        batch_op.add_column(sa.Column("model_api_turns", sa.Integer()))
        batch_op.add_column(sa.Column("frontend_tool_calls", sa.Integer()))
        batch_op.add_column(sa.Column("tool_search_calls", sa.Integer()))
        batch_op.add_column(sa.Column("tool_set_changes", sa.Integer()))
        batch_op.add_column(sa.Column("catalog_digest_changes", sa.Integer()))


def downgrade() -> None:
    with op.batch_alter_table("turns") as batch_op:
        batch_op.drop_column("catalog_digest_changes")
        batch_op.drop_column("tool_set_changes")
        batch_op.drop_column("tool_search_calls")
        batch_op.drop_column("frontend_tool_calls")
        batch_op.drop_column("model_api_turns")
        batch_op.drop_column("total_input_tokens")
        batch_op.drop_column("cache_creation_input_tokens")
        batch_op.drop_column("cache_read_input_tokens")
        batch_op.drop_column("uncached_input_tokens")
