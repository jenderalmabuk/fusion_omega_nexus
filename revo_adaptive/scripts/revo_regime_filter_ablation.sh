#!/bin/bash
# Regime filter ablation: none / btc_4h / breadth / combo
# With best SL from prior (fixed, atr2, atr3) × regime modes
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktestRegimeFilter"
OUT="/freqtrade/user_data/logs/revo_regime_filter_ablation.jsonl"
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
  local name="$1" regime="$2" sl_mode="$3" mult="$4" tr="$5"
  echo ">>> RUN $name regime=$regime sl=$sl_mode mult=$mult tr=$tr" >&2
  REVO_REGIME_MODE="$regime" REVO_SL_MODE="$sl_mode" REVO_SL_ATR_MULT="$mult" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$tr" --cache none --export trades >/tmp/ft_regime_${name}.log 2>&1
  local rc=$?
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ $rc -ne 0 ] || [ -z "$zip" ]; then
    echo "{\"scenario\":\"$name\",\"regime\":\"$regime\",\"sl_mode\":\"$sl_mode\",\"atr_mult\":\"$mult\",\"timerange\":\"$tr\",\"error\":\"backtest_failed_rc_$rc\"}" >> "$OUT"
    tail -n 20 /tmp/ft_regime_${name}.log >&2 || true
  else
    python3 "$EXTRACT" "$zip" "$REVO_ENTRY_MIN_SCORE" "$REVO_ENTRY_DISCOUNT_MIN_PCT" "$REVO_ENTRY_RSI_MAX" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
d['scenario']='$name'; d['regime']='$regime'; d['sl_mode']='$sl_mode'; d['atr_mult']='$mult'; d['timerange']='$tr'
print(json.dumps(d))
" >> "$OUT"
  fi
}

# IS/OOS/FULL × regime modes × best SL modes
for TR_NAME_TR in "is:20260601-20260625" "oos:20260625-20260710" "full:20260601-20260710"; do
  TR_NAME="${TR_NAME_TR%%:*}"
  TR="${TR_NAME_TR#*:}"
  for REGIME in none btc_4h breadth combo; do
    for SL_MULT in "fixed:0" "atr2:2.0" "atr3:3.0"; do
      SL_MODE="${SL_MULT%%:*}"
      MULT="${SL_MULT#*:}"
      run_one "${TR_NAME}_${REGIME}_${SL_MODE}" "$REGIME" "$SL_MODE" "$MULT" "$TR"
    done
  done
done

echo "REGIME FILTER ABLATION DONE -> $OUT" >&2
cat "$OUT"