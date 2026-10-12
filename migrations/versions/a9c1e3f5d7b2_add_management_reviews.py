"""add management reviews, their attendees and their actions

Management reviews (ISO 9001 clause 9.3, ``management-review``, decisions MR1
to MR6): a meeting date, the period reviewed (ending on or before the
meeting), attendees (``revision_asistentes``), a comment per input, the
conclusions and the input summary frozen when the review is held
(``datos_entrada``, JSONB). CHECK constraints keep the frozen inputs to held
reviews and the closing date to closed ones. Actions (``acciones_revision``)
have a type, an owner, a deadline and a completion date that only completed
actions carry. People cited by these rows, and reviews with actions, cannot be
deleted (``ON DELETE RESTRICT``); deleting a review removes its attendees.

The downgrade drops the three tables, losing the reviews and their actions.

Revision ID: a9c1e3f5d7b2
Revises: b4d6f8a1c3e5
Create Date: 2026-10-12 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a9c1e3f5d7b2'
down_revision = 'b4d6f8a1c3e5'
branch_labels = None
depends_on = None

REVIEWS = 'revisiones_direccion'
ATTENDEES = 'revision_asistentes'
ACTIONS = 'acciones_revision'
REVIEW_STATES = ('borrador', 'celebrada', 'cerrada')
ACTION_TYPES = ('mejora', 'cambio_sgc', 'recursos')
ACTION_STATES = ('pendiente', 'en_curso', 'completada', 'cancelada')
COMMENTS = ('comentario_acciones_previas', 'comentario_contexto', 'comentario_desempeno',
            'comentario_recursos', 'comentario_riesgos', 'comentario_mejora')
PERIOD = 'periodo_desde <= periodo_hasta AND periodo_hasta <= fecha'
INPUTS = ("(estado = 'borrador' AND datos_entrada IS NULL) OR "
          "(estado <> 'borrador' AND datos_entrada IS NOT NULL)")
CLOSING = ("(estado = 'cerrada' AND fecha_cierre IS NOT NULL) OR "
           "(estado <> 'cerrada' AND fecha_cierre IS NULL)")
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


def upgrade():
    op.create_table(
        REVIEWS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('periodo_desde', sa.Date(), nullable=False),
        sa.Column('periodo_hasta', sa.Date(), nullable=False),
        sa.Column('estado', _text_enum(REVIEW_STATES, f'ck_{REVIEWS}_estado'), nullable=False,
                  server_default='borrador'),
        *(sa.Column(name, sa.Text(), nullable=True) for name in COMMENTS),
        sa.Column('conclusiones', sa.Text(), nullable=True),
        sa.Column('datos_entrada', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
                  nullable=True),
        sa.Column('celebrada_por_id', sa.Integer(), nullable=True),
        sa.Column('fecha_cierre', sa.Date(), nullable=True),
        *_metadata_columns(REVIEWS),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(PERIOD, name=f'ck_{REVIEWS}_periodo'),
        sa.CheckConstraint(INPUTS, name=f'ck_{REVIEWS}_datos_entrada'),
        sa.CheckConstraint(CLOSING, name=f'ck_{REVIEWS}_cierre'),
        sa.ForeignKeyConstraint(
            ['celebrada_por_id'], ['personas.id'],
            name=f'fk_{REVIEWS}_celebrada_por_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{REVIEWS}_celebrada_por_id', REVIEWS, ['celebrada_por_id'])

    op.create_table(
        ATTENDEES,
        sa.Column('revision_id', sa.Integer(), nullable=False),
        sa.Column('persona_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('revision_id', 'persona_id', name=f'pk_{ATTENDEES}'),
        sa.ForeignKeyConstraint(
            ['revision_id'], [f'{REVIEWS}.id'],
            name=f'fk_{ATTENDEES}_revision_id_{REVIEWS}', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['persona_id'], ['personas.id'],
            name=f'fk_{ATTENDEES}_persona_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{ATTENDEES}_persona_id', ATTENDEES, ['persona_id'])

    op.create_table(
        ACTIONS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('revision_id', sa.Integer(), nullable=False),
        sa.Column('tipo', _text_enum(ACTION_TYPES, f'ck_{ACTIONS}_tipo'), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('responsable_id', sa.Integer(), nullable=False),
        sa.Column('fecha_limite', sa.Date(), nullable=False),
        sa.Column('estado', _text_enum(ACTION_STATES, f'ck_{ACTIONS}_estado'), nullable=False,
                  server_default='pendiente'),
        sa.Column('fecha_completada', sa.Date(), nullable=True),
        sa.Column('resultado', sa.Text(), nullable=True),
        *_metadata_columns(ACTIONS),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(COMPLETION, name=f'ck_{ACTIONS}_completada'),
        sa.ForeignKeyConstraint(
            ['revision_id'], [f'{REVIEWS}.id'],
            name=f'fk_{ACTIONS}_revision_id_{REVIEWS}', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name=f'fk_{ACTIONS}_responsable_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{ACTIONS}_revision_id', ACTIONS, ['revision_id'])
    op.create_index(f'ix_{ACTIONS}_responsable_id', ACTIONS, ['responsable_id'])


def downgrade():
    op.drop_index(f'ix_{ACTIONS}_responsable_id', table_name=ACTIONS)
    op.drop_index(f'ix_{ACTIONS}_revision_id', table_name=ACTIONS)
    op.drop_table(ACTIONS)
    op.drop_index(f'ix_{ATTENDEES}_persona_id', table_name=ATTENDEES)
    op.drop_table(ATTENDEES)
    op.drop_index(f'ix_{REVIEWS}_celebrada_por_id', table_name=REVIEWS)
    op.drop_table(REVIEWS)
