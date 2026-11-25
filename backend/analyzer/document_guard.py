"""Hash-locked document approval and anti-fabrication guardrails."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from backend.analyzer.llm_client import call_llm
from backend.analyzer.model_json import parse_model_json
from backend.analyzer.prompt_fence import fence
from backend.config import GEMINI_MODEL_FULL, GEMINI_MODEL_TRIAGE
from backend.models.db import Job, Persona, Resume

logger = logging.getLogger("jobnavigator.document_guard")


def compute_content_hash(json_data: Optional[dict]) -> str:
    canonical = json.dumps(json_data or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sync_content_hash_and_invalidate_if_edited(doc) -> bool:
    """Recompute content_hash; if document was approved and content changed, invalidate approval.

    Returns True if an existing approval was invalidated.
    """
    new_hash = compute_content_hash(doc.json_data)
    doc.content_hash = new_hash
    if doc.approval_status == "approved" and doc.approved_content_hash != new_hash:
        doc.approval_status = "draft"
        doc.approved_content_hash = None
        doc.approved_at = None
        return True
    return False


def approve_document(doc) -> str:
    """Lock approval to the exact current content_hash."""
    h = compute_content_hash(doc.json_data)
    doc.content_hash = h
    doc.approved_content_hash = h
    doc.approval_status = "approved"
    doc.approved_at = datetime.now(timezone.utc)
    return h


def reject_document(doc) -> None:
    doc.content_hash = compute_content_hash(doc.json_data)
    doc.approval_status = "rejected"
    doc.approved_content_hash = None
    doc.approved_at = None


def require_approved_for_final_export(doc) -> None:
    """Enforce that final exports use an explicitly approved, unedited version."""
    current_hash = compute_content_hash(doc.json_data)
    if doc.approval_status != "approved" or not doc.approved_content_hash:
        raise HTTPException(
            status_code=409,
            detail="Document must be explicitly approved before final export.",
        )
    if doc.approved_content_hash != current_hash:
        doc.approval_status = "draft"
        doc.approved_content_hash = None
        doc.approved_at = None
        raise HTTPException(
            status_code=409,
            detail="Document was edited after approval; re-approval is required before final export.",
        )


def collect_verified_skills(persona: Optional[Persona], base_json: Optional[dict]) -> set[str]:
    """Build normalized lowercase set of verified skills from Persona and base Resume."""
    verified: set[str] = set()

    def _add_from(skills_obj):
        if isinstance(skills_obj, dict):
            for items in skills_obj.values():
                if isinstance(items, list):
                    for item in items:
                        s = str(item).strip()
                        if s:
                            verified.add(s.lower())
        elif isinstance(skills_obj, list):
            for item in skills_obj:
                s = str(item).strip()
                if s:
                    verified.add(s.lower())

    if persona and isinstance(persona.resume_content, dict):
        _add_from(persona.resume_content.get("skills"))
    if isinstance(base_json, dict):
        _add_from(base_json.get("skills"))
    return verified


def strip_unverified_skills(tailored_json: dict, verified_skills: set[str]) -> tuple[dict, list[str]]:
    """Remove any skill keyword not present in the verified profile/base resume."""
    if not verified_skills:
        return tailored_json, []

    cleaned = dict(tailored_json)
    stripped: list[str] = []
    skills = cleaned.get("skills")

    if isinstance(skills, dict):
        new_skills = {}
        for cat, items in skills.items():
            if isinstance(items, list):
                kept = []
                for item in items:
                    s = str(item).strip()
                    if s.lower() in verified_skills:
                        kept.append(s)
                    else:
                        stripped.append(s)
                if kept:
                    new_skills[cat] = kept
            else:
                new_skills[cat] = items
        cleaned["skills"] = new_skills
    elif isinstance(skills, list):
        kept = []
        for item in skills:
            s = str(item).strip()
            if s.lower() in verified_skills:
                kept.append(s)
            else:
                stripped.append(s)
        cleaned["skills"] = kept

    return cleaned, stripped


async def tailor_resume_guarded(
    db,
    base_resume: Resume,
    job: Job,
    persona: Optional[Persona],
    *,
    model: Optional[str] = None,
) -> Resume:
    """Tailor a resume using Gemini with anti-fabrication guardrails and awaiting_approval state."""
    use_model = (model or GEMINI_MODEL_FULL or GEMINI_MODEL_TRIAGE or "").strip()
    base_json = base_resume.json_data or {}
    verified_skills = collect_verified_skills(persona, base_json)

    fenced_jd = fence(
        f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n\n{job.description or ''}",
        label="JOB POSTING",
    )
    verified_source = json.dumps(
        {
            "base_resume": base_json,
            "persona_resume_content": (persona.resume_content if persona else {}) or {},
        },
        indent=2,
    )

    system = (
        "You are a factual resume editor. Reorganize and emphasize ONLY verified experience, projects, "
        "skills, and achievements from the candidate's verified profile. NEVER invent accomplishments, "
        "metrics, employers, or skills not present in the verified source. Return ONLY valid JSON."
    )
    prompt = f"""Verified Candidate Source:
{verified_source}

{fenced_jd}

Return JSON with this exact schema:
{{
  "tailored_json": {{ ...same structure as base_resume... }},
  "changes_explanation": ["Brief explanation of each section/bullet emphasis change"],
  "missing_qualifications": ["JD requirements the candidate does not currently have"],
  "source_facts": [
    {{"claim": "Tailored bullet or summary statement", "source": "verified base_resume/persona section"}}
  ]
}}"""

    resp = await call_llm(prompt, system, max_tokens=2000, provider="gemini", model=use_model)
    parsed = parse_model_json(resp.get("text", "")) or {}
    raw_tailored = parsed.get("tailored_json")
    if not isinstance(raw_tailored, dict):
        raw_tailored = dict(base_json)

    cleaned_json, stripped_skills = strip_unverified_skills(raw_tailored, verified_skills)
    changes = list(parsed.get("changes_explanation") or [])
    if stripped_skills:
        changes.append(f"Anti-fabrication guard removed unverified skills: {', '.join(stripped_skills)}")

    tailored = Resume(
        account_id=base_resume.account_id,
        name=f"{base_resume.name} — {job.company or 'Tailored'}",
        is_base=False,
        parent_id=base_resume.id,
        job_id=job.id,
        template=base_resume.template,
        page_format=base_resume.page_format,
        json_data=cleaned_json,
        content_hash=compute_content_hash(cleaned_json),
        approval_status="awaiting_approval",
        approved_content_hash=None,
        approved_at=None,
        changes_explanation=changes,
        missing_qualifications=list(parsed.get("missing_qualifications") or []),
        source_facts=list(parsed.get("source_facts") or []),
    )
    db.add(tailored)
    db.commit()
    db.refresh(tailored)
    return tailored
