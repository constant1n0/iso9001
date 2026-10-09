"""add nonconformity origin, severity, containment, root cause and state enum

Nonconformities record where they were detected (``origen``), how serious they
are (``gravedad``), the containment applied, the root cause and, once
cancelled, why. ``estado`` becomes an enum stored by member name with a CHECK
constraint (decision N2 of ``nc-capa-loop``). The free-text states convert:
"Abierta" -> ``abierta``, "En proceso" -> ``accion_planificada``, "Cerrada" ->
``cerrada`` and any other text -> ``abierta``; the Alembic log reports how many
rows were rewritten and how many held unrecognised text. The downgrade maps
back: both intermediate states become "En proceso" and ``cancelada`` becomes
"Cerrada".

Revision ID: e7a9c1d3f5b8
Revises: c9e3a5b7d1f4
Create Date: 2026-10-09 12:00:00.000000

"""
import logging

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a9c1d3f5b8'
down_revision = 'c9e3a5b7d1f4'
branch_labels = None
depends_on = None

log = logging.getLogger('alembic.runtime.migration')

TABLE = 'no_conformidades'
ESTADOS = ('abierta', 'accion_planificada', 'en_verificacion', 'cerrada', 'cancelada')
ORIGENES = ('auditoria', 'cliente', 'proceso', 'proveedor', 'otro')
GRAVEDADES = ('mayor', 'menor', 'observacion')
ENUM_COLUMNS = (('origen', ORIGENES), ('gravedad', GRAVEDADES))
TEXT_COLUMNS = ('contencion', 'causa_raiz', 'motivo_cancelacion')
# Legacy free text -> member name; any other text becomes 'abierta'.
LEGACY_STATES = {'Abierta': 'abierta', 'En proceso': 'accion_planificada', 'Cerrada': 'cerrada'}
# Member name -> legacy free text, for the downgrade.
MEMBER_STATES = {
    'abierta': 'Abierta',
    'accion_planificada': 'En proceso',
    'en_verificacion': 'En proceso',
    'cerrada': 'Cerrada',
    'cancelada': 'Cerrada',
}


def _check(column, values):
    """Mirrors the CHECK that ``models.text_enum`` declares (member names only)."""
    allowed = ', '.join(f"'{value}'" for value in values)
    op.create_check_constraint(f'ck_{TABLE}_{column}', TABLE, f'{column} IN ({allowed})')


def _convert_states(mapping, fallback):
    """Rewrite every ``estado`` through ``mapping``; return the number of rows updated.

    ``mapping`` is a module constant, but the values still travel as bound
    parameters rather than SQL text.
    """
    whens, params = [], {'fallback': fallback}
    for index, (old, new) in enumerate(mapping.items()):
        whens.append(f'WHEN :old{index} THEN :new{index}')
        params |= {f'old{index}': old, f'new{index}': new}
    statement = sa.text(
        f"UPDATE {TABLE} SET estado = CASE estado {' '.join(whens)} ELSE :fallback END"
    )
    return op.get_bind().execute(statement, params).rowcount


def upgrade():
    for column, _ in ENUM_COLUMNS:
        op.add_column(TABLE, sa.Column(column, sa.String(length=20), nullable=True))
    for column in TEXT_COLUMNS:
        op.add_column(TABLE, sa.Column(column, sa.Text(), nullable=True))
    for column, values in ENUM_COLUMNS:
        _check(column, values)

    known = sa.bindparam('known', expanding=True)
    unrecognised = op.get_bind().execute(
        sa.text(f'SELECT count(*) FROM {TABLE} WHERE estado NOT IN :known').bindparams(known),
        {'known': list(LEGACY_STATES)},
    ).scalar_one()
    converted = _convert_states(LEGACY_STATES, 'abierta')
    log.info(
        'Rewrote %d nonconformity states as enum members (%d unrecognised values became '
        "'abierta').", converted, unrecognised,
    )
    op.alter_column(TABLE, 'estado', server_default='abierta')
    _check('estado', ESTADOS)


def downgrade():
    op.drop_constraint(f'ck_{TABLE}_estado', TABLE, type_='check')
    op.alter_column(TABLE, 'estado', server_default=None)
    converted = _convert_states(MEMBER_STATES, 'Abierta')
    log.info('Converted %d nonconformity states back to free text.', converted)
    for column, _ in reversed(ENUM_COLUMNS):
        op.drop_constraint(f'ck_{TABLE}_{column}', TABLE, type_='check')
    for column in reversed(TEXT_COLUMNS):
        op.drop_column(TABLE, column)
    for column, _ in reversed(ENUM_COLUMNS):
        op.drop_column(TABLE, column)
