"""Milestone 4 tests: 8-stage application pipeline, duplicate prevention, interview prep, Telegram alerts."""
import json
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.models.db import Application, Base, Interview, Job, Persona, SessionLocal, engine
from backend.analyzer.interview_prep import generate_interview_prep


def test_application_pipeline_stages_and_duplicate_guard():
    from backend.main import app
    client = TestClient(app)

    uid = uuid.uuid4().hex[:8]
    payload = {
        "company": f"PipelineCo-{uid}",
        "title": "Staff Software Architect",
        "url": f"https://example.com/jobs/{uid}",
    }

    r1 = client.post("/api/applications", json=payload)
    assert r1.status_code in (200, 201)
    app_id = r1.json()["id"]

    # Duplicate application for the same job must return 409
    r_dup = client.post("/api/applications", json=payload)
    assert r_dup.status_code == 409

    # Transition through new pipeline stages
    for stage in (
        "recruiter_contacted",
        "recruiter_response",
        "interview_scheduled",
        "interview_completed",
        "offer_received",
    ):
        r_upd = client.patch(f"/api/applications/{app_id}", json={"status": stage})
        assert r_upd.status_code == 200
        assert r_upd.json()["status"] == stage

    # Add interview and save feedback_notes
    r_iv = client.post(
        f"/api/applications/{app_id}/interviews",
        json={"what": "System Design Round", "where_text": "Google Meet"},
    )
    assert r_iv.status_code == 201
    iv_id = r_iv.json()["id"]

    r_iv_patch = client.patch(
        f"/api/applications/interviews/{iv_id}",
        json={"feedback_notes": "Discussed 3D configurator scaling and edge caching."},
    )
    assert r_iv_patch.status_code == 200
    assert "3D configurator" in r_iv_patch.json()["feedback_notes"]

    # Cleanup
    client.delete(f"/api/applications/{app_id}")


@pytest.mark.asyncio
async def test_generate_interview_prep_saves_plan(monkeypatch):
    from backend.accounts.context import unscoped
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    async def _fake_call_llm(prompt, system, max_tokens=1800, provider=None, model=None, **kw):
        return {
            "text": json.dumps({
                "company_overview": "3D commerce platform",
                "role_specific_questions": ["How do you optimize Three.js draw calls?"],
                "technical_topics": ["WebGL instancing", "Frontend architecture"],
                "system_design_questions": ["Design a multi-tenant 3D configurator pipeline"],
                "leadership_behavioral_questions": ["Describe driving an architecture migration"],
                "experience_based_questions": ["Walk through your 3D configurator architecture"],
                "preparation_plan": ["Review WebGL instancing", "Practice system design"],
            }),
            "provider": "gemini",
            "model": "gemini-4-hydrogen",
        }

    monkeypatch.setattr("backend.analyzer.interview_prep.call_llm", _fake_call_llm)

    with unscoped():
        job = Job(
            external_id=f"m4-ext-{uuid.uuid4().hex[:8]}",
            company="3D Commerce",
            title="Staff Frontend Architect",
            url="https://example.com/3d-arch",
            description="Three.js, Canvas, Platform Architecture",
            status="applied",
        )
        db.add(job)
        db.flush()
        app_row = Application(job_id=job.id, status="interview_scheduled")
        db.add(app_row)
        db.flush()
        iv = Interview(application_id=app_row.id, what="Architecture Round")
        db.add(iv)
        db.commit()
        db.refresh(iv)

        persona = db.query(Persona).first()
        plan = await generate_interview_prep(db, app_row, job, persona, interview_id=str(iv.id))
        assert plan["company_overview"] == "3D commerce platform"
        db.refresh(iv)
        assert iv.prep_plan["technical_topics"] == ["WebGL instancing", "Frontend architecture"]

        db.delete(app_row)
        db.delete(job)
        db.commit()
    db.close()


@pytest.mark.asyncio
async def test_milestone4_telegram_helpers(monkeypatch):
    from backend.notifier import telegram

    sent = []

    async def _fake_send(chat_id, text, **kw):
        sent.append((chat_id, text))
        return True

    monkeypatch.setattr(telegram, "resolve_target", lambda: ("12345", None))
    monkeypatch.setattr(telegram, "_is_enabled", lambda: True)
    monkeypatch.setattr(telegram, "_send_message", _fake_send)

    assert await telegram.send_document_ready_alert("Resume", "Staff Engineer", "Acme") is True
    assert await telegram.send_followup_reminder("Acme", "Staff Engineer", "2026-12-01") is True
    assert await telegram.send_interview_reminder("Acme", "Staff Engineer", "System Design", "2026-12-02 15:00") is True
    assert await telegram.send_status_change_alert("Acme", "Staff Engineer", "applied", "interview_scheduled") is True
    assert len(sent) == 4
