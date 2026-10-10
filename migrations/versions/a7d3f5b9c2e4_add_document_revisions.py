"""add document_revisions

Document control (ISO 9001 clause 7.5, ``document-control``). A document's
text moves into numbered revisions with a review workflow; the document keeps
its title, code and category and gains an owner, a next review date and the
withdrawal record (decisions DC1-DC6).

Every existing document becomes revision 1 ``vigente`` (DC8): its content, its
version text (``legacy_version``), approver text and signature move to the
revision, and its issue date (or, without one, the date it was created) becomes
the date the revision took effect. Then ``content``, ``version``,
``approved_by``, ``signature`` and ``issued_date`` leave ``documents``. Two
partial unique indexes allow one revision in preparation and one in force per
document.

The downgrade copies back the revision in force, or the latest revision of a
document without one; revision numbers stand in for a missing version text and
the approver's name for a missing approver text. The other revisions and the
withdrawal record are lost. PostgreSQL only (``DISTINCT ON``).

Revision ID: a7d3f5b9c2e4
Revises: f8b2d4a6c9e1
Create Date: 2026-10-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d3f5b9c2e4'
down_revision = 'f8b2d4a6c9e1'
branch_labels = None
depends_on = None

TABLE = 'document_revisions'
STATES = ('borrador', 'en_revision', 'aprobado', 'vigente', 'obsoleto')
PENDING = "estado IN ('borrador', 'en_revision', 'aprobado')"
EFFECTIVE = "estado = 'vigente'"
PERSON_LINKS = (('documents', 'owner_id'), ('documents', 'withdrawn_by_id'))
INDEXED = ('author_id', 'approver_id')


def upgrade():
    op.add_column('documents', sa.Column('owner_id', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('next_review_date', sa.Date(), nullable=True))
    op.add_column('documents', sa.Column('withdrawn_at', sa.Date(), nullable=True))
    op.add_column('documents', sa.Column('withdrawn_reason', sa.Text(), nullable=True))
    op.add_column('documents', sa.Column('withdrawn_by_id', sa.Integer(), nullable=True))
    for table, column in PERSON_LINKS:
        op.create_foreign_key(f'fk_{table}_{column}_personas', table, 'personas',
                              [column], ['id'], ondelete='RESTRICT')
        op.create_index(f'ix_{table}_{column}', table, [column])
    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('numero', sa.Integer(), nullable=False),
        sa.Column(
            'estado',
            # Mirrors ``models.text_enum``: member names in a VARCHAR(20) with a CHECK.
            sa.Enum(*STATES, native_enum=False, length=20, create_constraint=True,
                    name=f'ck_{TABLE}_estado'),
            server_default='borrador', nullable=False,
        ),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('change_summary', sa.Text(), nullable=True),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('approver_id', sa.Integer(), nullable=True),
        sa.Column('approved_at', sa.Date(), nullable=True),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('obsolete_from', sa.Date(), nullable=True),
        sa.Column('review_comment', sa.Text(), nullable=True),
        sa.Column('legacy_version', sa.String(length=10), nullable=True),
        sa.Column('legacy_approved_by', sa.String(length=100), nullable=True),
        sa.Column('legacy_signature', sa.String(length=255), nullable=True),
        # ``RecordMetadataMixin``
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'numero', name=f'uq_{TABLE}_document_id_numero'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'],
                                name=f'fk_{TABLE}_document_id_documents', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['author_id'], ['personas.id'],
                                name=f'fk_{TABLE}_author_id_personas', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['approver_id'], ['personas.id'],
                                name=f'fk_{TABLE}_approver_id_personas', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'],
                                name=f'fk_{TABLE}_created_by_id_users', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'],
                                name=f'fk_{TABLE}_updated_by_id_users', ondelete='SET NULL'),
    )
    for column in INDEXED:
        op.create_index(f'ix_{TABLE}_{column}', TABLE, [column])
    op.create_index(f'uq_{TABLE}_pending', TABLE, ['document_id'], unique=True,
                    postgresql_where=sa.text(PENDING))
    op.create_index(f'uq_{TABLE}_effective', TABLE, ['document_id'], unique=True,
                    postgresql_where=sa.text(EFFECTIVE))
    op.execute(
        f"INSERT INTO {TABLE} (document_id, numero, estado, content, legacy_version, "
        "legacy_approved_by, legacy_signature, effective_from, created_at, updated_at, "
        "created_by_id, updated_by_id) "
        "SELECT id, 1, 'vigente', content, version, approved_by, signature, "
        "COALESCE(issued_date, CAST(created_at AS DATE)), created_at, updated_at, "
        "created_by_id, updated_by_id FROM documents"
    )
    for column in ('content', 'version', 'approved_by', 'signature', 'issued_date'):
        op.drop_column('documents', column)


def downgrade():
    op.add_column('documents', sa.Column('version', sa.String(length=10), nullable=True))
    op.add_column('documents', sa.Column('issued_date', sa.Date(), nullable=True))
    op.add_column('documents', sa.Column('approved_by', sa.String(length=100), nullable=True))
    op.add_column('documents', sa.Column('signature', sa.String(length=255), nullable=True))
    op.add_column('documents', sa.Column('content', sa.Text(), nullable=True))
    op.execute(
        "UPDATE documents AS d SET content = r.content, "
        "version = COALESCE(r.legacy_version, CAST(r.numero AS VARCHAR)), "
        "approved_by = COALESCE(r.legacy_approved_by, LEFT(p.nombre, 100)), "
        "signature = r.legacy_signature, "
        "issued_date = COALESCE(r.effective_from, CAST(r.created_at AS DATE)) "
        f"FROM (SELECT DISTINCT ON (document_id) * FROM {TABLE} "
        "ORDER BY document_id, estado = 'vigente' DESC, numero DESC) AS r "
        "LEFT JOIN personas AS p ON p.id = r.approver_id "
        "WHERE r.document_id = d.id"
    )
    # A document without any revision (written outside the services) still needs both.
    op.execute("UPDATE documents SET content = COALESCE(content, ''), "
               "version = COALESCE(version, '1')")
    op.alter_column('documents', 'content', nullable=False)
    op.alter_column('documents', 'version', nullable=False)
    for name in ('uq_{}_effective', 'uq_{}_pending', *(f'ix_{{}}_{c}' for c in INDEXED)):
        op.drop_index(name.format(TABLE), table_name=TABLE)
    op.drop_table(TABLE)
    for table, column in reversed(PERSON_LINKS):
        op.drop_index(f'ix_{table}_{column}', table_name=table)
        op.drop_constraint(f'fk_{table}_{column}_personas', table, type_='foreignkey')
    for column in ('withdrawn_by_id', 'withdrawn_reason', 'withdrawn_at', 'next_review_date',
                   'owner_id'):
        op.drop_column('documents', column)
