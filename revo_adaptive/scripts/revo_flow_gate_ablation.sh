#!/bin/bash
# Isolated flow-gate ablation: pure long-MR vs scoring vs block_danger vs hard.
# Uses RevoAdaptiveBacktestGateAblation (OHLCV proxy flow) so modes diverge.
# Paper bots untouched. One env change per run only: REVO_FLOW_GATE_MODE.
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
STRAT="RevoAdaptiveBacktestGateAblation"
OUT="/freqtrade/user_data/logs/revo_flow_gate_ablation.jsonl"
RESDIR="/freqtrade/user_data/backtest_results"
EXTRACT="/freqtrade/user_data/scripts/revo_extract.py"
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

# Live-matched entry quality (from paper env)
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
  local name="$1" mode="$2" tr="$3"
  echo ">>> RUN $name mode=$mode tr=$tr" >&2
  REVO_FLOW_GATE_MODE="$mode" \
  freqtrade backtesting --config "$CFG" --strategy "$STRAT" \
    --timerange "$tr" --cache none --export trades >/tmp/ft_ablation_${name}.log 2>&1
  local rc=$?
  local zip
  zip=$(ls -t ${RESDIR}/*.zip 2>/dev/null | head -1)
  if [ $rc -ne 0 ] || [ -z "$zip" ]; then
    echo "{\"scenario\":\"$name\",\"mode\":\"$mode\",\"timerange\":\"$tr\",\"error\":\"backtest_failed_rc_$rc\"}" >> "$OUT"
    tail -n 20 /tmp/ft_ablation_${name}.log >&2 || true
  else
    python3 "$EXTRACT" "$zip" "$REVO_ENTRY_MIN_SCORE" "$REVO_ENTRY_DISCOUNT_MIN_PCT" "$REVO_ENTRY_RSI_MAX" | python3 -c "
import sys,json
d=json.loads(sys.stdin.read())
d['scenario']='$name'; d['mode']='$mode'; d['timerange']='$tr'
print(json.dumps(d))
" >> "$OUT"
  fi
}

# IS ~ Jun1-Jun25 | OOS Jun25-Jul10 | FULL Jun1-Jul10 (matches prior gate compare data)
for TR_NAME_TR in "is:20260601-20260625" "oos:20260625-20260710" "full:20260601-20260710"; do
  TR_NAME="${TR_NAME_TR%%:*}"
  TR="${TR_NAME_TR#*:}"
  for MODE in pure scoring block_danger hard; do
    run_one "${TR_NAME}_${MODE}" "$MODE" "$TR"
  done
done

echo "ABLATION DONE -> $OUT" >&2
cat "$OUT"
