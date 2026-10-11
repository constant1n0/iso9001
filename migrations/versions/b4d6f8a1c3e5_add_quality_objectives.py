"""add quality objectives and their measurements

Quality objectives (ISO 9001 clause 6.2, ``quality-objectives``, decisions QO1
and QO2): an indicator with a target and its direction (``mayor_igual`` or
``menor_igual``), an owner (a person), an optional process, a year, a period
and a measurement frequency, plus one measurement per objective and date.
People, processes and objectives cited by these rows cannot be deleted
(``ON DELETE RESTRICT``).

The downgrade drops both tables, losing the objectives and their measurements.

Revision ID: b4d6f8a1c3e5
Revises: e9c2a4f6b8d1
Create Date: 2026-10-11 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b4d6f8a1c3e5'
down_revision = 'e9c2a4f6b8d1'
branch_labels = None
depends_on = None

OBJECTIVES = 'objetivos_calidad'
MEASUREMENTS = 'mediciones_objetivo'
SENTIDOS = ('mayor_igual', 'menor_igual')
FRECUENCIAS = ('mensual', 'trimestral', 'semestral', 'anual')
ESTADOS = ('activo', 'alcanzado', 'no_alcanzado', 'cancelado')


def _text_enum(values, name):
    """Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK."""
    return sa.Enum(*values, native_enum=False, length=20, create_constraint=True, name=name)


def _metadata_columns(table):
    """``RecordMetadataMixin`` columns and their foreign keys to ``users``."""
    columns = [
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
    ]
    keys = [
        sa.ForeignKeyConstraint([f'{column}_id'], ['users.id'],
                                name=f'fk_{table}_{column}_id_users', ondelete='SET NULL')
        for column in ('created_by', 'updated_by')
    ]
    return [*columns, *keys]


def upgrade():
    op.create_table(
        OBJECTIVES,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=True),
        sa.Column('proceso_id', sa.Integer(), nullable=True),
        sa.Column('indicador', sa.String(length=200), nullable=False),
        sa.Column('unidad', sa.String(length=30), nullable=True),
        sa.Column('meta', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('sentido', _text_enum(SENTIDOS, f'ck_{OBJECTIVES}_sentido'), nullable=False),
        sa.Column('valor_inicial', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('responsable_id', sa.Integer(), nullable=False),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_limite', sa.Date(), nullable=False),
        sa.Column('frecuencia', _text_enum(FRECUENCIAS, f'ck_{OBJECTIVES}_frecuencia'),
                  nullable=False),
        sa.Column('recursos', sa.Text(), nullable=True),
        sa.Column('evaluacion', sa.Text(), nullable=True),
        sa.Column('estado', _text_enum(ESTADOS, f'ck_{OBJECTIVES}_estado'), nullable=False,
                  server_default='activo'),
        *_metadata_columns(OBJECTIVES),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('anio BETWEEN 2000 AND 2100', name=f'ck_{OBJECTIVES}_anio'),
        sa.CheckConstraint('fecha_limite >= fecha_inicio', name=f'ck_{OBJECTIVES}_fechas'),
        sa.ForeignKeyConstraint(
            ['proceso_id'], ['procesos_operacion.id_proceso'],
            name=f'fk_{OBJECTIVES}_proceso_id_procesos_operacion', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name=f'fk_{OBJECTIVES}_responsable_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{OBJECTIVES}_proceso_id', OBJECTIVES, ['proceso_id'])
    op.create_index(f'ix_{OBJECTIVES}_responsable_id', OBJECTIVES, ['responsable_id'])

    op.create_table(
        MEASUREMENTS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('objetivo_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('valor', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('comentario', sa.Text(), nullable=True),
        sa.Column('registrado_por_id', sa.Integer(), nullable=True),
        *_metadata_columns(MEASUREMENTS),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('objetivo_id', 'fecha', name=f'uq_{MEASUREMENTS}_objetivo_id_fecha'),
        sa.ForeignKeyConstraint(
            ['objetivo_id'], [f'{OBJECTIVES}.id'],
            name=f'fk_{MEASUREMENTS}_objetivo_id_{OBJECTIVES}', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['registrado_por_id'], ['personas.id'],
            name=f'fk_{MEASUREMENTS}_registrado_por_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{MEASUREMENTS}_objetivo_id', MEASUREMENTS, ['objetivo_id'])
    op.create_index(f'ix_{MEASUREMENTS}_registrado_por_id', MEASUREMENTS, ['registrado_por_id'])


def downgrade():
    op.drop_index(f'ix_{MEASUREMENTS}_registrado_por_id', table_name=MEASUREMENTS)
    op.drop_index(f'ix_{MEASUREMENTS}_objetivo_id', table_name=MEASUREMENTS)
    op.drop_table(MEASUREMENTS)
    op.drop_index(f'ix_{OBJECTIVES}_responsable_id', table_name=OBJECTIVES)
    op.drop_index(f'ix_{OBJECTIVES}_proceso_id', table_name=OBJECTIVES)
    op.drop_table(OBJECTIVES)
