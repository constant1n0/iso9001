"""re-sync the old risks register into the new registers and drop it

``risks-opportunities`` RO-2, decision RO5. ``c6e8a2f4b1d9`` copied every
``riesgos_oportunidades`` row into ``riesgos`` or ``oportunidades`` with its id
and kept the old table, which the old service went on writing. This revision
brings the registers up to date with the old table, then drops it and its
native type ``tipoenum``:

- copies whose old row was deleted, or changed type, are deleted (unless an
  action cites them, which only RO-2's service can write);
- copies of old rows that still exist get the old row's fields again: the
  title from the first non-blank line of the description, the description, the
  labelled notes and the record metadata, exactly as ``c6e8a2f4b1d9`` derives
  them (its identification date stays);
- old rows without a copy are copied as ``c6e8a2f4b1d9`` does, with their id.

The downgrade recreates ``riesgos_oportunidades`` and moves every risk, then
every opportunity, each in id order, back into it with new ids (a risk and an
opportunity may share an id): ``tipo`` from the register, ``descripcion`` and
the record metadata as they are, and ``notas`` split back by its labels:
``Objetivo de la calidad: `` up to ``\\n\\nPlan de acción: `` becomes
``objetivo_calidad`` and the rest ``plan_accion``; notes without a label go to
``plan_accion``. It then rebuilds ``riesgos``, ``oportunidades`` and
``acciones_riesgo`` from the old table, keyed by its new ids, so the database is
as ``c6e8a2f4b1d9`` leaves it. Lost on the downgrade:

- the ids of risks and opportunities, and every action (``acciones_riesgo``);
- ``titulo`` (derived again from the description), ``origen``, the interested
  party, process and owner, the assessments (``probabilidad``, ``impacto``,
  their residual pair, ``beneficio``, ``viabilidad``), ``tratamiento``,
  ``decision``, ``estado``, ``fecha_identificacion`` (derived again from
  ``created_at``) and ``fecha_revision``;
- blank quality-objective and action-plan texts, which come back as NULL, and
  the split of a note whose quality-objective text itself holds the plan's label.

Revision ID: e3b5d7f9a1c4
Revises: c6e8a2f4b1d9
Create Date: 2026-10-17 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3b5d7f9a1c4'
down_revision = 'c6e8a2f4b1d9'
branch_labels = None
depends_on = None

OLD = 'riesgos_oportunidades'
RISKS = 'riesgos'
OPPORTUNITIES = 'oportunidades'
ACTIONS = 'acciones_riesgo'
REGISTERS = ((RISKS, 'Riesgo', 'riesgo_id'), (OPPORTUNITIES, 'Oportunidad', 'oportunidad_id'))
TITULO_MAX = 200
OBJECTIVE = 'Objetivo de la calidad: '
PLAN = 'Plan de acción: '
PARAGRAPH = '\n\n'
METADATA = ('created_at', 'updated_at', 'created_by_id', 'updated_by_id')

# As in ``c6e8a2f4b1d9``, on the old row ``o``: the first non-blank line of the
# description, trimmed and cut to fit, and the non-blank legacy texts, labelled.
_TITLE = ("COALESCE(NULLIF(RTRIM(LEFT(BTRIM(split_part(regexp_replace(o.descripcion, "
          "'^\\s+', ''), chr(10), 1), ' ' || chr(9) || chr(13)), :titulo_max)), ''), "
          ":fallback || o.id_riesgo)")
_NOTES = ("NULLIF(CONCAT_WS(chr(10) || chr(10), "
          "CASE WHEN o.objetivo_calidad ~ '\\S' THEN :objective || o.objetivo_calidad END, "
          "CASE WHEN o.plan_accion ~ '\\S' THEN :plan || o.plan_accion END), '')")
_DROP_STALE = (
    "DELETE FROM {table} t WHERE NOT EXISTS (SELECT 1 FROM " + OLD + " o "
    "WHERE o.id_riesgo = t.id AND o.tipo = :tipo) "
    "AND NOT EXISTS (SELECT 1 FROM " + ACTIONS + " a WHERE a.{link} = t.id)"
)
_REFRESH = (
    f"UPDATE {{table}} t SET titulo = {_TITLE}, descripcion = o.descripcion, "
    f"notas = {_NOTES}, created_at = o.created_at, updated_at = o.updated_at, "
    "created_by_id = o.created_by_id, updated_by_id = o.updated_by_id "
    f"FROM {OLD} o WHERE o.id_riesgo = t.id AND o.tipo = :tipo"
)
_COPY = (
    "INSERT INTO {table} (id, titulo, descripcion, origen, fecha_identificacion, notas, "
    "created_at, updated_at, created_by_id, updated_by_id) "
    f"SELECT o.id_riesgo, {_TITLE}, o.descripcion, 'interno', "
    f"COALESCE(CAST(o.created_at AS DATE), CURRENT_DATE), {_NOTES}, o.created_at, "
    f"o.updated_at, o.created_by_id, o.updated_by_id FROM {OLD} o WHERE o.tipo = :tipo "
    "AND NOT EXISTS (SELECT 1 FROM {table} t WHERE t.id = o.id_riesgo) ORDER BY o.id_riesgo"
)
# Continue the sequence after the highest id (or restart it at 1 without any).
_RESEQUENCE = ("SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
               "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}")


def _sync(connection):
    """Bring both registers up to date with the old table (see the module docstring)."""
    for table, tipo, link in REGISTERS:
        params = {'tipo': tipo, 'fallback': f'{tipo} ', 'titulo_max': TITULO_MAX,
                  'objective': OBJECTIVE, 'plan': PLAN}
        for statement in (_DROP_STALE, _REFRESH, _COPY):
            connection.execute(sa.text(statement.format(table=table, link=link)), params)
        connection.execute(sa.text(_RESEQUENCE.format(table=table)))


def _split(notas):
    """``(objetivo_calidad, plan_accion)`` from the labelled notes (see the docstring)."""
    if notas is None:
        return None, None
    if notas.startswith(OBJECTIVE):
        objective, found, plan = notas[len(OBJECTIVE):].partition(PARAGRAPH + PLAN)
        return objective, (plan if found else None)
    if notas.startswith(PLAN):
        return None, notas[len(PLAN):]
    return None, notas


def upgrade():
    _sync(op.get_bind())
    op.drop_table(OLD)
    op.execute('DROP TYPE IF EXISTS tipoenum')


def downgrade():
    op.create_table(
        OLD,
        sa.Column('id_riesgo', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.Enum('Riesgo', 'Oportunidad', name='tipoenum'), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('objetivo_calidad', sa.Text(), nullable=True),
        sa.Column('plan_accion', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id_riesgo'),
        *(sa.ForeignKeyConstraint([column], ['users.id'], name=f'fk_{OLD}_{column}_users',
                                  ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    )
    connection = op.get_bind()
    moved = []
    for table, tipo, _ in REGISTERS:
        for row in connection.execute(sa.text(
                "SELECT descripcion, notas, created_at, updated_at, created_by_id, "
                f"updated_by_id FROM {table} ORDER BY id")).mappings():
            objective, plan = _split(row['notas'])
            moved.append({key: row[key] for key in METADATA} | {
                'tipo': tipo, 'descripcion': row['descripcion'],
                'objetivo_calidad': objective, 'plan_accion': plan})
    if moved:
        connection.execute(sa.text(
            f"INSERT INTO {OLD} (tipo, descripcion, objetivo_calidad, plan_accion, created_at, "
            "updated_at, created_by_id, updated_by_id) VALUES (:tipo, :descripcion, "
            ":objetivo_calidad, :plan_accion, :created_at, :updated_at, :created_by_id, "
            ":updated_by_id)"), moved)
    for table in (ACTIONS, OPPORTUNITIES, RISKS):
        connection.execute(sa.text(f"DELETE FROM {table}"))
    _sync(connection)
