"""Add audited knowledge citations and optional typed provenance (KN2).

Revision ID: f9b1d3e5a7c9
Revises: e8a0c2d4f6b8

Existing knowledge rows retain every value and begin with no links. Composite
primary keys index the knowledge FK; target indexes support reverse citations
and FK deletion checks. All references RESTRICT updates and deletes. Downgrade
removes only KN2 additions, in reverse dependency order.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f9b1d3e5a7c9'
down_revision = 'e8a0c2d4f6b8'
branch_labels = None
depends_on = None

ASSOCIATIONS = (
    ('knowledge_processes', 'procesos_operacion.id_proceso'),
    ('knowledge_documents', 'documents.id'),
    ('knowledge_experts', 'personas.id'),
    ('knowledge_trainings', 'capacitaciones.id'),
    ('knowledge_requirements', 'competencias_requeridas.id'),
)
ORIGINS = (
    ('no_conformidad_id', 'no_conformidades.id'),
    ('hallazgo_id', 'hallazgos_auditoria.id'),
    ('mejora_id', 'mejoras.id_mejora'),
    ('accion_revision_id', 'acciones_revision.id'),
)
ONE_ORIGIN = ' + '.join(
    f'(CASE WHEN {column} IS NULL THEN 0 ELSE 1 END)' for column, _ in ORIGINS
) + ' <= 1'


def upgrade() -> None:
    """Add nullable provenance columns and empty association tables."""
    # Batch supports isolated SQLite verification; PostgreSQL uses additive ALTERs.
    with op.batch_alter_table('knowledge_items') as batch:
        for column, target in ORIGINS:
            table, key = target.split('.')
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(f'fk_knowledge_items_{column}', table,
                                     [column], [key], ondelete='RESTRICT',
                                     onupdate='RESTRICT')
            batch.create_index(f'ix_knowledge_items_{column}', [column])
        batch.create_check_constraint('ck_knowledge_items_one_origin', ONE_ORIGIN)
    for name, target in ASSOCIATIONS:
        op.create_table(
            name,
            sa.Column('knowledge_id', sa.Integer(), nullable=False),
            sa.Column('target_id', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('knowledge_id', 'target_id', name=f'pk_{name}'),
            sa.ForeignKeyConstraint(
                ['knowledge_id'], ['knowledge_items.id'],
                name=f'fk_{name}_knowledge_id', ondelete='RESTRICT', onupdate='RESTRICT'),
            sa.ForeignKeyConstraint(
                ['target_id'], [target], name=f'fk_{name}_target_id',
                ondelete='RESTRICT', onupdate='RESTRICT'),
        )
        op.create_index(f'ix_{name}_target_id', name, ['target_id'])


def downgrade() -> None:
    """Remove KN2 links before their columns; preserve every knowledge row."""
    for name, _ in reversed(ASSOCIATIONS):
        op.drop_index(f'ix_{name}_target_id', table_name=name)
        op.drop_table(name)
    with op.batch_alter_table('knowledge_items') as batch:
        batch.drop_constraint('ck_knowledge_items_one_origin', type_='check')
        for column, _ in reversed(ORIGINS):
            batch.drop_index(f'ix_knowledge_items_{column}')
            batch.drop_constraint(f'fk_knowledge_items_{column}', type_='foreignkey')
            batch.drop_column(column)
