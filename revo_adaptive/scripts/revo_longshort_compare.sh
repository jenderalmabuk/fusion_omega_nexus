#!/bin/bash
# Long-only vs Long+Short A/B over the full 40-day range, current-live params.
# Same gates for both; the ONLY difference is whether shorts are allowed.
set -u
CFG="/freqtrade/user_data/configs/config.bybit.backtest.static.json"
TR="20260601-20260710"
COMMON="REVO_ENTRY_MIN_SCORE=9 REVO_ENTRY_DISCOUNT_MIN_PCT=3.5 REVO_ENTRY_DISCOUNT_MAX_PCT=6 REVO_ENTRY_RSI_MAX=40 REVO_LIQ_MODE=med48 REVO_MIN_QVOL_5M=50000 REVO_LOSS_COOLDOWN_HOURS=12"

run() {
  local name="$1" strat="$2"
  echo "========== $name ($strat) =========="
  env $COMMON freqtrade backtesting --config "$CFG" --strategy "$strat" \
    --timerange "$TR" --cache none 2>&1 \
    | grep -iE "revo_long |revo_short |^\W+TOTAL |Long / Short|Total profit %|Absolute profit|Profit factor|Max % of account|Total/Daily Avg Trades|CAGR|Sharpe|no leverage|impossible" \
    | head -30
  echo
}

run "LONG_ONLY"   RevoAdaptiveBacktest
run "LONG_SHORT"  RevoAdaptiveBacktestBoth
echo "LONGSHORT COMPARE DONE"
