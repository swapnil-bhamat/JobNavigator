"""Milestone 1 tests: Budget Guard, Career Profile seed, Gmail gate, OpenAI verify."""
import threading
import pytest
from fastapi.testclient import TestClient

from backend.models.db import Base, LlmBudgetLedger, Persona, SessionLocal, engine
from backend.analyzer.budget_guard import (
    BudgetExceededError,
    get_budget_status,
    reconcile_budget,
    reserve_budget,
)
from backend.seed import seed_persona


@pytest.fixture
def m1_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(LlmBudgetLedger).delete()
        db.commit()
        yield db
    finally:
        db.close()


def test_budget_guard_reserve_reconcile_and_thresholds(m1_db):
    # Reserve within ₹100 budget
    r1 = reserve_budget(m1_db, purpose="triage", estimated_inr=60.0, budget_inr=100.0, month_key="2026-11")
    status = get_budget_status(m1_db, month_key="2026-11")
    assert status["reserved_inr"] == 60.0
    assert status["spent_inr"] == 0.0

    # Concurrent / second reservation exceeding cap must fail
    with pytest.raises(BudgetExceededError):
        reserve_budget(m1_db, purpose="full", estimated_inr=50.0, budget_inr=100.0, month_key="2026-11")

    # Reconcile actual spend to ₹85 (>= 80% triggers 80% alert once)
    rec = reconcile_budget(m1_db, r1.id, actual_usd=1.0, usd_to_inr_rate=85.0)
    assert rec["actual_inr"] == 85.0
    assert rec["trigger_80_alert"] is True
    assert rec["tripped_100"] is False

    # Second reservation of ₹15 succeeds and reconciles as billable failure -> trips 100%
    r2 = reserve_budget(m1_db, purpose="retry", estimated_inr=15.0, budget_inr=100.0, month_key="2026-11")
    rec2 = reconcile_budget(m1_db, r2.id, actual_usd=0.0, failed_billable=True)
    assert rec2["actual_inr"] > 0.0
    assert rec2["trigger_80_alert"] is False  # only alerts once

    # Force spend to >= 100 and verify circuit breaker
    r3 = reserve_budget(m1_db, purpose="final", estimated_inr=11.0, budget_inr=100.0, month_key="2026-11")
    rec3 = reconcile_budget(m1_db, r3.id, actual_usd=0.2, usd_to_inr_rate=85.0)
    assert rec3["tripped_100"] is True

    with pytest.raises(BudgetExceededError):
        reserve_budget(m1_db, purpose="after_trip", estimated_inr=1.0, budget_inr=100.0, month_key="2026-11")


def test_budget_guard_concurrency_safety(m1_db):
    results = {"ok": 0, "blocked": 0}
    lock = threading.Lock()

    def _worker():
        db = SessionLocal()
        try:
            reserve_budget(db, purpose="parallel", estimated_inr=40.0, budget_inr=100.0, month_key="2026-12")
            with lock:
                results["ok"] += 1
        except BudgetExceededError:
            with lock:
                results["blocked"] += 1
        finally:
            db.close()

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Only 2 * 40 = 80 fits in 100; 3 must be blocked
    assert results["ok"] == 2
    assert results["blocked"] == 3


def test_seed_persona_career_profile(m1_db):
    from backend.accounts.context import unscoped
    with unscoped():
        m1_db.query(Persona).delete()
        m1_db.commit()
        seed_persona(m1_db)
        p = m1_db.query(Persona).first()
        assert p is not None
        assert p.contact["city"] == "Nagpur"
        assert p.work_auth["remote_only"] is True
        assert p.work_auth["notice_period_days"] == 60
        assert p.compensation["current_lpa"] == 33
        assert p.compensation["target_min_lpa"] == 50
        assert "Three.js" in p.resume_content["skills"]["Core"]

        # Re-running seed_persona must not overwrite user edits
        p.compensation = {"currency": "INR", "current_lpa": 35}
        m1_db.commit()
        seed_persona(m1_db)
        m1_db.refresh(p)
        assert p.compensation["current_lpa"] == 35


def test_gmail_disabled_and_gemini_verify_without_key(monkeypatch):
    monkeypatch.setattr("backend.config.GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from backend.main import app
    client = TestClient(app)

    r_gmail = client.get("/api/gmail/connect")
    assert r_gmail.status_code == 403

    r_budget = client.get("/api/llm/budget")
    assert r_budget.status_code == 200
    assert "budget_inr" in r_budget.json()

    r_gemini = client.get("/api/llm/verify-gemini")
    assert r_gemini.status_code == 200
    assert r_gemini.json()["configured"] is False
