#!/bin/bash
# Gate-rebalance comparison: does widening the discount band + tightening the
# falling-knife floor PRESERVE (or grow) entries while lifting quality?
#
# All runs share the 12h same-pair loss cooldown (now in base class), so the
# delta isolates the discount/rsi levers. Funding gate is NOT testable here
# (pure OHLCV backtest has no flow/funding context -> funding_ok defaults neutral).
#
# Output: one JSON line per scenario to revo_gate_compare_results.jsonl
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktest"
TR="20260601-20260710"
OUT="/freqtrade/user_data/logs/revo_gate_compare_results.jsonl"
RESDIR="/freqtrade/user_data/backtest_results"
EXTRACT="/freqtrade/user_data/scripts/revo_extract.py"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

run_one() {
  local name="$1" ms="$2" disc="$3" dmax="$4" rsi="$5" cd="$6"
  echo ">>> RUN $name (ms=$ms disc_min=$disc disc_max=$dmax rsi=$rsi cooldown=${cd}h)" >&2
  REVO_ENTRY_MIN_SCORE="$ms" \
  REVO_ENTRY_DISCOUNT_MIN_PCT="$disc" \
  REVO_ENTRY_DISCOUNT_MAX_PCT="$dmax" \
  REVO_ENTRY_RSI_MAX="$rsi" \
  REVO_LOSS_COOLDOWN_HOURS="$cd" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$TR" --cache none --export trades >/dev/null 2>&1
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ -z "$zip" ]; then
    echo "{\"scenario\":\"$name\",\"error\":\"no_export\"}" >> "$OUT"
  else
    python3 "$EXTRACT" "$zip" "$ms" "$disc" "$rsi" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
d['scenario']='$name'; d['disc_max']='$dmax'; d['cooldown_h']='$cd'
print(json.dumps(d))" >> "$OUT"
  fi
}

# scenario            ms  disc_min disc_max rsi  cooldown
run_one baseline_live  9   3.5      9        40   12
run_one band_only      9   2.5      9        40   12
run_one knife_only     9   3.5      6        40   12
run_one rsi_only       9   3.5      9        45   12
run_one rebalanced     9   2.5      6        45   12
run_one rebal_nocool   9   2.5      6        45   0

echo "COMPARE DONE" >&2
