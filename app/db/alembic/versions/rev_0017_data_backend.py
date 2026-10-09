"""Persist per-session data backend and verified MCP tool definitions."""
import sqlalchemy as sa
from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('sessions', sa.Column('data_backend', sa.String(16), nullable=False, server_default='sqlbot'))
    op.add_column('sessions', sa.Column('data_mcp_tools_json', sa.Text(), nullable=False, server_default='[]'))


def downgrade():
    op.drop_column('sessions', 'data_mcp_tools_json')
    op.drop_column('sessions', 'data_backend')
