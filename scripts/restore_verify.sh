#!/usr/bin/env bash
set -euo pipefail

# Verify age-encrypted backup + restore round-trip on a scratch database inside Compose.
# Usage: AGE_IDENTITY_FILE=/path/to/age.key ./scripts/restore_verify.sh db_xxx.sql.age [compose-file] [project-name]

ENCRYPTED_SQL="${1:?Pass path to encrypted .sql.age backup}"
COMPOSE_FILE="${2:-docker-compose.dev.yml}"
PROJECT_NAME="${3:-jobnavigator-dev}"
IDENTITY_FILE="${AGE_IDENTITY_FILE:?Set AGE_IDENTITY_FILE to your off-server age private key}"
TEST_DB="jobnavigator_restore_test"

if [[ ! -f "$ENCRYPTED_SQL" ]]; then
  echo "ERROR: Backup file not found: $ENCRYPTED_SQL" >&2
  exit 1
fi
if [[ ! -f "$IDENTITY_FILE" ]]; then
  echo "ERROR: Identity file not found: $IDENTITY_FILE" >&2
  exit 1
fi

echo "[1/4] Creating temporary restore verification database ($TEST_DB)..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T db \
  psql -U jobnavigator -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB};"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T db \
  psql -U jobnavigator -d postgres -c "CREATE DATABASE ${TEST_DB};"

echo "[2/4] Decrypting backup and restoring into $TEST_DB..."

# Portable pipe: decrypt on backend using identity passed via stdin? Better: use temp file inside container and shred/rm
TMP_KEY="/tmp/restore_key_$$.txt"
TMP_ENC="/tmp/restore_enc_$$.sql.age"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" cp "$IDENTITY_FILE" "backend:${TMP_KEY}"
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" cp "$ENCRYPTED_SQL" "backend:${TMP_ENC}"

trap 'docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend rm -f "$TMP_KEY" "$TMP_ENC" >/dev/null 2>&1 || true' EXIT

docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T backend \
  age -d -i "$TMP_KEY" "$TMP_ENC" \
  | docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T db \
  psql -U jobnavigator -d "$TEST_DB" -v ON_ERROR_STOP=1 >/dev/null

echo "[3/4] Verifying restored tables and alembic_version..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T db \
  psql -U jobnavigator -d "$TEST_DB" -c "SELECT version_num FROM alembic_version; SELECT count(*) AS accounts FROM accounts; SELECT count(*) AS personas FROM personas;"

echo "[4/4] Cleaning up temporary verification database..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" exec -T db \
  psql -U jobnavigator -d postgres -c "DROP DATABASE IF EXISTS ${TEST_DB};"

echo "Restore verification PASSED."
