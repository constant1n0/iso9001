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

"""Add the context issues, the interested parties' context fields and the risk link.

OC-1 of ``organization-context`` (ISO 9001 clauses 4.1 and 4.2, decisions OC1
and OC2). ``cuestiones_contexto`` holds the internal and external issues: the
category matches the type, an issue that is not relevant says why and the next
review is never before the identification (CHECK constraints). The interested
parties gain a type, relevance (NULL: "sin evaluar", as every existing row
starts), an owner, the next review and a climate-related-requirements flag
(false on existing rows). Risks and opportunities gain ``cuestion_id``, the
issue that gives rise to them. Every new link is ``ON DELETE RESTRICT``: a
cited issue and a person who owns an issue or a party cannot be deleted.

The downgrade drops the table and the columns, losing their data.

Revision ID: c8f2a4e6b0d3
Revises: d2f4a6c8e1b3
Create Date: 2026-10-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f2a4e6b0d3'
down_revision = 'd2f4a6c8e1b3'
branch_labels = None
depends_on = None

ISSUES = 'cuestiones_contexto'
PARTIES = 'partes_interesadas'
REGISTERS = ('riesgos', 'oportunidades')
TIPOS = ('interna', 'externa')
EXTERNAS = ('politica', 'economica', 'social', 'tecnologica', 'legal', 'ambiental')
INTERNAS = ('cultura', 'recursos', 'conocimiento', 'desempeno', 'estructura')
EFECTOS = ('positivo', 'negativo', 'neutro')
ESTADOS = ('vigente', 'superada')
PARTY_OWNER_FK = f'fk_{PARTIES}_responsable_id_personas'


def _names(values):
    return ', '.join(f"'{value}'" for value in values)


# Mirrors ``models._ISSUE_CATEGORY``, ``_ISSUE_RELEVANCE`` and ``_ISSUE_REVIEW``.
CATEGORY = (f"(tipo = 'externa' AND categoria IN ({_names(EXTERNAS)})) OR "
            f"(tipo = 'interna' AND categoria IN ({_names(INTERNAS)}))")
RELEVANCE = 'pertinente OR justificacion_pertinencia IS NOT NULL'
REVIEW = 'fecha_revision IS NULL OR fecha_revision >= fecha_identificacion'


def _text_enum(values, name, length=20):
    """Mirrors ``models.text_enum``: member names in a VARCHAR with a CHECK."""
    return sa.Enum(*values, native_enum=False, length=length, create_constraint=True, name=name)


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


def _issue_fk(table):
    return f'fk_{table}_cuestion_id_{ISSUES}'


def upgrade():
    op.create_table(
        ISSUES,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tipo', _text_enum(TIPOS, f'ck_{ISSUES}_tipo'), nullable=False),
        sa.Column('categoria',
                  _text_enum((*EXTERNAS, *INTERNAS), f'ck_{ISSUES}_categoria'),
                  nullable=False),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('efecto', _text_enum(EFECTOS, f'ck_{ISSUES}_efecto'), nullable=False),
        sa.Column('pertinente', sa.Boolean(), nullable=False),
        sa.Column('justificacion_pertinencia', sa.Text(), nullable=True),
        sa.Column('cambio_climatico', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('responsable_id', sa.Integer(), nullable=True),
        sa.Column('fecha_identificacion', sa.Date(), nullable=False),
        sa.Column('fecha_revision', sa.Date(), nullable=True),
        sa.Column('estado', _text_enum(ESTADOS, f'ck_{ISSUES}_estado'), nullable=False,
                  server_default='vigente'),
        sa.Column('notas', sa.Text(), nullable=True),
        *_metadata_columns(ISSUES),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(CATEGORY, name=f'ck_{ISSUES}_categoria_tipo'),
        sa.CheckConstraint(RELEVANCE, name=f'ck_{ISSUES}_justificacion'),
        sa.CheckConstraint(REVIEW, name=f'ck_{ISSUES}_fecha_revision'),
        sa.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name=f'fk_{ISSUES}_responsable_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{ISSUES}_responsable_id', ISSUES, ['responsable_id'])

    op.add_column(PARTIES, sa.Column('tipo', sa.String(length=20), nullable=True))
    op.add_column(PARTIES, sa.Column('pertinente', sa.Boolean(), nullable=True))
    op.add_column(PARTIES, sa.Column('responsable_id', sa.Integer(), nullable=True))
    op.add_column(PARTIES, sa.Column('fecha_revision', sa.Date(), nullable=True))
    op.add_column(PARTIES, sa.Column('requisitos_climaticos', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.create_check_constraint(f'ck_{PARTIES}_tipo', PARTIES, f'tipo IN ({_names(TIPOS)})')
    op.create_foreign_key(PARTY_OWNER_FK, PARTIES, 'personas', ['responsable_id'], ['id'],
                          ondelete='RESTRICT')
    op.create_index(f'ix_{PARTIES}_responsable_id', PARTIES, ['responsable_id'])

    for table in REGISTERS:
        op.add_column(table, sa.Column('cuestion_id', sa.Integer(), nullable=True))
        op.create_foreign_key(_issue_fk(table), table, ISSUES, ['cuestion_id'], ['id'],
                              ondelete='RESTRICT')
        op.create_index(f'ix_{table}_cuestion_id', table, ['cuestion_id'])


def downgrade():
    for table in reversed(REGISTERS):
        op.drop_index(f'ix_{table}_cuestion_id', table_name=table)
        op.drop_constraint(_issue_fk(table), table, type_='foreignkey')
        op.drop_column(table, 'cuestion_id')

    op.drop_index(f'ix_{PARTIES}_responsable_id', table_name=PARTIES)
    op.drop_constraint(PARTY_OWNER_FK, PARTIES, type_='foreignkey')
    op.drop_constraint(f'ck_{PARTIES}_tipo', PARTIES, type_='check')
    for column in ('requisitos_climaticos', 'fecha_revision', 'responsable_id', 'pertinente',
                   'tipo'):
        op.drop_column(PARTIES, column)

    op.drop_index(f'ix_{ISSUES}_responsable_id', table_name=ISSUES)
    op.drop_table(ISSUES)
