"""Add immutable communication occurrences (ISO 9001 7.4, CM2).

Revision ID: b1d3e5f7a9c1
Revises: a0c2e4f6b8d0

Additive only; no existing plan or audit row changes. Integer identities match
existing registers. The history B-tree indexes the plan FK and latest lookup;
communicator and attribution FK indexes support reverse references. Downgrade
removes only the new table and its indexes, leaving existing plans untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b1d3e5f7a9c1'
down_revision = 'a0c2e4f6b8d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create an empty immutable log with restrictive plan and person citations."""
    table = 'communication_log'
    op.create_table(
        table,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('communicator_id', sa.Integer(), nullable=False),
        sa.Column('channel', sa.Enum('correo', 'reunion', 'telefono', 'tablon_intranet',
                                    'web', 'otro', native_enum=False, length=20,
                                    create_constraint=True,
                                    name='ck_communication_log_channel'), nullable=False),
        sa.Column('channel_detail', sa.Text()),
        sa.Column('audience_text', sa.Text()),
        sa.Column('summary', sa.Text()),
        sa.Column('evidence', sa.Text(), nullable=False),
        sa.Column('next_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        sa.PrimaryKeyConstraint('id', name='pk_communication_log'),
        sa.CheckConstraint('length(trim(evidence)) > 0', name='ck_communication_log_evidence'),
        sa.CheckConstraint('next_date > date', name='ck_communication_log_next_date'),
        sa.ForeignKeyConstraint(['plan_id'], ['communication_plan.id'],
                                name='fk_communication_log_plan_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['communicator_id'], ['personas.id'],
                                name='fk_communication_log_communicator_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    )
    op.create_index('ix_communication_log_history', table, ['plan_id', 'date', 'id'])
    for column in ('communicator_id', 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade() -> None:
    """Remove only CM2 additions, in reverse order."""
    table = 'communication_log'
    for column in ('updated_by_id', 'created_by_id', 'communicator_id'):
        op.drop_index(f'ix_{table}_{column}', table_name=table)
    op.drop_index('ix_communication_log_history', table_name=table)
    op.drop_table(table)
