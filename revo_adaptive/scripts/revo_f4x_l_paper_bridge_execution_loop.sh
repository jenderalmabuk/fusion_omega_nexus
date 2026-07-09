#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
RUNTIME="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
SLEEP_SEC="${F4X_L_SLEEP_SEC:-300}"
COOLDOWN_SEC="${F4X_L_COOLDOWN_SEC:-1800}"

cd "$REPO_DIR"

while true; do
  echo "=== F4X-L PAPER EXECUTION DRYRUN CYCLE $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  ./scripts/revo_f4x_full_confluence_paper_pipeline.sh || true

  python3 scripts/revo_f4x_c_lane_separation_smc_watch_persistence.py \
    --runtime-dir "$RUNTIME" || true

  if [ -x ./F4X_H_GATE_TO_SMC_AND_LATEST_STATE_ATTRIBUTION_AUDIT_ONLY.sh ]; then
    ./F4X_H_GATE_TO_SMC_AND_LATEST_STATE_ATTRIBUTION_AUDIT_ONLY.sh || true
  elif [ -f scripts/revo_f4x_h_gate_to_smc_latest_state_attribution_audit.py ]; then
    python3 scripts/revo_f4x_h_gate_to_smc_latest_state_attribution_audit.py \
      --runtime-dir "$RUNTIME" || true
  fi

  if [ -x ./F4X_I2_SIDE_AWARE_GATE_MAPPING_REPLAY_AUDIT_ONLY.sh ]; then
    ./F4X_I2_SIDE_AWARE_GATE_MAPPING_REPLAY_AUDIT_ONLY.sh || true
  elif [ -f scripts/revo_f4x_i2_side_aware_gate_mapping_replay_audit.py ]; then
    python3 scripts/revo_f4x_i2_side_aware_gate_mapping_replay_audit.py \
      --runtime-dir "$RUNTIME" || true
  fi

  python3 scripts/revo_f4x_j_side_aware_mapping_shadow_classifier.py \
    --runtime-dir "$RUNTIME" || true

  python3 scripts/revo_f4x_k_paper_bridge_intent_runner.py \
    --runtime-dir "$RUNTIME" || true

  python3 scripts/revo_f4x_l_paper_bridge_execution_sandbox_dryrun.py \
    --repo-dir "$REPO_DIR" \
    --runtime-dir "$RUNTIME" \
    --cooldown-sec "$COOLDOWN_SEC" \
    --execute || true

  python3 scripts/revo_f4x_l_paper_trade_outcome_audit.py \
    --repo-dir "$REPO_DIR" \
    --runtime-dir "$RUNTIME" || true

  echo "=== F4X-L PAPER EXECUTION DRYRUN SLEEP ${SLEEP_SEC}s ==="
  sleep "$SLEEP_SEC"
done
