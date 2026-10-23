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

"""Add the versioned scope of the QMS and the applicability of each requirement.

OC-2 of ``organization-context`` (ISO 9001 clause 4.3, decision OC3).
``alcances_sgc`` holds numbered versions (unique ``version``, at least 1) that
move from ``borrador`` to ``vigente`` to ``obsoleto``: two partial unique
indexes keep at most one draft and one current version, an approved version
records who approved it and when, and an obsolete one when it stopped applying
(CHECK constraints). The approving person is ``ON DELETE RESTRICT``.
``aplicabilidad_alcance`` holds one row per version and requirement code;
a requirement that does not apply carries a justification, and the rows go
with their version (``ON DELETE CASCADE``).

The downgrade drops both tables, losing their data.

Revision ID: e4a6c8b0d2f5
Revises: c8f2a4e6b0d3
Create Date: 2026-10-22 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4a6c8b0d2f5'
down_revision = 'c8f2a4e6b0d3'
branch_labels = None
depends_on = None

SCOPES = 'alcances_sgc'
ENTRIES = 'aplicabilidad_alcance'
ESTADOS = ('borrador', 'vigente', 'obsoleto')

# Mirror ``models._DRAFT_SCOPE``, ``_CURRENT_SCOPE``, ``_SCOPE_APPROVAL``,
# ``_SCOPE_OBSOLESCENCE`` and ``_EXCLUSION_JUSTIFIED``.
DRAFT = "estado = 'borrador'"
CURRENT = "estado = 'vigente'"
APPROVAL = (
    "(estado = 'borrador' AND aprobado_por_id IS NULL AND fecha_aprobacion IS NULL) OR "
    "(estado <> 'borrador' AND aprobado_por_id IS NOT NULL AND fecha_aprobacion IS NOT NULL)"
)
OBSOLESCENCE = (
    "(estado = 'obsoleto' AND fecha_obsolescencia IS NOT NULL) OR "
    "(estado <> 'obsoleto' AND fecha_obsolescencia IS NULL)"
)
JUSTIFIED = "aplica OR (justificacion IS NOT NULL AND trim(justificacion) <> '')"


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
        SCOPES,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        # Mirrors ``models.text_enum``: member names in a VARCHAR with a CHECK.
        sa.Column('estado',
                  sa.Enum(*ESTADOS, native_enum=False, length=20, create_constraint=True,
                          name=f'ck_{SCOPES}_estado'),
                  nullable=False, server_default='borrador'),
        sa.Column('declaracion', sa.Text(), nullable=False),
        sa.Column('productos_servicios', sa.Text(), nullable=True),
        sa.Column('centros', sa.Text(), nullable=True),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('aprobado_por_id', sa.Integer(), nullable=True),
        sa.Column('fecha_aprobacion', sa.Date(), nullable=True),
        sa.Column('fecha_obsolescencia', sa.Date(), nullable=True),
        *_metadata_columns(SCOPES),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version', name=f'uq_{SCOPES}_version'),
        sa.CheckConstraint('version >= 1', name=f'ck_{SCOPES}_version'),
        sa.CheckConstraint(APPROVAL, name=f'ck_{SCOPES}_aprobacion'),
        sa.CheckConstraint(OBSOLESCENCE, name=f'ck_{SCOPES}_obsolescencia'),
        sa.ForeignKeyConstraint(
            ['aprobado_por_id'], ['personas.id'],
            name=f'fk_{SCOPES}_aprobado_por_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{SCOPES}_aprobado_por_id', SCOPES, ['aprobado_por_id'])
    op.create_index(f'uq_{SCOPES}_borrador', SCOPES, ['estado'], unique=True,
                    postgresql_where=sa.text(DRAFT))
    op.create_index(f'uq_{SCOPES}_vigente', SCOPES, ['estado'], unique=True,
                    postgresql_where=sa.text(CURRENT))

    op.create_table(
        ENTRIES,
        sa.Column('alcance_id', sa.Integer(), nullable=False),
        sa.Column('apartado', sa.String(length=10), nullable=False),
        sa.Column('aplica', sa.Boolean(), nullable=False),
        sa.Column('justificacion', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('alcance_id', 'apartado', name=f'pk_{ENTRIES}'),
        sa.ForeignKeyConstraint(
            ['alcance_id'], [f'{SCOPES}.id'],
            name=f'fk_{ENTRIES}_alcance_id_{SCOPES}', ondelete='CASCADE',
        ),
        sa.CheckConstraint(JUSTIFIED, name=f'ck_{ENTRIES}_justificacion'),
    )


def downgrade():
    op.drop_table(ENTRIES)
    op.drop_index(f'uq_{SCOPES}_vigente', table_name=SCOPES)
    op.drop_index(f'uq_{SCOPES}_borrador', table_name=SCOPES)
    op.drop_index(f'ix_{SCOPES}_aprobado_por_id', table_name=SCOPES)
    op.drop_table(SCOPES)
