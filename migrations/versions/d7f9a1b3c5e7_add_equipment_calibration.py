"""Add measuring equipment and immutable calibration/verification history.

New, empty tables only; downgrade drops their evidence and requires a backup.
Equipment check ordering is indexed by equipment/date/id; responsibility has
a RESTRICT foreign key and index following the existing person-link convention.

Revision ID: d7f9a1b3c5e7
Revises: c6e8f0a2b4d6
"""
from alembic import op
import sqlalchemy as sa

revision = 'd7f9a1b3c5e7'
down_revision = 'c6e8f0a2b4d6'
branch_labels = None
depends_on = None


def _metadata(table):
    return [
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
            name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    ]


def upgrade():
    op.create_table(
        'equipos_medicion',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('codigo', sa.String(50), nullable=False, unique=True),
        sa.Column('nombre', sa.String(200), nullable=False),
        sa.Column('numero_serie', sa.String(100)),
        sa.Column('ubicacion', sa.String(200)),
        sa.Column('uso_previsto', sa.Text()),
        sa.Column('responsable_id', sa.Integer()),
        sa.Column('retirado', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['responsable_id'], ['personas.id'],
            name='fk_equipos_medicion_responsable_id_personas', ondelete='RESTRICT'),
        *_metadata('equipos_medicion'),
    )
    op.create_index('ix_equipos_medicion_responsable_id', 'equipos_medicion', ['responsable_id'])
    op.create_table(
        'comprobaciones_equipo',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('equipo_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('tipo', sa.Enum('calibracion', 'verificacion', native_enum=False,
            length=20, create_constraint=True, name='ck_comprobaciones_equipo_tipo'), nullable=False),
        sa.Column('resultado', sa.Enum('apto', 'no_apto', native_enum=False,
            length=20, create_constraint=True, name='ck_comprobaciones_equipo_resultado'), nullable=False),
        sa.Column('realizado_por', sa.String(200), nullable=False),
        sa.Column('referencia', sa.Text(), nullable=False),
        sa.Column('certificado', sa.String(200)),
        sa.Column('fecha_proxima', sa.Date()),
        sa.Column('evaluacion_impacto', sa.Text()),
        sa.Column('acciones', sa.Text()),
        sa.ForeignKeyConstraint(['equipo_id'], ['equipos_medicion.id'],
            name='fk_comprobaciones_equipo_equipo_id', ondelete='RESTRICT'),
        sa.CheckConstraint(
            "(resultado = 'no_apto' AND evaluacion_impacto IS NOT NULL "
            "AND length(trim(evaluacion_impacto)) > 0 AND acciones IS NOT NULL "
            "AND length(trim(acciones)) > 0) OR resultado = 'apto'",
            name='ck_comprobaciones_equipo_impacto'),
        sa.CheckConstraint(
            "(resultado = 'apto' AND fecha_proxima IS NOT NULL AND fecha_proxima > fecha) "
            "OR (resultado = 'no_apto' AND fecha_proxima IS NULL)",
            name='ck_comprobaciones_equipo_proxima'),
        *_metadata('comprobaciones_equipo'),
    )
    op.create_index('ix_comprobaciones_equipo_historial', 'comprobaciones_equipo',
                    ['equipo_id', 'fecha', 'id'])


def downgrade():
    op.drop_table('comprobaciones_equipo')
    op.drop_table('equipos_medicion')
