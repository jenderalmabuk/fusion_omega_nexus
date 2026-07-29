#!/bin/bash
# Exit thesis ablation: roi_steps vs trail_atr vs trail_pct vs time_max vs partial_50 vs partial_33
# Uses RevoAdaptiveBacktestExitThesis (ATR3 SL base, no regime filter)
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktestExitThesis"
OUT="/freqtrade/user_data/logs/revo_exit_thesis_ablation.jsonl"
RESDIR="/freqtrade/user_data/backtest_results"
EXTRACT="/freqtrade/user_data/scripts/revo_extract.py"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

export REVO_ENTRY_MIN_SCORE="${REVO_ENTRY_MIN_SCORE:-9}"
export REVO_ENTRY_DISCOUNT_MIN_PCT="${REVO_ENTRY_DISCOUNT_MIN_PCT:-3.5}"
export REVO_ENTRY_DISCOUNT_MAX_PCT="${REVO_ENTRY_DISCOUNT_MAX_PCT:-6}"
export REVO_ENTRY_RSI_MAX="${REVO_ENTRY_RSI_MAX:-40}"
export REVO_LOSS_COOLDOWN_HOURS="${REVO_LOSS_COOLDOWN_HOURS:-12}"
export REVO_LIQ_MODE="${REVO_LIQ_MODE:-med48}"
export REVO_MIN_QVOL_5M="${REVO_MIN_QVOL_5M:-50000}"
export REVO_ATR_PCT_MAX="${REVO_ATR_PCT_MAX:-4.0}"
export REVO_ER_CHOP_MAX="${REVO_ER_CHOP_MAX:-0.15}"

run_one() {
  local name="$1" exit_mode="$2" trail_atr_mult="$3" trail_pct="$4" max_hold_hours="$5" tr="$6"
  echo ">>> RUN $name exit=$exit_mode trail_atr=$trail_atr_mult trail_pct=$trail_pct max_hold=$max_hold_hours tr=$tr" >&2
  REVO_EXIT_MODE="$exit_mode" REVO_TRAIL_ATR_MULT="$trail_atr_mult" REVO_TRAIL_PCT="$trail_pct" REVO_MAX_HOLD_HOURS="$max_hold_hours" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$tr" --cache none --export trades >/tmp/ft_exit_${name}.log 2>&1
  local rc=$?
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ $rc -ne 0 ] || [ -z "$zip" ]; then
    echo "{\"scenario\":\"$name\",\"exit_mode\":\"$exit_mode\",\"trail_atr_mult\":\"$trail_atr_mult\",\"trail_pct\":\"$trail_pct\",\"max_hold_hours\":\"$max_hold_hours\",\"timerange\":\"$tr\",\"error\":\"backtest_failed_rc_$rc\"}" >> "$OUT"
    tail -n 20 /tmp/ft_exit_${name}.log >&2 || true
  else
    python3 "$EXTRACT" "$zip" "$REVO_ENTRY_MIN_SCORE" "$REVO_ENTRY_DISCOUNT_MIN_PCT" "$REVO_ENTRY_RSI_MAX" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
d['scenario']='$name'; d['exit_mode']='$exit_mode'; d['trail_atr_mult']='$trail_atr_mult'; d['trail_pct']='$trail_pct'; d['max_hold_hours']='$max_hold_hours'; d['timerange']='$tr'
print(json.dumps(d))
" >> "$OUT"
  fi
}

# IS/OOS/FULL × exit modes
for TR_NAME_TR in "is:20260601-20260625" "oos:20260625-20260710" "full:20260601-20260710"; do
  TR_NAME="${TR_NAME_TR%%:*}"
  TR="${TR_NAME_TR#*:}"
  # roi_steps (baseline)
  run_one "${TR_NAME}_roi_steps" "roi_steps" "2.0" "0.015" "6" "$TR"
  # trail_atr
  run_one "${TR_NAME}_trail_atr2" "trail_atr" "2.0" "0.015" "6" "$TR"
  run_one "${TR_NAME}_trail_atr3" "trail_atr" "3.0" "0.015" "6" "$TR"
  # trail_pct
  run_one "${TR_NAME}_trail_pct15" "trail_pct" "2.0" "0.015" "6" "$TR"
  run_one "${TR_NAME}_trail_pct20" "trail_pct" "2.0" "0.020" "6" "$TR"
  # time_max
  run_one "${TR_NAME}_time_4h" "time_max" "2.0" "0.015" "4" "$TR"
  run_one "${TR_NAME}_time_6h" "time_max" "2.0" "0.015" "6" "$TR"
  run_one "${TR_NAME}_time_8h" "time_max" "2.0" "0.015" "8" "$TR"
  # partial_50
  run_one "${TR_NAME}_partial_50" "partial_50" "2.0" "0.015" "6" "$TR"
  # partial_33
  run_one "${TR_NAME}_partial_33" "partial_33" "2.0" "0.015" "6" "$TR"
done

echo "EXIT THESIS ABLATION DONE -> $OUT" >&2
cat "$OUT"