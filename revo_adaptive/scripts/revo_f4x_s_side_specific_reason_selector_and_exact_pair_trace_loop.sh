#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_S_SLEEP_SEC:-300}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-S SIDE SPECIFIC REASON SELECTOR AND EXACT PAIR TRACE $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  python3 scripts/revo_f4x_s_side_specific_reason_selector_and_exact_pair_trace_audit.py \
    --runtime-dir "$RUNTIME" \
    --top 25 \
    --max-lines-per-file 60000 || true

  echo "=== F4X-S SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
