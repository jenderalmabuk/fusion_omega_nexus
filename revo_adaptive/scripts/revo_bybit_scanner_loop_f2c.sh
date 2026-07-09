#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="${REVO_REPO_DIR:-/home/fusion_omega/revo_adaptive}"
cd "$REPO_DIR" || exit 2

export REVO_RUNTIME_DIR="${REVO_RUNTIME_DIR:-$REPO_DIR/user_data/revo_alpha/runtime/bybit}"
export REVO_RUNTIME_PROFILE="${REVO_RUNTIME_PROFILE:-bybit}"
export REVO_MARKET_SOURCE="${REVO_MARKET_SOURCE:-BYBIT}"
export REVO_TOP_UNIVERSE_LIMIT="${REVO_TOP_UNIVERSE_LIMIT:-200}"

INTERVAL="${F2C_LOOP_INTERVAL_SEC:-300}"
MAX_AGE="${F2C_MAX_AGE_SEC:-360}"

mkdir -p "$REVO_RUNTIME_DIR"

LOG_FILE="${F2C_LOG_FILE:-$REVO_RUNTIME_DIR/F2C_BYBIT_SCANNER_LOOP.log}"
LAST_OUT="$REVO_RUNTIME_DIR/F2C_LAST_CYCLE.out"
COMPACT="$REVO_RUNTIME_DIR/F2C_BYBIT_SCANNER_LOOP_COMPACT.txt"

run_cycle() {
  local cycle
  cycle="$(date -u +%Y%m%dT%H%M%SZ)"
  local rc=0

  {
    echo "F2C_BYBIT_SCANNER_LOOP_CYCLE"
    echo "cycle=$cycle"
    echo "repo=$REPO_DIR"
    echo "runtime=$REVO_RUNTIME_DIR"
    echo "profile=$REVO_RUNTIME_PROFILE"
    echo "market_source=$REVO_MARKET_SOURCE"
    echo "top_universe_limit=$REVO_TOP_UNIVERSE_LIMIT"
    echo "interval_sec=$INTERVAL"

    echo "=== STEP 1: BTC MODE ROUTER ==="
    python3 user_data/revo_alpha/tools/revo_btc_mode_router_v135.py \
      --runtime-dir "$REVO_RUNTIME_DIR" || rc=1

    echo "=== STEP 2: DYNAMIC UNIVERSE SCANNER ==="
    python3 user_data/revo_alpha/tools/revo_dynamic_universe_scanner_v13.py \
      --runtime-dir "$REVO_RUNTIME_DIR" || rc=1

    echo "=== STEP 3: TOP FLOW ENGINE ==="
    python3 user_data/revo_alpha/tools/revo_top100_flow_engine_v132.py \
      --runtime-dir "$REVO_RUNTIME_DIR" \
      --top-n "$REVO_TOP_UNIVERSE_LIMIT" || rc=1

    echo "=== STEP 3B: EARLY REMOTE PAIRLIST + EXECUTION CONTEXT PUBLISH ==="
    python3 -c "
import json, time
from pathlib import Path
rt = Path('${REVO_RUNTIME_DIR}')
source = rt / 'pair_universe_top100.json'
if not source.exists():
    source = rt / 'pair_universe_remote.json'
data = json.load(open(source)) if source.exists() else {'pairs': []}
raw_pairs = data.get('pairs', []) if isinstance(data, dict) else []
pairs = []
for item in raw_pairs:
    pair = item.get('pair') if isinstance(item, dict) else str(item)
    if pair and pair not in pairs:
        pairs.append(pair)
out = {'pairs': pairs[:200]}
json.dump(out, open(rt / 'pair_universe_freqtrade.json', 'w'), indent=2)
json.dump(out, open(rt / 'freqtrade_pairlist.json', 'w'), indent=2)
exec_ctx = {
    'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'contract_status': 'OK',
    'remote_pair_count': len(pairs),
    'execution_pair_count': len(out['pairs']),
    'source': 'revo_adaptive_scanner_loop_early_publish',
}
json.dump(exec_ctx, open(rt / 'revo_execution_context.json', 'w'), indent=2)
print(f'Early published {len(out[\"pairs\"])} freqtrade pairs from {source.name}; remote_total={len(pairs)}')
" || rc=1

    echo "=== STEP 4: F3A MARKET-WIDE FLOW CACHE (OI/FUNDING) ==="
    python3 scripts/revo_f3a_market_wide_bybit_flow_cache.py \
      --runtime-dir "$REVO_RUNTIME_DIR" \
      --max-pairs "${F3A_MAX_PAIRS:-80}" --fast-max-pairs "${F3A_FAST_MAX_PAIRS:-20}" || true

    echo "=== STEP 5: HYBRID FLOW EVALUATOR ==="
    if [ -s "$REVO_RUNTIME_DIR/pair_universe_sticky_state.json" ]; then
      python3 user_data/revo_alpha/tools/revo_hybrid_flow_evaluator_v1.py \
       --runtime-dir "$REVO_RUNTIME_DIR" \
       --top-n "$REVO_TOP_UNIVERSE_LIMIT" || rc=1
    else
      echo "hybrid_evaluator=SKIP missing pair_universe_sticky_state.json; early_publish already refreshed RemotePairList"
    fi

    echo "=== STEP 6: REMOTE PAIRLIST PUBLISHER ==="
    # Transform hybrid format to RemotePairList-compatible format (simple strings array) when present.
    # If hybrid is absent, keep Step 3B early-published freqtrade_pairlist.json.
    if [ -s "$REVO_RUNTIME_DIR/pair_universe_hybrid.json" ]; then
      python3 -c "
import json, sys
h = json.load(open('${REVO_RUNTIME_DIR}/pair_universe_hybrid.json'))
pairs = [p['pair'] for p in h.get('pairs', []) if isinstance(p, dict) and p.get('pair')]
out = {'pairs': pairs[:200]}
json.dump(out, open('${REVO_RUNTIME_DIR}/pair_universe_freqtrade.json', 'w'), indent=2)
json.dump(out, open('${REVO_RUNTIME_DIR}/freqtrade_pairlist.json', 'w'), indent=2)
print(f'Published {len(out[\"pairs\"])} hybrid pairs to freqtrade', file=sys.stderr)
" || rc=1
    else
      echo "hybrid_publish=SKIP missing pair_universe_hybrid.json; using early published freqtrade_pairlist.json"
    fi

    echo "=== STEP 7: CANONICAL FREEZE ==="
    if [ -s "$REVO_RUNTIME_DIR/revo_flow_context.json" ]; then
      cp -f "$REVO_RUNTIME_DIR/revo_flow_context.json" "$REVO_RUNTIME_DIR/revo_flow_context_canonical.json" || rc=1
      echo "canonical_copy=OK"
    else
      echo "canonical_copy=SKIP missing revo_flow_context.json"
      rc=1
    fi

    echo "=== STEP 8: F2K STICKY HYGIENE ==="
    python3 scripts/revo_f2k_sticky_hygiene_apply.py \
      --runtime-dir "$REVO_RUNTIME_DIR" \
      --apply || true

    echo "=== STEP 9: FRESHNESS VALIDATOR ==="
    python3 scripts/revo_validate_f2c_bybit_scanner_freshness.py \
      --runtime-dir "$REVO_RUNTIME_DIR" \
      --max-age-sec "$MAX_AGE" || rc=1

    echo "=== STEP 10: COVERAGE HEALTH CHECK ==="
    WATCHLIST_SIZE=$(python3 -c "
import json
try:
    d = json.load(open('$REVO_RUNTIME_DIR/pair_universe_remote.json'))
    print(len(d.get('pairs', [])))
except Exception:
    print(0)
" 2>/dev/null || echo "0")
    echo "watchlist_size=$WATCHLIST_SIZE"
    if [ "$WATCHLIST_SIZE" -lt 40 ]; then
      echo "COVERAGE_WARNING watchlist_size=$WATCHLIST_SIZE below_threshold=40"
    fi

    echo "cycle_rc=$rc"
    echo "cycle_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$LAST_OUT" 2>&1

  rc=$?
  cat "$LAST_OUT" | tee -a "$LOG_FILE"

  {
    echo "F2C_BYBIT_SCANNER_LOOP_COMPACT"
    echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "last_cycle=$cycle"
    echo "runtime=$REVO_RUNTIME_DIR"
    echo "last_rc=$rc"
    tail -80 "$LAST_OUT"
  } > "$COMPACT"

  return "$rc"
}

if [ "${1:-}" = "--once" ]; then
  run_cycle
  exit $?
fi

echo "F2C_BYBIT_SCANNER_LOOP_START runtime=$REVO_RUNTIME_DIR interval=$INTERVAL" | tee -a "$LOG_FILE"

while true; do
  run_cycle || true
  sleep "$INTERVAL"
done
