"""Concurrency-safe monthly AI budget guard in INR.

Enforces:
- Pre-call budget check against `AI_MONTHLY_BUDGET_INR` (default 500 INR).
- Atomic spending reservation (`SELECT ... FOR UPDATE` on Postgres, lock on SQLite)
  using worst-case estimated token cost * `AI_BUDGET_SAFETY_MARGIN`.
- Post-call reconciliation releasing the reservation and recording actual INR spend
  (including retries and potentially billable failed calls).
- 80% warning notification (once per month) and 100% hard circuit breaker.
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from backend.config import (
    AI_BUDGET_SAFETY_MARGIN,
    AI_MONTHLY_BUDGET_INR,
    USD_TO_INR_RATE,
)
from backend.analyzer.llm_cost import FREE_PROVIDERS, calc_cost, get_pricing
from backend.models.db import LlmBudgetLedger, LlmBudgetReservation, SessionLocal

logger = logging.getLogger("jobnavigator.budget_guard")

# In-process lock for SQLite unit tests (Postgres uses row-level FOR UPDATE).
_SQLITE_LOCK = threading.Lock()

# Conservative fallback pricing (USD per 1M tokens) when a custom/new model is
# not yet in the static pricing table, so budget reservations still protect spend.
_FALLBACK_PER_MTOK = {"input_per_mtok": 5.0, "output_per_mtok": 20.0}


class BudgetExceededError(RuntimeError):
    """Raised when the monthly INR AI budget would be exceeded."""


def current_month_key(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m")


def usd_to_inr(usd: float, rate: Optional[float] = None) -> float:
    r = rate if rate is not None else USD_TO_INR_RATE
    return max(0.0, float(usd) * float(r))


def estimate_call_cost_inr(
    provider: str,
    model: str,
    prompt: str,
    system: str,
    max_tokens: int,
    safety_margin: Optional[float] = None,
    usd_to_inr_rate: Optional[float] = None,
) -> float:
    """Estimate worst-case INR cost for a single call attempt with safety margin."""
    if provider in FREE_PROVIDERS:
        return 0.0
    # ~4 chars per token conservative upper bound (+ system + prompt)
    est_input_tokens = max(1, (len(prompt or "") + len(system or "")) // 3 + 64)
    est_output_tokens = max(1, int(max_tokens or 1200))

    pricing = get_pricing(provider, model)
    if pricing:
        est_usd = calc_cost(
            provider,
            model,
            input_tokens=est_input_tokens,
            output_tokens=est_output_tokens,
        )
    else:
        est_usd = (
            est_input_tokens * _FALLBACK_PER_MTOK["input_per_mtok"] / 1_000_000
            + est_output_tokens * _FALLBACK_PER_MTOK["output_per_mtok"] / 1_000_000
        )

    margin = safety_margin if safety_margin is not None else AI_BUDGET_SAFETY_MARGIN
    return usd_to_inr(est_usd, usd_to_inr_rate) * float(margin)


def _get_or_create_ledger(db, month_key: str, budget_inr: float, for_update: bool = False) -> LlmBudgetLedger:
    q = db.query(LlmBudgetLedger).filter(LlmBudgetLedger.month_key == month_key)
    if for_update and db.bind and db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    ledger = q.first()
    if ledger is None:
        ledger = LlmBudgetLedger(
            month_key=month_key,
            budget_inr=float(budget_inr),
            reserved_inr=0.0,
            spent_inr=0.0,
        )
        db.add(ledger)
        db.flush()
    return ledger


def reserve_budget(
    db,
    *,
    purpose: str,
    estimated_inr: float,
    account_id=None,
    budget_inr: Optional[float] = None,
    month_key: Optional[str] = None,
) -> LlmBudgetReservation:
    """Atomically reserve estimated INR budget before calling a paid LLM API."""
    cap = float(budget_inr if budget_inr is not None else AI_MONTHLY_BUDGET_INR)
    mkey = month_key or current_month_key()
    est = max(0.0, float(estimated_inr))

    with _SQLITE_LOCK:
        ledger = _get_or_create_ledger(db, mkey, cap, for_update=True)
        # Keep cap synced with current config if increased/decreased
        ledger.budget_inr = cap
        projected = ledger.spent_inr + ledger.reserved_inr + est

        if ledger.tripped_100_at is not None or (cap > 0 and projected > cap) or cap <= 0:
            if ledger.tripped_100_at is None and (ledger.spent_inr + ledger.reserved_inr) >= cap:
                ledger.tripped_100_at = datetime.now(timezone.utc)
                db.commit()
            raise BudgetExceededError(
                f"Monthly AI budget limit reached (spent=₹{ledger.spent_inr:.2f}, "
                f"reserved=₹{ledger.reserved_inr:.2f}, requested=₹{est:.2f}, cap=₹{cap:.2f})"
            )

        ledger.reserved_inr = float(ledger.reserved_inr) + est
        reservation = LlmBudgetReservation(
            ledger_id=ledger.id,
            account_id=account_id,
            purpose=purpose or "",
            estimated_inr=est,
            status="reserved",
        )
        db.add(reservation)
        db.commit()
        db.refresh(reservation)
        return reservation


def reconcile_budget(
    db,
    reservation_id,
    *,
    actual_usd: float = 0.0,
    failed_billable: bool = False,
    usd_to_inr_rate: Optional[float] = None,
) -> dict:
    """Release reservation and record actual INR spend (including billable failures).

    Returns {"actual_inr": float, "trigger_80_alert": bool, "tripped_100": bool, "pct_used": float}.
    """
    with _SQLITE_LOCK:
        res = db.query(LlmBudgetReservation).filter(LlmBudgetReservation.id == reservation_id).first()
        if res is None or res.status != "reserved":
            return {"actual_inr": 0.0, "trigger_80_alert": False, "tripped_100": False, "pct_used": 0.0}

        q = db.query(LlmBudgetLedger).filter(LlmBudgetLedger.id == res.ledger_id)
        if db.bind and db.bind.dialect.name == "postgresql":
            q = q.with_for_update()
        ledger = q.first()

        actual_inr = usd_to_inr(actual_usd, usd_to_inr_rate)
        if failed_billable and actual_inr == 0.0:
            # Conservative charge for failed/timed-out requests that may have consumed input tokens
            actual_inr = min(res.estimated_inr, res.estimated_inr * 0.25)

        if ledger is not None:
            ledger.reserved_inr = max(0.0, float(ledger.reserved_inr) - float(res.estimated_inr))
            ledger.spent_inr = float(ledger.spent_inr) + actual_inr

        res.actual_inr = actual_inr
        res.status = "settled" if (actual_inr > 0 or not failed_billable) else "released"
        res.settled_at = datetime.now(timezone.utc)

        trigger_80_alert = False
        tripped_100 = False
        pct_used = 0.0
        if ledger is not None and ledger.budget_inr > 0:
            pct_used = (ledger.spent_inr / ledger.budget_inr) * 100.0
            if pct_used >= 80.0 and ledger.warned_80_at is None:
                ledger.warned_80_at = datetime.now(timezone.utc)
                trigger_80_alert = True
            if ledger.spent_inr >= ledger.budget_inr and ledger.tripped_100_at is None:
                ledger.tripped_100_at = datetime.now(timezone.utc)
                tripped_100 = True

        db.commit()
        return {
            "actual_inr": actual_inr,
            "trigger_80_alert": trigger_80_alert,
            "tripped_100": tripped_100,
            "pct_used": round(pct_used, 2),
        }


def get_budget_status(db, month_key: Optional[str] = None) -> dict:
    mkey = month_key or current_month_key()
    cap = float(AI_MONTHLY_BUDGET_INR)
    ledger = db.query(LlmBudgetLedger).filter(LlmBudgetLedger.month_key == mkey).first()
    spent = float(ledger.spent_inr) if ledger else 0.0
    reserved = float(ledger.reserved_inr) if ledger else 0.0
    effective_cap = float(ledger.budget_inr) if ledger else cap
    remaining = max(0.0, effective_cap - spent - reserved)
    pct_used = (spent / effective_cap * 100.0) if effective_cap > 0 else 100.0
    return {
        "month_key": mkey,
        "budget_inr": round(effective_cap, 2),
        "spent_inr": round(spent, 2),
        "reserved_inr": round(reserved, 2),
        "remaining_inr": round(remaining, 2),
        "pct_used": round(pct_used, 2),
        "warned_80": bool(ledger and ledger.warned_80_at),
        "tripped_100": bool(ledger and ledger.tripped_100_at) or (effective_cap > 0 and spent >= effective_cap),
    }


async def notify_budget_warning_if_needed(reconcile_result: dict, status_snapshot: Optional[dict] = None) -> None:
    """Send Telegram alert at 80% threshold (best-effort, non-fatal)."""
    if not reconcile_result.get("trigger_80_alert"):
        return
    try:
        from backend.notifier.telegram import send_system_alert
        pct = reconcile_result.get("pct_used", 80.0)
        msg = (
            f"<b>AI Budget Warning</b>\n"
            f"Monthly OpenAI/LLM spend has reached <b>{pct:.1f}%</b> of your "
            f"₹{AI_MONTHLY_BUDGET_INR:.0f} budget."
        )
        await send_system_alert(msg)
    except Exception as e:
        logger.warning("Failed to send 80%% budget Telegram alert: %s", e)


def run_notify_async(reconcile_result: dict) -> None:
    if not reconcile_result.get("trigger_80_alert"):
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(notify_budget_warning_if_needed(reconcile_result))
    except RuntimeError:
        pass
