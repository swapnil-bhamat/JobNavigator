"""AI Interview Preparation generator using Gemini."""
import json
import logging
from typing import Optional

from backend.analyzer.llm_client import call_llm
from backend.analyzer.model_json import parse_model_json
from backend.analyzer.prompt_fence import fence
from backend.config import GEMINI_MODEL_FULL, GEMINI_MODEL_TRIAGE
from backend.models.db import Application, Interview, Job, Persona

logger = logging.getLogger("jobnavigator.interview_prep")


async def generate_interview_prep(
    db,
    app: Application,
    job: Optional[Job],
    persona: Optional[Persona],
    *,
    interview_id: Optional[str] = None,
    model: Optional[str] = None,
) -> dict:
    """Generate structured interview prep grounded in verified candidate experience."""
    use_model = (model or GEMINI_MODEL_FULL or GEMINI_MODEL_TRIAGE or "").strip()

    fenced_jd = fence(
        f"Title: {job.title if job else ''}\n"
        f"Company: {job.company if job else ''}\n"
        f"Location: {job.location if job else ''}\n\n"
        f"{(job.description or '') if job else ''}",
        label="JOB POSTING",
    )
    candidate_json = json.dumps(
        {
            "preferences": (persona.preferences if persona else {}) or {},
            "resume_content": (persona.resume_content if persona else {}) or {},
        },
        indent=2,
    )

    system = (
        "You are a Staff/Principal engineering interview coach. Create a tailored, practical "
        "interview preparation plan grounded ONLY in the candidate's verified background. "
        "Return ONLY valid JSON."
    )
    prompt = f"""Candidate Verified Profile:
{candidate_json}

{fenced_jd}

Return JSON with this exact schema:
{{
  "company_overview": "Concise overview of company product/domain",
  "role_specific_questions": ["Question 1", "Question 2"],
  "technical_topics": ["Topic 1", "Topic 2"],
  "system_design_questions": ["System design prompt 1"],
  "leadership_behavioral_questions": ["Leadership/behavioral question 1"],
  "experience_based_questions": ["Question tied directly to candidate's actual background"],
  "preparation_plan": ["Step 1", "Step 2", "Step 3"]
}}"""

    resp = await call_llm(prompt, system, max_tokens=1800, provider="gemini", model=use_model)
    plan = parse_model_json(resp.get("text", "")) or {
        "company_overview": "",
        "role_specific_questions": [],
        "technical_topics": [],
        "system_design_questions": [],
        "leadership_behavioral_questions": [],
        "experience_based_questions": [],
        "preparation_plan": [],
    }

    if interview_id:
        iv = db.query(Interview).filter(Interview.id == interview_id, Interview.application_id == app.id).first()
        if iv:
            iv.prep_plan = plan
            db.commit()
            db.refresh(iv)

    return plan
