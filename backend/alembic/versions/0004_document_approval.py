"""Add hash-locked approval, changes explanation, and source facts to resumes and cover_letters.

Revision ID: 0004_document_approval
Revises: 0003_job_evaluation_remote
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_document_approval"
down_revision = "0003_job_evaluation_remote"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("resumes", "cover_letters"):
        if not sa.inspect(bind).has_table(table):
            continue
        if not _has_column(bind, table, "content_hash"):
            op.add_column(table, sa.Column("content_hash", sa.String(64), nullable=True))
            op.create_index(f"ix_{table}_content_hash", table, ["content_hash"])
        if not _has_column(bind, table, "approval_status"):
            op.add_column(
                table,
                sa.Column("approval_status", sa.String(24), nullable=False, server_default="draft"),
            )
        if not _has_column(bind, table, "approved_content_hash"):
            op.add_column(table, sa.Column("approved_content_hash", sa.String(64), nullable=True))
        if not _has_column(bind, table, "approved_at"):
            op.add_column(table, sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        if not _has_column(bind, table, "changes_explanation"):
            op.add_column(table, sa.Column("changes_explanation", sa.JSON(), nullable=True))
        if not _has_column(bind, table, "missing_qualifications"):
            op.add_column(table, sa.Column("missing_qualifications", sa.JSON(), nullable=True))
        if not _has_column(bind, table, "source_facts"):
            op.add_column(table, sa.Column("source_facts", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("resumes", "cover_letters"):
        if not sa.inspect(bind).has_table(table):
            continue
        for col in (
            "source_facts",
            "missing_qualifications",
            "changes_explanation",
            "approved_at",
            "approved_content_hash",
            "approval_status",
            "content_hash",
        ):
            if _has_column(bind, table, col):
                op.drop_column(table, col)
