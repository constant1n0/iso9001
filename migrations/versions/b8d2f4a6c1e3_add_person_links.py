"""link trainings, nonconformities and audits to personas

Each register gains a nullable reference to the person it names, next to the
legacy free-text column, which stays as it is (no automatic matching). The
foreign keys are ``ON DELETE RESTRICT``: a person still cited is deactivated
rather than deleted.

Revision ID: b8d2f4a6c1e3
Revises: a3c5e7f9b2d4
Create Date: 2026-10-08 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d2f4a6c1e3'
down_revision = 'a3c5e7f9b2d4'
branch_labels = None
depends_on = None

# (table, column) pairs; constraint and index names match ``models.person_link``.
LINKS = (
    ('capacitaciones', 'persona_id'),
    ('no_conformidades', 'responsable_id'),
    ('auditorias', 'auditor_id'),
)


def upgrade():
    for table, column in LINKS:
        op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))
        op.create_foreign_key(
            f'fk_{table}_{column}_personas',
            table,
            'personas',
            [column],
            ['id'],
            ondelete='RESTRICT',
        )
        op.create_index(f'ix_{table}_{column}', table, [column])


def downgrade():
    for table, column in reversed(LINKS):
        op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_constraint(f'fk_{table}_{column}_personas', table, type_='foreignkey')
        op.drop_column(table, column)
