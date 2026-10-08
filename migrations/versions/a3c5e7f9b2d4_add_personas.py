"""add personas and persona_roles

People who do work under the QMS, optionally linked to one user account, and
the roles (``roles_responsabilidades``) each one holds. Deleting a user keeps
the person and clears the link; deleting a person or a role removes only the
assignments between them.

Revision ID: a3c5e7f9b2d4
Revises: f2c7a9e4b1d6
Create Date: 2026-10-07 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c5e7f9b2d4'
down_revision = 'f2c7a9e4b1d6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'personas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('nombre', sa.String(length=150), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notas', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_personas_user_id_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_id'],
            ['users.id'],
            name='fk_personas_created_by_id_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by_id'],
            ['users.id'],
            name='fk_personas_updated_by_id_users',
            ondelete='SET NULL',
        ),
        sa.UniqueConstraint('user_id', name='uq_personas_user_id'),
        sa.UniqueConstraint('email', name='uq_personas_email'),
    )
    op.create_index('ix_personas_nombre', 'personas', ['nombre'])
    op.create_table(
        'persona_roles',
        sa.Column('persona_id', sa.Integer(), nullable=False),
        sa.Column('rol_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('persona_id', 'rol_id', name='pk_persona_roles'),
        sa.ForeignKeyConstraint(
            ['persona_id'],
            ['personas.id'],
            name='fk_persona_roles_persona_id_personas',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['rol_id'],
            ['roles_responsabilidades.id_rol'],
            name='fk_persona_roles_rol_id_roles_responsabilidades',
            ondelete='CASCADE',
        ),
    )
    op.create_index('ix_persona_roles_rol_id', 'persona_roles', ['rol_id'])


def downgrade():
    op.drop_index('ix_persona_roles_rol_id', table_name='persona_roles')
    op.drop_table('persona_roles')
    op.drop_index('ix_personas_nombre', table_name='personas')
    op.drop_table('personas')
