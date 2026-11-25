#!/usr/bin/env bash
set -euo pipefail

# Encrypt PostgreSQL dump and career_data volume using an age public recipient key.
# Usage: BACKUP_AGE_RECIPIENT=age1... ./scripts/backup_encrypt.sh [compose-file] [project-name]

COMPOSE_FILE="${1:-docker-compose.dev.yml}"
PROJECT_NAME="${2:-jobnavigator-dev}"
RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
BACKUP_DIR="./backups"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ -z "$RECIPIENT" ]]; then
  echo "ERROR: BACKUP_AGE_RECIPIENT (age1...) is required." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "[1/2] Dumping PostgreSQL and encrypting with age..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T db \
  pg_dump -U jobnavigator jobnavigator \
  | docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend \
  age -r "$RECIPIENT" -o "/app/backups/db_${STAMP}.sql.age"

echo "[2/2] Archiving /app/data career documents and encrypting with age..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend \
  sh -c "tar -C /app -czf - data 2>/dev/null || tar -C /app -czf - --files-from /dev/null" \
  | docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend \
  age -r "$RECIPIENT" -o "/app/backups/docs_${STAMP}.tar.gz.age"

echo "Encrypted backups created:"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend \
  ls -lh "/app/backups/db_${STAMP}.sql.age" "/app/backups/docs_${STAMP}.tar.gz.age"
