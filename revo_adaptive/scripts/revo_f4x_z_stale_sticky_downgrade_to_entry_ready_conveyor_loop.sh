#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_Z_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-Z STALE STICKY DOWNGRADE TO ENTRY READY CONVEYOR SHADOW $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_z_stale_sticky_downgrade_to_entry_ready_conveyor_shadow.py \
    --runtime-dir "$RUNTIME" || true

  echo "=== F4X-Z SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
