"""Add the communication plan and its audience links (ISO 9001 7.4, CM1).

Revision ID: a0c2e4f6b8d0
Revises: f9b1d3e5a7c9

Additive tables only; existing records are untouched. Integer identities match
all existing registers. Composite audience PKs index the parent FK; target and
owner B-tree indexes support reverse citation and restrictive deletion checks.
No occurrence log or sending infrastructure is created. Downgrade removes only
these additions in reverse dependency order.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a0c2e4f6b8d0'
down_revision = 'f9b1d3e5a7c9'
branch_labels = None
depends_on = None

ASSOCIATIONS = (
    ('communication_stakeholders', 'partes_interesadas.id_interesado'),
    ('communication_customers', 'clientes.id'),
    ('communication_providers', 'proveedores.id'),
    ('communication_people', 'personas.id'),
)


def upgrade() -> None:
    """Create an empty plan register and restrictive audience association tables."""
    table = 'communication_plan'
    op.create_table(
        table,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('scope', sa.Enum('interna', 'externa', 'ambas', native_enum=False,
                                  length=20, create_constraint=True,
                                  name='ck_communication_plan_scope'), nullable=False),
        sa.Column('channel', sa.Enum('correo', 'reunion', 'telefono', 'tablon_intranet',
                                    'web', 'otro', native_enum=False, length=20,
                                    create_constraint=True,
                                    name='ck_communication_plan_channel'), nullable=False),
        sa.Column('channel_detail', sa.Text()),
        sa.Column('audience_text', sa.Text()),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('frequency', sa.Text(), nullable=False),
        sa.Column('next_date', sa.Date(), nullable=False),
        sa.Column('retired', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        sa.PrimaryKeyConstraint('id', name='pk_communication_plan'),
        *(sa.CheckConstraint(f'length(trim({column})) > 0',
                             name=f'ck_communication_plan_{column}')
          for column in ('title', 'content', 'frequency')),
        sa.ForeignKeyConstraint(['owner_id'], ['personas.id'],
                                name='fk_communication_plan_owner_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    )
    op.create_index('ix_communication_plan_owner_id', table, ['owner_id'])
    for name, target in ASSOCIATIONS:
        op.create_table(
            name,
            sa.Column('communication_id', sa.Integer(), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('communication_id', 'target_id', name=f'pk_{name}'),
            sa.ForeignKeyConstraint(
                ['communication_id'], ['communication_plan.id'],
                name=f'fk_{name}_communication_id', ondelete='RESTRICT', onupdate='RESTRICT'),
            sa.ForeignKeyConstraint(
                ['target_id'], [target], name=f'fk_{name}_target_id',
                ondelete='RESTRICT', onupdate='RESTRICT'),
        )
        op.create_index(f'ix_{name}_target_id', name, ['target_id'])


def downgrade() -> None:
    """Remove audience tables before their parent, preserving all prior tables."""
    for name, _ in reversed(ASSOCIATIONS):
        op.drop_index(f'ix_{name}_target_id', table_name=name)
        op.drop_table(name)
    op.drop_index('ix_communication_plan_owner_id', table_name='communication_plan')
    op.drop_table('communication_plan')
