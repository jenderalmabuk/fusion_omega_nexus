#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_W_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-W STICKY TTL REFRESH AND CYCLE ID STALENESS $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_w_sticky_ttl_refresh_and_cycle_id_staleness_audit.py \
    --runtime-dir "$RUNTIME" \
    --pair "AAVE/USDT:USDT" \
    --side LONG \
    --max-cycle-drift-sec 1800 \
    --max-lines-per-file 180000 \
    --top 30 || true

  echo "=== F4X-W SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
