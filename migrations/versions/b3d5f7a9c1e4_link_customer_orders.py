"""Link customer evidence and optional production orders (8.2.1).

Additive: legacy rows keep a NULL citation. Integer keys match existing records.
Composite primary keys index each parent; B-trees support reverse FK lookups.
No data backfill or state change. Downgrade removes only these additions.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b3d5f7a9c1e4'
down_revision = 'a2c4e6f8b0d3'
branch_labels = None
depends_on = None

ASSOCIATIONS = (('customer_order_communications', 'communication_log.id'),
                ('customer_order_complaints', 'reclamaciones.id'))


def upgrade() -> None:
    """Add restrictive evidence links without altering existing rows."""
    with op.batch_alter_table('production_orders') as batch:
        batch.add_column(sa.Column('customer_order_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_production_orders_customer_order_id',
            'customer_orders', ['customer_order_id'], ['id'],
            ondelete='RESTRICT', onupdate='RESTRICT')
        batch.create_index('ix_production_orders_customer_order_id', ['customer_order_id'])
    for name, target in ASSOCIATIONS:
        op.create_table(name,
            sa.Column('order_id', sa.Integer(), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('order_id', 'target_id', name=f'pk_{name}'),
            *(sa.ForeignKeyConstraint([key], [reference], name=f'fk_{name}_{key}',
                ondelete='RESTRICT', onupdate='RESTRICT') for key, reference in (
                    ('order_id', 'customer_orders.id'), ('target_id', target))))
        op.create_index(f'ix_{name}_target_id', name, ['target_id'])


def downgrade() -> None:
    """Reverse dependencies, preserving all pre-existing tables and records."""
    for name, _ in reversed(ASSOCIATIONS):
        op.drop_index(f'ix_{name}_target_id', table_name=name)
        op.drop_table(name)
    with op.batch_alter_table('production_orders') as batch:
        batch.drop_index('ix_production_orders_customer_order_id')
        batch.drop_constraint('fk_production_orders_customer_order_id', type_='foreignkey')
        batch.drop_column('customer_order_id')
