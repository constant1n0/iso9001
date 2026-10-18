"""add the process and role register fields

``process-role-registers`` RG-1, decisions PR1 and PR3 (ISO 9001 clauses 4.4,
8.1 and 5.3). A process (``procesos_operacion``) gains a type (``tipo``, the
member names of ``models.TipoProceso`` with a CHECK), an owner
(``responsable_id``, a person), its inputs, outputs, resources and acceptance
criteria, and the processes it interacts with (``proceso_relaciones``: each pair
once, lower id first, never a process with itself). A role
(``roles_responsabilidades``) gains its responsibilities, its authorities and
the role it reports to (``reporta_a_id``, never itself). Every new column is
nullable, so existing rows keep NULL. Every new link is ``ON DELETE
RESTRICT``: a person owning a process, a process with relations and a role
others report to cannot be deleted.

The downgrade drops the table and the columns, losing their data.

Revision ID: f5a7c9e1b3d6
Revises: e3b5d7f9a1c4
Create Date: 2026-10-17 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5a7c9e1b3d6'
down_revision = 'e3b5d7f9a1c4'
branch_labels = None
depends_on = None

PROCESSES = 'procesos_operacion'
RELATIONS = 'proceso_relaciones'
ROLES = 'roles_responsabilidades'
TIPOS = ('estrategico', 'operativo', 'soporte')
PROCESS_TEXTS = ('entradas', 'salidas', 'recursos', 'criterios_aceptacion')
ROLE_TEXTS = ('responsabilidades', 'autoridades')
OWNER_FK = f'fk_{PROCESSES}_responsable_id_personas'
LINE_FK = f'fk_{ROLES}_reporta_a_id_{ROLES}'


def upgrade():
    # Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK.
    op.add_column(PROCESSES, sa.Column('tipo', sa.String(length=20), nullable=True))
    op.add_column(PROCESSES, sa.Column('responsable_id', sa.Integer(), nullable=True))
    for column in PROCESS_TEXTS:
        op.add_column(PROCESSES, sa.Column(column, sa.Text(), nullable=True))
    allowed = ', '.join(f"'{tipo}'" for tipo in TIPOS)
    op.create_check_constraint(f'ck_{PROCESSES}_tipo', PROCESSES, f'tipo IN ({allowed})')
    op.create_foreign_key(OWNER_FK, PROCESSES, 'personas', ['responsable_id'], ['id'],
                          ondelete='RESTRICT')
    op.create_index(f'ix_{PROCESSES}_responsable_id', PROCESSES, ['responsable_id'])

    op.create_table(
        RELATIONS,
        sa.Column('proceso_id', sa.Integer(), nullable=False),
        sa.Column('relacionado_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('proceso_id', 'relacionado_id', name=f'pk_{RELATIONS}'),
        sa.CheckConstraint('proceso_id < relacionado_id', name=f'ck_{RELATIONS}_orden'),
        sa.ForeignKeyConstraint(
            ['proceso_id'], [f'{PROCESSES}.id_proceso'],
            name=f'fk_{RELATIONS}_proceso_id_{PROCESSES}', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['relacionado_id'], [f'{PROCESSES}.id_proceso'],
            name=f'fk_{RELATIONS}_relacionado_id_{PROCESSES}', ondelete='RESTRICT',
        ),
    )
    op.create_index(f'ix_{RELATIONS}_relacionado_id', RELATIONS, ['relacionado_id'])

    for column in ROLE_TEXTS:
        op.add_column(ROLES, sa.Column(column, sa.Text(), nullable=True))
    op.add_column(ROLES, sa.Column('reporta_a_id', sa.Integer(), nullable=True))
    op.create_check_constraint(f'ck_{ROLES}_reporta_a', ROLES, 'reporta_a_id <> id_rol')
    op.create_foreign_key(LINE_FK, ROLES, ROLES, ['reporta_a_id'], ['id_rol'],
                          ondelete='RESTRICT')
    op.create_index(f'ix_{ROLES}_reporta_a_id', ROLES, ['reporta_a_id'])


def downgrade():
    op.drop_index(f'ix_{ROLES}_reporta_a_id', table_name=ROLES)
    op.drop_constraint(LINE_FK, ROLES, type_='foreignkey')
    op.drop_constraint(f'ck_{ROLES}_reporta_a', ROLES, type_='check')
    for column in ('reporta_a_id', *reversed(ROLE_TEXTS)):
        op.drop_column(ROLES, column)

    op.drop_index(f'ix_{RELATIONS}_relacionado_id', table_name=RELATIONS)
    op.drop_table(RELATIONS)

    op.drop_index(f'ix_{PROCESSES}_responsable_id', table_name=PROCESSES)
    op.drop_constraint(OWNER_FK, PROCESSES, type_='foreignkey')
    op.drop_constraint(f'ck_{PROCESSES}_tipo', PROCESSES, type_='check')
    for column in (*reversed(PROCESS_TEXTS), 'responsable_id', 'tipo'):
        op.drop_column(PROCESSES, column)
