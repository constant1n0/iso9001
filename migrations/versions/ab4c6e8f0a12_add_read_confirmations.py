"""Add read confirmations (5.2, 7.3) and reconcile document categories.

Downgrade drops the register but leaves the additive PostgreSQL enum values,
including the legacy PLAN_ACCION: removing native enum values is unsafe.

Revision ID: ab4c6e8f0a12
Revises: 9a2c4e6f8b10
"""
from alembic import op
import sqlalchemy as sa

revision = 'ab4c6e8f0a12'
down_revision = '9a2c4e6f8b10'
branch_labels = None
depends_on = None

TABLE = 'confirmaciones_lectura'
CATEGORIES = ('PLAN_ACCION_CORRECTIVA', 'PLAN_ACCION_PREVENTIVA', 'REGISTRO_CALIDAD',
              'INFORME_REVISION', 'POLITICA_SEGURIDAD', 'INDICADOR_DESEMPENO',
              'PLAN_CAPACITACION', 'POLITICA_CALIDAD')


def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        for name in CATEGORIES:
            op.execute(f"ALTER TYPE documentcategory ADD VALUE IF NOT EXISTS '{name}'")
    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('persona_id', sa.Integer(), nullable=False),
        sa.Column('fecha_confirmacion', sa.Date(), nullable=False),
        sa.Column('tipo', sa.String(20), nullable=False),
        sa.Column('document_revision_id', sa.Integer(), nullable=True),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("tipo IN ('documento', 'objetivos')", name=f'ck_{TABLE}_tipo'),
        sa.CheckConstraint(
            "(tipo = 'documento' AND document_revision_id IS NOT NULL) OR "
            "(tipo = 'objetivos' AND document_revision_id IS NULL)", name=f'ck_{TABLE}_objeto'),
        sa.ForeignKeyConstraint(['persona_id'], ['personas.id'],
                                name=f'fk_{TABLE}_persona_id_personas', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['document_revision_id'], ['document_revisions.id'],
                                name=f'fk_{TABLE}_document_revision_id', ondelete='RESTRICT'),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{TABLE}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    )
    for column in ('persona_id', 'document_revision_id'):
        op.create_index(f'ix_{TABLE}_{column}', TABLE, [column])


def downgrade():
    op.drop_table(TABLE)
    # Native enum additions deliberately remain: this downgrade is additive.
