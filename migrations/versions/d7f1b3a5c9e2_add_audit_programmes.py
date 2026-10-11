"""add audit programmes and audit planning fields

Internal audits (ISO 9001 clause 9.2, ``audit-programme``, decisions AP1 and
AP2): a programme per year (``programas_auditoria``) with objectives and scope,
a draft until an administrator approves it with a date and an approving person.
Audits may belong to a programme and gain scope, criteria, the audited-area
owner (a person) and a team (``auditoria_equipo``). Every new column is
nullable, so existing audits keep NULL. People cited by these links cannot be
deleted (``ON DELETE RESTRICT``); deleting an audit removes its team.

The downgrade drops the tables and columns, losing the planning data.

Revision ID: d7f1b3a5c9e2
Revises: c3e8a1f6d4b2
Create Date: 2026-10-10 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7f1b3a5c9e2'
down_revision = 'c3e8a1f6d4b2'
branch_labels = None
depends_on = None

PROGRAMMES = 'programas_auditoria'
AUDITS = 'auditorias'
TEAM = 'auditoria_equipo'
ESTADOS = ('borrador', 'aprobado')
APPROVAL = (
    "(estado = 'borrador' AND aprobado_en IS NULL AND aprobado_por_id IS NULL) OR "
    "(estado = 'aprobado' AND aprobado_en IS NOT NULL AND aprobado_por_id IS NOT NULL)"
)


def upgrade():
    op.create_table(
        PROGRAMMES,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('objetivos', sa.Text(), nullable=False),
        sa.Column('alcance', sa.Text(), nullable=False),
        sa.Column(
            'estado',
            # Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK.
            sa.Enum(*ESTADOS, native_enum=False, length=20, create_constraint=True,
                    name=f'ck_{PROGRAMMES}_estado'),
            nullable=False,
            server_default='borrador',
        ),
        sa.Column('aprobado_en', sa.Date(), nullable=True),
        sa.Column('aprobado_por_id', sa.Integer(), nullable=True),
        # ``RecordMetadataMixin``
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('anio', name=f'uq_{PROGRAMMES}_anio'),
        sa.CheckConstraint('anio BETWEEN 2000 AND 2100', name=f'ck_{PROGRAMMES}_anio'),
        sa.CheckConstraint(APPROVAL, name=f'ck_{PROGRAMMES}_aprobacion'),
        sa.ForeignKeyConstraint(
            ['aprobado_por_id'], ['personas.id'],
            name=f'fk_{PROGRAMMES}_aprobado_por_id_personas', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['users.id'],
            name=f'fk_{PROGRAMMES}_created_by_id_users', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by_id'], ['users.id'],
            name=f'fk_{PROGRAMMES}_updated_by_id_users', ondelete='SET NULL',
        ),
    )
    op.create_index(f'ix_{PROGRAMMES}_aprobado_por_id', PROGRAMMES, ['aprobado_por_id'])

    op.add_column(AUDITS, sa.Column('programa_id', sa.Integer(), nullable=True))
    op.add_column(AUDITS, sa.Column('alcance', sa.Text(), nullable=True))
    op.add_column(AUDITS, sa.Column('criterios', sa.Text(), nullable=True))
    op.add_column(AUDITS, sa.Column('responsable_area_id', sa.Integer(), nullable=True))
    op.create_foreign_key(f'fk_{AUDITS}_programa_id_{PROGRAMMES}', AUDITS, PROGRAMMES,
                          ['programa_id'], ['id'], ondelete='RESTRICT')
    op.create_foreign_key(f'fk_{AUDITS}_responsable_area_id_personas', AUDITS, 'personas',
                          ['responsable_area_id'], ['id'], ondelete='RESTRICT')
    op.create_index(f'ix_{AUDITS}_programa_id', AUDITS, ['programa_id'])
    op.create_index(f'ix_{AUDITS}_responsable_area_id', AUDITS, ['responsable_area_id'])

    op.create_table(
        TEAM,
        sa.Column('auditoria_id', sa.Integer(), nullable=False),
        sa.Column('persona_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('auditoria_id', 'persona_id', name=f'pk_{TEAM}'),
        sa.ForeignKeyConstraint(
            ['auditoria_id'], [f'{AUDITS}.id'],
            name=f'fk_{TEAM}_auditoria_id_{AUDITS}', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['persona_id'], ['personas.id'],
            name=f'fk_{TEAM}_persona_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{TEAM}_persona_id', TEAM, ['persona_id'])


def downgrade():
    op.drop_index(f'ix_{TEAM}_persona_id', table_name=TEAM)
    op.drop_table(TEAM)
    op.drop_index(f'ix_{AUDITS}_responsable_area_id', table_name=AUDITS)
    op.drop_index(f'ix_{AUDITS}_programa_id', table_name=AUDITS)
    op.drop_constraint(f'fk_{AUDITS}_responsable_area_id_personas', AUDITS, type_='foreignkey')
    op.drop_constraint(f'fk_{AUDITS}_programa_id_{PROGRAMMES}', AUDITS, type_='foreignkey')
    for column in ('responsable_area_id', 'criterios', 'alcance', 'programa_id'):
        op.drop_column(AUDITS, column)
    op.drop_index(f'ix_{PROGRAMMES}_aprobado_por_id', table_name=PROGRAMMES)
    op.drop_table(PROGRAMMES)
