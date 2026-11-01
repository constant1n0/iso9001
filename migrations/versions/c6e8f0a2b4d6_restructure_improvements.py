# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Restructure the empty improvement register (ISO 9001 10.1/10.3).

No legacy rows exist in production, so upgrade performs no backfill. Downgrade
keeps the description as the legacy nonconformity text and loses lifecycle data.

Revision ID: c6e8f0a2b4d6
Revises: ab4c6e8f0a12
"""
from alembic import op
import sqlalchemy as sa

revision = 'c6e8f0a2b4d6'
down_revision = 'ab4c6e8f0a12'
branch_labels = None
depends_on = None

LINKS = (
    ('no_conformidad_id', 'no_conformidad', 'no_conformidades'),
    ('hallazgo_id', 'auditoria', 'hallazgos_auditoria'),
    ('accion_revision_id', 'revision_direccion', 'acciones_revision'),
    ('oportunidad_id', 'oportunidad', 'oportunidades'),
    ('objetivo_id', 'objetivo', 'objetivos_calidad'),
)
PEOPLE = ('responsable_id', 'aprobado_por_id', 'revisor_eficacia_id')
ENUMS = {
    'origen': ('no_conformidad', 'auditoria', 'revision_direccion', 'oportunidad', 'objetivo', 'otro'),
    'estado': ('propuesta', 'aprobada', 'implantada', 'eficacia_revisada', 'cancelada'),
    'eficacia': ('eficaz', 'no_eficaz'),
}
CHECKS = {
    'aprobacion': (
        "(aprobado_por_id IS NULL AND fecha_aprobacion IS NULL "
        "AND estado IN ('propuesta', 'cancelada')) OR "
        "(aprobado_por_id IS NOT NULL AND fecha_aprobacion IS NOT NULL AND estado <> 'propuesta')"),
    'implantacion': (
        "(fecha_implementacion IS NOT NULL AND estado IN ('implantada', 'eficacia_revisada')) OR "
        "(fecha_implementacion IS NULL AND estado NOT IN ('implantada', 'eficacia_revisada'))"),
    'revision_eficacia': (
        "(eficacia IS NOT NULL AND fecha_eficacia IS NOT NULL AND revisor_eficacia_id IS NOT NULL "
        "AND estado = 'eficacia_revisada') OR "
        "(eficacia IS NULL AND fecha_eficacia IS NULL AND revisor_eficacia_id IS NULL "
        "AND estado <> 'eficacia_revisada')"),
    'comentario_eficacia': "eficacia IS NULL OR eficacia = 'eficaz' OR comentario_eficacia IS NOT NULL",
    'revisor_eficacia': "revisor_eficacia_id IS NULL OR revisor_eficacia_id <> responsable_id",
    'cancelacion': (
        "(motivo_cancelacion IS NOT NULL AND fecha_cancelacion IS NOT NULL "
        "AND estado = 'cancelada') OR "
        "(motivo_cancelacion IS NULL AND fecha_cancelacion IS NULL AND estado <> 'cancelada')"),
}
TEXTS = ('beneficio', 'comentario_eficacia', 'motivo_cancelacion')
DATES = ('fecha_aprobacion', 'fecha_eficacia', 'fecha_cancelacion')


def upgrade():
    with op.batch_alter_table('mejoras') as batch:
        batch.add_column(sa.Column('descripcion', sa.Text(), nullable=False))
        batch.add_column(sa.Column('fecha_identificacion', sa.Date(), nullable=False))
        for column in TEXTS:
            batch.add_column(sa.Column(column, sa.Text(), nullable=True))
        for column in DATES:
            batch.add_column(sa.Column(column, sa.Date(), nullable=True))
        for column, values in ENUMS.items():
            default = {'origen': 'otro', 'estado': 'propuesta'}.get(column)
            batch.add_column(sa.Column(column, sa.String(20), nullable=column == 'eficacia',
                                       server_default=default))
            allowed = ', '.join(repr(value) for value in values)
            batch.create_check_constraint(f'ck_mejoras_{column}', f'{column} IN ({allowed})')
        for column, origin, target in LINKS:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(f'fk_mejoras_{column}_{target}', target, [column], ['id'],
                                     ondelete='RESTRICT')
            batch.create_index(f'ix_mejoras_{column}', [column])
            batch.create_check_constraint(f'ck_mejoras_{column.removesuffix("_id")}',
                                           f"{column} IS NULL OR origen = '{origin}'")
        for column in PEOPLE:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(f'fk_mejoras_{column}_personas', 'personas', [column], ['id'],
                                     ondelete='RESTRICT')
            batch.create_index(f'ix_mejoras_{column}', [column])
        batch.alter_column('fecha_implementacion', existing_type=sa.DateTime(),
                           type_=sa.Date(), existing_nullable=True, server_default=None)
        for name, expression in CHECKS.items():
            batch.create_check_constraint(f'ck_mejoras_{name}', expression)
        for column in ('no_conformidad', 'accion_correctiva', 'accion_preventiva'):
            batch.drop_column(column)


def downgrade():
    with op.batch_alter_table('mejoras') as batch:
        for name in CHECKS:
            batch.drop_constraint(f'ck_mejoras_{name}', type_='check')
        for column, _, target in LINKS:
            batch.drop_constraint(f'ck_mejoras_{column.removesuffix("_id")}', type_='check')
            batch.drop_constraint(f'fk_mejoras_{column}_{target}', type_='foreignkey')
            batch.drop_index(f'ix_mejoras_{column}')
            batch.drop_column(column)
        for column in PEOPLE:
            batch.drop_constraint(f'fk_mejoras_{column}_personas', type_='foreignkey')
            batch.drop_index(f'ix_mejoras_{column}')
            batch.drop_column(column)
        for column in ENUMS:
            batch.drop_constraint(f'ck_mejoras_{column}', type_='check')
            batch.drop_column(column)
        for column in (*TEXTS, *DATES, 'fecha_identificacion'):
            batch.drop_column(column)
        batch.alter_column('descripcion', new_column_name='no_conformidad',
                           existing_type=sa.Text(), existing_nullable=False)
        batch.add_column(sa.Column('accion_correctiva', sa.Text(), nullable=True))
        batch.add_column(sa.Column('accion_preventiva', sa.Text(), nullable=True))
        batch.alter_column('fecha_implementacion', existing_type=sa.Date(),
                           type_=sa.DateTime(), existing_nullable=True)
