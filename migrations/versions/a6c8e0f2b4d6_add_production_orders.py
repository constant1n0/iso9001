"""Add production/service orders and exact controlled-condition citations (PS1).

Additive: no existing rows change. Integer keys follow the existing registers.
B-tree FK indexes support reverse citations; state/kind support list filters.
Downgrade removes only these new tables, in reverse dependency order.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a6c8e0f2b4d6'
down_revision = 'f5b7c9d1e3a5'
branch_labels = None
depends_on = None

REFERENCES = (('process_id', 'procesos_operacion.id_proceso'), ('customer_id', 'clientes.id'),
              ('owner_id', 'personas.id'), ('design_output_id', 'design_outputs.id'))
ASSOCIATIONS = (('production_revisions', 'document_revisions.id'),
                ('production_equipment', 'equipos_medicion.id'),
                ('production_people', 'personas.id'))
INDEXES = ('process_id', 'customer_id', 'owner_id', 'design_output_id',
           'state', 'kind', 'created_by_id', 'updated_by_id')


def upgrade() -> None:
    """Create empty orders and restrictive link sets without modifying old tables."""
    table = 'production_orders'
    op.create_table(
        table, sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('kind', sa.Enum('production', 'service', native_enum=False,
                  length=20, create_constraint=True, name='ck_production_orders_kind'), nullable=False),
        sa.Column('state', sa.Enum('planned', 'in_progress', 'finished', 'cancelled',
                  native_enum=False, length=20, create_constraint=True,
                  name='ck_production_orders_state'), nullable=False, server_default='planned'),
        sa.Column('process_id', sa.Integer(), nullable=False),
        *(sa.Column(column, sa.Integer()) for column, _ in REFERENCES if column != 'process_id'),
        sa.Column('lot', sa.Text()), sa.Column('planned_start', sa.Date()),
        sa.Column('planned_end', sa.Date()), sa.Column('cancellation_reason', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()), sa.Column('updated_by_id', sa.Integer()),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
        sa.PrimaryKeyConstraint('id', name='pk_production_orders'),
        sa.UniqueConstraint('code', name='uq_production_orders_code'),
        *(sa.CheckConstraint(f'length(trim({column})) > 0', name=f'ck_{table}_{column}')
          for column in ('code', 'title')),
        sa.CheckConstraint('planned_end >= planned_start', name='ck_production_orders_dates'),
        sa.CheckConstraint(
            "(state = 'cancelled' AND cancellation_reason IS NOT NULL AND "
            "length(trim(cancellation_reason)) > 0) OR "
            "(state != 'cancelled' AND cancellation_reason IS NULL)",
            name='ck_production_orders_cancellation'),
        *(sa.ForeignKeyConstraint([column], [target], name=f'fk_{table}_{column}',
                                  ondelete='RESTRICT', onupdate='RESTRICT')
          for column, target in REFERENCES),
    )
    for column in INDEXES:
        op.create_index(f'ix_{table}_{column}', table, [column])
    for name, target in ASSOCIATIONS:
        op.create_table(
            name, sa.Column('order_id', sa.Integer(), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('order_id', 'target_id', name=f'pk_{name}'),
            sa.ForeignKeyConstraint(['order_id'], ['production_orders.id'],
                                    name=f'fk_{name}_order_id',
                                    ondelete='RESTRICT', onupdate='RESTRICT'),
            sa.ForeignKeyConstraint(['target_id'], [target], name=f'fk_{name}_target_id',
                                    ondelete='RESTRICT', onupdate='RESTRICT'),
        )
        op.create_index(f'ix_{name}_target_id', name, ['target_id'])


def downgrade() -> None:
    """Drop only PS1 additions; the old schema and data remain unchanged."""
    for name, _ in reversed(ASSOCIATIONS):
        op.drop_index(f'ix_{name}_target_id', table_name=name)
        op.drop_table(name)
    for column in reversed(INDEXES):
        op.drop_index(f'ix_production_orders_{column}', table_name='production_orders')
    op.drop_table('production_orders')
