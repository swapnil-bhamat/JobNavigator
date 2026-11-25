"""Weighted AI Job Matching Engine using Google Gemini (triage + full tiers).

Scoring dimensions (total 100%):
- technical_skills: 30%
- experience_seniority: 25%
- architecture_leadership: 20%
- remote_location: 15% (plus hard exclusion on onsite / hybrid / relocation required)
- compensation_alignment: 10% (neutral 50/100 when undisclosed; never auto-rejected)
"""
import hashlib
import json
import logging
from typing import Optional

from backend.analyzer.llm_client import call_llm
from backend.analyzer.model_json import parse_model_json
from backend.analyzer.prompt_fence import fence
from backend.config import GEMINI_MODEL_FULL, GEMINI_MODEL_TRIAGE
from backend.models.db import Job, Persona

logger = logging.getLogger("jobnavigator.job_matcher")

WEIGHTS = {
    "technical_skills": 0.30,
    "experience_seniority": 0.25,
    "architecture_leadership": 0.20,
    "remote_location": 0.15,
    "compensation_alignment": 0.10,
}

REMOTE_ELIGIBILITY_VALUES = (
    "india_remote",
    "worldwide_remote",
    "country_restricted_remote",
    "hybrid",
    "onsite",
    "unknown",
)


def compute_profile_hash(persona: Persona) -> str:
    payload = json.dumps(
        {
            "contact": persona.contact or {},
            "work_auth": persona.work_auth or {},
            "compensation": persona.compensation or {},
            "preferences": persona.preferences or {},
            "resume_content": persona.resume_content or {},
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compute_eval_cache_key(job: Job, profile_hash: str, triage_model: str, full_model: str) -> str:
    job_sig = job.content_hash or hashlib.sha256(
        f"{job.company or ''}|{job.title or ''}|{job.location or ''}|{job.description or ''}".encode("utf-8")
    ).hexdigest()[:16]
    raw = f"{job_sig}:{profile_hash}:{triage_model}:{full_model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def compute_weighted_score(dimensions: dict, remote_eligibility: str, requires_relocation_or_onsite: bool,
                           india_eligible: bool = True, salary_disclosed: bool = True) -> dict:
    """Compute final 0-100 weighted score and apply hard location exclusion rules."""
    hard_excluded = (
        requires_relocation_or_onsite
        or remote_eligibility in ("onsite", "hybrid")
        or (remote_eligibility == "country_restricted_remote" and not india_eligible)
    )

    dims = {}
    for key in WEIGHTS:
        val = dimensions.get(key)
        if key == "compensation_alignment" and not salary_disclosed:
            # Undisclosed compensation must not penalize or auto-reject the role
            val = 50.0
        try:
            num = max(0.0, min(100.0, float(val if val is not None else 50.0)))
        except (TypeError, ValueError):
            num = 50.0
        dims[key] = round(num, 1)

    if hard_excluded:
        dims["remote_location"] = 0.0
        return {
            "score": 0.0,
            "is_hard_excluded": True,
            "exclusion_reason": (
                f"Location/arrangement incompatible ({remote_eligibility}; "
                f"relocation_or_onsite={requires_relocation_or_onsite}, india_eligible={india_eligible})"
            ),
            "dimensions": dims,
        }

    total = sum(dims[k] * w for k, w in WEIGHTS.items())
    return {
        "score": round(total, 1),
        "is_hard_excluded": False,
        "exclusion_reason": None,
        "dimensions": dims,
    }


def _persona_summary(persona: Persona) -> str:
    return json.dumps(
        {
            "location": (persona.contact or {}).get("location"),
            "work_auth": persona.work_auth or {},
            "compensation": persona.compensation or {},
            "preferences": persona.preferences or {},
            "resume_content": persona.resume_content or {},
        },
        indent=2,
    )


async def evaluate_job(
    db,
    job: Job,
    persona: Persona,
    *,
    triage_model: Optional[str] = None,
    full_model: Optional[str] = None,
    force: bool = False,
) -> dict:
    """Evaluate a Job against Persona using two-tier Gemini matching + caching."""
    t_model = (triage_model or GEMINI_MODEL_TRIAGE or "").strip()
    f_model = (full_model or GEMINI_MODEL_FULL or t_model).strip()

    profile_hash = compute_profile_hash(persona)
    cache_key = compute_eval_cache_key(job, profile_hash, t_model, f_model)

    if not force and job.evaluation_cache_key == cache_key and job.evaluation_dimensions:
        return {**job.evaluation_dimensions, "cached": True, "cache_key": cache_key}

    fenced_jd = fence(
        f"Title: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n\n{job.description or ''}",
        label="JOB POSTING",
    )
    profile_text = _persona_summary(persona)

    # Stage 1: Triage & structured extraction (economical Flash model)
    triage_system = (
        "You are an expert technical career evaluator. Extract job requirements and evaluate fit "
        "strictly against the candidate's verified profile. Return ONLY valid JSON."
    )
    triage_prompt = f"""Candidate Profile (verified):
{profile_text}

{fenced_jd}

Return JSON with this exact schema:
{{
  "required_skills": ["..."],
  "preferred_skills": ["..."],
  "seniority": "staff|principal|architect|senior|lead|mid|unknown",
  "remote_eligibility": "india_remote|worldwide_remote|country_restricted_remote|hybrid|onsite|unknown",
  "india_eligible": true,
  "requires_relocation_or_onsite": false,
  "location_restrictions": "string or null",
  "salary_disclosed": false,
  "salary_min_inr_lpa": null,
  "salary_max_inr_lpa": null,
  "dimensions": {{
    "technical_skills": 0,
    "experience_seniority": 0,
    "architecture_leadership": 0,
    "remote_location": 0,
    "compensation_alignment": 50
  }},
  "strengths": ["..."],
  "skill_gaps": ["..."],
  "explanation": "2-3 sentence explanation"
}}"""

    triage_resp = await call_llm(
        triage_prompt,
        triage_system,
        max_tokens=1000,
        provider="gemini",
        model=t_model,
    )
    parsed = parse_model_json(triage_resp.get("text", "")) or {}

    remote_elig = parsed.get("remote_eligibility", "unknown")
    if remote_elig not in REMOTE_ELIGIBILITY_VALUES:
        remote_elig = "unknown"
    req_reloc = bool(parsed.get("requires_relocation_or_onsite", False))
    india_elig = bool(parsed.get("india_eligible", True))
    salary_disclosed = bool(parsed.get("salary_disclosed", False))

    weighted = compute_weighted_score(
        parsed.get("dimensions") or {},
        remote_elig,
        req_reloc,
        india_eligible=india_elig,
        salary_disclosed=salary_disclosed,
    )

    model_used = t_model

    # Stage 2: Deep evaluation with capable Gemini model if not hard-excluded and promising (>= 55)
    if (
        not weighted["is_hard_excluded"]
        and weighted["score"] >= 55.0
        and f_model
        and f_model != t_model
    ):
        full_prompt = f"""Perform a deep Staff/Principal/Architect evaluation for this candidate and role.
Candidate Profile (verified):
{profile_text}

{fenced_jd}

Refine the 0-100 dimension scores and provide concrete strengths, skill gaps, and explanation.
Return ONLY JSON with the same schema (dimensions, strengths, skill_gaps, explanation)."""
        full_resp = await call_llm(
            full_prompt,
            triage_system,
            max_tokens=1400,
            provider="gemini",
            model=f_model,
        )
        full_parsed = parse_model_json(full_resp.get("text", "")) or {}
        if full_parsed.get("dimensions"):
            weighted = compute_weighted_score(
                full_parsed["dimensions"],
                remote_elig,
                req_reloc,
                india_eligible=india_elig,
                salary_disclosed=salary_disclosed,
            )
            parsed["strengths"] = full_parsed.get("strengths", parsed.get("strengths", []))
            parsed["skill_gaps"] = full_parsed.get("skill_gaps", parsed.get("skill_gaps", []))
            parsed["explanation"] = full_parsed.get("explanation", parsed.get("explanation", ""))
            model_used = f_model

    result = {
        "score": weighted["score"],
        "is_hard_excluded": weighted["is_hard_excluded"],
        "exclusion_reason": weighted["exclusion_reason"],
        "remote_eligibility": remote_elig,
        "india_eligible": india_elig,
        "requires_relocation_or_onsite": req_reloc,
        "location_restrictions": parsed.get("location_restrictions"),
        "seniority": parsed.get("seniority", "unknown"),
        "required_skills": parsed.get("required_skills", []),
        "preferred_skills": parsed.get("preferred_skills", []),
        "salary_disclosed": salary_disclosed,
        "salary_min_inr_lpa": parsed.get("salary_min_inr_lpa"),
        "salary_max_inr_lpa": parsed.get("salary_max_inr_lpa"),
        "dimensions": weighted["dimensions"],
        "strengths": parsed.get("strengths", []),
        "skill_gaps": parsed.get("skill_gaps", []),
        "explanation": parsed.get("explanation", ""),
        "model_used": model_used,
        "cached": False,
        "cache_key": cache_key,
    }

    job.remote_eligibility = remote_elig
    job.evaluation_cache_key = cache_key
    job.evaluation_dimensions = result
    job.best_cv_score = weighted["score"]
    job.scoring_report = result
    db.commit()

    return result
