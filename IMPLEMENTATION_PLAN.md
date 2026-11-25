# Revised Implementation Plan: Personal AI Career Agent

> **Status**: Planning Revision (Phase 0 Complete — No code/schema/config modified yet).  
> **Scope**: Transform [`vesaias/JobNavigator`](https://github.com/vesaias/JobNavigator) into a self-hosted, mobile-first Personal AI Career Agent.  
> **Development Rule**: All development and testing happen on the local Linux PC only. Do not touch the HP t620 server or its existing services. Deliver one milestone at a time with tests and a review gate before proceeding.

---

## Core Architectural Decisions & Preserved Modules

### Preserve & Reuse (Do Not Rewrite)
- **Auth & Multi-Tenancy**: `backend/accounts/auth.py`, `scoping.py`, `context.py` (HMAC-SHA256 cookies, scrypt PINs, rate-limiting).
- **Scraper Shared Pipeline**: `backend/scraper/_shared/dedup.py`, `posting_links.py`, `same_role.py`, `filters.py`, `url_safety.py` (SSRF DNS validation), and permitted ATS adapters (`greenhouse`, `lever`, `ashby`, `workday`, etc.).
- **AI Safety & Cost Logging**: `backend/analyzer/prompt_fence.py`, `llm_client.py` (retry/fallback structure), `llm_logger.py`, `LlmCallLog`.
- **Document Rendering**: `backend/api/routes_resumes.py` & `routes_cover_letters.py` (8 resume + 7 cover-letter Jinja2 templates, Playwright PDF export, `python-docx` DOCX export, `pdfplumber` import, `parent_id` versioning).
- **Notifications & Scheduler**: `backend/notifier/telegram.py` (bot messaging, inline callbacks, daily digest) and `backend/scheduler.py` (`APScheduler`).

### Disable / Remove
- `linkedin_personal.py` and `linkedin_lookup.py` (unofficial private LinkedIn API): disabled by default; all LinkedIn activity remains manual.
- `runner-manager` (mounts `/var/run/docker.sock`) and CLI runner containers (`codex-runner`, `claude-runner`, `antigravity-runner`): excluded from dev/prod Compose.
- `caddy` binding `:80/:443` in production: replaced with loopback port exposure (`127.0.0.1:8088:80`) behind the host's existing reverse proxy.

---

## Database Entities & Migrations Affected

All schema changes will use **Alembic** versioned migrations (replacing ad-hoc `ALTER TABLE` statements), starting from an initial baseline migration matching current `backend/models/db.py`.

1. **Alembic Baseline (`0001_baseline`)**:
   - Captures existing tables (`accounts`, `settings`, `section_policies`, `searches`, `companies`, `visa_cache`, `jobs`, `job_locations`, `applications`, `interviews`, `scrape_log`, `job_runs`, `llm_call_log`, `activity_log_v2`, `resumes`, `cover_letters`, `personas`, `tracer_links`, `tracer_click_events`).
2. **Budget Ledger & Reservations (`0002_budget_guard`)**:
   - New table `llm_budget_ledger` (`id`, `month_key` `YYYY-MM`, `budget_inr`, `reserved_inr`, `spent_inr`, `warned_80_at`, `tripped_100_at`, `updated_at`) for atomic `SELECT ... FOR UPDATE` reservations and reconciliation.
   - New table `llm_budget_reservations` (`id`, `ledger_id`, `account_id`, `purpose`, `estimated_inr`, `actual_inr`, `status` `reserved|settled|released`, `created_at`, `settled_at`).
   - Alter `llm_call_log`: add `cost_inr` (Float), `usd_to_inr_rate` (Float), `reservation_id` (UUID FK).
3. **Job Evaluation & Remote Eligibility (`0003_job_evaluation_remote`)**:
   - Alter `jobs`:
     - Add `remote_eligibility` (`india_remote | worldwide_remote | country_restricted_remote | hybrid | onsite | unknown`).
     - Add `evaluation_cache_key` (String, indexed) and `evaluation_dimensions` (JSON: 5-dimension breakdown + explanation + `is_hard_excluded`).
     - Map existing `jobs.status`: `new` -> `discovered`, `saved` -> `shortlisted`, while preserving `skip | ignored | duplicate | applied` and adding `resume_generated | awaiting_approval | ready_to_apply`.
4. **Document Approval & Fact Provenance (`0004_document_approval`)**:
   - Alter `resumes` and `cover_letters`:
     - Add `content_hash` (SHA-256 of canonical `json_data`), `approval_status` (`draft | awaiting_approval | approved | rejected`), `approved_content_hash` (String), `approved_at` (DateTime), `changes_explanation` (JSON/Text), `missing_qualifications` (JSON), `source_facts` (JSON mapping generated bullets/claims to verified `Persona`/base `Resume` IDs).
5. **Application Pipeline & Interview Prep (`0005_application_pipeline_prep`)**:
   - Alter `applications`:
     - Add `cover_letter_id` (UUID FK -> `cover_letters.id`), `follow_up_date` (Date).
     - Add unique partial/composite constraint preventing duplicate active applications per `(account_id, job_id)`.
     - Migrate `applications.status` cleanly preserving `status_transitions` history and Stats analytics: `interview` -> `interview_scheduled`, `offer` -> `offer_received` (with backward-compatible analytics grouping for funnel/Sankey), adding `recruiter_contacted`, `recruiter_response`, `interview_completed`, `withdrawn`.
   - Alter `interviews`:
     - Add `prep_plan` (JSON: company overview, role/technical/system-design/behavioral/experience questions, study plan) and `feedback_notes` (Text).
6. **Recruiter Outreach CRM (`0006_recruiters`)**:
   - New table `recruiters` (`id`, `account_id`, `job_id`, `application_id`, `name`, `title`, `company`, `profile_url`, `email`, `source_provenance`, `outreach_status`, `draft_connection_note`, `draft_outreach_msg`, `draft_followup_msg`, `notes`, `last_contacted_at`, `follow_up_at`, `created_at`, `updated_at`).
   - Alter `applications`: add `recruiter_id` (UUID FK -> `recruiters.id`, `ON DELETE SET NULL`).

---

## Phased Milestones, Risks, Dependencies & Acceptance Criteria

### Milestone 1: Foundation, Migrations, Security, Budget Guard & Career Profile
- **Scope**:
  - Separate `docker-compose.dev.yml` (local PC) and `docker-compose.prod.yml` (HP t620 blueprint: no `runner-manager`, no Docker socket, no `:80/:443` clash, resource caps, healthchecks).
  - `.env.example` with separate dev/prod vars and configurable Gemini model IDs (`GEMINI_MODEL_TRIAGE`, `GEMINI_MODEL_FULL` — no hardcoded deprecated/unverified models; verify availability, pricing, structured JSON outputs, rate limits, and `google-genai` SDK compatibility before setting defaults).
  - Use Google's official `google-genai` SDK exclusively for AI calls (`GEMINI_API_KEY` in local `.env`; no OpenAI integration or fallback; AI remains optional for startup).
  - Initialize **Alembic** (`0001_baseline`, `0002_budget_guard`) and verify idempotency on fresh DB.
  - Keep secrets in `.env` (outside DB); redact/encrypt any optional DB settings.
  - Implement concurrency-safe **Budget Guard** (`llm_budget_ledger` + `llm_budget_reservations`, Gemini-specific token usage & INR conversion with safety margin, retry/failure accounting, 80% Telegram alert, 100% circuit breaker).
  - Seed initial editable `Persona` + base `Resume` (Nagpur, Remote only, 12+ yrs, ₹33 LPA → ₹50–60 LPA, 60d notice, JS/Frontend/Canvas/Three.js/3D configurators/Full-stack/Platform).
  - Encrypted `pg_dump` + document backups using `age` (recovery key kept separate from server/backups) and tested restore script.
- **Dependencies**: `GEMINI_API_KEY` (for live connectivity check), Docker Compose on local PC.
- **Risks**: Existing `create_tables()`/`seed.py` conflicting with Alembic if not stamped/migrated cleanly; model pricing table drift.
- **Acceptance Criteria**:
  1. `docker compose -f docker-compose.dev.yml up -d` starts cleanly on fresh volume; `alembic upgrade head` succeeds.
  2. Unit tests pass for atomic budget reservation, concurrent contention, retry/failure reconciliation, 80% alert, and 100% circuit breaker.
  3. Gemini API smoke test verifies configured model IDs, structured JSON output, and token/INR logging.
  4. `age` backup + decrypt + restore round-trip verified on local Postgres.

### Milestone 2: Job Discovery & Weighted AI Matching
- **Scope**:
  - Add RSS/Atom adapter (`backend/scraper/sources/rss.py`) and `robots.txt` compliance checker (`robots.py` — treated as a compliance signal, not permission) + per-domain rate limiter.
  - Disable `linkedin_personal` / `linkedin_lookup` by default.
  - Harden manual URL import with `url_safety.py` SSRF checks across redirects, async DNS resolution, and private/loopback/link-local IP ranges.
  - Extend `cv_scorer.py` with two-tier configurable Gemini routing (economical Flash triage vs. capable Gemini full match), evaluation caching, 5-dimension rubric (30/25/20/15/10), explicit `remote_eligibility` (`india_remote`, `worldwide_remote`, `country_restricted_remote`, `hybrid`, `onsite`), hard exclusion for relocation/onsite, and neutral handling of undisclosed compensation.
  - Configure scheduled discovery (`APScheduler`) with low concurrency (`MAX_CONCURRENT_SCRAPES=1`) and India/global-remote presets.
- **Dependencies**: Milestone 1 (Alembic, Budget Guard, Career Profile).
- **Risks**: Career pages blocking requests or changing HTML; misclassifying "Remote (US only)" as worldwide remote.
- **Acceptance Criteria**:
  1. SSRF tests block private IPs, localhost, and malicious HTTP redirects on manual URL import.
  2. `robots.txt` disallow rules skip disallowed paths; RSS adapter ingests and deduplicates feeds.
  3. Scoring tests verify 30/25/20/15/10 weights, hard exclusion on onsite/relocation, and non-rejection of undisclosed salary.
  4. Repeated evaluation of identical `(job_content_hash, profile_hash, model)` hits cache with ₹0 API cost.

### Milestone 3: Resume Engine & Hash-Locked Approval Workflow
- **Scope**:
  - Master resume upload (PDF/DOCX) + structured `Persona` management.
  - Job-specific resume & cover letter tailoring with `changes_explanation`, `missing_qualifications`, and `source_facts` provenance linking every claim to verified profile data.
  - Anti-fabrication prompt rules and post-generation validation (reject unverified skills).
  - Hash-locked approval (`0004_document_approval`): `approval_status` tied to `approved_content_hash`; any edit to `json_data` invalidates approval; final PDF/DOCX exports require `approval_status == 'approved'` and export the approved snapshot.
- **Dependencies**: Milestone 1 (Profile), Milestone 2 (Job evaluation & JD parsing).
- **Risks**: Playwright Chromium memory spikes during PDF export on constrained hardware; subtle JSON formatting edits accidentally invalidating approval.
- **Acceptance Criteria**:
  1. Editing a resume after approval resets `approval_status` to `draft` and clears `approved_content_hash`.
  2. Final export endpoint rejects unapproved documents (or clearly marks draft preview vs. final export).
  3. Anti-fabrication tests verify no unverified skills are injected and `source_facts` are preserved.

### Milestone 4: Application Pipeline, Interview Prep & Telegram Alerts
- **Scope**:
  - Alembic migration `0005_application_pipeline_prep` separating Job evaluation states (`discovered` → `ready_to_apply`) from Application states (`applied` → `recruiter_contacted` → `recruiter_response` → `interview_scheduled` → `interview_completed` → `offer_received` | `rejected` | `withdrawn`).
  - Create `Application` only when pursuing/applying; prevent duplicate applications per job; preserve `status_transitions` and Stats funnel/Sankey analytics.
  - AI Interview Prep generator (company overview, role/technical/system-design/behavioral/experience questions, study plan) + feedback notes.
  - Expand `telegram.py` for resume drafts ready, awaiting approval, follow-ups due, interview reminders, and status changes, with configurable thresholds/frequency.
- **Dependencies**: Milestone 3 (Approved documents).
- **Risks**: Breaking existing Stats funnel/Sankey queries when migrating `interview`/`offer` statuses.
- **Acceptance Criteria**:
  1. Migration preserves all existing `Application` rows, `status_transitions`, and Stats analytics.
  2. Duplicate application creation for the same job returns HTTP 409.
  3. Interview prep generates structured plan and saves feedback notes; Telegram alerts fire for configured events.

### Milestone 5: Compliant LinkedIn & Recruiter Outreach Assistant
- **Scope**:
  - Alembic migration `0006_recruiters` + `Recruiter` CRUD API.
  - Store `source_provenance` (`manual_entry`, `user_supplied_url`, `public_company_page`).
  - AI drafting for connection requests, initial outreach, and follow-ups with configurable message-length limits.
  - UI actions: edit draft, copy-to-clipboard, open original `profile_url`, and manual outreach status updates. Zero LinkedIn automation.
- **Dependencies**: Milestone 4 (Application pipeline).
- **Risks**: Users expecting automated LinkedIn sync (mitigated by clear manual-only UI/workflow).
- **Acceptance Criteria**:
  1. Zero automated LinkedIn requests, logins, or scraping paths exist.
  2. Message drafts respect configurable character limits and provenance is persisted.

### Milestone 6: Mobile-First PWA, Cost Dashboard & Release Gate
- **Scope**:
  - Responsive mobile-first UI (`@media (max-width: 768px)`): bottom navigation bar, mobile job cards, drawer filters, ≥44px touch targets, mobile document preview & copy actions.
  - PWA `manifest.json`, icons, and Service Worker (`sw.js`) for installability on Android Chrome.
  - Cost & health dashboard (daily/monthly INR spend, budget bar, container health).
  - Full 9-point pre-production release verification on local PC (fresh Docker install, migrations, persistent volumes, `age` encrypted backup/restore, mobile PWA, Gemini API, approval workflow, resource usage < 1.5 GB RAM, rollback docs).
- **Dependencies**: Milestones 1–5.
- **Risks**: Service Worker caching stale API responses (must use network-first for `/api/*`).
- **Acceptance Criteria**:
  1. All Vitest + Pytest tests pass.
  2. Android/mobile viewport audit passes with zero horizontal overflow and installable PWA manifest.
  3. Resource usage under realistic scrape + PDF export stays within HP t620 RAM/CPU limits.

---

## Confirmed Assumptions

1. **AI Provider**: Google Gemini API exclusively via Google's official `google-genai` SDK (`GEMINI_API_KEY` in local `.env`, `GEMINI_MODEL_TRIAGE` + `GEMINI_MODEL_FULL`, optional for non-AI startup). No OpenAI integration or fallback.
2. **Reverse Proxy on HP t620**: Existing host Caddy handles HTTPS; `docker-compose.prod.yml` binds `127.0.0.1:8088:80` only. No changes to HP t620 during development.
3. **Encrypted Backups**: `age` encryption for PostgreSQL dumps and career documents; recovery key kept separate from server and backups.
4. **Gmail Integration**: Disabled initially (`JN_ENABLE_GMAIL=0`); manual recruiter and application updates.
