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

"""Add the changes to the QMS, their tasks and the processes and documents they affect.

CP-1 of ``change-planning`` (ISO 9001 clause 6.3, decisions CP1 to CP4).
``cambios_sgc`` holds each change: its origin and, optionally, the record
behind it (a review action, a nonconformity, an audit, a risk, an opportunity or
a context issue), each link allowed only with its own origin (CHECK); its
purpose and the 6.3 considerations; its owner and dates; and the columns of
each stage of its lifecycle, which only the states carrying them hold (CHECK
constraints for the approval, the implementation, the effectiveness review and
the cancellation; the reviewer is never the owner). ``tareas_cambio`` holds the
tasks that carry a change out, with a completion date only when completed.
``cambio_procesos`` and ``cambio_documentos`` link a change to the processes
and documents it affects. Every foreign key is ``ON DELETE RESTRICT`` except
the attribution columns (``SET NULL``).

The downgrade drops the four tables, losing their data.

Revision ID: 29637060aaf0
Revises: e4a6c8b0d2f5
Create Date: 2026-10-24 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '29637060aaf0'
down_revision = 'e4a6c8b0d2f5'
branch_labels = None
depends_on = None

CHANGES = 'cambios_sgc'
TASKS = 'tareas_cambio'
PROCESSES = 'cambio_procesos'
DOCUMENTS = 'cambio_documentos'
ORIGINS = ('revision_direccion', 'no_conformidad', 'auditoria', 'riesgo', 'oportunidad',
           'cuestion_contexto', 'otro')
STATES = ('propuesto', 'aprobado', 'implantado', 'eficacia_revisada', 'cancelado')
RESULTS = ('eficaz', 'no_eficaz')
TASK_STATES = ('pendiente', 'en_curso', 'completada', 'cancelada')
# Mirrors ``models.CHANGE_LINKS``: column, the origin it needs and the table it cites.
LINKS = (
    ('accion_revision_id', 'revision_direccion', 'acciones_revision'),
    ('no_conformidad_id', 'no_conformidad', 'no_conformidades'),
    ('auditoria_id', 'auditoria', 'auditorias'),
    ('riesgo_id', 'riesgo', 'riesgos'),
    ('oportunidad_id', 'oportunidad', 'oportunidades'),
    ('cuestion_id', 'cuestion_contexto', 'cuestiones_contexto'),
)
PEOPLE = ('responsable_id', 'aprobado_por_id', 'revisor_eficacia_id')

# Mirror ``models._CHANGE_APPROVAL``, ``_CHANGE_IMPLEMENTATION``,
# ``_CHANGE_EFFECTIVENESS``, ``_INEFFECTIVE_EXPLAINED``, ``_INDEPENDENT_REVIEWER``,
# ``_CHANGE_CANCELLATION`` and ``_ACTION_COMPLETION``.
APPROVAL = (
    "(aprobado_por_id IS NULL AND fecha_aprobacion IS NULL "
    "AND estado IN ('propuesto', 'cancelado')) OR "
    "(aprobado_por_id IS NOT NULL AND fecha_aprobacion IS NOT NULL AND estado <> 'propuesto')"
)
IMPLEMENTATION = (
    "(fecha_implantacion IS NOT NULL AND estado IN ('implantado', 'eficacia_revisada')) OR "
    "(fecha_implantacion IS NULL AND estado NOT IN ('implantado', 'eficacia_revisada'))"
)
EFFECTIVENESS = (
    "(eficacia IS NOT NULL AND fecha_eficacia IS NOT NULL AND revisor_eficacia_id IS NOT NULL "
    "AND estado = 'eficacia_revisada') OR "
    "(eficacia IS NULL AND fecha_eficacia IS NULL AND revisor_eficacia_id IS NULL "
    "AND estado <> 'eficacia_revisada')"
)
INEFFECTIVE_EXPLAINED = (
    "eficacia IS NULL OR eficacia = 'eficaz' OR comentario_eficacia IS NOT NULL"
)
INDEPENDENT_REVIEWER = "revisor_eficacia_id IS NULL OR revisor_eficacia_id <> responsable_id"
CANCELLATION = (
    "(motivo_cancelacion IS NOT NULL AND fecha_cancelacion IS NOT NULL "
    "AND estado = 'cancelado') OR "
    "(motivo_cancelacion IS NULL AND fecha_cancelacion IS NULL AND estado <> 'cancelado')"
)
COMPLETION = ("(estado = 'completada' AND fecha_completada IS NOT NULL) OR "
              "(estado <> 'completada' AND fecha_completada IS NULL)")


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


def _restrict(table, column, target, target_column='id'):
    """A ``RESTRICT`` foreign key named as the models name it."""
    return sa.ForeignKeyConstraint([column], [f'{target}.{target_column}'],
                                   name=f'fk_{table}_{column}_{target}', ondelete='RESTRICT')


def _link_table(table, column, target, target_column):
    """A change's link to a process or a document: composite key, both keys ``RESTRICT``."""
    op.create_table(
        table,
        sa.Column('cambio_id', sa.Integer(), nullable=False),
        sa.Column(column, sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('cambio_id', column, name=f'pk_{table}'),
        _restrict(table, 'cambio_id', CHANGES),
        _restrict(table, column, target, target_column),
    )
    op.create_index(f'ix_{table}_{column}', table, [column])


def upgrade():
    op.create_table(
        CHANGES,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('titulo', sa.String(length=200), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('origen', _text_enum(ORIGINS, f'ck_{CHANGES}_origen'), nullable=False),
        *(sa.Column(column, sa.Integer(), nullable=True) for column, _, _ in LINKS),
        sa.Column('proposito', sa.Text(), nullable=False),
        *(sa.Column(name, sa.Text(), nullable=True)
          for name in ('consecuencias', 'integridad', 'recursos', 'responsabilidades',
                       'comunicacion')),
        sa.Column('responsable_id', sa.Integer(), nullable=False),
        sa.Column('fecha_propuesta', sa.Date(), nullable=False),
        sa.Column('fecha_prevista', sa.Date(), nullable=True),
        sa.Column('estado', _text_enum(STATES, f'ck_{CHANGES}_estado'), nullable=False,
                  server_default='propuesto'),
        sa.Column('aprobado_por_id', sa.Integer(), nullable=True),
        sa.Column('fecha_aprobacion', sa.Date(), nullable=True),
        sa.Column('fecha_implantacion', sa.Date(), nullable=True),
        sa.Column('eficacia', _text_enum(RESULTS, f'ck_{CHANGES}_eficacia'), nullable=True),
        sa.Column('fecha_eficacia', sa.Date(), nullable=True),
        sa.Column('revisor_eficacia_id', sa.Integer(), nullable=True),
        sa.Column('comentario_eficacia', sa.Text(), nullable=True),
        sa.Column('motivo_cancelacion', sa.Text(), nullable=True),
        sa.Column('fecha_cancelacion', sa.Date(), nullable=True),
        *_metadata_columns(CHANGES),
        sa.PrimaryKeyConstraint('id'),
        *(_restrict(CHANGES, column, target) for column, _, target in LINKS),
        *(sa.CheckConstraint(f"{column} IS NULL OR origen = '{origin}'",
                             name=f'ck_{CHANGES}_{column.removesuffix("_id")}')
          for column, origin, _ in LINKS),
        *(_restrict(CHANGES, column, 'personas') for column in PEOPLE),
        sa.CheckConstraint(APPROVAL, name=f'ck_{CHANGES}_aprobacion'),
        sa.CheckConstraint(IMPLEMENTATION, name=f'ck_{CHANGES}_implantacion'),
        sa.CheckConstraint(EFFECTIVENESS, name=f'ck_{CHANGES}_revision_eficacia'),
        sa.CheckConstraint(INEFFECTIVE_EXPLAINED, name=f'ck_{CHANGES}_comentario_eficacia'),
        sa.CheckConstraint(INDEPENDENT_REVIEWER, name=f'ck_{CHANGES}_revisor_eficacia'),
        sa.CheckConstraint(CANCELLATION, name=f'ck_{CHANGES}_cancelacion'),
    )
    for column in (*(column for column, _, _ in LINKS), *PEOPLE):
        op.create_index(f'ix_{CHANGES}_{column}', CHANGES, [column])

    op.create_table(
        TASKS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cambio_id', sa.Integer(), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('responsable_id', sa.Integer(), nullable=False),
        sa.Column('fecha_limite', sa.Date(), nullable=False),
        sa.Column('estado', _text_enum(TASK_STATES, f'ck_{TASKS}_estado'), nullable=False,
                  server_default='pendiente'),
        sa.Column('fecha_completada', sa.Date(), nullable=True),
        sa.Column('resultado', sa.Text(), nullable=True),
        *_metadata_columns(TASKS),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(COMPLETION, name=f'ck_{TASKS}_completada'),
        _restrict(TASKS, 'cambio_id', CHANGES),
        _restrict(TASKS, 'responsable_id', 'personas'),
    )
    op.create_index(f'ix_{TASKS}_cambio_id', TASKS, ['cambio_id'])
    op.create_index(f'ix_{TASKS}_responsable_id', TASKS, ['responsable_id'])

    _link_table(PROCESSES, 'proceso_id', 'procesos_operacion', 'id_proceso')
    _link_table(DOCUMENTS, 'documento_id', 'documents', 'id')


def downgrade():
    for table, column in ((DOCUMENTS, 'documento_id'), (PROCESSES, 'proceso_id')):
        op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_table(table)
    op.drop_index(f'ix_{TASKS}_responsable_id', table_name=TASKS)
    op.drop_index(f'ix_{TASKS}_cambio_id', table_name=TASKS)
    op.drop_table(TASKS)
    for column in (*(column for column, _, _ in LINKS), *PEOPLE):
        op.drop_index(f'ix_{CHANGES}_{column}', table_name=CHANGES)
    op.drop_table(CHANGES)
