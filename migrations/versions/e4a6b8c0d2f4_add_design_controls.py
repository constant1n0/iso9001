"""Add separate planned controls and immutable control evidence (DD3, 8.3.4).

Revision ID: e4a6b8c0d2f4
Revises: d3f5a7b9c1e3

Additive; DD1/DD2 rows are untouched. Integer keys and named portable text-enum
CHECKs follow DD1/DD2. Restrictive indexed citations preserve evidence. B-tree
(project_id, date, id) supports newest-first history; individual FK indexes
support reverse citations/attribution. Downgrade reverses creation order.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e4a6b8c0d2f4'
down_revision = 'd3f5a7b9c1e3'
branch_labels = None
depends_on = None

KINDS = ('review', 'verification', 'validation')
RESULTS = ('conforming', 'conforming_with_observations', 'nonconforming')


def _create(table: str, *, record: bool = False) -> None:
    """Create an attributed child with named checks and indexed restrictive keys."""
    links = [('project_id', 'design_projects.id'), ('stage_id', 'design_stages.id'),
             ('responsible_id', 'personas.id')]
    if record:
        links.append(('planned_control_id', 'design_planned_controls.id'))
    columns = [
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum(*KINDS, native_enum=False, length=20,
                                 create_constraint=True, name=f'ck_{table}_kind'),
                  nullable=False),
        sa.Column('responsible_id', sa.Integer(), nullable=False),
        sa.Column('stage_id', sa.Integer()),
    ]
    if record:
        columns += [
            sa.Column('date', sa.Date(), nullable=False),
            sa.Column('result', sa.Enum(*RESULTS, native_enum=False, length=30,
                                       create_constraint=True, name=f'ck_{table}_result'),
                      nullable=False),
            sa.Column('evidence', sa.Text(), nullable=False),
            sa.Column('actions', sa.Text()),
            sa.Column('planned_control_id', sa.Integer()),
            sa.CheckConstraint('length(trim(evidence)) > 0', name=f'ck_{table}_evidence'),
            sa.CheckConstraint(
                "result = 'conforming' OR (actions IS NOT NULL AND length(trim(actions)) > 0)",
                name=f'ck_{table}_actions'),
        ]
    else:
        columns += [
            sa.Column('description', sa.Text(), nullable=False),
            sa.Column('planned_date', sa.Date()),
            sa.Column('withdrawn', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('withdrawal_reason', sa.Text()),
            sa.CheckConstraint('length(trim(description)) > 0', name=f'ck_{table}_description'),
            sa.CheckConstraint(
                '(withdrawn AND withdrawal_reason IS NOT NULL AND '
                'length(trim(withdrawal_reason)) > 0) OR '
                '(NOT withdrawn AND withdrawal_reason IS NULL)', name=f'ck_{table}_withdrawal'),
        ]
    op.create_table(
        table, *columns,
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        sa.PrimaryKeyConstraint('id', name=f'pk_{table}'),
        *(sa.ForeignKeyConstraint([column], [target], name=f'fk_{table}_{column}',
                                  ondelete='RESTRICT', onupdate='RESTRICT')
          for column, target in links),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    )
    for column in [c for c, _ in links] + ['created_by_id', 'updated_by_id']:
        op.create_index(f'ix_{table}_{column}', table, [column])
    if record:
        op.create_index(f'ix_{table}_history', table, ['project_id', 'date', 'id'])


def upgrade() -> None:
    """Add empty control registers, without modifying existing data or policy."""
    _create('design_planned_controls')
    _create('design_control_records', record=True)


def downgrade() -> None:
    """Remove evidence before its plan, reversing every index/table operation."""
    for table in ('design_control_records', 'design_planned_controls'):
        if table == 'design_control_records':
            op.drop_index(f'ix_{table}_history', table_name=table)
        columns = ['project_id', 'stage_id', 'responsible_id']
        if table == 'design_control_records':
            columns.append('planned_control_id')
        columns += ['created_by_id', 'updated_by_id']
        for column in reversed(columns):
            op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_table(table)
