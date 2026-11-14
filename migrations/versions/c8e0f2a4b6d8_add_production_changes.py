"""Add the production change register (8.5.6), without changing PS1/PS2 rows.

Integer keys match existing records. B-tree state and FK indexes support filters
and reverse citations; association PK covers change_id. Downgrade reverses only PS3.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8e0f2a4b6d8'
down_revision = 'b7d9f1a3c5e7'
branch_labels = None
depends_on = None
TABLE = 'production_changes'
LINK = 'production_change_orders'
REFERENCES = (('design_change_id', 'design_changes.id'),
              ('qms_change_id', 'cambios_sgc.id'), ('authorized_by_id', 'personas.id'))
INDEXES = (*(key for key, _ in REFERENCES), 'state', 'created_by_id', 'updated_by_id')


def upgrade() -> None:
    """Create empty tables with named CHECKs, indexed restrictive citations and attribution."""
    op.create_table(TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        *(sa.Column(key, sa.Text(), nullable=False) for key in ('reason', 'description', 'actions')),
        *(sa.Column(key, sa.Text()) for key in ('review', 'rejection_reason')),
        *(sa.Column(key, sa.Integer()) for key, _ in REFERENCES),
        sa.Column('authorized_at', sa.Date()),
        sa.Column('state', sa.Enum('proposed', 'authorized', 'rejected', native_enum=False,
            length=20, create_constraint=True, name=f'ck_{TABLE}_state'),
            nullable=False, server_default='proposed'),
        sa.PrimaryKeyConstraint('id', name=f'pk_{TABLE}'),
        *(sa.CheckConstraint(f'length(trim({key})) > 0', name=f'ck_{TABLE}_{key}')
          for key in ('reason', 'description', 'actions')),
        sa.CheckConstraint(
            "(state = 'authorized' AND authorized_by_id IS NOT NULL AND "
            "authorized_at IS NOT NULL AND review IS NOT NULL AND length(trim(review)) > 0) "
            "OR (state != 'authorized' AND authorized_by_id IS NULL AND authorized_at IS NULL)",
            name=f'ck_{TABLE}_authorization'),
        sa.CheckConstraint(
            "(state = 'rejected' AND rejection_reason IS NOT NULL AND "
            "length(trim(rejection_reason)) > 0) OR "
            "(state != 'rejected' AND rejection_reason IS NULL)", name=f'ck_{TABLE}_rejection'),
        *(sa.ForeignKeyConstraint([key], [target], name=f'fk_{TABLE}_{key}',
            ondelete='RESTRICT', onupdate='RESTRICT') for key, target in REFERENCES),
        *(sa.Column(key, sa.DateTime(timezone=True)) for key in ('created_at', 'updated_at')),
        *(sa.Column(key, sa.Integer()) for key in ('created_by_id', 'updated_by_id')),
        *(sa.ForeignKeyConstraint([key], ['users.id'], name=f'fk_{TABLE}_{key}_users',
            ondelete='SET NULL') for key in ('created_by_id', 'updated_by_id')),
    )
    for key in INDEXES:
        op.create_index(f'ix_{TABLE}_{key}', TABLE, [key])
    op.create_table(LINK,
        sa.Column('change_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('change_id', 'order_id', name=f'pk_{LINK}'),
        *(sa.ForeignKeyConstraint([key], [target], name=f'fk_{LINK}_{key}',
            ondelete='RESTRICT', onupdate='RESTRICT') for key, target in (
                ('change_id', f'{TABLE}.id'), ('order_id', 'production_orders.id'))),
    )
    op.create_index(f'ix_{LINK}_order_id', LINK, ['order_id'])


def downgrade() -> None:
    """Drop links before changes, preserving all older records and their evidence."""
    op.drop_index(f'ix_{LINK}_order_id', table_name=LINK)
    op.drop_table(LINK)
    for key in reversed(INDEXES):
        op.drop_index(f'ix_{TABLE}_{key}', table_name=TABLE)
    op.drop_table(TABLE)
