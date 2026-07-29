#!/bin/bash
# ATR-based SL ablation: fixed vs atr1..atr3
# Uses RevoAdaptiveBacktestATRSL (pure long-MR gates)
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktestATRSL"
OUT="/freqtrade/user_data/logs/revo_atr_sl_ablation.jsonl"
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
  local name="$1" mode="$2" mult="$3" tr="$4"
  echo ">>> RUN $name mode=$mode mult=$mult tr=$tr" >&2
  REVO_SL_MODE="$mode" REVO_SL_ATR_MULT="$mult" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$tr" --cache none --export trades >/tmp/ft_atr_${name}.log 2>&1
  local rc=$?
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ $rc -ne 0 ] || [ -z "$zip" ]; then
    echo "{\"scenario\":\"$name\",\"sl_mode\":\"$mode\",\"atr_mult\":\"$mult\",\"timerange\":\"$tr\",\"error\":\"backtest_failed_rc_$rc\"}" >> "$OUT"
    tail -n 20 /tmp/ft_atr_${name}.log >&2 || true
  else
    python3 "$EXTRACT" "$zip" "$REVO_ENTRY_MIN_SCORE" "$REVO_ENTRY_DISCOUNT_MIN_PCT" "$REVO_ENTRY_RSI_MAX" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
d['scenario']='$name'; d['sl_mode']='$mode'; d['atr_mult']='$mult'; d['timerange']='$tr'
print(json.dumps(d))
" >> "$OUT"
  fi
}

# IS/OOS/FULL × SL modes
for TR_NAME_TR in "is:20260601-20260625" "oos:20260625-20260710" "full:20260601-20260710"; do
  TR_NAME="${TR_NAME_TR%%:*}"
  TR="${TR_NAME_TR#*:}"
  for SL in "fixed:0" "atr1:1.0" "atr1_5:1.5" "atr2:2.0" "atr2_5:2.5" "atr3:3.0"; do
    MODE="${SL%%:*}"
    MULT="${SL#*:}"
    run_one "${TR_NAME}_${MODE}" "$MODE" "$MULT" "$TR"
  done
done

echo "ATR SL ABLATION DONE -> $OUT" >&2
cat "$OUT"