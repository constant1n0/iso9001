"""Add design projects and ordered stages (ISO 9001 8.3, DD1).

Revision ID: c2e4f6a8b0d2
Revises: b1d3e5f7a9c1

Additive only: existing scope, people, customer, process and audit rows stay
unchanged. Integer identities follow existing registers. B-tree FK indexes
support reverse citations; state supports list filtering. Stage positions use
an immediate unique key on both SQLite and PostgreSQL: services park affected
rows above the maximum before assigning final positions under the parent lock.
Downgrade removes the association, stages, then projects, in reverse order.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c2e4f6a8b0d2'
down_revision = 'b1d3e5f7a9c1'
branch_labels = None
depends_on = None


def _metadata(table: str) -> list:
    """Attribution columns and named nullable user links, like existing registers."""
    return [
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    ]


def upgrade() -> None:
    """Create empty project/stage registers and restrictive process associations."""
    op.create_table(
        'design_projects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer()),
        sa.Column('planned_start', sa.Date()),
        sa.Column('planned_end', sa.Date()),
        sa.Column('state', sa.Enum('borrador', 'planificado', 'en_curso', 'completado',
                                  'cancelado', native_enum=False, length=20,
                                  create_constraint=True, name='ck_design_projects_state'),
                  nullable=False, server_default='borrador'),
        sa.Column('cancellation_reason', sa.Text()),
        *_metadata('design_projects'),
        sa.PrimaryKeyConstraint('id', name='pk_design_projects'),
        sa.CheckConstraint('length(trim(title)) > 0', name='ck_design_projects_title'),
        sa.CheckConstraint('length(trim(description)) > 0', name='ck_design_projects_description'),
        sa.CheckConstraint('planned_end >= planned_start', name='ck_design_projects_dates'),
        sa.CheckConstraint("state != 'cancelado' OR (cancellation_reason IS NOT NULL AND "
                           "length(trim(cancellation_reason)) > 0)", name='ck_design_projects_cancelled'),
        sa.ForeignKeyConstraint(['owner_id'], ['personas.id'], name='fk_design_projects_owner_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['customer_id'], ['clientes.id'], name='fk_design_projects_customer_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
    )
    for column in ('owner_id', 'customer_id', 'state', 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_design_projects_{column}', 'design_projects', [column])
    op.create_table(
        'design_stages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('owner_id', sa.Integer(), nullable=False),
        sa.Column('planned_date', sa.Date()),
        sa.Column('done', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('done_date', sa.Date()),
        *_metadata('design_stages'),
        sa.PrimaryKeyConstraint('id', name='pk_design_stages'),
        sa.UniqueConstraint('project_id', 'position', name='uq_design_stages_position'),
        sa.CheckConstraint('position > 0', name='ck_design_stages_position'),
        sa.CheckConstraint('length(trim(name)) > 0', name='ck_design_stages_name'),
        sa.CheckConstraint('(done AND done_date IS NOT NULL) OR (NOT done AND done_date IS NULL)',
                           name='ck_design_stages_done'),
        sa.ForeignKeyConstraint(['project_id'], ['design_projects.id'], name='fk_design_stages_project_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['owner_id'], ['personas.id'], name='fk_design_stages_owner_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
    )
    for column in ('project_id', 'owner_id', 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_design_stages_{column}', 'design_stages', [column])
    op.create_table(
        'design_processes',
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('process_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('project_id', 'process_id', name='pk_design_processes'),
        sa.ForeignKeyConstraint(['project_id'], ['design_projects.id'], name='fk_design_processes_project_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['process_id'], ['procesos_operacion.id_proceso'],
                                name='fk_design_processes_process_id', ondelete='RESTRICT', onupdate='RESTRICT'),
    )
    op.create_index('ix_design_processes_process_id', 'design_processes', ['process_id'])


def downgrade() -> None:
    """Remove only DD1 additions; no pre-existing table or row is changed."""
    op.drop_index('ix_design_processes_process_id', table_name='design_processes')
    op.drop_table('design_processes')
    for column in ('updated_by_id', 'created_by_id', 'owner_id', 'project_id'):
        op.drop_index(f'ix_design_stages_{column}', table_name='design_stages')
    op.drop_table('design_stages')
    for column in ('updated_by_id', 'created_by_id', 'state', 'customer_id', 'owner_id'):
        op.drop_index(f'ix_design_projects_{column}', table_name='design_projects')
    op.drop_table('design_projects')
