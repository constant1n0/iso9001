"""add audit_logs append-only table

Revision ID: c4d8e1f2a9b7
Revises: b7e2c9d41f03
Create Date: 2026-10-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c4d8e1f2a9b7'
down_revision = 'b7e2c9d41f03'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            'occurred_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('entity_type', sa.String(length=64), nullable=False),
        sa.Column('entity_id', sa.BigInteger(), nullable=True),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('actor_label', sa.String(length=150), nullable=False),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column(
            'before',
            sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
            nullable=True,
        ),
        sa.Column(
            'after',
            sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
            nullable=True,
        ),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_audit_logs'),
        sa.ForeignKeyConstraint(
            ['actor_user_id'],
            ['users.id'],
            name='fk_audit_logs_actor_user_id_users',
            ondelete='SET NULL',
        ),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete')",
            name='ck_audit_logs_action',
        ),
        sa.CheckConstraint(
            "channel IN ('web', 'mcp', 'cli', 'system')",
            name='ck_audit_logs_channel',
        ),
    )
    op.create_index(
        'ix_audit_logs_entity',
        'audit_logs',
        ['entity_type', 'entity_id', 'id'],
    )
    op.create_index('ix_audit_logs_occurred_at', 'audit_logs', ['occurred_at'])
    op.create_index('ix_audit_logs_actor_user_id', 'audit_logs', ['actor_user_id'])


def downgrade():
    op.drop_index('ix_audit_logs_actor_user_id', table_name='audit_logs')
    op.drop_index('ix_audit_logs_occurred_at', table_name='audit_logs')
    op.drop_index('ix_audit_logs_entity', table_name='audit_logs')
    op.drop_table('audit_logs')
