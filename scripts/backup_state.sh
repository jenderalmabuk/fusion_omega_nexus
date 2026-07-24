#!/usr/bin/env bash
# Bundle everything git does NOT carry, so a new server can be a full replica:
#   TimescaleDB dump + .env + *.session + runtime/ + journal/
# Run on the OLD server (this one). Produces one nexus_backup_<ts>.tar.gz.
#   usage: scripts/backup_state.sh [output.tar.gz]
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$(pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-$REPO/nexus_backup_$TS.tar.gz}"
DB_CONTAINER="nexus_timescaledb"
DB_USER="nexus"
DB_NAME="nexus"

# Stage on the repo disk (big), not /tmp (often small on a VPS).
STAGE="$REPO/.backup_stage_$TS"
mkdir -p "$STAGE/payload"
cleanup() { $SUDO rm -rf "$STAGE"; }
trap cleanup EXIT

# runtime/ has root-owned state files — need sudo to read them.
SUDO=""
if [ -n "$(find runtime journal -not -user "$(id -un)" -print -quit 2>/dev/null)" ]; then
  SUDO="sudo"
fi

echo "[1/4] dumping $DB_NAME (custom format, compressed)…"
docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -Fc -d "$DB_NAME" > "$STAGE/payload/nexus_db.dump"

echo "[2/4] copying secrets + auth session…"
[ -f "$REPO/.env" ] && $SUDO cp -a "$REPO/.env" "$STAGE/payload/.env" || echo "  (warn) no .env found"
shopt -s nullglob
for s in "$REPO"/*.session; do $SUDO cp -a "$s" "$STAGE/payload/"; done
shopt -u nullglob

echo "[3/4] copying runtime/ + journal/ (state, positions, PnL)…"
[ -d "$REPO/runtime" ] && $SUDO cp -a "$REPO/runtime" "$STAGE/payload/runtime"
[ -d "$REPO/journal" ] && $SUDO cp -a "$REPO/journal" "$STAGE/payload/journal"

echo "[4/4] packing → $OUT"
$SUDO tar -C "$STAGE/payload" -czpf "$OUT" .
$SUDO chown "$(id -u):$(id -g)" "$OUT"

echo
echo "DONE: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Contains: nexus_db.dump, .env, *.session, runtime/, journal/"
echo "SECRET — move over scp/rsync only, delete after transfer."

# ponytail: no encryption / no incremental. add gpg + rsync --link-dest when
# backups become routine or leave the trusted network.
