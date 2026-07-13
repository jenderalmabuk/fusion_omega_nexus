#!/bin/bash
# Decisive falling-knife floor sweep at MIN_SCORE=9 — the backtest population
# that represents LIVE behavior (live MS=7 == backtest MS=9 because proxy mode
# grants +3 free flow points). This is where the offline reconstruction showed
# the -7% floor lifts net +114.9 -> +172.7. Confirm under the real engine.
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktest"
TR="20260601-20260710"
OUT="/freqtrade/user_data/logs/revo_dmax_sweep_ms9.jsonl"
RESDIR="/freqtrade/user_data/backtest_results"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

run_one() {
  local dmax="$1"
  echo ">>> RUN ms9 discount_max=$dmax" >&2
  REVO_ENTRY_MIN_SCORE=9 \
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
    python3 /freqtrade/user_data/scripts/revo_extract.py "$zip" 9 3.5 40 \
      | python3 -c "import sys,json;d=json.loads(sys.stdin.read());d['discount_max']='$dmax';print(json.dumps(d))" >> "$OUT"
  fi
}

for dmax in 999 9 8 7 6 5; do
  run_one "$dmax"
done
echo "DMAX MS9 SWEEP DONE" >&2
