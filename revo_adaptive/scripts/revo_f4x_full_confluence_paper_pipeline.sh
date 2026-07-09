#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
cd "$REPO_DIR"

RUNTIME_DIR="${REVO_RUNTIME_DIR:-/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit}"
export REVO_RUNTIME_DIR="$RUNTIME_DIR"
export REVO_RUNTIME_PROFILE="${REVO_RUNTIME_PROFILE:-bybit}"
export REVO_MARKET_SOURCE="${REVO_MARKET_SOURCE:-BYBIT}"

TS="$(date -u +%Y%m%d_%H%M%S)"
LOG="F4X_FULL_CONFLUENCE_PAPER_PIPELINE_${TS}.log"

{
echo "F4X_FULL_CONFLUENCE_PAPER_PIPELINE"
echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "runtime=$RUNTIME_DIR"
echo "paper_mode_only=1"
echo "live_allowed=0"

python3 scripts/revo_f4x_full_confluence_paper_engine.py \
  --runtime-dir "$RUNTIME_DIR" \
  --max-pairs "${F4X_MAX_PAIRS:-150}" \
  --fast-max-pairs "${F4X_FAST_MAX_PAIRS:-36}" \
  --candidate-limit "${F4X_CANDIDATE_LIMIT:-36}" \
  --cvd-trade-limit "${F4X_CVD_TRADE_LIMIT:-200}" \
  --kline-limit "${F4X_KLINE_LIMIT:-80}" \
  --http-sleep-sec "${F4X_HTTP_SLEEP_SEC:-0.16}" \
  --collector-sleep-sec "${F4X_COLLECTOR_SLEEP_SEC:-0.18}" \
  --signal-ttl-sec "${F4X_SIGNAL_TTL_SEC:-300}" \
  ${F4X_SKIP_F3:+--skip-f3} \
  ${F4X_ALLOW_F3_FAIL:+--allow-f3-fail} \
  ${F4X_ALLOW_MISSING_F3:+--allow-missing-f3}

python3 scripts/revo_validate_f4x_full_confluence_paper.py \
  --runtime-dir "$RUNTIME_DIR"

echo "=== F4X FINAL COMPACT ==="
cat "$RUNTIME_DIR/F4X_EXTENDED_CONFLUENCE_FINAL_COMPACT.txt"

echo "=== F4X DATA QUALITY ==="
cat "$RUNTIME_DIR/F4X_DATA_QUALITY_COMPACT.txt"

echo "F4X_FULL_CONFLUENCE_PAPER_PIPELINE_PASS"
} 2>&1 | tee "$LOG"

echo "log=$LOG"
