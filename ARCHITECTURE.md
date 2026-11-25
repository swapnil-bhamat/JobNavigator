# JobNavigator Architecture Audit

> **Phase 0 Audit** — Baseline architecture of [`vesaias/JobNavigator`](https://github.com/vesaias/JobNavigator) (v3.1.0, commit `a58e284`) prior to transforming it into a Personal AI Career Agent.

---

## 1. High-Level Topology

JobNavigator is a containerized, multi-account, self-hosted job-hunt automation platform.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Caddy (:80 / :443)                              │
│   /api/*, /health, /docs, /cv/*  ──▶  backend:8000                      │
│   /*                             ──▶  frontend:80                       │
└───────────────────────────────┬─────────────────────┬───────────────────┘
                                │                     │
                                ▼                     ▼
┌────────────────────────────────────────┐   ┌────────────────────────────┐
│  backend (FastAPI + Python 3.12)       │   │  frontend (nginx + React)  │
│  - REST API (/api/*) + MCP (/api/mcp)  │   │  - React 18 + Vite 6       │
│  - APScheduler (AsyncIOScheduler)      │   │  - Recharts, Lucide icons  │
│  - Playwright (Chromium)               │   │  - Token-based CSS themes  │
│  - SQLAlchemy 2.0 ORM                  │   └────────────────────────────┘
└──────────┬─────────────────┬───────────┘
           │                 │
           ▼                 ▼
┌──────────────────┐   ┌──────────────────────────────────────────────────┐
│  db              │   │  runner-manager (Docker socket) [optional]       │
│  PostgreSQL 16   │   │  Spawns codex-runner / claude-runner /           │
│  (pgdata vol)    │   │  antigravity-runner on `llm` network             │
└──────────────────┘   └──────────────────────────────────────────────────┘
```

---

## 2. Backend Architecture (`backend/`)

- **Entrypoint & Middleware (`backend/main.py`)**:
  - FastAPI `0.136.3` with `uvicorn` (`0.34.0`).
  - Lifespan hook: logs image version (`image_check.py`), runs pre-migration `pg_dump` backup (`upgrade_backup.py`), creates tables (`create_tables()`), runs idempotent seeds/migrations (`seed.run_seeds()`), cleans stale `JobRun` rows, starts `APScheduler`, warms OpenRouter pricing cache, starts `runner-manager` coordinator if enabled, and mounts the MCP server (`/api/mcp`).
  - HTTP Auth Middleware: two-layer auth (`X-API-Key` / `jn_session` cookie for install password; `X-Account-Token` / `jn_account` cookie for account selection).
- **Multi-Tenancy (`backend/accounts/`)**:
  - Supports up to 5 accounts (`owner` + `member`s) via `AccountScoped` SQLAlchemy mixin (`backend/accounts/scoping.py`) and `ContextVar`-based session scoping (`backend/accounts/context.py`).
  - Optional 4-digit PINs (`scrypt` hashed, rate-limited per client IP and globally).
- **Database (`backend/models/db.py`)**:
  - PostgreSQL 16 (`postgres:16-alpine`), SQLAlchemy 2.0.36 (`pool_size=20`, `max_overflow=10`, `pool_timeout=5s`, `lock_timeout=10s`).
  - No Alembic; schema evolution uses `Base.metadata.create_all()` + idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `create_tables()` and `seed.py`.
  - Core tables:
    - `accounts`, `settings`, `section_policies`
    - `searches`, `companies`, `visa_cache`, `linkedin_lookups`
    - `jobs`, `job_locations` (normalized locations, `arr_remote`/`arr_hybrid`/`arr_onsite`, `cv_scores`, `scoring_report`, `posting_key`, `duplicate_of`, `role_hash`, deferred `cached_page_html`/`cached_page_text`)
    - `applications`, `interviews` (`status_transitions` JSON audit trail)
    - `resumes`, `cover_letters`, `personas` (`contact`, `work_auth`, `demographics`, `compensation`, `preferences`, `resume_content`, `qa_bank`)
    - `tracer_links`, `tracer_click_events`
    - `scrape_log`, `job_runs`, `llm_call_log`, `activity_log_v2`
- **Job Discovery (`backend/scraper/`)**:
  - **Orchestrator (`orchestrator.py`)**: Coordinates shared scraping across accounts with concurrency limiters in `job_monitor.py`.
  - **Sources (`sources/`)**: `jobspy` (LinkedIn, Indeed, ZipRecruiter, Google), `company_pages`, `apify`, `jobright`, `levelsfyi`, `freehire`, `caribbeanjobs`, `trulyremote`, `creative`, `himalayas`, `eluta`, `linkedin_personal`, `linkedin_extension`.
  - **ATS Adapters (`ats/`)**: 13 specialized handlers (`workday`, `greenhouse`, `lever`, `ashby`, `oracle_hcm`, `phenom`, `talentbrew`, `rippling`, `smartrecruiters`, `amazon`, `uber`, `meta`, `google`) + `generic.py` (Playwright/HTML fallback).
  - **Shared Processing (`_shared/`)**: `dedup.py` (SHA256 `external_id`, `content_hash`), `posting_links.py`, `same_role.py`, `filters.py`, `country_filter.py`, `url_safety.py` (SSRF DNS validation), `bot_wall.py`, `still_open.py`.
- **AI Engine (`backend/analyzer/`)**:
  - `llm_client.py`: Provider-agnostic router with retry (4 attempts, exponential backoff) and fallback provider support. Supports `claude_api`, `openai`, `openrouter`, `ollama`, `lmstudio`, `claude_code`, `codex_cli`, `antigravity_cli`, `opencode_go`.
  - `cv_scorer.py`: Scores jobs against base resumes or `Persona.resume_content` in `light` or `full` depth using a process-wide semaphore.
  - `cover_letter_generator.py`: Generates tailored cover letters from resume/persona + JD.
  - `prompt_fence.py`: Wraps untrusted external job text in `<<<JOB POSTING>>>...<<<END JOB POSTING>>>` and neutralizes embedded fence markers.
  - `llm_cost.py` & `llm_logger.py`: Logs token counts (`input`, `output`, `cache_read`, `cache_write`), USD cost, and latency to `llm_call_log`.
- **Documents (`backend/api/routes_resumes.py`, `routes_cover_letters.py`)**:
  - 8 Jinja2 HTML resume templates + 7 cover letter templates.
  - PDF rendering via headless Playwright Chromium; DOCX rendering via `python-docx`.
  - PDF import via `pdfplumber` + LLM extraction.
  - Version lineage via `parent_id` self-referential FK.
- **Notifications & Integrations**:
  - `backend/notifier/telegram.py`: Bot alerts, inline action callbacks, daily digest, scrape health alerts.
  - `backend/email_monitor/`: Gmail OAuth2 polling + LLM response classification.
  - `backend/db_backup.py`: Scheduled `pg_dump` to `backups/` (retains 5).
  - `backend/scheduler.py`: `APScheduler` configured dynamically from `settings` table.

---

## 3. Frontend Architecture (`frontend/`)

- **Stack**: React 18.3, Vite 6.0, React Router 7.1, Axios, Recharts, `@hello-pangea/dnd`, `lucide-react`, `diff`.
- **Layout (`frontend/src/Shell.jsx`)**: Desktop-centric fixed left navigation rail (`206px` expanded / `50px` collapsed) with 3 groups (`Find`, `Apply`, `You`) and 9 screens (`JobFeed`, `Searches`, `Companies`, `Applications`, `Resumes`, `CoverLetters`, `Persona`, `Stats`, `Settings`).
- **Styling (`base.css`, `theme.css`)**: Custom CSS design system with 5 themes × light/dark/system modes. No Tailwind; no responsive `@media` layout breakpoints.
- **Testing**: Vitest + Testing Library (`frontend/src/__tests__/`), Pytest (`backend/tests/`), Playwright E2E (`tests/e2e/`).

---

## 4. Infrastructure & Deployment (`docker-compose.yml`, `Caddyfile`)

- **Services**: `db`, `backend`, `runner-manager`, `frontend`, `caddy`, plus profile-gated CLI runners (`codex-runner`, `claude-runner`, `antigravity-runner`).
- **Networking & Ports**: `caddy` binds host `:80` and `:443`; `backend` binds `127.0.0.1:8000`; `db` binds `127.0.0.1:5432`.
- **Storage**: Named volumes (`pgdata`, `caddy_data`, `caddy_config`, `codex_auth`, `antigravity_auth`, `claude_accounts`, `runner_control`) + bind mounts (`./backend`, `./backups`).
