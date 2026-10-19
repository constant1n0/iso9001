"""add external providers, their evaluations and the nonconformity link

``external-providers`` EP-1, decisions EP1 to EP4 (ISO 9001 clause 8.4). A
provider (``proveedores``) has a unique name, a tax id unique when given, its
contact, what it supplies (``tipo_suministro``, the member names of
``models.TipoSuministro``), the selection criteria, the next evaluation date
and its state (``models.EstadoProveedor``, ``pendiente`` at first) with the
date, the deciding person and the reason (``ck_proveedores_decision``). The
processes it affects live in ``proveedor_procesos``. An evaluation
(``evaluaciones_proveedor``) scores quality, delivery time, service and
documentation from 1 to 5, and its next date comes after its own. A
nonconformity (``no_conformidades``) gains ``proveedor_id``, nullable, so
existing rows keep NULL, and only with the ``proveedor`` origin. Every new link
is ``ON DELETE RESTRICT``: an evaluated, linked or cited provider, a process
with providers and a person who decided or evaluated cannot be deleted.

The downgrade drops the tables and the column, losing their data.

Revision ID: d2f4a6c8e1b3
Revises: f5a7c9e1b3d6
Create Date: 2026-10-18 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2f4a6c8e1b3'
down_revision = 'f5a7c9e1b3d6'
branch_labels = None
depends_on = None

PROVIDERS = 'proveedores'
LINKS = 'proveedor_procesos'
EVALUATIONS = 'evaluaciones_proveedor'
NCS = 'no_conformidades'
TIPOS = ('producto', 'servicio', 'proceso_externalizado')
ESTADOS = ('pendiente', 'aprobado', 'condicionado', 'bloqueado')
SCORES = ('calidad', 'plazo', 'servicio', 'documentacion')
NC_FK = f'fk_{NCS}_proveedor_id_{PROVIDERS}'
DECISION = (
    "(estado = 'pendiente' AND fecha_estado IS NULL AND decidido_por_id IS NULL "
    "AND motivo_estado IS NULL) OR "
    "(estado = 'aprobado' AND fecha_estado IS NOT NULL) OR "
    "(estado IN ('condicionado', 'bloqueado') AND fecha_estado IS NOT NULL "
    "AND motivo_estado IS NOT NULL)"
)


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


def upgrade():
    op.create_table(
        PROVIDERS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=200), nullable=False),
        sa.Column('nif', sa.String(length=20), nullable=True),
        sa.Column('contacto', sa.String(length=150), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('telefono', sa.String(length=30), nullable=True),
        sa.Column('tipo_suministro',
                  _text_enum(TIPOS, f'ck_{PROVIDERS}_tipo_suministro', length=30),
                  nullable=False),
        sa.Column('descripcion_suministro', sa.Text(), nullable=True),
        sa.Column('criterios_seleccion', sa.Text(), nullable=True),
        sa.Column('estado', _text_enum(ESTADOS, f'ck_{PROVIDERS}_estado'), nullable=False,
                  server_default='pendiente'),
        sa.Column('fecha_estado', sa.Date(), nullable=True),
        sa.Column('decidido_por_id', sa.Integer(), nullable=True),
        sa.Column('motivo_estado', sa.Text(), nullable=True),
        sa.Column('fecha_proxima_evaluacion', sa.Date(), nullable=True),
        sa.Column('notas', sa.Text(), nullable=True),
        *_metadata_columns(PROVIDERS),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nombre', name=f'uq_{PROVIDERS}_nombre'),
        sa.UniqueConstraint('nif', name=f'uq_{PROVIDERS}_nif'),
        sa.CheckConstraint(DECISION, name=f'ck_{PROVIDERS}_decision'),
        sa.ForeignKeyConstraint(
            ['decidido_por_id'], ['personas.id'],
            name=f'fk_{PROVIDERS}_decidido_por_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{PROVIDERS}_decidido_por_id', PROVIDERS, ['decidido_por_id'])

    op.create_table(
        LINKS,
        sa.Column('proveedor_id', sa.Integer(), nullable=False),
        sa.Column('proceso_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('proveedor_id', 'proceso_id', name=f'pk_{LINKS}'),
        sa.ForeignKeyConstraint(
            ['proveedor_id'], [f'{PROVIDERS}.id'],
            name=f'fk_{LINKS}_proveedor_id_{PROVIDERS}', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['proceso_id'], ['procesos_operacion.id_proceso'],
            name=f'fk_{LINKS}_proceso_id_procesos_operacion', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{LINKS}_proceso_id', LINKS, ['proceso_id'])

    op.create_table(
        EVALUATIONS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('proveedor_id', sa.Integer(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('evaluador_id', sa.Integer(), nullable=False),
        *(sa.Column(score, sa.Integer(), nullable=False) for score in SCORES),
        sa.Column('comentario', sa.Text(), nullable=True),
        sa.Column('fecha_proxima', sa.Date(), nullable=True),
        *_metadata_columns(EVALUATIONS),
        sa.PrimaryKeyConstraint('id'),
        *(sa.CheckConstraint(f'{score} BETWEEN 1 AND 5', name=f'ck_{EVALUATIONS}_{score}')
          for score in SCORES),
        sa.CheckConstraint('fecha_proxima IS NULL OR fecha_proxima > fecha',
                           name=f'ck_{EVALUATIONS}_fecha_proxima'),
        sa.ForeignKeyConstraint(
            ['proveedor_id'], [f'{PROVIDERS}.id'],
            name=f'fk_{EVALUATIONS}_proveedor_id_{PROVIDERS}', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['evaluador_id'], ['personas.id'],
            name=f'fk_{EVALUATIONS}_evaluador_id_personas', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{EVALUATIONS}_proveedor_id', EVALUATIONS, ['proveedor_id'])
    op.create_index(f'ix_{EVALUATIONS}_evaluador_id', EVALUATIONS, ['evaluador_id'])

    op.add_column(NCS, sa.Column('proveedor_id', sa.Integer(), nullable=True))
    op.create_foreign_key(NC_FK, NCS, PROVIDERS, ['proveedor_id'], ['id'], ondelete='RESTRICT')
    op.create_index(f'ix_{NCS}_proveedor_id', NCS, ['proveedor_id'])
    op.create_check_constraint(f'ck_{NCS}_proveedor', NCS,
                               "proveedor_id IS NULL OR origen = 'proveedor'")


def downgrade():
    op.drop_constraint(f'ck_{NCS}_proveedor', NCS, type_='check')
    op.drop_index(f'ix_{NCS}_proveedor_id', table_name=NCS)
    op.drop_constraint(NC_FK, NCS, type_='foreignkey')
    op.drop_column(NCS, 'proveedor_id')

    op.drop_index(f'ix_{EVALUATIONS}_evaluador_id', table_name=EVALUATIONS)
    op.drop_index(f'ix_{EVALUATIONS}_proveedor_id', table_name=EVALUATIONS)
    op.drop_table(EVALUATIONS)
    op.drop_index(f'ix_{LINKS}_proceso_id', table_name=LINKS)
    op.drop_table(LINKS)
    op.drop_index(f'ix_{PROVIDERS}_decidido_por_id', table_name=PROVIDERS)
    op.drop_table(PROVIDERS)
