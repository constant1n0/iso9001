"""add audit findings

Internal audit findings (ISO 9001 clause 9.2.2, ``audit-programme``, decisions
AP4 and AP5): each belongs to one audit and is a major or minor nonconformity,
an observation or an opportunity for improvement, with a description, an
optional ISO clause and evidence. A nonconformity finding keeps a link to the
nonconformity it created, at most one finding per nonconformity. An audit with
findings cannot be deleted (``ON DELETE RESTRICT``); deleting a nonconformity
clears the link (``ON DELETE SET NULL``).

The downgrade drops the table, losing the findings; their nonconformities stay.

Revision ID: e9c2a4f6b8d1
Revises: d7f1b3a5c9e2
Create Date: 2026-10-11 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9c2a4f6b8d1'
down_revision = 'd7f1b3a5c9e2'
branch_labels = None
depends_on = None

FINDINGS = 'hallazgos_auditoria'
TIPOS = ('nc_mayor', 'nc_menor', 'observacion', 'oportunidad_mejora')


def upgrade():
    op.create_table(
        FINDINGS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('auditoria_id', sa.Integer(), nullable=False),
        sa.Column(
            'tipo',
            # Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK.
            sa.Enum(*TIPOS, native_enum=False, length=20, create_constraint=True,
                    name=f'ck_{FINDINGS}_tipo'),
            nullable=False,
        ),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('clausula', sa.String(length=20), nullable=True),
        sa.Column('evidencia', sa.Text(), nullable=True),
        sa.Column('no_conformidad_id', sa.Integer(), nullable=True),
        # ``RecordMetadataMixin``
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('no_conformidad_id', name=f'uq_{FINDINGS}_no_conformidad_id'),
        sa.ForeignKeyConstraint(
            ['auditoria_id'], ['auditorias.id'],
            name=f'fk_{FINDINGS}_auditoria_id_auditorias', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['no_conformidad_id'], ['no_conformidades.id'],
            name=f'fk_{FINDINGS}_no_conformidad_id_no_conformidades', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['users.id'],
            name=f'fk_{FINDINGS}_created_by_id_users', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by_id'], ['users.id'],
            name=f'fk_{FINDINGS}_updated_by_id_users', ondelete='SET NULL',
        ),
    )
    op.create_index(f'ix_{FINDINGS}_auditoria_id', FINDINGS, ['auditoria_id'])


def downgrade():
    op.drop_index(f'ix_{FINDINGS}_auditoria_id', table_name=FINDINGS)
    op.drop_table(FINDINGS)
