"""Add commercial identities and numbered requirements; no existing rows change.

Integer keys match customers/people. B-tree reverse FK and attribution indexes;
(order_id, number) serves revision history. Review lives one-to-one on its revision.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a2c4e6f8b0d3'
down_revision = 'f1b3d5e7a9c2'
branch_labels = None
depends_on = None


def _base(table: str, references: tuple) -> list:
    return [sa.Column('id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=f'pk_{table}'),
        *(sa.ForeignKeyConstraint([k], [target], name=f'fk_{table}_{k}',
            ondelete='RESTRICT', onupdate='RESTRICT') for k, target in references),
        *(sa.Column(k, sa.DateTime(timezone=True)) for k in ('created_at', 'updated_at')),
        *(sa.Column(k, sa.Integer()) for k in ('created_by_id', 'updated_by_id')),
        *(sa.ForeignKeyConstraint([k], ['users.id'], name=f'fk_{table}_{k}_users',
            ondelete='SET NULL') for k in ('created_by_id', 'updated_by_id'))]


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, create_constraint=True, length=20, name=name)


def upgrade() -> None:
    """Create empty additive tables with named keys and null-safe review CHECKs."""
    table = 'customer_orders'
    op.create_table(table, *_base(table, (('customer_id', 'clientes.id'),)),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(50), nullable=False),
        sa.UniqueConstraint('code', name='uq_customer_orders_code'),
        sa.Column('kind', _enum('ck_customer_orders_kind', 'order', 'contract', 'accepted_offer'), nullable=False),
        sa.Column('customer_reference', sa.Text()), sa.Column('received_date', sa.Date(), nullable=False),
        sa.Column('cancelled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('cancellation_reason', sa.Text()),
        sa.CheckConstraint('length(trim(code)) > 0', name='ck_customer_orders_code'),
        sa.CheckConstraint('(cancelled AND cancellation_reason IS NOT NULL AND '
            'length(trim(cancellation_reason)) > 0) OR '
            '(NOT cancelled AND cancellation_reason IS NULL)', name='ck_customer_orders_cancelled'))
    for key in ('customer_id', 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_{table}_{key}', table, [key])
    table = 'customer_order_revisions'
    review_keys = ('reviewer_id', 'review_date', 'differences_resolved', 'undocumented',
                   *(f'check_{c}' for c in 'abcde'))
    optional = ('confirmation_date', 'confirmation_means', *(f'note_{c}' for c in 'abcde'))
    complete = ('(result IS NULL AND ' + ' AND '.join(f'{k} IS NULL' for k in (*review_keys, *optional))
        + ') OR (result IS NOT NULL AND ' + ' AND '.join(f'{k} IS NOT NULL' for k in review_keys) + ')')
    acceptance = "result IS NULL OR result != 'accepted' OR (differences_resolved AND " + (
        ' AND '.join(f"check_{c} IN ('conforming', 'not_applicable')" for c in 'abcde')) + ')'
    confirmation = ('(undocumented IS NULL AND confirmation_date IS NULL AND confirmation_means IS NULL) OR '
        '(undocumented IS NOT NULL AND ((NOT undocumented AND confirmation_date IS NULL AND confirmation_means IS NULL) '
        'OR (undocumented AND confirmation_date IS NOT NULL AND confirmation_date <= review_date '
        'AND confirmation_means IS NOT NULL AND length(trim(confirmation_means)) > 0)))')
    op.create_table(table, *_base(table, (('order_id', 'customer_orders.id'), ('reviewer_id', 'personas.id'))),
        sa.Column('order_id', sa.Integer(), nullable=False), sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('requirements', sa.Text(), nullable=False),
        *(sa.Column(k, sa.Text()) for k in ('delivery_requirements', 'post_delivery_requirements',
            'change_reason', 'communicated_to', 'confirmation_means', *(f'note_{c}' for c in 'abcde'))),
        sa.Column('reviewer_id', sa.Integer()), sa.Column('review_date', sa.Date()),
        sa.Column('confirmation_date', sa.Date()), sa.Column('differences_resolved', sa.Boolean()),
        sa.Column('undocumented', sa.Boolean()),
        sa.Column('result', _enum('ck_customer_order_revisions_result', 'accepted', 'rejected')),
        *(sa.Column(f'check_{c}', _enum(f'ck_order_check_{c}', 'conforming', 'nonconforming', 'not_applicable')) for c in 'abcde'),
        sa.UniqueConstraint('order_id', 'number', name='uq_customer_order_revisions_number'),
        *(sa.CheckConstraint(sql, name=f'ck_customer_order_revisions_{name}') for name, sql in (
            ('number', 'number >= 1'), ('requirements', 'length(trim(requirements)) > 0'),
            ('change', 'number = 1 OR (change_reason IS NOT NULL AND length(trim(change_reason)) > 0 '
             'AND communicated_to IS NOT NULL AND length(trim(communicated_to)) > 0)'),
            ('review', complete), ('acceptance', acceptance), ('confirmation', confirmation))))
    for key in ('order_id', 'reviewer_id', 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_{table}_{key}', table, [key])


def downgrade() -> None:
    """Remove children first; preserve all pre-existing rows and audit evidence."""
    op.drop_table('customer_order_revisions')
    op.drop_table('customer_orders')
