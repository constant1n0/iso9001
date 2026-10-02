"""add created/updated attribution columns to the domain tables

Revision ID: d5a9f3b7c1e2
Revises: c4d8e1f2a9b7
Create Date: 2026-10-02 15:00:00.000000

Columns are nullable with no server default and no backfill: rows that
existed before this revision keep NULL instead of an invented attribution.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5a9f3b7c1e2'
down_revision = 'c4d8e1f2a9b7'
branch_labels = None
depends_on = None

TABLES = (
    'auditorias',
    'auditorias_indicadores',
    'capacitaciones',
    'documents',
    'mejoras',
    'no_conformidades',
    'partes_interesadas',
    'procesos_operacion',
    'recursos_capacitacion',
    'riesgos_oportunidades',
    'roles_responsabilidades',
    'satisfaccion_cliente',
)


def upgrade():
    for table in TABLES:
        op.add_column(
            table, sa.Column('created_at', sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(
            table, sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True)
        )
        for column in ('created_by_id', 'updated_by_id'):
            op.add_column(table, sa.Column(column, sa.Integer(), nullable=True))
            op.create_foreign_key(
                f'fk_{table}_{column}_users',
                table,
                'users',
                [column],
                ['id'],
                ondelete='SET NULL',
            )


def downgrade():
    for table in reversed(TABLES):
        for column in ('updated_by_id', 'created_by_id'):
            op.drop_constraint(f'fk_{table}_{column}_users', table, type_='foreignkey')
        for column in ('updated_by_id', 'created_by_id', 'updated_at', 'created_at'):
            op.drop_column(table, column)
