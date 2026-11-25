# Deployment Guide: Development vs. HP t620 Production

> **Note**: Do NOT deploy to the HP t620 home server during local development. All development and testing happen on your local Linux PC first.

---

## 1. Target Environments

| Aspect | Local Development (Linux PC) | Production (HP t620 Home Server) |
|---|---|---|
| **Hardware** | Personal Linux PC | HP t620 Thin Client (AMD GX-415GA 4c @ 1.5 GHz, 16 GB RAM) |
| **OS** | Linux x86-64 | Debian GNU/Linux 13 (x86-64) |
| **Compose Project** | `jobnavigator-dev` | `jobnavigator-prod` (isolated network & volumes) |
| **Reverse Proxy** | Direct localhost (`:80` or `:5173`) | Existing home-server reverse proxy -> `127.0.0.1:8088` |
| **CLI Runners** | Disabled (`JN_RUNNER_MANAGEMENT=0`) | Disabled (`JN_RUNNER_MANAGEMENT=0`) — OpenAI Cloud API only |
| **Concurrency** | Standard | Strict limits (`MAX_CONCURRENT_SCRAPES=1`, `SCORING_MAX_CONCURRENT=1`) |

---

## 2. HP t620 Resource Budget & Constraints

The HP t620 shares 16 GB RAM and a 4-core 1.5 GHz Jaguar CPU with Immich, Paperless, Home Assistant, Jellyfin, Vaultwarden, and AdGuard Home.

### Proposed Container Resource Limits (`docker-compose.prod.yml`)
- **`db` (`postgres:16-alpine`)**: `memory: 384M`, `cpus: "0.75"`
- **`backend` (FastAPI + APScheduler + Playwright)**: `memory: 1024M` (headroom for occasional Chromium PDF/scrape), `cpus: "1.5"`
- **`frontend` (`nginx:alpine`)**: `memory: 64M`, `cpus: "0.25"`
- **Total Cap**: **~1.5 GB RAM**, **2.5 vCPU burst** (idle RAM ~250–350 MB).
- **Excluded Services**: `runner-manager` (Docker socket), `codex-runner`, `claude-runner`, `antigravity-runner`, and `caddy` (if using the host's existing reverse proxy).

---

## 3. Environment Variables (`.env`)

Keep separate `.env.dev` and `.env.prod` files (never commit either to Git; only `.env.example` is tracked):
- `COMPOSE_PROJECT_NAME=jobnavigator-prod`
- `POSTGRES_PASSWORD=<strong-random-password>`
- `INITIAL_API_KEY=<strong-dashboard-password>`
- `JN_SECRET_KEY=<fernet-key-for-at-rest-encryption>`
- `OPENAI_API_KEY=<openai-api-key>`
- `OPENAI_MODEL_TRIAGE=gpt-4o-mini`
- `OPENAI_MODEL_FULL=gpt-4o`
- `AI_MONTHLY_BUDGET_INR=500`
- `TELEGRAM_BOT_TOKEN=<bot-token>`
- `JN_RUNNER_MANAGEMENT=0`
- `JN_TELEMETRY=off`
- `ALLOWED_ORIGINS=https://career.yourdomain.local`

---

## 4. Persistent Storage & `age`-Encrypted Backup / Restore

### Persistent Volumes / Directories
- `pgdata_prod`: PostgreSQL 16 database files.
- `career_data_prod`: Uploaded master resumes and exported PDF/DOCX artifacts (`/app/data`).
- `./backups`: Encrypted `.age` backup archives (5-rotation default).

### `age` Key Management, Recovery & Rotation
1. **Generate an `age` keypair on your personal PC / password manager (NOT on the server):**
   ```bash
   age-keygen -o jobnavigator-backup.key
   # Prints: Public key: age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
2. **Store ONLY the public key (`age1...`)** in `.env.prod` as `BACKUP_AGE_RECIPIENT`.
3. **Store the private identity (`AGE-SECRET-KEY-1...`)** in Vaultwarden / offline encrypted storage — **never** on the same disk/directory as `./backups`.
4. **Key rotation**: Generate a new keypair, update `BACKUP_AGE_RECIPIENT` in `.env.prod`, run a fresh backup, verify restore with the new identity key, then retire the old key once old rotated backups age out.

### Backup & Restore Verification
```bash
# Create age-encrypted DB + document backups
BACKUP_AGE_RECIPIENT=age1... ./scripts/backup_encrypt.sh docker-compose.prod.yml jobnavigator-prod

# Verify restore integrity on a scratch database (pass private key from secure workstation)
AGE_IDENTITY_FILE=/path/to/jobnavigator-backup.key ./scripts/restore_verify.sh backups/db_YYYYMMDD_HHMMSS.sql.age docker-compose.prod.yml jobnavigator-prod
```

---

## 5. Pre-Production Release Checklist

Before deploying to the HP t620, all 9 checks must pass on the local Linux PC:
1. [ ] Clean installation on a fresh Docker environment (`docker compose down -v && docker compose up -d`).
2. [ ] Database migrations idempotent on empty and existing schemas.
3. [ ] Persistent storage survives container recreation.
4. [ ] Backup (`pg_dump`) and restore (`pg_restore`) verified end-to-end.
5. [ ] Mobile PWA verified in Android Chrome / DevTools mobile viewport (installable, touch-friendly, no horizontal overflow).
6. [ ] OpenAI API integration verified (triage + full scoring + resume tailoring + INR budget cap).
7. [ ] Application & resume approval workflows verified (no unapproved finalization or auto-submit).
8. [ ] Resource usage measured under realistic scrape + PDF export load (< 1.5 GB RAM total).
9. [ ] Production deployment and rollback procedures documented and tested.
