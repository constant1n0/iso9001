"""add estado to auditoria and email to user

Revision ID: a1b2c3d4e5f6
Revises: 8dcf131930c5
Create Date: 2024-01-14 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '8dcf131930c5'
branch_labels = None
depends_on = None


def upgrade():
    # Crear el tipo ENUM para estado de auditoría
    estado_auditoria_enum = sa.Enum(
        'PENDIENTE', 'EN_PROCESO', 'COMPLETADA', 'CANCELADA',
        name='estadoauditoriaenum'
    )
    estado_auditoria_enum.create(op.get_bind(), checkfirst=True)

    # Agregar columna estado a auditorias
    op.add_column('auditorias', sa.Column(
        'estado',
        sa.Enum('PENDIENTE', 'EN_PROCESO', 'COMPLETADA', 'CANCELADA', name='estadoauditoriaenum'),
        nullable=True
    ))

    # Establecer valor por defecto para registros existentes
    op.execute("UPDATE auditorias SET estado = 'PENDIENTE' WHERE estado IS NULL")

    # Hacer la columna NOT NULL después de establecer valores
    op.alter_column('auditorias', 'estado', nullable=False)

    # Agregar columna email a users
    op.add_column('users', sa.Column('email', sa.String(255), nullable=True))

    # Crear índice único para email
    op.create_index('ix_users_email', 'users', ['email'], unique=True)


def downgrade():
    # Eliminar índice de email
    op.drop_index('ix_users_email', table_name='users')

    # Eliminar columna email de users
    op.drop_column('users', 'email')

    # Eliminar columna estado de auditorias
    op.drop_column('auditorias', 'estado')

    # Eliminar el tipo ENUM
    sa.Enum(name='estadoauditoriaenum').drop(op.get_bind(), checkfirst=True)
