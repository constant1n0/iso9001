"""Add draft release register and verifications without changing any 8.5 rows.

Integer keys match existing entities. B-tree FK indexes support reverse citations;
the partial unique index reserves each order until rejection. No backfill.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1b3d5e7a9c2'
down_revision = 'e0a2c4d6f8b1'
branch_labels = None
depends_on = None


def _base(table: str, references: tuple) -> list:
    """Named integer keys, RESTRICT citations, nullable attribution as in PS5."""
    return [sa.Column('id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=f'pk_{table}'),
        *(sa.ForeignKeyConstraint([k], [target], name=f'fk_{table}_{k}',
            ondelete='RESTRICT', onupdate='RESTRICT') for k, target in references),
        *(sa.Column(k, sa.DateTime(timezone=True)) for k in ('created_at', 'updated_at')),
        *(sa.Column(k, sa.Integer()) for k in ('created_by_id', 'updated_by_id')),
        *(sa.ForeignKeyConstraint([k], ['users.id'], name=f'fk_{table}_{k}_users',
            ondelete='SET NULL') for k in ('created_by_id', 'updated_by_id'))]


def upgrade() -> None:
    """Create empty release/evidence tables and schema-ready decision columns."""
    table = 'product_releases'
    references = (('order_id', 'production_orders.id'), ('decided_by_id', 'personas.id'),
        ('decided_by_user_id', 'users.id'), ('concession_approver_id', 'personas.id'))
    op.create_table(table, *_base(table, references),
        *(sa.Column(k, sa.Integer(), nullable=k != 'order_id') for k, _ in references),
        sa.Column('notes', sa.Text()),
        sa.Column('status', sa.Enum('draft', 'released', 'rejected', native_enum=False,
            length=20, create_constraint=True, name='ck_product_releases_status'),
            nullable=False, server_default='draft'),
        *(sa.Column(k, sa.Date()) for k in ('decision_date', 'customer_approval_date')),
        *(sa.Column(k, sa.Text()) for k in ('decision_note', 'concession_reason', 'customer_approval_means')),
        sa.Column('concession', sa.Boolean()),
        sa.CheckConstraint(
            "(status = 'draft' AND decision_date IS NULL AND decided_by_id IS NULL "
            "AND decided_by_user_id IS NULL AND decision_note IS NULL AND concession IS NULL "
            "AND concession_reason IS NULL AND concession_approver_id IS NULL "
            "AND customer_approval_date IS NULL AND customer_approval_means IS NULL) OR "
            "(status != 'draft' AND decision_date IS NOT NULL AND decided_by_id IS NOT NULL "
            "AND decided_by_user_id IS NOT NULL AND concession IS NOT NULL)", name='ck_product_releases_decision'),
        sa.CheckConstraint(
            "(concession IS NULL OR NOT concession) AND concession_reason IS NULL "
            "AND concession_approver_id IS NULL AND customer_approval_date IS NULL "
            "AND customer_approval_means IS NULL OR (concession AND status = 'released' "
            "AND concession_reason IS NOT NULL AND length(trim(concession_reason)) > 0 "
            "AND concession_approver_id IS NOT NULL)", name='ck_product_releases_concession'),
        sa.CheckConstraint("status != 'rejected' OR (decision_note IS NOT NULL "
            "AND length(trim(decision_note)) > 0)", name='ck_product_releases_rejection'),
        sa.CheckConstraint('(customer_approval_date IS NULL AND customer_approval_means IS NULL) OR '
            '(customer_approval_date IS NOT NULL AND customer_approval_means IS NOT NULL '
            'AND length(trim(customer_approval_means)) > 0)', name='ck_product_releases_customer'))
    for key in (*(k for k, _ in references), 'created_by_id', 'updated_by_id', 'status'):
        op.create_index(f'ix_{table}_{key}', table, [key])
    op.create_index('uq_product_releases_open_order', table, ['order_id'], unique=True,
        sqlite_where=sa.text("status != 'rejected'"), postgresql_where=sa.text("status != 'rejected'"))
    table = 'release_verifications'
    references = (('release_id', 'product_releases.id'), ('person_id', 'personas.id'))
    op.create_table(table, *_base(table, references),
        *(sa.Column(k, sa.Integer(), nullable=False) for k, _ in references),
        *(sa.Column(k, sa.Text(), nullable=False) for k in ('criterion', 'evidence')),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('result', sa.Enum('conforming', 'nonconforming', native_enum=False,
            length=20, create_constraint=True, name='ck_release_verifications_result'), nullable=False),
        *(sa.CheckConstraint(f'length(trim({k})) > 0', name=f'ck_release_verifications_{k}')
          for k in ('criterion', 'evidence')))
    for key in (*(k for k, _ in references), 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_{table}_{key}', table, [key])


def downgrade() -> None:
    """Reverse children first; keep 8.5 and all existing audit rows untouched."""
    op.drop_table('release_verifications')
    op.drop_table('product_releases')
