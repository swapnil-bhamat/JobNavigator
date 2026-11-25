"""Add remote_eligibility, evaluation cache/dimensions, and migrate Job evaluation statuses.

Revision ID: 0003_job_evaluation_remote
Revises: 0002_budget_guard
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_job_evaluation_remote"
down_revision = "0002_budget_guard"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("jobs"):
        if not _has_column(bind, "jobs", "remote_eligibility"):
            op.add_column("jobs", sa.Column("remote_eligibility", sa.String(32), nullable=True))
            op.create_index("ix_jobs_remote_eligibility", "jobs", ["remote_eligibility"])
        if not _has_column(bind, "jobs", "evaluation_cache_key"):
            op.add_column("jobs", sa.Column("evaluation_cache_key", sa.String(64), nullable=True))
            op.create_index("ix_jobs_evaluation_cache_key", "jobs", ["evaluation_cache_key"])
        if not _has_column(bind, "jobs", "evaluation_dimensions"):
            op.add_column("jobs", sa.Column("evaluation_dimensions", sa.JSON(), nullable=True))

        # Preserve existing job rows while adopting separated Job evaluation statuses
        bind.execute(sa.text("UPDATE jobs SET status = 'discovered' WHERE status = 'new'"))
        bind.execute(sa.text("UPDATE jobs SET status = 'shortlisted' WHERE status = 'saved'"))


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("jobs"):
        bind.execute(sa.text("UPDATE jobs SET status = 'new' WHERE status = 'discovered'"))
        bind.execute(sa.text("UPDATE jobs SET status = 'saved' WHERE status = 'shortlisted'"))
        for col in ("evaluation_dimensions", "evaluation_cache_key", "remote_eligibility"):
            if _has_column(bind, "jobs", col):
                op.drop_column("jobs", col)
