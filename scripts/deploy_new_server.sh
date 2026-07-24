#!/usr/bin/env bash
# Restore a full Nexus replica on a NEW server from a backup_state.sh archive.
# Run AFTER `git clone` + `cd fusion_omega_nexus` (code/scripts come via git).
#   usage: scripts/deploy_new_server.sh <nexus_backup.tar.gz>
set -euo pipefail

ARCHIVE="${1:?usage: scripts/deploy_new_server.sh <nexus_backup.tar.gz>}"
[ -f "$ARCHIVE" ] || { echo "archive not found: $ARCHIVE"; exit 1; }
ARCHIVE="$(readlink -f "$ARCHIVE")"

cd "$(dirname "$0")/.."
REPO="$(pwd)"
COMPOSE="docker compose -f docker/docker-compose.yml"
DB_CONTAINER="nexus_timescaledb"
DB_USER="nexus"
DB_NAME="nexus"

command -v docker >/dev/null || { echo "docker required"; exit 1; }
$COMPOSE version >/dev/null 2>&1 || { echo "docker compose v2 required"; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "[1/6] extracting archive…"
tar -C "$STAGE" -xzf "$ARCHIVE"
[ -f "$STAGE/nexus_db.dump" ] || { echo "archive missing nexus_db.dump"; exit 1; }

echo "[2/6] restoring secrets + state into repo…"
[ -f "$STAGE/.env" ] && cp -a "$STAGE/.env" "$REPO/.env"
shopt -s nullglob
for s in "$STAGE"/*.session; do cp -a "$s" "$REPO/"; done
shopt -u nullglob
[ -d "$STAGE/runtime" ] && { rm -rf "$REPO/runtime"; cp -a "$STAGE/runtime" "$REPO/runtime"; }
[ -d "$STAGE/journal" ] && { rm -rf "$REPO/journal"; cp -a "$STAGE/journal" "$REPO/journal"; }

echo "[3/6] starting TimescaleDB…"
$COMPOSE up -d timescaledb
echo -n "  waiting for DB"
for _ in $(seq 1 90); do
  if docker exec "$DB_CONTAINER" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    echo " ready"; break
  fi
  echo -n "."; sleep 2
done

echo "[4/6] restoring database (TimescaleDB pre/post_restore)…"
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT timescaledb_pre_restore();"
docker exec -i "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --clean --if-exists < "$STAGE/nexus_db.dump" || true
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "SELECT timescaledb_post_restore();"

echo "[5/6] building bot image…"
docker build -f docker/bot/Dockerfile -t nexus-bot:latest "$REPO"

echo "[6/6] starting full stack (profile bots)…"
$COMPOSE --profile bots up -d --build

echo
echo "=== containers ==="
docker ps --format '{{.Names}}\t{{.Status}}'
echo
echo "verify next: universe=466, stale=0 in engine logs, DB rows:"
echo "  docker exec $DB_CONTAINER psql -U $DB_USER -d $DB_NAME -tc \"SELECT count(*) FROM klines;\""

# ponytail: assumes same arch/docker as source. add platform pin + healthcheck
# gate before starting bots when servers diverge.
