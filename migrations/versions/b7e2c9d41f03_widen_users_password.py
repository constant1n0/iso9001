"""widen users.password to match the model

The model declares String(256); the initial migration created VARCHAR(200).

Revision ID: b7e2c9d41f03
Revises: a1b2c3d4e5f6
Create Date: 2026-09-28 20:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2c9d41f03'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        'users',
        'password',
        existing_type=sa.String(length=200),
        type_=sa.String(length=256),
        existing_nullable=False,
    )


def downgrade():
    # Fails if any stored hash is longer than 200 characters, which is the
    # safe outcome: truncating a hash would lock the user out.
    op.alter_column(
        'users',
        'password',
        existing_type=sa.String(length=256),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
