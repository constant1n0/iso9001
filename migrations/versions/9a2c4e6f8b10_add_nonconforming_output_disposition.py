"""Add nonconforming output disposition, concession evidence and process link (8.7).

Existing rows keep NULL fields and meet the concession CHECK. Downgrade loses
these fields. Explicit VARCHAR plus CHECK matches models.text_enum, avoiding
ADD COLUMN enum CHECK differences between Alembic versions.

Revision ID: 9a2c4e6f8b10
Revises: 7c4e9a2b6d10
"""
from alembic import op
import sqlalchemy as sa

revision = '9a2c4e6f8b10'
down_revision = '7c4e9a2b6d10'
branch_labels = None
depends_on = None

TABLE = 'no_conformidades'
TREATMENTS = ('correccion', 'separacion', 'devolucion', 'concesion', 'reproceso', 'desecho')
CONCESSION = (
    "(tratamiento IS NOT NULL AND tratamiento = 'concesion' "
    "AND concesion_autorizada_por_id IS NOT NULL AND fecha_concesion IS NOT NULL) OR "
    "((tratamiento IS NULL OR tratamiento <> 'concesion') "
    "AND concesion_autorizada_por_id IS NULL AND fecha_concesion IS NULL)"
)
LINKS = (('proceso_id', 'procesos_operacion', 'id_proceso'),
         ('concesion_autorizada_por_id', 'personas', 'id'))


def upgrade():
    for column in (
        sa.Column('proceso_id', sa.Integer(), nullable=True),
        sa.Column('tratamiento', sa.String(20), nullable=True),
        sa.Column('concesion_autorizada_por_id', sa.Integer(), nullable=True),
        sa.Column('fecha_concesion', sa.Date(), nullable=True),
        sa.Column('justificacion_concesion', sa.Text(), nullable=True),
    ):
        op.add_column(TABLE, column)
    for column, target, key in LINKS:
        op.create_foreign_key(f'fk_{TABLE}_{column}_{target}', TABLE, target,
                              [column], [key], ondelete='RESTRICT')
        op.create_index(f'ix_{TABLE}_{column}', TABLE, [column])
    allowed = ', '.join(repr(value) for value in TREATMENTS)
    op.create_check_constraint(f'ck_{TABLE}_tratamiento', TABLE, f'tratamiento IN ({allowed})')
    op.create_check_constraint(f'ck_{TABLE}_concesion', TABLE, CONCESSION)


def downgrade():
    op.drop_constraint(f'ck_{TABLE}_concesion', TABLE, type_='check')
    op.drop_constraint(f'ck_{TABLE}_tratamiento', TABLE, type_='check')
    for column, target, _ in reversed(LINKS):
        op.drop_index(f'ix_{TABLE}_{column}', table_name=TABLE)
        op.drop_constraint(f'fk_{TABLE}_{column}_{target}', TABLE, type_='foreignkey')
    for column in ('justificacion_concesion', 'fecha_concesion',
                   'concesion_autorizada_por_id', 'tratamiento', 'proceso_id'):
        op.drop_column(TABLE, column)
