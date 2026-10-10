"""add document revision attachments

Document control (ISO 9001 clause 7.5, ``document-control``, decision DC7):
each revision may record one attached file kept in the document storage
directory. ``attachment_path`` holds the file's random stored name (never a
path) and is unique, so no two revisions share a file; the name, size, SHA-256
and detected MIME type describe it. The five columns are set together or not
at all, with a positive size.

The downgrade drops the columns: the stored files stay on disk unreferenced,
for ``flask cleanup-document-files`` to remove once nothing needs them.

Revision ID: c3e8a1f6d4b2
Revises: a7d3f5b9c2e4
Create Date: 2026-10-10 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e8a1f6d4b2'
down_revision = 'a7d3f5b9c2e4'
branch_labels = None
depends_on = None

TABLE = 'document_revisions'
COLUMNS = (
    ('attachment_name', sa.String(length=255)),
    ('attachment_path', sa.String(length=32)),
    ('attachment_size', sa.Integer()),
    ('attachment_sha256', sa.String(length=64)),
    ('attachment_mime', sa.String(length=100)),
)
NAMES = tuple(name for name, _type in COLUMNS)
COMPLETE = (
    '(' + ' AND '.join(f'{c} IS NULL' for c in NAMES) + ') OR ('
    + ' AND '.join(f'{c} IS NOT NULL' for c in NAMES) + ' AND attachment_size > 0)'
)


def upgrade():
    for name, type_ in COLUMNS:
        op.add_column(TABLE, sa.Column(name, type_, nullable=True))
    op.create_unique_constraint('uq_document_revisions_attachment_path', TABLE,
                                ['attachment_path'])
    op.create_check_constraint('ck_document_revisions_attachment', TABLE, COMPLETE)


def downgrade():
    op.drop_constraint('ck_document_revisions_attachment', TABLE, type_='check')
    op.drop_constraint('uq_document_revisions_attachment_path', TABLE, type_='unique')
    for name in reversed(NAMES):
        op.drop_column(TABLE, name)
