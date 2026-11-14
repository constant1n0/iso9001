"""Add third-party property (8.5.3); preserve PS1–PS3 and their audit history.

Integer keys match existing entities. B-tree FK indexes support reverse citations;
the association PK covers item_id. No data backfill; downgrade reverses only PS4.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9f1a3b5c7e0'
down_revision = 'c8e0f2a4b6d8'
branch_labels = None
depends_on = None
REFERENCES = {
    'customer_property': (('customer_id', 'clientes.id'), ('provider_id', 'proveedores.id'),
                          ('responsible_id', 'personas.id')),
    'property_incidents': (('item_id', 'customer_property.id'), ('complaint_id', 'reclamaciones.id'),
                           ('nonconformity_id', 'no_conformidades.id'))}


def _common(table: str) -> list:
    """Nullable explicit attribution and named restrictive indexed domain citations."""
    return [sa.Column('id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=f'pk_{table}'),
        *(sa.Column(k, sa.Integer(), nullable=(k not in ('responsible_id', 'item_id')))
          for k, _ in REFERENCES[table]),
        *(sa.ForeignKeyConstraint([k], [target], name=f'fk_{table}_{k}',
            ondelete='RESTRICT', onupdate='RESTRICT') for k, target in REFERENCES[table]),
        *(sa.Column(k, sa.DateTime(timezone=True)) for k in ('created_at', 'updated_at')),
        *(sa.Column(k, sa.Integer()) for k in ('created_by_id', 'updated_by_id')),
        *(sa.ForeignKeyConstraint([k], ['users.id'], name=f'fk_{table}_{k}_users',
            ondelete='SET NULL') for k in ('created_by_id', 'updated_by_id'))]


def upgrade() -> None:
    """Create empty custody, incident and order-citation tables."""
    table = 'customer_property'
    op.create_table(table, *_common(table),
        *(sa.Column(k, sa.Text(), nullable=False) for k in ('identification', 'description', 'location', 'condition')),
        sa.Column('reference', sa.Text()), sa.Column('received_date', sa.Date(), nullable=False),
        sa.Column('terminal_date', sa.Date()), sa.Column('terminal_note', sa.Text()),
        sa.Column('status', sa.Enum('custody', 'returned', 'disposed', native_enum=False,
            length=20, create_constraint=True, name=f'ck_{table}_status'), nullable=False, server_default='custody'),
        sa.CheckConstraint('(customer_id IS NOT NULL) != (provider_id IS NOT NULL)', name=f'ck_{table}_owner'),
        *(sa.CheckConstraint(f'length(trim({k})) > 0', name=f'ck_{table}_{k}')
          for k in ('identification', 'description', 'location', 'condition')),
        sa.CheckConstraint("(status = 'custody' AND terminal_date IS NULL AND terminal_note IS NULL) OR "
            "(status != 'custody' AND terminal_date IS NOT NULL AND terminal_note IS NOT NULL "
            "AND length(trim(terminal_note)) > 0)", name=f'ck_{table}_terminal'))
    table = 'property_incidents'
    op.create_table(table, *_common(table),
        *(sa.Column(k, sa.Date(), nullable=False) for k in ('date', 'notification_date')),
        *(sa.Column(k, sa.Text(), nullable=False) for k in ('description', 'notification_means')),
        sa.Column('owner_notified', sa.Boolean(), nullable=False),
        sa.Column('kind', sa.Enum('loss', 'damage', 'unsuitable', 'other', native_enum=False,
            length=20, create_constraint=True, name=f'ck_{table}_kind'), nullable=False),
        sa.CheckConstraint('length(trim(description)) > 0', name=f'ck_{table}_description'),
        sa.CheckConstraint('owner_notified AND notification_date >= date AND '
            'length(trim(notification_means)) > 0', name=f'ck_{table}_notification'))
    for table, references in REFERENCES.items():
        for key in (*(k for k, _ in references), 'created_by_id', 'updated_by_id',
                    *(('status',) if table == 'customer_property' else ())):
            op.create_index(f'ix_{table}_{key}', table, [key])
    op.create_table('property_orders',
        sa.Column('item_id', sa.Integer(), nullable=False), sa.Column('order_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('item_id', 'order_id', name='pk_property_orders'),
        *(sa.ForeignKeyConstraint([k], [target], name=f'fk_property_orders_{k}',
            ondelete='RESTRICT', onupdate='RESTRICT') for k, target in (
                ('item_id', 'customer_property.id'), ('order_id', 'production_orders.id'))))
    op.create_index('ix_property_orders_order_id', 'property_orders', ['order_id'])


def downgrade() -> None:
    """Drop children before parents, leaving all prior tables and rows untouched."""
    for table in ('property_orders', 'property_incidents', 'customer_property'):
        op.drop_table(table)
