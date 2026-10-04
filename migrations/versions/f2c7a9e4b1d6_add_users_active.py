"""add users.active

Users are deactivated instead of deleted. Existing users stay active through
the server default.

Revision ID: f2c7a9e4b1d6
Revises: e6b1a4c8d3f7
Create Date: 2026-10-04 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2c7a9e4b1d6'
down_revision = 'e6b1a4c8d3f7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'users',
        sa.Column(
            'active',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade():
    op.drop_column('users', 'active')
