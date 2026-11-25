"""Milestone 2 tests: SSRF hardening, robots.txt, RSS adapter, weighted Gemini matching, LinkedIn disable."""
import json
import pytest
from fastapi.testclient import TestClient

from backend.models.db import Base, Job, Persona, SessionLocal, engine
from backend.analyzer.job_matcher import compute_weighted_score, evaluate_job
from backend.scraper._shared.robots import parse_robots_txt
from backend.scraper._shared.url_safety import UnsafeURLError, assert_public_http_url
from backend.scraper.sources.rss import parse_feed_xml


def test_ssrf_blocks_private_and_loopback_urls():
    for bad in (
        "http://127.0.0.1:8000/api/settings",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://[::1]/",
        "file:///etc/passwd",
    ):
        with pytest.raises(UnsafeURLError):
            assert_public_http_url(bad)


def test_manual_job_endpoint_rejects_ssrf():
    from backend.main import app
    client = TestClient(app)
    resp = client.post(
        "/api/jobs/manual",
        json={"title": "Staff Engineer", "company": "Evil", "url": "http://127.0.0.1:8000/health"},
    )
    assert resp.status_code == 400
    assert "Unsafe URL" in resp.json()["detail"]


def test_robots_txt_compliance():
    rp = parse_robots_txt(
        "https://example.com/robots.txt",
        "User-agent: *\nDisallow: /private/\nCrawl-delay: 2\n",
    )
    assert rp.can_fetch("JobNavigatorPersonalAgent/1.0", "https://example.com/careers/rss") is True
    assert rp.can_fetch("JobNavigatorPersonalAgent/1.0", "https://example.com/private/jobs") is False
    assert rp.crawl_delay("JobNavigatorPersonalAgent/1.0") == 2


def test_rss_and_atom_parsing():
    rss_xml = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Staff Frontend Architect</title>
        <author>Acme 3D</author>
        <link>https://example.com/jobs/1</link>
        <description>&lt;p&gt;Three.js and Canvas architecture.&lt;/p&gt;</description>
        <pubDate>Tue, 25 Nov 2026 10:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    jobs = parse_feed_xml(rss_xml, source_name="test_rss")
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Staff Frontend Architect"
    assert jobs[0]["company"] == "Acme 3D"
    assert "Three.js" in jobs[0]["description"]

    atom_xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Principal Platform Engineer</title>
        <author><name>PlatformCo</name></author>
        <link href="https://example.com/jobs/2" />
        <summary>Remote India</summary>
      </entry>
    </feed>"""
    atom_jobs = parse_feed_xml(atom_xml, source_name="test_atom")
    assert len(atom_jobs) == 1
    assert atom_jobs[0]["company"] == "PlatformCo"
    assert atom_jobs[0]["url"] == "https://example.com/jobs/2"


def test_weighted_scoring_and_hard_exclusions():
    # Exact 30/25/20/15/10 weights, undisclosed salary -> neutral 50 on comp
    res = compute_weighted_score(
        {
            "technical_skills": 90,
            "experience_seniority": 80,
            "architecture_leadership": 85,
            "remote_location": 100,
            "compensation_alignment": 0,  # should be overridden to 50 when undisclosed
        },
        remote_eligibility="india_remote",
        requires_relocation_or_onsite=False,
        india_eligible=True,
        salary_disclosed=False,
    )
    # 90*0.3 + 80*0.25 + 85*0.2 + 100*0.15 + 50*0.1 = 27 + 20 + 17 + 15 + 5 = 84.0
    assert res["is_hard_excluded"] is False
    assert res["score"] == 84.0

    # Hard exclusion on onsite/hybrid/relocation/country-restricted excluding India
    for elig, reloc, india_ok in [
        ("onsite", False, True),
        ("hybrid", False, True),
        ("worldwide_remote", True, True),
        ("country_restricted_remote", False, False),
    ]:
        excl = compute_weighted_score(
            {"technical_skills": 100, "experience_seniority": 100, "architecture_leadership": 100, "remote_location": 100, "compensation_alignment": 100},
            remote_eligibility=elig,
            requires_relocation_or_onsite=reloc,
            india_eligible=india_ok,
        )
        assert excl["is_hard_excluded"] is True
        assert excl["score"] == 0.0


@pytest.mark.asyncio
async def test_evaluate_job_uses_cache_and_updates_job(monkeypatch):
    from backend.accounts.context import unscoped
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    calls = {"count": 0}

    async def _fake_call_llm(prompt, system, max_tokens=1000, provider=None, model=None, **kw):
        calls["count"] += 1
        return {
            "text": json.dumps({
                "required_skills": ["Three.js", "TypeScript"],
                "preferred_skills": ["WebGL"],
                "seniority": "staff",
                "remote_eligibility": "worldwide_remote",
                "india_eligible": True,
                "requires_relocation_or_onsite": False,
                "salary_disclosed": False,
                "dimensions": {
                    "technical_skills": 90,
                    "experience_seniority": 90,
                    "architecture_leadership": 85,
                    "remote_location": 100,
                    "compensation_alignment": 50,
                },
                "strengths": ["12+ yrs & Three.js"],
                "skill_gaps": [],
                "explanation": "Strong Staff match.",
            }),
            "provider": "gemini",
            "model": model or "gemini-3.5-flash-lite",
        }

    monkeypatch.setattr("backend.analyzer.job_matcher.call_llm", _fake_call_llm)

    with unscoped():
        persona = db.query(Persona).first() or Persona(contact={"location": "Nagpur"})
        if persona.id is None:
            db.add(persona)
            db.commit()
        import uuid
        ext_id = f"m2-test-ext-{uuid.uuid4().hex[:8]}"
        job = Job(
            external_id=ext_id,
            content_hash="m2-test-hash-1",
            company="3D Co",
            title="Staff 3D Architect",
            url="https://example.com/job/3d",
            description="Three.js WebGL remote worldwide",
            status="discovered",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        r1 = await evaluate_job(db, job, persona, triage_model="gemini-3.5-flash-lite", full_model="gemini-3.5-flash-lite")
        assert r1["cached"] is False
        assert r1["remote_eligibility"] == "worldwide_remote"
        assert r1["score"] == 86.5
        assert calls["count"] == 1

        # Second evaluation hits cache -> 0 LLM calls
        r2 = await evaluate_job(db, job, persona, triage_model="gemini-3.5-flash-lite", full_model="gemini-3.5-flash-lite")
        assert r2["cached"] is True
        assert calls["count"] == 1
        db.delete(job)
        db.commit()
    db.close()


@pytest.mark.asyncio
async def test_unofficial_linkedin_disabled_by_default():
    from backend.scraper.sources.linkedin_personal import run as li_run
    from backend.scraper._shared.linkedin_lookup import _linkedin_pass

    res1 = await li_run(None)
    assert res1.get("disabled") is True
    res2 = await _linkedin_pass(5, None, 0.0, None)
    assert res2.get("stopped") == "disabled"
