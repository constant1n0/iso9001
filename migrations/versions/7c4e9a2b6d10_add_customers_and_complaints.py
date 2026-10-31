"""Add customers and complaints; link satisfaction surveys to customer master data.

Existing distinct non-empty survey names become customers without invented
attribution. Survey type/channel default to otra/otro, including for raw SQL
inserts. An empty legacy customer name has no valid match: the NOT NULL change
refuses it transactionally rather than discarding a survey or inventing a name.
Downgrade restores the legacy name before dropping the links. Names longer than
its original VARCHAR(100) refuse downgrade rather than silently truncating data.
Complaint and customer master data are lost on downgrade.

Revision ID: 7c4e9a2b6d10
Revises: 29637060aaf0
"""
from alembic import op
import sqlalchemy as sa

revision = '7c4e9a2b6d10'
down_revision = '29637060aaf0'
branch_labels = None
depends_on = None

CUSTOMERS = 'clientes'
COMPLAINTS = 'reclamaciones'
SURVEYS = 'satisfaccion_cliente'
CHANNELS = ('telefono', 'email', 'presencial', 'web', 'escrito', 'otro')
TYPES = ('postventa', 'periodica', 'puntual', 'otra')
STATES = ('recibida', 'respondida', 'cerrada', 'cancelada')
RESPONSE = (
    "(estado IN ('respondida','cerrada') AND respuesta IS NOT NULL "
    "AND fecha_respuesta IS NOT NULL) OR (estado IN ('recibida','cancelada') "
    "AND respuesta IS NULL AND fecha_respuesta IS NULL)"
)
CLOSING = (
    "(estado = 'cerrada' AND fecha_cierre IS NOT NULL) OR "
    "(estado <> 'cerrada' AND fecha_cierre IS NULL)"
)
CANCELLATION = (
    "(estado = 'cancelada' AND motivo_cancelacion IS NOT NULL) OR "
    "(estado <> 'cancelada' AND motivo_cancelacion IS NULL)"
)


def _text_enum(values, name):
    return sa.Enum(*values, native_enum=False, length=20, create_constraint=True, name=name)


def _metadata_columns(table):
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        *(sa.ForeignKeyConstraint([f'{column}_id'], ['users.id'],
                                  name=f'fk_{table}_{column}_id_users', ondelete='SET NULL')
          for column in ('created_by', 'updated_by')),
    ]


def _restrict(table, column, target):
    return sa.ForeignKeyConstraint([column], [f'{target}.id'],
                                   name=f'fk_{table}_{column}_{target}', ondelete='RESTRICT')


def upgrade():
    op.create_table(
        CUSTOMERS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(200), nullable=False),
        sa.Column('nif', sa.String(20), nullable=True),
        sa.Column('contacto', sa.String(150), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('telefono', sa.String(30), nullable=True),
        sa.Column('notas', sa.Text(), nullable=True),
        *_metadata_columns(CUSTOMERS),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('nombre', name='uq_clientes_nombre'),
        sa.UniqueConstraint('nif', name='uq_clientes_nif'),
    )
    op.execute(sa.text(
        "INSERT INTO clientes (nombre) SELECT DISTINCT cliente FROM satisfaccion_cliente "
        "WHERE cliente IS NOT NULL AND btrim(cliente) <> ''"))
    op.create_table(
        COMPLAINTS,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cliente_id', sa.Integer(), nullable=False),
        sa.Column('fecha_recepcion', sa.Date(), nullable=False),
        sa.Column('descripcion', sa.Text(), nullable=False),
        sa.Column('canal', _text_enum(CHANNELS, 'ck_reclamaciones_canal'), nullable=True),
        sa.Column('estado', _text_enum(STATES, 'ck_reclamaciones_estado'), nullable=False,
                  server_default='recibida'),
        sa.Column('respuesta', sa.Text(), nullable=True),
        sa.Column('fecha_respuesta', sa.Date(), nullable=True),
        sa.Column('fecha_cierre', sa.Date(), nullable=True),
        sa.Column('motivo_cancelacion', sa.Text(), nullable=True),
        sa.Column('no_conformidad_id', sa.Integer(), nullable=True),
        *_metadata_columns(COMPLAINTS),
        sa.PrimaryKeyConstraint('id'),
        _restrict(COMPLAINTS, 'cliente_id', CUSTOMERS),
        _restrict(COMPLAINTS, 'no_conformidad_id', 'no_conformidades'),
        sa.CheckConstraint(RESPONSE, name='ck_reclamaciones_respuesta'),
        sa.CheckConstraint(CLOSING, name='ck_reclamaciones_cierre'),
        sa.CheckConstraint(CANCELLATION, name='ck_reclamaciones_cancelacion'),
    )
    for column in ('cliente_id', 'no_conformidad_id'):
        op.create_index(f'ix_{COMPLAINTS}_{column}', COMPLAINTS, [column])

    op.add_column(SURVEYS, sa.Column('cliente_id', sa.Integer(), nullable=True))
    op.execute(sa.text(
        "UPDATE satisfaccion_cliente SET cliente_id = clientes.id FROM clientes "
        "WHERE clientes.nombre = satisfaccion_cliente.cliente"))
    # Add VARCHAR columns and explicit CHECKs: ADD COLUMN's enum CHECK handling
    # varies across Alembic versions; the final schema matches text_enum exactly.
    for column, values, default in (('tipo_encuesta', TYPES, 'otra'), ('canal', CHANNELS, 'otro')):
        op.add_column(SURVEYS, sa.Column(column, sa.String(20), nullable=True,
                                        server_default=default))
        op.execute(sa.text(f"UPDATE {SURVEYS} SET {column} = '{default}' WHERE {column} IS NULL"))
        op.alter_column(SURVEYS, column, existing_type=sa.String(20), nullable=False)
        allowed = ', '.join(repr(value) for value in values)
        op.create_check_constraint(f'ck_{SURVEYS}_{column}', SURVEYS,
                                   f'{column} IN ({allowed})')
    op.alter_column(SURVEYS, 'cliente_id', existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key('fk_satisfaccion_cliente_cliente_id_clientes', SURVEYS, CUSTOMERS,
                          ['cliente_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_satisfaccion_cliente_cliente_id', SURVEYS, ['cliente_id'])
    op.drop_column(SURVEYS, 'cliente')


def downgrade():
    # Check before dropping anything, even if transactional DDL is disabled by a caller.
    op.execute(sa.text("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM satisfaccion_cliente s JOIN clientes c ON c.id=s.cliente_id
                     WHERE char_length(c.nombre) > 100) THEN
            RAISE EXCEPTION 'Customer names exceed the legacy survey limit of 100 characters';
          END IF;
        END $$
    """))
    op.drop_index('ix_reclamaciones_no_conformidad_id', table_name=COMPLAINTS)
    op.drop_index('ix_reclamaciones_cliente_id', table_name=COMPLAINTS)
    op.drop_table(COMPLAINTS)
    op.add_column(SURVEYS, sa.Column('cliente', sa.String(100), nullable=True))
    op.execute(sa.text(
        "UPDATE satisfaccion_cliente SET cliente = clientes.nombre FROM clientes "
        "WHERE clientes.id = satisfaccion_cliente.cliente_id"))
    op.alter_column(SURVEYS, 'cliente', existing_type=sa.String(100), nullable=False)
    op.drop_index('ix_satisfaccion_cliente_cliente_id', table_name=SURVEYS)
    op.drop_constraint('fk_satisfaccion_cliente_cliente_id_clientes', SURVEYS, type_='foreignkey')
    for column in ('canal', 'tipo_encuesta'):
        op.drop_constraint(f'ck_{SURVEYS}_{column}', SURVEYS, type_='check')
        op.drop_column(SURVEYS, column)
    op.drop_column(SURVEYS, 'cliente_id')
    op.drop_table(CUSTOMERS)
