#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_X_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-X STICKY SOURCE CYCLE GUARD SHADOW $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_x_sticky_source_cycle_guard_shadow_only.py \
    --runtime-dir "$RUNTIME" \
    --max-cycle-drift-sec 1800 \
    --min-repeat-count 3 \
    --priority-pair "AAVE/USDT:USDT" || true

  echo "=== F4X-X SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
