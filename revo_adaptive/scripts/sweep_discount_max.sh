#!/bin/bash
# Sweep the falling-knife floor (REVO_ENTRY_DISCOUNT_MAX_PCT) at LIVE params
# (MIN_SCORE=7, DISCOUNT_MIN=3.5, RSI_MAX=40). Confirms the -7% floor found by
# offline reconstruction holds under the real freqtrade backtest engine.
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktest"
TR="20260601-20260710"
OUT="/freqtrade/user_data/logs/revo_dmax_sweep.jsonl"
RESDIR="/freqtrade/user_data/backtest_results"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

run_one() {
  local dmax="$1"
  echo ">>> RUN discount_max=$dmax" >&2
  REVO_ENTRY_MIN_SCORE=7 \
  REVO_ENTRY_DISCOUNT_MIN_PCT=3.5 \
  REVO_ENTRY_RSI_MAX=40 \
  REVO_ENTRY_DISCOUNT_MAX_PCT="$dmax" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$TR" --cache none --export trades >/dev/null 2>&1
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ -z "$zip" ]; then
    echo "{\"discount_max\":\"$dmax\",\"error\":\"no_export\"}" >> "$OUT"
  else
    # revo_extract emits one json line; splice discount_max in via python
    python3 /freqtrade/user_data/scripts/revo_extract.py "$zip" 7 3.5 40 \
      | python3 -c "import sys,json;d=json.loads(sys.stdin.read());d['discount_max']='$dmax';print(json.dumps(d))" >> "$OUT"
  fi
}

# 999 = disabled (baseline). Then the candidate floors.
for dmax in 999 12 9 8 7 6; do
  run_one "$dmax"
done
echo "DMAX SWEEP DONE" >&2
