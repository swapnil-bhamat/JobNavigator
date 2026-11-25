"""Baseline schema matching existing JobNavigator models.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-25
"""
from alembic import op
from sqlalchemy import text
from backend.models.db import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(text("CREATE SEQUENCE IF NOT EXISTS jobs_short_id_seq"))
    # Create baseline tables idempotently (safe on both fresh DBs and existing installs)
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass
