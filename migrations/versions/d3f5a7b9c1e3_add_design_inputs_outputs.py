"""Add typed design inputs and outputs (ISO 9001 8.3, DD2).

Revision ID: d3f5a7b9c1e3
Revises: c2e4f6a8b0d2

Additive: no existing row changes. Integer keys follow DD1. Named text-enum
CHECKs work on SQLite and PostgreSQL. B-tree FK indexes support project lists,
reverse revision citations and user attribution. Downgrade reverses creation.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd3f5a7b9c1e3'
down_revision = 'c2e4f6a8b0d2'
branch_labels = None
depends_on = None

INPUT_TYPES = ('functional_performance', 'legal_regulatory', 'prior_knowledge',
               'failure_consequences', 'other')
OUTPUT_TYPES = ('specification', 'drawing', 'procedure_instruction',
                'prototype_sample', 'software', 'other')


def _create(table: str, kinds: tuple[str, ...], *, output: bool = False) -> None:
    """Create an attributed child with restrictive parent/exact-revision links."""
    op.create_table(
        table,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum(*kinds, native_enum=False, length=30,
                                 create_constraint=True, name=f'ck_{table}_kind'),
                  nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        *([sa.Column('acceptance_criteria', sa.Text(), nullable=False)] if output else []),
        sa.Column('revision_id', sa.Integer()),
        sa.Column('created_at', sa.DateTime(timezone=True)),
        sa.Column('updated_at', sa.DateTime(timezone=True)),
        sa.Column('created_by_id', sa.Integer()),
        sa.Column('updated_by_id', sa.Integer()),
        sa.PrimaryKeyConstraint('id', name=f'pk_{table}'),
        sa.CheckConstraint('length(trim(text)) > 0', name=f'ck_{table}_text'),
        *([sa.CheckConstraint('length(trim(acceptance_criteria)) > 0',
                              name=f'ck_{table}_acceptance_criteria')] if output else []),
        sa.ForeignKeyConstraint(['project_id'], ['design_projects.id'],
                                name=f'fk_{table}_project_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        sa.ForeignKeyConstraint(['revision_id'], ['document_revisions.id'],
                                name=f'fk_{table}_revision_id',
                                ondelete='RESTRICT', onupdate='RESTRICT'),
        *(sa.ForeignKeyConstraint([column], ['users.id'],
                                  name=f'fk_{table}_{column}_users', ondelete='SET NULL')
          for column in ('created_by_id', 'updated_by_id')),
    )
    for column in ('project_id', 'revision_id', 'created_by_id', 'updated_by_id'):
        op.create_index(f'ix_{table}_{column}', table, [column])


def upgrade() -> None:
    """Add empty child registers without changing DD1 or document control."""
    _create('design_inputs', INPUT_TYPES)
    _create('design_outputs', OUTPUT_TYPES, output=True)


def downgrade() -> None:
    """Remove outputs then inputs; existing DD1/document rows remain unchanged."""
    for table in ('design_outputs', 'design_inputs'):
        for column in ('updated_by_id', 'created_by_id', 'revision_id', 'project_id'):
            op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_table(table)
