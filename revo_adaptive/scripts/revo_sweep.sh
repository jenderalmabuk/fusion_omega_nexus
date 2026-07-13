#!/bin/bash
# Revo Adaptive parameter sweep via freqtrade backtesting.
# Two-stage grid to avoid combinatorial blowup:
#   STAGE=1: sweep MIN_SCORE (dominant lever) at fixed disc/rsi
#   STAGE=2: finetune DISCOUNT x RSI_MAX around chosen MIN_SCORE (BEST_MS env)
# Output: one JSON line per combo to /freqtrade/user_data/logs/revo_sweep_results.jsonl

set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktest"
TR="20260601-20260710"
OUT="/freqtrade/user_data/logs/revo_sweep_results.jsonl"
RESDIR="/freqtrade/user_data/backtest_results"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

run_one() {
  local ms="$1" disc="$2" rsi="$3"
  local tag="ms${ms}_disc${disc}_rsi${rsi}"
  echo ">>> RUN $tag" >&2
  REVO_ENTRY_MIN_SCORE="$ms" \
  REVO_ENTRY_DISCOUNT_MIN_PCT="$disc" \
  REVO_ENTRY_RSI_MAX="$rsi" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$TR" --cache none --export trades >/dev/null 2>&1
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ -z "$zip" ]; then
    echo "{\"min_score\":\"$ms\",\"discount\":\"$disc\",\"rsi_max\":\"$rsi\",\"error\":\"no_export\"}" >> "$OUT"
  else
    python3 /freqtrade/user_data/scripts/revo_extract.py "$zip" "$ms" "$disc" "$rsi" >> "$OUT"
  fi
}

STAGE="${STAGE:-1}"
if [ "$STAGE" = "1" ]; then
  for ms in 6 7 8 9 10; do
    run_one "$ms" 2.5 45
  done
else
  BEST_MS="${BEST_MS:-8}"
  for disc in 2.0 2.5 3.0 3.5; do
    for rsi in 35 40 45; do
      run_one "$BEST_MS" "$disc" "$rsi"
    done
  done
fi

echo "SWEEP DONE stage=$STAGE" >&2
