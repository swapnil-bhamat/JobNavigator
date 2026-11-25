"""Milestone 5 tests: compliant manual Recruiter CRM, provenance, configurable char limits."""
import json
import pytest
from fastapi.testclient import TestClient


def test_recruiter_crud_and_provenance(monkeypatch):
    from backend.main import app

    async def _fake_call_llm(prompt, system, max_tokens=900, provider=None, model=None, **kw):
        return {
            "text": json.dumps({
                "connection_note": "Hi Jane, I have 12+ yrs in frontend architecture & Three.js and noticed the Staff role at 3DCo." + "x" * 300,
                "outreach_msg": "Hello Jane, reaching out regarding the Staff Frontend Architect role at 3DCo.",
                "followup_msg": "Hi Jane, following up on my note regarding the Staff Frontend Architect role.",
            }),
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
        }

    monkeypatch.setattr("backend.api.routes_recruiters.call_llm", _fake_call_llm)
    client = TestClient(app)

    r_create = client.post(
        "/api/recruiters",
        json={
            "name": "Jane Recruiter",
            "title": "Technical Recruiter",
            "company": "3DCo",
            "profile_url": "https://www.linkedin.com/in/jane-recruiter-example",
            "source_provenance": "user_supplied_url",
        },
    )
    assert r_create.status_code == 201
    rid = r_create.json()["id"]
    assert r_create.json()["source_provenance"] == "user_supplied_url"
    assert r_create.json()["outreach_status"] == "not_contacted"

    # Draft outreach with custom configurable char limit (e.g. 200)
    r_draft = client.post(f"/api/recruiters/{rid}/draft-outreach?connection_max_chars=200")
    assert r_draft.status_code == 200
    assert len(r_draft.json()["draft_connection_note"]) <= 200
    assert "3DCo" in r_draft.json()["draft_outreach_msg"]

    # Manual outreach status update
    r_patch = client.patch(f"/api/recruiters/{rid}", json={"outreach_status": "connection_sent"})
    assert r_patch.status_code == 200
    assert r_patch.json()["outreach_status"] == "connection_sent"

    client.delete(f"/api/recruiters/{rid}")
