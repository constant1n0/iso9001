"""add api_tokens

Revision ID: e6b1a4c8d3f7
Revises: d5a9f3b7c1e2
Create Date: 2026-10-03 10:00:00.000000

Only the HMAC of a token is stored. Deleting a user deletes the user's tokens.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6b1a4c8d3f7'
down_revision = 'd5a9f3b7c1e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'api_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('prefix', sa.String(length=8), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scopes', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by_label', sa.String(length=150), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_label', sa.String(length=150), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_api_tokens'),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_api_tokens_user_id_users',
            ondelete='CASCADE',
        ),
        sa.UniqueConstraint('prefix', name='uq_api_tokens_prefix'),
    )
    op.create_index('ix_api_tokens_user_id', 'api_tokens', ['user_id'])


def downgrade():
    op.drop_index('ix_api_tokens_user_id', table_name='api_tokens')
    op.drop_table('api_tokens')
