#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_V_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-V EXECUTION CONTEXT STICKY FLOW SOURCE PRIORITY $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_v_execution_context_sticky_flow_source_priority_audit.py \
    --runtime-dir "$RUNTIME" \
    --top 25 \
    --max-lines-per-file 120000 || true

  echo "=== F4X-V SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
