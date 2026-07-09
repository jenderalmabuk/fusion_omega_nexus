#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
cd "$REPO_DIR" || exit 1

TS="$(date -u +%Y%m%d_%H%M%S)"
OUT="F2S_E_TOP150_TEMPORARY_TEST_${TS}.txt"
RUNTIME="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"

{
echo "F2S_E_TOP150_TEMPORARY_TEST"
echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "=== STOP DEFAULT100 LOOP ==="
tmux kill-session -t f2c_bybit_scanner 2>/dev/null || true
tmux kill-session -t f2s_top150_temp 2>/dev/null || true
echo "loops_stopped=1"
echo

echo "=== START TEMP TOP150 LOOP ==="
tmux new-session -d -s f2s_top150_temp "
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
echo "top150_temp_started=1"
sleep 170

echo "=== STOP TEMP TOP150 LOOP AFTER CONTROLLED WINDOW ==="
tmux kill-session -t f2s_top150_temp 2>/dev/null || true
echo "top150_temp_stopped=1"
echo

echo "=== VALIDATE TOP150 OUTPUT ==="
python3 scripts/revo_validate_f2c_bybit_scanner_freshness.py \
  --runtime-dir "$RUNTIME" \
  --max-age-sec 420 \
  --expected-top-n 150 || true
echo

python3 scripts/revo_validate_f2k_sticky_hygiene.py \
  --runtime-dir "$RUNTIME" \
  --expect-enabled || true
echo

echo "=== TOP150 SNAPSHOT ==="
python3 - <<'PY'
import json
from pathlib import Path
from collections import Counter

base = Path("user_data/revo_alpha/runtime/bybit")

def load(name):
    p = base / name
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
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

ex = load("revo_execution_context.json")
if isinstance(ex, dict):
    print("contract_status=", ex.get("contract_status"))
    print("remote_pair_count=", ex.get("remote_pair_count"))
    print("execution_pair_count=", ex.get("execution_pair_count"))

print("=== F2K HEAD ===")
p = base / "F2K_STICKY_HYGIENE_COMPACT.txt"
if p.exists():
    for line in p.read_text(errors="replace").splitlines()[:80]:
        if line.startswith(("enabled=", "writes_pairlist=", "before_count=", "after_count=", "drop_count=", "KEEP|", "DROP|")):
            print(line)
PY
echo

echo "=== RESTART SAFE DEFAULT100 LOOP ==="
tmux new-session -d -s f2c_bybit_scanner "
cd /home/fusion_omega/revo_adaptive
export REVO_RUNTIME_DIR=/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit
export REVO_RUNTIME_PROFILE=bybit
export REVO_MARKET_SOURCE=BYBIT
export REVO_TOP_UNIVERSE_LIMIT=100
export F2C_LOOP_INTERVAL_SEC=300
export F2C_MAX_AGE_SEC=420
export REVO_STICKY_DROP_NO_TRADE=1
./scripts/revo_bybit_scanner_loop_f2c.sh
"
sleep 5
tmux list-sessions 2>/dev/null | grep -E "f2c_bybit_scanner|f2s_top150_temp" || true
echo

echo "=== DECISION_HINT ==="
echo "PASS if expected_top_n=150 validator passes, flow_rows between 100-150, flow_ready_ratio >=0.95, OK ratio >=0.90, F2K PASS, and pairlist_count improves versus default100 baseline=5."
echo "If top150 passes and clean pairlist rises materially, next step is 2-cycle Top150 observation, not permanent promotion yet."
echo "No entry/gate behavior changed."

} | tee "$OUT"

echo
echo "COMPACT_FILE=$OUT"
