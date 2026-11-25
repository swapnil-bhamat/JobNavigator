"""Add 8-stage application pipeline, cover_letter_id, follow_up_date, and interview prep/feedback.

Revision ID: 0005_application_pipeline_prep
Revises: 0004_document_approval
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_application_pipeline_prep"
down_revision = "0004_document_approval"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)

    if sa.inspect(bind).has_table("applications"):
        if not _has_column(bind, "applications", "cover_letter_id"):
            op.add_column(
                "applications",
                sa.Column("cover_letter_id", uuid_type, sa.ForeignKey("cover_letters.id", ondelete="SET NULL"), nullable=True),
            )
        if not _has_column(bind, "applications", "follow_up_date"):
            op.add_column("applications", sa.Column("follow_up_date", sa.Date(), nullable=True))

        # Preserve status_transitions history while migrating legacy statuses
        bind.execute(sa.text("UPDATE applications SET status = 'interview_scheduled' WHERE status = 'interview'"))
        bind.execute(sa.text("UPDATE applications SET status = 'offer_received' WHERE status = 'offer'"))

    if sa.inspect(bind).has_table("interviews"):
        if not _has_column(bind, "interviews", "prep_plan"):
            op.add_column("interviews", sa.Column("prep_plan", sa.JSON(), nullable=True))
        if not _has_column(bind, "interviews", "feedback_notes"):
            op.add_column("interviews", sa.Column("feedback_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("interviews"):
        for col in ("feedback_notes", "prep_plan"):
            if _has_column(bind, "interviews", col):
                op.drop_column("interviews", col)
    if sa.inspect(bind).has_table("applications"):
        bind.execute(sa.text("UPDATE applications SET status = 'interview' WHERE status = 'interview_scheduled'"))
        bind.execute(sa.text("UPDATE applications SET status = 'offer' WHERE status = 'offer_received'"))
        for col in ("follow_up_date", "cover_letter_id"):
            if _has_column(bind, "applications", col):
                op.drop_column("applications", col)
