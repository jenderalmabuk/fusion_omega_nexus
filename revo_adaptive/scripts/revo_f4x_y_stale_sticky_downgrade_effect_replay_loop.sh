#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_Y_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-Y STALE STICKY DOWNGRADE EFFECT REPLAY $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_y_stale_sticky_downgrade_effect_replay_audit.py \
    --runtime-dir "$RUNTIME" \
    --priority-pair "AAVE/USDT:USDT" || true

  echo "=== F4X-Y SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
