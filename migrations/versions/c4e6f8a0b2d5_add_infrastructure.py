"""Add infrastructure and immutable maintenance history (7.1.3).

Revision ID: c4e6f8a0b2d5
Revises: b3d5f7a9c1e4

Additive only. Downgrade removes the new register and its history, not any
existing people, processes, nonconformities or audit rows; back up evidence first.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c4e6f8a0b2d5'
down_revision = 'b3d5f7a9c1e4'
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


def _enum(name, *values):
    return sa.Enum(*values, native_enum=False, length=20, create_constraint=True, name=name)


def upgrade():
    op.create_table(
        'infraestructura',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('codigo', sa.String(50), nullable=False, unique=True),
        sa.Column('nombre', sa.String(200), nullable=False),
        sa.Column('tipo', _enum('ck_infraestructura_tipo', 'edificio', 'equipo_proceso',
                                'hardware_software', 'transporte', 'tic'), nullable=False),
        sa.Column('responsable_id', sa.Integer()),
        sa.Column('ubicacion', sa.String(200)),
        sa.Column('frecuencia', sa.Text(), nullable=False),
        sa.Column('fecha_proxima', sa.Date(), nullable=False),
        sa.Column('retirado', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['responsable_id'], ['personas.id'],
            name='fk_infraestructura_responsable_id_personas', ondelete='RESTRICT'),
        *_metadata('infraestructura'),
    )
    op.create_index('ix_infraestructura_responsable_id', 'infraestructura', ['responsable_id'])
    op.create_table(
        'infraestructura_procesos',
        sa.Column('elemento_id', sa.Integer(), primary_key=True),
        sa.Column('proceso_id', sa.Integer(), primary_key=True),
        sa.ForeignKeyConstraint(['elemento_id'], ['infraestructura.id'],
            name='fk_infraestructura_procesos_elemento_id', ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['proceso_id'], ['procesos_operacion.id_proceso'],
            name='fk_infraestructura_procesos_proceso_id', ondelete='RESTRICT', onupdate='RESTRICT'),
    )
    op.create_index('ix_infraestructura_procesos_proceso_id', 'infraestructura_procesos', ['proceso_id'])
    op.create_table(
        'mantenimientos_infraestructura',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('elemento_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('tipo', _enum('ck_mant_infra_tipo', 'preventivo', 'correctivo'), nullable=False),
        sa.Column('resultado', _enum('ck_mant_infra_resultado', 'conforme', 'no_conforme'), nullable=False),
        sa.Column('realizado_por', sa.String(200), nullable=False),
        sa.Column('evidencia', sa.Text(), nullable=False),
        sa.Column('acciones', sa.Text()),
        sa.Column('no_conformidad_id', sa.Integer()),
        sa.Column('fecha_proxima', sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(['elemento_id'], ['infraestructura.id'],
            name='fk_mant_infra_elemento_id', ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['no_conformidad_id'], ['no_conformidades.id'],
            name='fk_mant_infra_no_conformidad_id', ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.CheckConstraint('length(trim(evidencia)) > 0', name='ck_mant_infra_evidencia'),
        sa.CheckConstraint('fecha_proxima > fecha', name='ck_mant_infra_proxima'),
        sa.CheckConstraint("resultado = 'conforme' OR (acciones IS NOT NULL AND "
                           "length(trim(acciones)) > 0)", name='ck_mant_infra_acciones'),
        *_metadata('mantenimientos_infraestructura'),
    )
    op.create_index('ix_mant_infra_historial', 'mantenimientos_infraestructura',
                    ['elemento_id', 'fecha', 'id'])
    op.create_index('ix_mant_infra_no_conformidad_id', 'mantenimientos_infraestructura',
                    ['no_conformidad_id'])


def downgrade():
    op.drop_table('mantenimientos_infraestructura')
    op.drop_table('infraestructura_procesos')
    op.drop_table('infraestructura')
