"""Add output approval/version metadata, design changes and completion (DD4).

Revision ID: f5b7c9d1e3a5
Revises: e4a6b8c0d2f4

Additive: content remains in design_outputs. Backfill one draft version per
existing output, retaining every original identifier/content/citation. Downgrade
removes metadata only; all DD2 content rows (including later versions) survive.
Integer keys and portable text CHECKs follow DD1-DD3. B-tree FK indexes support
reverse citations; unique lineage/version supports ordered version history.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f5b7c9d1e3a5'
down_revision = 'e4a6b8c0d2f4'
branch_labels = None
depends_on = None


def _metadata(table: str) -> list:
    """Attributed columns and named account citations, matching the common mixin."""
    return [
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
            name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    ]


def _fk(table: str, column: str, target: str) -> sa.ForeignKeyConstraint:
    """Retain citations in both directions, never cascade evidence deletion."""
    return sa.ForeignKeyConstraint([column], [target], name=f'fk_{table}_{column}',
                                   ondelete='RESTRICT', onupdate='RESTRICT')


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f'ix_{table}_{column}', table, [column])


def upgrade() -> None:
    """Create registers and backfill draft metadata without rewriting DD2 content."""
    with op.batch_alter_table('design_projects') as batch:
        batch.add_column(sa.Column('completed_at', sa.Date()))
        batch.add_column(sa.Column('completed_by_id', sa.Integer()))
        batch.create_foreign_key('fk_design_projects_completed_by_id', 'personas',
            ['completed_by_id'], ['id'], ondelete='RESTRICT', onupdate='RESTRICT')
        batch.create_index('ix_design_projects_completed_by_id', ['completed_by_id'])
    table = 'design_changes'
    op.create_table(table,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('impact', sa.Text(), nullable=False),
        sa.Column('review', sa.Text()),
        sa.Column('actions', sa.Text(), nullable=False),
        sa.Column('state', sa.Enum('proposed', 'authorized', 'rejected',
            native_enum=False, length=20, create_constraint=True, name='ck_design_changes_state'),
            nullable=False, server_default='proposed'),
        sa.Column('rejection_reason', sa.Text()),
        sa.Column('authorized_by_id', sa.Integer()),
        sa.Column('authorized_at', sa.Date()),
        sa.PrimaryKeyConstraint('id', name='pk_design_changes'),
        *(sa.CheckConstraint(f'length(trim({column})) > 0',
                             name=f'ck_design_changes_{column}')
          for column in ('reason', 'impact', 'actions')),
        sa.CheckConstraint(
            "(state = 'authorized' AND authorized_by_id IS NOT NULL AND "
            "authorized_at IS NOT NULL AND review IS NOT NULL AND "
            "length(trim(review)) > 0) OR (state != 'authorized' AND "
            "authorized_by_id IS NULL AND authorized_at IS NULL)",
            name='ck_design_changes_authorization'),
        sa.CheckConstraint(
            "(state = 'rejected' AND rejection_reason IS NOT NULL AND "
            "length(trim(rejection_reason)) > 0) OR "
            "(state != 'rejected' AND rejection_reason IS NULL)",
            name='ck_design_changes_rejection'),
        _fk(table, 'project_id', 'design_projects.id'),
        _fk(table, 'authorized_by_id', 'personas.id'), *_metadata(table))
    _indexes(table, ('project_id', 'authorized_by_id', 'created_by_id', 'updated_by_id'))
    table = 'design_change_outputs'
    op.create_table(table,
        sa.Column('change_id', sa.Integer(), nullable=False),
        sa.Column('output_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('change_id', 'output_id', name='pk_design_change_outputs'),
        _fk(table, 'change_id', 'design_changes.id'),
        _fk(table, 'output_id', 'design_outputs.id'))
    _indexes(table, ('output_id',))
    table = 'design_output_versions'
    op.create_table(table,
        sa.Column('output_id', sa.Integer(), nullable=False),
        sa.Column('lineage_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('state', sa.Enum('draft', 'approved', native_enum=False, length=20,
            create_constraint=True, name='ck_design_output_versions_state'),
            nullable=False, server_default='draft'),
        sa.Column('approved_by_id', sa.Integer()),
        sa.Column('approved_at', sa.Date()),
        sa.Column('change_id', sa.Integer()),
        sa.PrimaryKeyConstraint('output_id', name='pk_design_output_versions'),
        sa.UniqueConstraint('lineage_id', 'version',
                            name='uq_design_output_versions_lineage_version'),
        sa.UniqueConstraint('change_id', 'lineage_id',
                            name='uq_design_output_versions_change_lineage'),
        sa.CheckConstraint('version >= 1', name='ck_design_output_versions_version'),
        sa.CheckConstraint(
            '(version = 1 AND output_id = lineage_id AND change_id IS NULL) OR '
            '(version > 1 AND output_id != lineage_id AND change_id IS NOT NULL)',
            name='ck_design_output_versions_origin'),
        sa.CheckConstraint(
            "(state = 'approved' AND approved_by_id IS NOT NULL AND "
            "approved_at IS NOT NULL) OR (state = 'draft' AND "
            "approved_by_id IS NULL AND approved_at IS NULL)",
            name='ck_design_output_versions_approval'),
        *(_fk(table, column, target) for column, target in (
            ('output_id', 'design_outputs.id'), ('lineage_id', 'design_outputs.id'),
            ('approved_by_id', 'personas.id'), ('change_id', 'design_changes.id'))),
        *_metadata(table))
    _indexes(table, ('lineage_id', 'change_id', 'approved_by_id', 'created_by_id', 'updated_by_id'))
    outputs = sa.table('design_outputs', sa.column('id', sa.Integer()))
    versions = sa.table(table, sa.column('output_id', sa.Integer()),
        sa.column('lineage_id', sa.Integer()), sa.column('version', sa.Integer()),
        sa.column('state', sa.String()))
    op.execute(versions.insert().from_select(
        ['output_id', 'lineage_id', 'version', 'state'],
        sa.select(outputs.c.id, outputs.c.id, sa.literal(1), sa.literal('draft'))))


def downgrade() -> None:
    """Reverse metadata in dependency order; preserve every output content row."""
    op.drop_table('design_output_versions')
    op.drop_table('design_change_outputs')
    op.drop_table('design_changes')
    with op.batch_alter_table('design_projects') as batch:
        batch.drop_index('ix_design_projects_completed_by_id')
        batch.drop_constraint('fk_design_projects_completed_by_id', type_='foreignkey')
        batch.drop_column('completed_by_id')
        batch.drop_column('completed_at')
