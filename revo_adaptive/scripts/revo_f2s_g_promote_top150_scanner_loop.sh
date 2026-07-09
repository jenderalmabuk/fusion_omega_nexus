#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
cd "$REPO_DIR"

TS="$(date -u +%Y%m%d_%H%M%S)"
REPORT="F2S_G_TOP150_PROMOTION_REPORT_${TS}.txt"
RUNTIME="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"

{
echo "F2S_G_TOP150_SCANNER_UNIVERSE_PROMOTION"
echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "promotion=REVO_TOP_UNIVERSE_LIMIT=150"
echo "entry_gate_change=NONE"
echo "roi_sl_tp_leverage_sizing_change=NONE"
echo "vip_change=NONE"
echo

echo "=== STOP CURRENT F2C LOOP ==="
tmux kill-session -t f2c_bybit_scanner 2>/dev/null || true
echo "stopped_or_not_found=1"
echo

echo "=== START F2C LOOP WITH TOP150 ==="
tmux new-session -d -s f2c_bybit_scanner "
cd /home/fusion_omega/revo_adaptive
export REVO_RUNTIME_DIR=/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit
export REVO_RUNTIME_PROFILE=bybit
export REVO_MARKET_SOURCE=BYBIT
export REVO_TOP_UNIVERSE_LIMIT=150
export F2C_LOOP_INTERVAL_SEC=300
export F2C_MAX_AGE_SEC=420
export REVO_STICKY_DROP_NO_TRADE=1
./scripts/revo_bybit_scanner_loop_f2c.sh
"
echo "top150_loop_started=1"
echo

sleep 120

echo "=== VALIDATE TOP150 LOOP ==="
python3 scripts/revo_validate_f2c_bybit_scanner_freshness.py \
  --runtime-dir "$RUNTIME" \
  --max-age-sec 420 \
  --expected-top-n 150

python3 scripts/revo_validate_f2k_sticky_hygiene.py \
  --runtime-dir "$RUNTIME" \
  --expect-enabled

echo
echo "=== RUNTIME SNAPSHOT ==="
python3 - <<'PY'
import json
from pathlib import Path
from collections import Counter

base = Path("user_data/revo_alpha/runtime/bybit")

def load(name):
    p = base / name
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(name, "READ_ERROR", e)
        return None

flow = load("revo_flow_context.json")
rows = list(flow.values()) if isinstance(flow, dict) else flow if isinstance(flow, list) else []
print("flow_rows=", len(rows))
print("flow_ready=", sum(1 for r in rows if isinstance(r, dict) and r.get("flow_ready") is True))
print("entry_eligible=", sum(1 for r in rows if isinstance(r, dict) and str(r.get("flow_authority")) == "ENTRY_ELIGIBLE"))
print("data_quality=", Counter(str(r.get("data_quality", "UNKNOWN")) for r in rows if isinstance(r, dict)).most_common())

pair = load("pair_universe_remote.json")
if isinstance(pair, dict):
    print("pairlist_count=", len(pair.get("pairs", []) or []))
    print("current_actionable_count=", pair.get("current_actionable_count"))
    print("sticky_retained_count=", pair.get("sticky_retained_count"))
    print("f2k_enabled=", pair.get("f2k_sticky_hygiene_enabled"))
    print("f2k_drop_count=", pair.get("f2k_drop_count"))
    print("pairs=", pair.get("pairs", []))
PY

echo
echo "=== DOCKER HEALTH ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" | grep -E "revo_freqtrade_f2_bybit_dynamic_paper|revo_bybit_flow_collector|revo_binance_flow_collector|freqtrade|collector" || true

echo
echo "=== LOOP STATUS ==="
tmux list-sessions | grep f2c_bybit_scanner || true

echo
echo "F2S_G_TOP150_PROMOTION_DONE"
} | tee "$REPORT"

echo
echo "COMPACT_FILE=$REPORT"
