#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_AJ_SLEEP_SEC:-300}"
cd "$REPO_DIR"
while true; do
  python3 scripts/revo_f4x_aj_scanner_driven_strict_k_paper_intent_conveyor_shadow.py \
    --repo-dir "$REPO_DIR" \
    --runtime-dir "$RUNTIME" || true
  sleep "$SLEEP_SEC"
done
