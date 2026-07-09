#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_P_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-P CORE ALIGNED ENTRY BLOCKER CONVEYOR AUDIT $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_p_core_aligned_entry_blocker_conveyor_audit.py \
    --runtime-dir "$RUNTIME" \
    --top 25 || true

  echo "=== F4X-P SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
