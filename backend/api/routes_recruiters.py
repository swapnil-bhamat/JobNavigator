"""Compliant Recruiter Outreach Assistant endpoints (100% manual LinkedIn workflow)."""
import json
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.accounts.owned import get_persona
from backend.analyzer.llm_client import call_llm
from backend.analyzer.model_json import parse_model_json
from backend.config import GEMINI_MODEL_TRIAGE
from backend.models.db import Application, Job, Recruiter, get_db, utcnow

logger = logging.getLogger("jobnavigator.recruiters")
router = APIRouter(prefix="/recruiters", tags=["recruiters"])

VALID_PROVENANCE = {"manual_entry", "user_supplied_url", "public_company_page"}
VALID_OUTREACH_STATUSES = {
    "not_contacted",
    "connection_sent",
    "contacted",
    "responded",
    "follow_up_due",
    "closed",
}


def _to_dict(r: Recruiter) -> dict:
    return {
        "id": str(r.id),
        "job_id": str(r.job_id) if r.job_id else None,
        "application_id": str(r.application_id) if r.application_id else None,
        "name": r.name,
        "title": r.title,
        "company": r.company,
        "profile_url": r.profile_url,
        "email": r.email,
        "source_provenance": r.source_provenance,
        "outreach_status": r.outreach_status,
        "draft_connection_note": r.draft_connection_note,
        "draft_outreach_msg": r.draft_outreach_msg,
        "draft_followup_msg": r.draft_followup_msg,
        "notes": r.notes,
        "last_contacted_at": r.last_contacted_at.isoformat() if r.last_contacted_at else None,
        "follow_up_at": r.follow_up_at.isoformat() if r.follow_up_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("")
def list_recruiters(db: Session = Depends(get_db)):
    rows = db.query(Recruiter).order_by(Recruiter.updated_at.desc()).all()
    return [_to_dict(r) for r in rows]


@router.post("", status_code=201)
def create_recruiter(body: dict, db: Session = Depends(get_db)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Recruiter name is required")
    provenance = (body.get("source_provenance") or "manual_entry").strip()
    if provenance not in VALID_PROVENANCE:
        raise HTTPException(400, f"source_provenance must be one of {sorted(VALID_PROVENANCE)}")
    status = (body.get("outreach_status") or "not_contacted").strip()
    if status not in VALID_OUTREACH_STATUSES:
        raise HTTPException(400, f"outreach_status must be one of {sorted(VALID_OUTREACH_STATUSES)}")

    r = Recruiter(
        name=name,
        title=body.get("title"),
        company=body.get("company"),
        profile_url=body.get("profile_url"),
        email=body.get("email"),
        source_provenance=provenance,
        outreach_status=status,
        notes=body.get("notes"),
        job_id=body.get("job_id"),
        application_id=body.get("application_id"),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _to_dict(r)


@router.patch("/{recruiter_id}")
def update_recruiter(recruiter_id: str, body: dict, db: Session = Depends(get_db)):
    r = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if not r:
        raise HTTPException(404, "Recruiter not found")
    if "source_provenance" in body and body["source_provenance"] not in VALID_PROVENANCE:
        raise HTTPException(400, f"source_provenance must be one of {sorted(VALID_PROVENANCE)}")
    if "outreach_status" in body and body["outreach_status"] not in VALID_OUTREACH_STATUSES:
        raise HTTPException(400, f"outreach_status must be one of {sorted(VALID_OUTREACH_STATUSES)}")

    for field in (
        "name", "title", "company", "profile_url", "email",
        "source_provenance", "outreach_status", "draft_connection_note",
        "draft_outreach_msg", "draft_followup_msg", "notes",
    ):
        if field in body:
            setattr(r, field, body[field])
    if "follow_up_at" in body:
        val = body["follow_up_at"]
        r.follow_up_at = date.fromisoformat(val) if val else None
    if "last_contacted_at" in body:
        val = body["last_contacted_at"]
        r.last_contacted_at = datetime.fromisoformat(val) if val else None
    r.updated_at = utcnow()
    db.commit()
    db.refresh(r)
    return _to_dict(r)


@router.delete("/{recruiter_id}")
def delete_recruiter(recruiter_id: str, db: Session = Depends(get_db)):
    r = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if not r:
        raise HTTPException(404, "Recruiter not found")
    db.delete(r)
    db.commit()
    return {"deleted": True}


@router.post("/{recruiter_id}/draft-outreach")
async def draft_outreach_messages(
    recruiter_id: str,
    connection_max_chars: int = 300,
    outreach_max_chars: int = 1200,
    followup_max_chars: int = 800,
    db: Session = Depends(get_db),
):
    """Generate personalized outreach drafts for manual copy-and-send (no LinkedIn automation)."""
    r = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if not r:
        raise HTTPException(404, "Recruiter not found")

    persona = get_persona(db)
    job = db.query(Job).filter(Job.id == r.job_id).first() if r.job_id else None

    system = (
        "You draft concise, respectful, factual recruiter outreach messages for a Staff/Principal/Architect "
        "engineer. Never invent experience or metrics. Return ONLY valid JSON."
    )
    prompt = f"""Recruiter: {r.name} ({r.title or 'Recruiter'}) at {r.company or (job.company if job else '')}
Target Role: {job.title if job else 'Senior/Staff Engineering'}
Candidate Profile: {json.dumps({'preferences': persona.preferences, 'summary': (persona.resume_content or {}).get('summary')})}

Length limits (strict):
- connection_note: <= {connection_max_chars} characters
- outreach_msg: <= {outreach_max_chars} characters
- followup_msg: <= {followup_max_chars} characters

Return JSON:
{{
  "connection_note": "...",
  "outreach_msg": "...",
  "followup_msg": "..."
}}"""

    resp = await call_llm(prompt, system, max_tokens=900, provider="gemini", model=GEMINI_MODEL_TRIAGE)
    parsed = parse_model_json(resp.get("text", "")) or {}

    conn = str(parsed.get("connection_note") or "")[:connection_max_chars]
    outreach = str(parsed.get("outreach_msg") or "")[:outreach_max_chars]
    followup = str(parsed.get("followup_msg") or "")[:followup_max_chars]

    r.draft_connection_note = conn
    r.draft_outreach_msg = outreach
    r.draft_followup_msg = followup
    r.updated_at = utcnow()
    db.commit()
    db.refresh(r)
    return _to_dict(r)
