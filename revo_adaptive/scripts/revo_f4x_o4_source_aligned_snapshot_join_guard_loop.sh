#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_O4_SLEEP_SEC:-300}"
MAX_DELTA_SEC="${F4X_O4_MAX_DELTA_SEC:-420}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-O4 SOURCE ALIGNED SNAPSHOT JOIN GUARD $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_o4_source_aligned_snapshot_join_guard_audit.py \
    --runtime-dir "$RUNTIME" \
    --max-delta-sec "$MAX_DELTA_SEC" || true

  echo "=== F4X-O4 SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
