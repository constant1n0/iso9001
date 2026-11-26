"""Add process environment conditions and immutable checks (7.1.4).

Revision ID: d5f7a9b1c3e6
Revises: c4e6f8a0b2d5

Additive only. Back up evidence before downgrade removes these two tables;
existing processes, people, infrastructure and audit rows are not changed.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd5f7a9b1c3e6'
down_revision = 'c4e6f8a0b2d5'
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
        'condiciones_ambiente',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('proceso_id', sa.Integer(), nullable=False),
        sa.Column('factor', _enum('ck_condiciones_ambiente_factor',
                                  'fisico', 'social', 'psicologico'), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('criterio', sa.Text(), nullable=False),
        sa.Column('frecuencia', sa.Text(), nullable=False),
        sa.Column('responsable_id', sa.Integer()),
        sa.Column('fecha_proxima', sa.Date(), nullable=False),
        sa.Column('retirado', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['proceso_id'], ['procesos_operacion.id_proceso'],
            name='fk_condiciones_ambiente_proceso_id', ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['responsable_id'], ['personas.id'],
            name='fk_condiciones_ambiente_responsable_id_personas', ondelete='RESTRICT'),
        *_metadata('condiciones_ambiente'),
    )
    op.create_index('ix_condiciones_ambiente_proceso_id', 'condiciones_ambiente', ['proceso_id'])
    op.create_index('ix_condiciones_ambiente_responsable_id', 'condiciones_ambiente', ['responsable_id'])
    op.create_table(
        'comprobaciones_ambiente',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('condicion_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('resultado', _enum('ck_comp_ambiente_resultado', 'conforme', 'no_conforme'), nullable=False),
        sa.Column('realizado_por', sa.String(200), nullable=False),
        sa.Column('evidencia', sa.Text(), nullable=False),
        sa.Column('acciones', sa.Text()),
        sa.Column('fecha_proxima', sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(['condicion_id'], ['condiciones_ambiente.id'],
            name='fk_comp_ambiente_condicion_id', ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.CheckConstraint('length(trim(evidencia)) > 0', name='ck_comp_ambiente_evidencia'),
        sa.CheckConstraint('fecha_proxima > fecha', name='ck_comp_ambiente_proxima'),
        sa.CheckConstraint("resultado = 'conforme' OR (acciones IS NOT NULL AND "
                           "length(trim(acciones)) > 0)", name='ck_comp_ambiente_acciones'),
        *_metadata('comprobaciones_ambiente'),
    )
    op.create_index('ix_comp_ambiente_historial', 'comprobaciones_ambiente', ['condicion_id', 'fecha', 'id'])


def downgrade():
    op.drop_table('comprobaciones_ambiente')
    op.drop_table('condiciones_ambiente')
