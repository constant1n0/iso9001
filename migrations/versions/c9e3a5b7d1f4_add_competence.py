"""add competencias_requeridas and competencias_acreditadas

The competence each role requires (education, training, skill or experience)
and the competence each person has demonstrated, with its evidence, dates and
effectiveness evaluation (ISO 9001 clause 7.2). Both enums are stored as text
with a CHECK constraint. Roles, requirements and people still cited cannot be
deleted (``RESTRICT``); deleting a cited training only clears the link.

Revision ID: c9e3a5b7d1f4
Revises: b8d2f4a6c1e3
Create Date: 2026-10-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9e3a5b7d1f4'
down_revision = 'b8d2f4a6c1e3'
branch_labels = None
depends_on = None

TIPOS = ('educacion', 'formacion', 'habilidad', 'experiencia')
EVALUACIONES = ('pendiente', 'eficaz', 'no_eficaz')
RECORD_LINKS = ('persona_id', 'requisito_id', 'capacitacion_id', 'evaluador_id')


def _text_enum(values, constraint):
    """Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK."""
    return sa.Enum(
        *values, native_enum=False, length=20, create_constraint=True, name=constraint
    )


def _metadata_columns(table):
    """``RecordMetadataMixin`` columns and their foreign keys to ``users``."""
    return (
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['created_by_id'],
            ['users.id'],
            name=f'fk_{table}_created_by_id_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by_id'],
            ['users.id'],
            name=f'fk_{table}_updated_by_id_users',
            ondelete='SET NULL',
        ),
    )


def upgrade():
    op.create_table(
        'competencias_requeridas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('rol_id', sa.Integer(), nullable=False),
        sa.Column(
            'tipo', _text_enum(TIPOS, 'ck_competencias_requeridas_tipo'), nullable=False
        ),
        sa.Column('descripcion', sa.String(length=500), nullable=False),
        sa.Column('criterio', sa.Text(), nullable=True),
        *_metadata_columns('competencias_requeridas'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['rol_id'],
            ['roles_responsabilidades.id_rol'],
            name='fk_competencias_requeridas_rol_id_roles_responsabilidades',
            ondelete='RESTRICT',
        ),
    )
    op.create_index(
        'ix_competencias_requeridas_rol_id', 'competencias_requeridas', ['rol_id']
    )
    op.create_table(
        'competencias_acreditadas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('persona_id', sa.Integer(), nullable=False),
        sa.Column('requisito_id', sa.Integer(), nullable=True),
        sa.Column('evidencia', sa.String(length=500), nullable=False),
        sa.Column('capacitacion_id', sa.Integer(), nullable=True),
        sa.Column('fecha_obtencion', sa.Date(), nullable=False),
        sa.Column('fecha_caducidad', sa.Date(), nullable=True),
        sa.Column(
            'evaluacion_eficacia',
            _text_enum(EVALUACIONES, 'ck_competencias_acreditadas_evaluacion_eficacia'),
            nullable=False,
            server_default='pendiente',
        ),
        sa.Column('fecha_evaluacion', sa.Date(), nullable=True),
        sa.Column('evaluador_id', sa.Integer(), nullable=True),
        *_metadata_columns('competencias_acreditadas'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['persona_id'],
            ['personas.id'],
            name='fk_competencias_acreditadas_persona_id_personas',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['requisito_id'],
            ['competencias_requeridas.id'],
            name='fk_competencias_acreditadas_requisito_id',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['capacitacion_id'],
            ['capacitaciones.id'],
            name='fk_competencias_acreditadas_capacitacion_id_capacitaciones',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['evaluador_id'],
            ['personas.id'],
            name='fk_competencias_acreditadas_evaluador_id_personas',
            ondelete='RESTRICT',
        ),
    )
    for column in RECORD_LINKS:
        op.create_index(
            f'ix_competencias_acreditadas_{column}', 'competencias_acreditadas', [column]
        )


def downgrade():
    for column in reversed(RECORD_LINKS):
        op.drop_index(
            f'ix_competencias_acreditadas_{column}', table_name='competencias_acreditadas'
        )
    op.drop_table('competencias_acreditadas')
    op.drop_index('ix_competencias_requeridas_rol_id', table_name='competencias_requeridas')
    op.drop_table('competencias_requeridas')
