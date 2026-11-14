"""Add immutable production execution and preservation evidence (PS2).

Additive empty tables; no PS1 data changes. Integer keys match existing orders.
B-tree FK indexes support reverse citations, history indexes order/date/id reads.
Downgrade drops only these additions in reverse order.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7d9f1a3c5e7'
down_revision = 'a6c8e0f2b4d6'
branch_labels = None
depends_on = None
TABLES = ('production_control_records', 'production_preservation_records')


def upgrade() -> None:
    """Create restrictive citations and named content/enum checks."""
    for table in TABLES:
        control = table == TABLES[0]
        selector = 'kind' if control else 'aspect'
        members = (('inspection', 'measurement', 'conditions', 'other') if control else
                   ('identification', 'handling', 'packaging', 'storage', 'transport', 'protection', 'other'))
        references = (('order_id', 'production_orders.id'), ('responsible_id', 'personas.id'),
                      ('nonconformity_id', 'no_conformidades.id'))
        if control:
            references += (('equipment_id', 'equipos_medicion.id'),)
        op.create_table(table,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('order_id', sa.Integer(), nullable=False),
            sa.Column('responsible_id', sa.Integer(), nullable=False),
            sa.Column('nonconformity_id', sa.Integer()),
            sa.Column('date', sa.Date(), nullable=False),
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column(selector, sa.Enum(*members, native_enum=False, length=20,
                create_constraint=True, name=f'ck_{table}_{selector}'), nullable=False),
            sa.Column('result', sa.Enum('conforming', 'nonconforming', native_enum=False,
                length=20, create_constraint=True, name=f'ck_{table}_result'), nullable=False),
            *((sa.Column('equipment_id', sa.Integer()), sa.Column('actions', sa.Text()),
               sa.Column('evidence', sa.Text(), nullable=False),
               sa.CheckConstraint('length(trim(evidence)) > 0', name=f'ck_{table}_evidence'),
               sa.CheckConstraint("result = 'conforming' OR nonconformity_id IS NOT NULL OR "
                   '(actions IS NOT NULL AND length(trim(actions)) > 0)', name=f'ck_{table}_actions'))
              if control else ()),
            sa.CheckConstraint('length(trim(description)) > 0', name=f'ck_{table}_description'),
            sa.PrimaryKeyConstraint('id', name=f'pk_{table}'),
            *(sa.ForeignKeyConstraint([column], [target], name=f'fk_{table}_{column}',
                ondelete='RESTRICT', onupdate='RESTRICT') for column, target in references),
            *(sa.Column(column, sa.DateTime(timezone=True)) for column in ('created_at', 'updated_at')),
            *(sa.Column(column, sa.Integer()) for column in ('created_by_id', 'updated_by_id')),
            *(sa.ForeignKeyConstraint([column], ['users.id'], name=f'fk_{table}_{column}_users',
                ondelete='SET NULL') for column in ('created_by_id', 'updated_by_id')),
        )
        for column in (*(column for column, _ in references), 'created_by_id', 'updated_by_id'):
            op.create_index(f'ix_{table}_{column}', table, [column])
        op.create_index(f'ix_{table}_history', table, ['order_id', 'date', 'id'])


def downgrade() -> None:
    """Reverse PS2 without modifying orders, conditions, or any earlier rows."""
    for table in reversed(TABLES):
        op.drop_index(f'ix_{table}_history', table_name=table)
        columns = ('order_id', 'responsible_id', 'nonconformity_id',
                   *(('equipment_id',) if table == TABLES[0] else ()), 'created_by_id', 'updated_by_id')
        for column in reversed(columns):
            op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_table(table)
