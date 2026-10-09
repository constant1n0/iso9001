"""add acciones_correctivas

Corrective actions of a nonconformity (ISO 9001 clause 10.2, decision N3 of
``nc-capa-loop``): description, owner, planned and done dates, and the
effectiveness verification (result, date, verifier and evidence). The result
is stored as text with a CHECK constraint; the action's status is derived and
not stored. Deleting a nonconformity deletes its actions (``CASCADE``); an
owner or verifier still cited cannot be deleted (``RESTRICT``).

Revision ID: f8b2d4a6c9e1
Revises: e7a9c1d3f5b8
Create Date: 2026-10-09 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f8b2d4a6c9e1'
down_revision = 'e7a9c1d3f5b8'
branch_labels = None
depends_on = None

TABLE = 'acciones_correctivas'
RESULTADOS = ('eficaz', 'no_eficaz')
INDEXED = ('no_conformidad_id', 'responsable_id', 'verificador_id')


def upgrade():
    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('no_conformidad_id', sa.Integer(), nullable=False),
        sa.Column('descripcion', sa.String(length=1000), nullable=False),
        sa.Column('responsable_id', sa.Integer(), nullable=False),
        sa.Column('fecha_prevista', sa.Date(), nullable=False),
        sa.Column('fecha_realizada', sa.Date(), nullable=True),
        sa.Column(
            'resultado_verificacion',
            # Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK.
            sa.Enum(*RESULTADOS, native_enum=False, length=20, create_constraint=True,
                    name=f'ck_{TABLE}_resultado_verificacion'),
            nullable=True,
        ),
        sa.Column('fecha_verificacion', sa.Date(), nullable=True),
        sa.Column('verificador_id', sa.Integer(), nullable=True),
        sa.Column('evidencia_verificacion', sa.Text(), nullable=True),
        # ``RecordMetadataMixin``
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['no_conformidad_id'], ['no_conformidades.id'],
            name=f'fk_{TABLE}_no_conformidad_id_no_conformidades', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name=f'fk_{TABLE}_responsable_id_personas', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['verificador_id'], ['personas.id'],
            name=f'fk_{TABLE}_verificador_id_personas', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['users.id'],
            name=f'fk_{TABLE}_created_by_id_users', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by_id'], ['users.id'],
            name=f'fk_{TABLE}_updated_by_id_users', ondelete='SET NULL',
        ),
    )
    for column in INDEXED:
        op.create_index(f'ix_{TABLE}_{column}', TABLE, [column])


def downgrade():
    for column in reversed(INDEXED):
        op.drop_index(f'ix_{TABLE}_{column}', table_name=TABLE)
    op.drop_table(TABLE)
