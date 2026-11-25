# Gap Analysis: JobNavigator vs. Personal AI Career Agent

This document maps existing JobNavigator capabilities against the Personal AI Career Agent requirements, identifying what to reuse, what to modify, what to disable, and what to build.

---

## 1. Summary Matrix

| Module | Existing Coverage | Gap / Required Change | Action |
|---|---|---|---|
| **Career Profile & Persona** | `Persona` model (`contact`, `compensation`, `preferences`, `resume_content`, `qa_bank`) + UI | Needs initial profile seeding for Staff/Principal/Architect track (Nagpur, Remote, 12+ yrs, ₹33LPA -> ₹50–60LPA, 60d notice, Three.js/Canvas/3D/JS/Platform) as editable data | **Reuse + Seed** |
| **Job Discovery Engine** | 13 ATS adapters, JobSpy, Himalayas, TrulyRemote, Company Pages, Dedup | Missing RSS/Atom adapter, `robots.txt` enforcement, India-remote / global-remote-open-to-India presets, heavy Playwright usage | **Extend + Constrain** |
| **AI Job Matching Engine** | `cv_scorer.py` (light/full scoring), `prompt_fence.py`, `openai` client | Missing weighted 5-dimension rubric (30/25/20/15/10), hard exclusion for onsite/relocation, INR/LPA salary normalization, tiered cheap-vs-capable OpenAI routing | **Modify** |
| **Resume & Cover Letter** | `Resume` & `CoverLetter` models, 8+7 templates, PDF/DOCX export, `parent_id` versioning | Missing explicit approval workflow (`Awaiting Approval` -> `Approved`), "explain changes made" diff/rationale, anti-fabrication prompt & validation guardrails | **Extend** |
| **Application Management** | `Application` (4 statuses: `applied`, `interview`, `offer`, `rejected`) + `Interview` | Missing 13-stage pipeline, recruiter contact fields, cover letter version tracking, follow-up date reminders, duplicate application guard | **Extend** |
| **LinkedIn & Recruiter Assistant** | `linkedin_personal.py` (unofficial API), `linkedin_extension.py` | Unofficial scraping/automation violates LinkedIn rules; missing compliant Recruiter CRM, AI outreach/follow-up drafts, copy-to-clipboard, manual tracking | **Replace / Build New** |
| **Interview Preparation** | `prep_bundle` text export + `Interview` table | Missing in-app AI-generated structured prep (company overview, role/tech/system-design/behavioral/experience questions, prep plan) & feedback notes | **Build New** |
| **Mobile-First PWA** | Desktop React SPA (`Shell.jsx` left rail, no `@media` breakpoints, no manifest/SW) | Needs responsive mobile navigation (bottom bar/drawer), large touch targets, mobile job cards, `manifest.json`, Service Worker, offline shell | **Refactor UI + Add PWA** |
| **Notifications (Telegram)** | `telegram.py` (new job alerts, daily digest, email alerts, inline buttons) | Missing alerts for resume drafts ready, approval required, recruiter follow-ups due, interview reminders, status changes, configurable thresholds | **Extend** |
| **Cost Management (₹500/mo)** | `LlmCallLog` + `llm_cost.py` (USD tracking) | Missing INR (`₹`) budget ceiling, hard spending safeguard/circuit-breaker, two-tier OpenAI model routing, evaluation caching | **Extend** |
| **HP t620 & Security** | `caddy` on `:80/:443`, `runner-manager` with Docker socket, plaintext DB keys | Needs separate Compose project, no `:80/:443` clash, remove `runner-manager`/CLI runners, resource limits, encrypted secrets at rest, `robots.txt` | **Harden + Reconfigure** |

---

## 2. Detailed Gaps & Risks

### A. Job Discovery & Compliance
- **Missing RSS/Atom Adapter**: Many remote-first engineering boards (We Work Remotely, RemoteOK RSS, company engineering/career feeds) expose clean RSS/Atom feeds that require zero Playwright/browser overhead.
- **`robots.txt` & Rate Limiting**: Current scrapers do not check `robots.txt` before fetching arbitrary career pages. Must add a cached `robots.txt` parser (`urllib.robotparser`) and per-domain rate limiting.
- **LinkedIn Compliance**: `backend/scraper/sources/linkedin_personal.py` and `backend/scraper/_shared/linkedin_lookup.py` use `linkedin-api` (unofficial private API). These must be **disabled by default** in favor of permitted sources, manual URL submission, and the compliant Recruiter Outreach Assistant.

### B. AI Matching & Cost Control (₹500/month ≈ \$6/month)
- **Model Routing**: Currently `cv_scorer.py` uses one primary model for `light` and `full` unless manually overridden. To stay within ₹500/month:
  - Stage 1 (Pre-filter / Triage): Deterministic keyword/location/title filter (₹0) + low-cost OpenAI model (e.g., `gpt-4o-mini` or `gpt-5.4-nano`) for initial classification.
  - Stage 2 (Deep Evaluation & Resume Tailoring): Capable OpenAI model (e.g., `gpt-4o` or `gpt-5.4`) only for shortlisted/high-potential jobs.
- **Budget Safeguard**: `llm_cost.py` logs `cost_usd`, but nothing stops scheduled jobs from exceeding a monthly cap. We need a pre-flight budget check against `monthly_budget_inr` (and `usd_to_inr_rate`) with automatic pause + Telegram warning at 80% and 100%.
- **Weighted Scoring**: Need structured JSON output matching the 5 weighted dimensions (30/25/20/15/10) plus hard exclusion flag when onsite/relocation is required.

### C. Resume & Application Workflows
- **Approval Gate**: `Resume` currently has no `approval_status` (`draft | awaiting_approval | approved | rejected`). Users must explicitly approve a tailored resume/cover letter before it is marked final or used to move an application to `Ready to Apply`.
- **Anti-Fabrication Guardrail**: Tailoring prompts must explicitly forbid adding unverified skills/experience, and a post-generation diff/audit (`changes_explanation`, `missing_qualifications`) must be stored and shown to the user.
- **13-Stage Pipeline**: `Application.status` currently normalizes to 4 values. Must expand to all 13 required statuses while preserving `status_transitions` history and existing Stats funnel/Sankey compatibility.

### D. Mobile-First PWA
- **Zero Mobile Breakpoints**: `frontend/src/base.css` and `theme.css` have no layout `@media` queries. The `206px` fixed sidebar and multi-pane desktop views will overflow or cramp on Android mobile screens (`360px–412px` wide).
- **No PWA Assets**: `frontend/public/` lacks `manifest.json`, maskable icons, and a Service Worker (`sw.js`) for installability and fast asset caching.

### E. HP t620 Hardware & Host Coexistence
- **CPU/RAM Bottleneck**: AMD GX-415GA (4 cores @ 1.5 GHz) is ~5–8x slower per core than modern desktop CPUs. Running Playwright Chromium concurrently with `runner-manager`, `db`, and existing host services (Immich, Jellyfin, Home Assistant, Paperless, Vaultwarden, AdGuard Home) risks OOM or CPU starvation.
- **Port Conflict**: `docker-compose.yml` binds host ports `80:80` and `443:443` via Caddy, which will conflict with an existing home-server reverse proxy. Production Compose must expose only an internal/loopback port (e.g., `127.0.0.1:8088:80`) or attach to the reverse proxy cleanly without touching existing services.
- **Unneeded Containers**: `runner-manager` (which mounts `/var/run/docker.sock`) and CLI runners (`codex-runner`, `claude-runner`, `antigravity-runner`) are unnecessary when using the cloud OpenAI API and pose extra security/resource overhead.
