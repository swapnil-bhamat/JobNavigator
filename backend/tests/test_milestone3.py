"""Milestone 3 tests: hash-locked document approval, edit invalidation, final export gate, anti-fabrication."""
import json
import uuid
import pytest
from fastapi.testclient import TestClient

from backend.models.db import Base, CoverLetter, Job, Persona, Resume, SessionLocal, engine
from backend.analyzer.document_guard import (
    collect_verified_skills,
    strip_unverified_skills,
    tailor_resume_guarded,
)


def test_anti_fabrication_strips_unverified_skills():
    persona = Persona(resume_content={"skills": {"Core": ["Three.js", "Canvas", "JavaScript ecosystem"]}})
    base_json = {"skills": {"Backend": ["Node.js"]}}
    verified = collect_verified_skills(persona, base_json)

    tailored = {
        "skills": {
            "Core": ["Three.js", "Rust", "Kubernetes"],
            "Backend": ["Node.js", "Go"],
        }
    }
    cleaned, stripped = strip_unverified_skills(tailored, verified)
    assert cleaned["skills"]["Core"] == ["Three.js"]
    assert cleaned["skills"]["Backend"] == ["Node.js"]
    assert set(stripped) == {"Rust", "Kubernetes", "Go"}


def test_resume_hash_locked_approval_and_final_export_gate():
    from backend.main import app
    from backend.accounts.context import unscoped

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    with unscoped():
        r = Resume(
            name="M3 Test Resume",
            is_base=False,
            json_data={"header": {"name": "Swapnil"}, "skills": {"Core": ["Three.js"]}},
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        rid = str(r.id)
    db.close()

    client = TestClient(app)

    # Unapproved resume cannot be final-exported
    resp_unapproved = client.get(f"/api/resumes/{rid}/docx?final=true")
    assert resp_unapproved.status_code == 409

    # Approve locks content_hash
    resp_approve = client.post(f"/api/resumes/{rid}/approve")
    assert resp_approve.status_code == 200
    body = resp_approve.json()
    assert body["approval_status"] == "approved"
    assert body["approved_content_hash"] == body["content_hash"]

    # Final export now succeeds
    resp_final = client.get(f"/api/resumes/{rid}/docx?final=true")
    assert resp_final.status_code == 200

    # Any subsequent edit invalidates approval
    resp_patch = client.patch(
        f"/api/resumes/{rid}",
        json={"json_data": {"header": {"name": "Swapnil"}, "skills": {"Core": ["Three.js", "Canvas"]}}},
    )
    assert resp_patch.status_code == 200
    patched = resp_patch.json()
    assert patched["approval_status"] == "draft"
    assert patched["approved_content_hash"] is None

    # Final export is blocked again until re-approved
    resp_after_edit = client.get(f"/api/resumes/{rid}/docx?final=true")
    assert resp_after_edit.status_code == 409

    # Cleanup
    client.delete(f"/api/resumes/{rid}")


def test_cover_letter_approval_and_rejection():
    from backend.main import app
    from backend.accounts.context import unscoped

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    with unscoped():
        cl = CoverLetter(
            name="M3 Cover Letter",
            json_data={"greeting": "Dear Hiring Team,", "body_paragraphs": ["Paragraph 1"]},
        )
        db.add(cl)
        db.commit()
        db.refresh(cl)
        cid = str(cl.id)
    db.close()

    client = TestClient(app)

    assert client.post(f"/api/cover-letters/{cid}/approve").json()["approval_status"] == "approved"
    patched = client.patch(
        f"/api/cover-letters/{cid}",
        json={"json_data": {"greeting": "Hi Team,", "body_paragraphs": ["Edited"]}},
    ).json()
    assert patched["approval_status"] == "draft"
    assert patched["approved_content_hash"] is None

    rejected = client.post(f"/api/cover-letters/{cid}/reject").json()
    assert rejected["approval_status"] == "rejected"

    client.delete(f"/api/cover-letters/{cid}")


@pytest.mark.asyncio
async def test_tailor_resume_guarded_sets_awaiting_approval_and_provenance(monkeypatch):
    from backend.accounts.context import unscoped

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    async def _fake_call_llm(prompt, system, max_tokens=2000, provider=None, model=None, **kw):
        return {
            "text": json.dumps({
                "tailored_json": {
                    "header": {"name": "Swapnil"},
                    "skills": {"Core": ["Three.js", "InventedSkill"]},
                },
                "changes_explanation": ["Prioritized Three.js experience"],
                "missing_qualifications": ["Rust"],
                "source_facts": [{"claim": "Three.js architecture", "source": "persona.resume_content.skills"}],
            }),
            "provider": "gemini",
            "model": "gemini-4-hydrogen",
        }

    monkeypatch.setattr("backend.analyzer.document_guard.call_llm", _fake_call_llm)

    with unscoped():
        persona = Persona(resume_content={"skills": {"Core": ["Three.js"]}})
        base = Resume(
            name="Base Arch",
            is_base=True,
            json_data={"header": {"name": "Swapnil"}, "skills": {"Core": ["Three.js"]}},
        )
        job = Job(
            external_id=f"m3-ext-{uuid.uuid4().hex[:8]}",
            content_hash=f"m3-hash-{uuid.uuid4().hex[:8]}",
            company="3D Labs",
            title="Staff 3D Architect",
            url="https://example.com/3d",
            description="Need Three.js and Rust",
            status="shortlisted",
        )
        db.add_all([base, job])
        db.commit()
        db.refresh(base)
        db.refresh(job)

        tailored = await tailor_resume_guarded(db, base, job, persona)
        assert tailored.approval_status == "awaiting_approval"
        assert tailored.approved_content_hash is None
        assert tailored.json_data["skills"]["Core"] == ["Three.js"]
        assert any("InventedSkill" in c for c in tailored.changes_explanation)
        assert tailored.missing_qualifications == ["Rust"]
        assert len(tailored.source_facts) == 1

        db.delete(tailored)
        db.delete(job)
        db.delete(base)
        db.commit()
    db.close()
