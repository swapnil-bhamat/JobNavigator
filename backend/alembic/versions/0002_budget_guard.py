"""Add AI budget guard ledger, reservations, and INR tracking on llm_call_log.

Revision ID: 0002_budget_guard
Revises: 0001_baseline
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_budget_guard"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, col: str) -> bool:
    return any(c["name"] == col for c in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)

    if not _has_table(bind, "llm_budget_ledger"):
        op.create_table(
            "llm_budget_ledger",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("month_key", sa.String(7), nullable=False, unique=True),
            sa.Column("budget_inr", sa.Float(), nullable=False, server_default="500.0"),
            sa.Column("reserved_inr", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("spent_inr", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("warned_80_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("tripped_100_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_llm_budget_ledger_month_key", "llm_budget_ledger", ["month_key"], unique=True)

    if not _has_table(bind, "llm_budget_reservations"):
        op.create_table(
            "llm_budget_reservations",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("ledger_id", uuid_type, sa.ForeignKey("llm_budget_ledger.id", ondelete="CASCADE"), nullable=False),
            sa.Column("account_id", uuid_type, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
            sa.Column("purpose", sa.String(), nullable=False, server_default=""),
            sa.Column("estimated_inr", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("actual_inr", sa.Float(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="reserved"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_llm_budget_reservations_ledger_id", "llm_budget_reservations", ["ledger_id"])
        op.create_index("ix_llm_budget_reservations_account_id", "llm_budget_reservations", ["account_id"])

    if _has_table(bind, "llm_call_log"):
        if not _has_column(bind, "llm_call_log", "cost_inr"):
            op.add_column("llm_call_log", sa.Column("cost_inr", sa.Float(), nullable=False, server_default="0.0"))
        if not _has_column(bind, "llm_call_log", "usd_to_inr_rate"):
            op.add_column("llm_call_log", sa.Column("usd_to_inr_rate", sa.Float(), nullable=True))
        if not _has_column(bind, "llm_call_log", "reservation_id"):
            op.add_column(
                "llm_call_log",
                sa.Column("reservation_id", uuid_type, sa.ForeignKey("llm_budget_reservations.id", ondelete="SET NULL"), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "llm_call_log"):
        for col in ("reservation_id", "usd_to_inr_rate", "cost_inr"):
            if _has_column(bind, "llm_call_log", col):
                op.drop_column("llm_call_log", col)
    if _has_table(bind, "llm_budget_reservations"):
        op.drop_table("llm_budget_reservations")
    if _has_table(bind, "llm_budget_ledger"):
        op.drop_table("llm_budget_ledger")
