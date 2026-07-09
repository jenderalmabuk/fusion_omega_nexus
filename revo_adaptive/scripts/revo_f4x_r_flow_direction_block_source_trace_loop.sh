#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_R_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-R FLOW DIRECTION BLOCK SOURCE TRACE $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_r_flow_direction_block_source_trace_audit.py \
    --runtime-dir "$RUNTIME" \
    --top 25 || true

  echo "=== F4X-R SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
