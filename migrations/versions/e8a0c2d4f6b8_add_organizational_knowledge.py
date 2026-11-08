"""Add the organizational knowledge register (7.1.6, KN1).

Only a new empty table is added; existing records and attribution are untouched.
Owner references use an indexed RESTRICT foreign key. Downgrade removes the
register and its contents; back up its evidence before downgrading.

Revision ID: e8a0c2d4f6b8
Revises: d7f9a1b3c5e7
"""
from alembic import op
import sqlalchemy as sa

revision = 'e8a0c2d4f6b8'
down_revision = 'd7f9a1b3c5e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the register with explicit integrity and attribution constraints."""
    op.create_table(
        'knowledge_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('conocimiento', 'leccion', native_enum=False,
            length=20, create_constraint=True, name='ck_knowledge_items_kind'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('source', sa.Text()),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('next_review_date', sa.Date()),
        sa.Column('retired', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        sa.PrimaryKeyConstraint('id', name='pk_knowledge_items'),
        sa.ForeignKeyConstraint(['owner_id'], ['personas.id'],
            name='fk_knowledge_items_owner_id_personas', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'],
            name='fk_knowledge_items_created_by_id_users', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'],
            name='fk_knowledge_items_updated_by_id_users', ondelete='SET NULL'),
        sa.CheckConstraint('length(trim(title)) > 0', name='ck_knowledge_items_title'),
        sa.CheckConstraint('length(trim(summary)) > 0', name='ck_knowledge_items_summary'),
    )
    op.create_index('ix_knowledge_items_owner_id', 'knowledge_items', ['owner_id'])


def downgrade() -> None:
    """Remove the index before its table, reversing the additive upgrade."""
    op.drop_index('ix_knowledge_items_owner_id', table_name='knowledge_items')
    op.drop_table('knowledge_items')
