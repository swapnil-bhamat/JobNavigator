"""Add compliant manual Recruiter CRM table and applications.recruiter_id.

Revision ID: 0006_recruiters
Revises: 0005_application_pipeline_prep
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_recruiters"
down_revision = "0005_application_pipeline_prep"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)

    if not _has_table(bind, "recruiters"):
        op.create_table(
            "recruiters",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("account_id", uuid_type, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
            sa.Column("job_id", uuid_type, sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("application_id", uuid_type, sa.ForeignKey("applications.id", ondelete="SET NULL"), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("company", sa.String(), nullable=True),
            sa.Column("profile_url", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("source_provenance", sa.String(32), nullable=False, server_default="manual_entry"),
            sa.Column("outreach_status", sa.String(32), nullable=False, server_default="not_contacted"),
            sa.Column("draft_connection_note", sa.Text(), nullable=True),
            sa.Column("draft_outreach_msg", sa.Text(), nullable=True),
            sa.Column("draft_followup_msg", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("follow_up_at", sa.Date(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_recruiters_account_id", "recruiters", ["account_id"])
        op.create_index("ix_recruiters_job_id", "recruiters", ["job_id"])
        op.create_index("ix_recruiters_application_id", "recruiters", ["application_id"])

    if _has_table(bind, "applications") and not _has_column(bind, "applications", "recruiter_id"):
        op.add_column(
            "applications",
            sa.Column("recruiter_id", uuid_type, sa.ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "applications") and _has_column(bind, "applications", "recruiter_id"):
        op.drop_column("applications", "recruiter_id")
    if _has_table(bind, "recruiters"):
        op.drop_table("recruiters")
