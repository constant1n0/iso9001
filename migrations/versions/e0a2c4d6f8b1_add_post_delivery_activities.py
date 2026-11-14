"""Add post-delivery obligations (8.5.5), preserving PS1–PS4 and audit history.

Integer keys match cited entities. B-tree FK indexes support reverse citations;
status/due supports overdue queries. No backfill; downgrade reverses only PS5.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e0a2c4d6f8b1'
down_revision = 'd9f1a3b5c7e0'
branch_labels = None
depends_on = None
TABLE = 'post_delivery_activities'
REFERENCES = (('customer_id', 'clientes.id'), ('responsible_id', 'personas.id'),
              ('complaint_id', 'reclamaciones.id'), ('nonconformity_id', 'no_conformidades.id'))


def upgrade() -> None:
    """Add empty obligation and order-citation tables with restrictive named keys."""
    op.create_table(TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=f'pk_{TABLE}'),
        *(sa.Column(k, sa.Integer(), nullable=(k != 'responsible_id')) for k, _ in REFERENCES),
        *(sa.ForeignKeyConstraint([k], [target], name=f'fk_{TABLE}_{k}',
            ondelete='RESTRICT', onupdate='RESTRICT') for k, target in REFERENCES),
        *(sa.Column(k, sa.DateTime(timezone=True)) for k in ('created_at', 'updated_at')),
        *(sa.Column(k, sa.Integer()) for k in ('created_by_id', 'updated_by_id')),
        *(sa.ForeignKeyConstraint([k], ['users.id'], name=f'fk_{TABLE}_{k}_users',
            ondelete='SET NULL') for k in ('created_by_id', 'updated_by_id')),
        sa.Column('description', sa.Text(), nullable=False), sa.Column('source', sa.Text()),
        sa.Column('kind', sa.Enum('warranty', 'maintenance', 'technical_support', 'recovery',
            'complementary', 'other', native_enum=False, length=20, create_constraint=True,
            name='ck_post_delivery_kind'), nullable=False),
        sa.Column('status', sa.Enum('pending', 'completed', 'cancelled', native_enum=False,
            length=20, create_constraint=True, name='ck_post_delivery_status'),
            nullable=False, server_default='pending'),
        sa.Column('due_date', sa.Date(), nullable=False), sa.Column('result_date', sa.Date()),
        *(sa.Column(k, sa.Text()) for k in ('result', 'evidence', 'reason')),
        sa.CheckConstraint('length(trim(description)) > 0', name='ck_post_delivery_description'),
        sa.CheckConstraint(
            "(status = 'pending' AND result_date IS NULL AND result IS NULL AND evidence IS NULL AND reason IS NULL) OR "
            "(status = 'completed' AND result_date IS NOT NULL AND result IS NOT NULL AND length(trim(result)) > 0 "
            "AND evidence IS NOT NULL AND length(trim(evidence)) > 0 AND reason IS NULL) OR "
            "(status = 'cancelled' AND result_date IS NOT NULL AND reason IS NOT NULL AND length(trim(reason)) > 0 "
            "AND result IS NULL AND evidence IS NULL)", name='ck_post_delivery_terminal'))
    for key in (*(k for k, _ in REFERENCES), 'created_by_id', 'updated_by_id', 'kind'):
        op.create_index(f'ix_{TABLE}_{key}', TABLE, [key])
    op.create_index(f'ix_{TABLE}_status_due', TABLE, ['status', 'due_date'])
    op.create_table('post_delivery_orders',
        sa.Column('activity_id', sa.Integer(), nullable=False), sa.Column('order_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('activity_id', 'order_id', name='pk_post_delivery_orders'),
        *(sa.ForeignKeyConstraint([k], [target], name=f'fk_post_delivery_orders_{k}',
            ondelete='RESTRICT', onupdate='RESTRICT') for k, target in (
                ('activity_id', f'{TABLE}.id'), ('order_id', 'production_orders.id'))))
    op.create_index('ix_post_delivery_orders_order_id', 'post_delivery_orders', ['order_id'])


def downgrade() -> None:
    """Remove children first; leave all PS1–PS4 tables and rows untouched."""
    op.drop_table('post_delivery_orders')
    op.drop_table(TABLE)
