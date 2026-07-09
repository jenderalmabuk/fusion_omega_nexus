#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_U_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-U SIGNAL VS EXECUTION FLOW SOURCE ALIGNMENT $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_u_signal_vs_execution_flow_source_alignment_audit.py \
    --runtime-dir "$RUNTIME" \
    --top 25 \
    --max-lines-per-file 100000 || true

  echo "=== F4X-U SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
