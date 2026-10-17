"""add the risk and opportunity registers and their actions

Risks and opportunities (ISO 9001 clause 6.1, ``risks-opportunities``,
decisions RO1 to RO5) become two registers: ``riesgos``, assessed by
likelihood and impact before and after their actions, and ``oportunidades``,
assessed by benefit and feasibility. Each pair is on a 1-5 scale and either
complete or still pending (CHECK). Both cite an optional interested party,
process and owner. Actions (``acciones_riesgo``) belong to exactly one risk or
one opportunity, carry a completion date only when completed, and are
evaluated (result and date together) only once completed. Everything these
rows cite is kept from being deleted (``ON DELETE RESTRICT``).

Every ``riesgos_oportunidades`` row is copied to the register of its type
with the same id (the sequences continue after the copied ids):

- ``titulo``: the first non-blank line of ``descripcion``, trimmed and cut to
  200 characters; ``Riesgo <id>`` or ``Oportunidad <id>`` for a blank one.
- ``descripcion`` as it is; ``origen`` ``interno``; the default state; no
  assessment, owner, interested party or process (decision RO4).
- ``notas``: the non-blank ``objetivo_calidad`` and ``plan_accion`` texts,
  labelled ``Objetivo de la calidad:`` and ``Plan de acción:``, one paragraph
  each.
- ``fecha_identificacion``: the date of ``created_at``, or the migration date
  for rows without one; the record metadata columns are kept.

``riesgos_oportunidades`` stays: its service, JSON endpoints and MCP module
still use it until RO-2, whose migration drops the table and moves the rows
back on its downgrade. This downgrade drops the three new tables; the old
table still holds every row it held, so nothing it had is lost. Whatever was
written only to the new tables is lost.

Revision ID: c6e8a2f4b1d9
Revises: a9c1e3f5d7b2
Create Date: 2026-10-17 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6e8a2f4b1d9'
down_revision = 'a9c1e3f5d7b2'
branch_labels = None
depends_on = None

OLD = 'riesgos_oportunidades'
RISKS = 'riesgos'
OPPORTUNITIES = 'oportunidades'
ACTIONS = 'acciones_riesgo'
ORIGINS = ('interno', 'externo')
TREATMENTS = ('aceptar', 'mitigar', 'transferir', 'evitar')
RISK_STATES = ('identificado', 'en_tratamiento', 'controlado', 'cerrado')
DECISIONS = ('aprovechar', 'posponer', 'descartar')
OPPORTUNITY_STATES = ('identificada', 'en_curso', 'aprovechada', 'descartada')
ACTION_STATES = ('pendiente', 'en_curso', 'completada', 'cancelada')
EFFECTIVENESS = ('eficaz', 'no_eficaz')
REGISTER_LINKS = (('parte_interesada_id', 'partes_interesadas', 'id_interesado'),
                  ('proceso_id', 'procesos_operacion', 'id_proceso'),
                  ('responsable_id', 'personas', 'id'))
ACTION_LINKS = (('riesgo_id', RISKS, 'id'), ('oportunidad_id', OPPORTUNITIES, 'id'),
                ('responsable_id', 'personas', 'id'))
ONE_REGISTER = ('(riesgo_id IS NOT NULL AND oportunidad_id IS NULL) OR '
                '(riesgo_id IS NULL AND oportunidad_id IS NOT NULL)')
COMPLETION = ("(estado = 'completada' AND fecha_completada IS NOT NULL) OR "
              "(estado <> 'completada' AND fecha_completada IS NULL)")
EVALUATION = ("(eficacia IS NULL AND fecha_evaluacion IS NULL) OR "
              "(eficacia IS NOT NULL AND fecha_evaluacion IS NOT NULL "
              "AND estado = 'completada')")
TITULO_MAX = 200

# The first non-blank line of the old description, trimmed and cut to fit.
_FIRST_LINE = ("RTRIM(LEFT(BTRIM(split_part(regexp_replace(descripcion, '^\\s+', ''), "
               "chr(10), 1), ' ' || chr(9) || chr(13)), :titulo_max))")
# The non-blank legacy texts, labelled, one paragraph each; NULL without any.
_NOTES = ("NULLIF(CONCAT_WS(chr(10) || chr(10), "
          "CASE WHEN objetivo_calidad ~ '\\S' THEN :objective || objetivo_calidad END, "
          "CASE WHEN plan_accion ~ '\\S' THEN :plan || plan_accion END), '')")
_COPY = (
    "INSERT INTO {table} (id, titulo, descripcion, origen, fecha_identificacion, notas, "
    "created_at, updated_at, created_by_id, updated_by_id) "
    f"SELECT id_riesgo, COALESCE(NULLIF({_FIRST_LINE}, ''), :fallback || id_riesgo), "
    f"descripcion, 'interno', COALESCE(CAST(created_at AS DATE), CURRENT_DATE), {_NOTES}, "
    f"created_at, updated_at, created_by_id, updated_by_id FROM {OLD} "
    "WHERE tipo = :tipo ORDER BY id_riesgo"
)
# Continue the sequence after the copied ids (or restart it at 1 without any).
_RESEQUENCE = ("SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
               "COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}")


def _text_enum(values, name):
    """Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK."""
    return sa.Enum(*values, native_enum=False, length=20, create_constraint=True, name=name)


def _scale_pair(first, second):
    """Mirrors ``models._scale_pair``: both on the 1-5 scale, or both NULL."""
    return (f'({first} IS NULL AND {second} IS NULL) OR '
            f'({first} IS NOT NULL AND {second} IS NOT NULL AND '
            f'{first} BETWEEN 1 AND 5 AND {second} BETWEEN 1 AND 5)')


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


def _links(table, links):
    """``ON DELETE RESTRICT`` foreign keys named as the models name them."""
    return [
        sa.ForeignKeyConstraint([column], [f'{target}.{key}'],
                                name=f'fk_{table}_{column}_{target}', ondelete='RESTRICT')
        for column, target, key in links
    ]


def _register_columns(table):
    """Columns a risk and an opportunity share, before their assessment."""
    return [
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('titulo', sa.String(length=TITULO_MAX), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('origen', _text_enum(ORIGINS, f'ck_{table}_origen'), nullable=False),
        sa.Column('parte_interesada_id', sa.Integer(), nullable=True),
        sa.Column('proceso_id', sa.Integer(), nullable=True),
        sa.Column('responsable_id', sa.Integer(), nullable=True),
    ]


def _register_tail(table, states):
    """State, dates, notes, metadata, primary key and links of a register."""
    return [
        sa.Column('estado', _text_enum(states, f'ck_{table}_estado'), nullable=False,
                  server_default=states[0]),
        sa.Column('fecha_identificacion', sa.Date(), nullable=False),
        sa.Column('fecha_revision', sa.Date(), nullable=True),
        sa.Column('notas', sa.Text(), nullable=True),
        *_metadata_columns(table),
        sa.PrimaryKeyConstraint('id'),
        *_links(table, REGISTER_LINKS),
    ]


def _create_indexes(table, links):
    for column, _, _ in links:
        op.create_index(f'ix_{table}_{column}', table, [column])


def _drop_indexes(table, links):
    for column, _, _ in reversed(links):
        op.drop_index(f'ix_{table}_{column}', table_name=table)


def upgrade():
    op.create_table(
        RISKS,
        *_register_columns(RISKS),
        sa.Column('probabilidad', sa.Integer(), nullable=True),
        sa.Column('impacto', sa.Integer(), nullable=True),
        sa.Column('tratamiento', _text_enum(TREATMENTS, f'ck_{RISKS}_tratamiento'),
                  nullable=True),
        sa.Column('probabilidad_residual', sa.Integer(), nullable=True),
        sa.Column('impacto_residual', sa.Integer(), nullable=True),
        *_register_tail(RISKS, RISK_STATES),
        sa.CheckConstraint(_scale_pair('probabilidad', 'impacto'), name=f'ck_{RISKS}_valoracion'),
        sa.CheckConstraint(_scale_pair('probabilidad_residual', 'impacto_residual'),
                           name=f'ck_{RISKS}_valoracion_residual'),
    )
    _create_indexes(RISKS, REGISTER_LINKS)

    op.create_table(
        OPPORTUNITIES,
        *_register_columns(OPPORTUNITIES),
        sa.Column('beneficio', sa.Integer(), nullable=True),
        sa.Column('viabilidad', sa.Integer(), nullable=True),
        sa.Column('decision', _text_enum(DECISIONS, f'ck_{OPPORTUNITIES}_decision'),
                  nullable=True),
        *_register_tail(OPPORTUNITIES, OPPORTUNITY_STATES),
        sa.CheckConstraint(_scale_pair('beneficio', 'viabilidad'),
                           name=f'ck_{OPPORTUNITIES}_valoracion'),
    )
    _create_indexes(OPPORTUNITIES, REGISTER_LINKS)

    op.create_table(
        ACTIONS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('riesgo_id', sa.Integer(), nullable=True),
        sa.Column('oportunidad_id', sa.Integer(), nullable=True),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('responsable_id', sa.Integer(), nullable=False),
        sa.Column('fecha_limite', sa.Date(), nullable=False),
        sa.Column('estado', _text_enum(ACTION_STATES, f'ck_{ACTIONS}_estado'), nullable=False,
                  server_default='pendiente'),
        sa.Column('fecha_completada', sa.Date(), nullable=True),
        sa.Column('eficacia', _text_enum(EFFECTIVENESS, f'ck_{ACTIONS}_eficacia'),
                  nullable=True),
        sa.Column('fecha_evaluacion', sa.Date(), nullable=True),
        sa.Column('comentario_eficacia', sa.Text(), nullable=True),
        *_metadata_columns(ACTIONS),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(ONE_REGISTER, name=f'ck_{ACTIONS}_un_registro'),
        sa.CheckConstraint(COMPLETION, name=f'ck_{ACTIONS}_completada'),
        sa.CheckConstraint(EVALUATION, name=f'ck_{ACTIONS}_evaluacion'),
        *_links(ACTIONS, ACTION_LINKS),
    )
    _create_indexes(ACTIONS, ACTION_LINKS)

    connection = op.get_bind()
    for table, tipo, fallback in ((RISKS, 'Riesgo', 'Riesgo '),
                                  (OPPORTUNITIES, 'Oportunidad', 'Oportunidad ')):
        connection.execute(sa.text(_COPY.format(table=table)), {
            'tipo': tipo, 'fallback': fallback, 'titulo_max': TITULO_MAX,
            'objective': 'Objetivo de la calidad: ', 'plan': 'Plan de acción: ',
        })
        connection.execute(sa.text(_RESEQUENCE.format(table=table)))


def downgrade():
    for table, links in ((ACTIONS, ACTION_LINKS), (OPPORTUNITIES, REGISTER_LINKS),
                         (RISKS, REGISTER_LINKS)):
        _drop_indexes(table, links)
        op.drop_table(table)
