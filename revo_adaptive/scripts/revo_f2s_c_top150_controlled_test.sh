#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
cd "$REPO_DIR" || exit 1

TS="$(date -u +%Y%m%d_%H%M%S)"
OUT="F2S_C_TOP150_CONTROLLED_TEST_${TS}.txt"
RUNTIME="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"

{
echo "F2S_C_TOP150_CONTROLLED_TEST"
echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "=== STOP F2C LOOP TO AVOID RACE ==="
tmux kill-session -t f2c_bybit_scanner 2>/dev/null || true
echo "f2c_loop_stopped_or_not_found=1"
echo

echo "=== BASELINE BEFORE TOP150 ==="
python3 scripts/revo_validate_f2c_bybit_scanner_freshness.py \
  --runtime-dir "$RUNTIME" \
  --max-age-sec 420 || true
echo

python3 scripts/revo_validate_f2k_sticky_hygiene.py \
  --runtime-dir "$RUNTIME" \
  --expect-enabled || true
echo

echo "=== RUN ONE-CYCLE TOP150 WITH F2K ON ==="
export REVO_RUNTIME_DIR="$RUNTIME"
export REVO_RUNTIME_PROFILE=bybit
export REVO_MARKET_SOURCE=BYBIT
export REVO_TOP_UNIVERSE_LIMIT=150
export REVO_STICKY_DROP_NO_TRADE=1
export F2C_MAX_AGE_SEC=420

./scripts/revo_bybit_scanner_loop_f2c.sh once || true
echo

echo "=== VALIDATE AFTER TOP150 ==="
python3 scripts/revo_validate_f2c_bybit_scanner_freshness.py \
  --runtime-dir "$RUNTIME" \
  --max-age-sec 420 || true
echo

python3 scripts/revo_validate_f2k_sticky_hygiene.py \
  --runtime-dir "$RUNTIME" \
  --expect-enabled || true
echo

echo "=== TOP150 SUMMARY ==="
python3 - <<'PY'
import json, time
from pathlib import Path

base = Path("user_data/revo_alpha/runtime/bybit")

flow_p = base / "revo_flow_context.json"
exec_p = base / "revo_execution_context.json"
pair_p = base / "pair_universe_remote.json"
f2k_p = base / "F2K_STICKY_HYGIENE_COMPACT.txt"
top_p = base / "TOP100_FLOW_ENGINE_COMPACT.txt"

def load(p):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None

flow = load(flow_p)
rows = list(flow.values()) if isinstance(flow, dict) else flow if isinstance(flow, list) else []
print("flow_rows=", len(rows))
print("flow_ready=", sum(1 for r in rows if isinstance(r, dict) and r.get("flow_ready") is True))
print("entry_eligible=", sum(1 for r in rows if isinstance(r, dict) and str(r.get("flow_authority")) == "ENTRY_ELIGIBLE"))

dq = {}
for r in rows:
    if isinstance(r, dict):
        dq[str(r.get("data_quality", "UNKNOWN"))] = dq.get(str(r.get("data_quality", "UNKNOWN")), 0) + 1
print("data_quality_counts=", dq)

pair = load(pair_p)
pairs = pair.get("pairs", []) if isinstance(pair, dict) else []
print("pairlist_count=", len(pairs))
print("current_actionable_count=", pair.get("current_actionable_count") if isinstance(pair, dict) else None)
print("sticky_retained_count=", pair.get("sticky_retained_count") if isinstance(pair, dict) else None)
print("f2k_enabled=", pair.get("f2k_sticky_hygiene_enabled") if isinstance(pair, dict) else None)
print("f2k_drop_count=", pair.get("f2k_drop_count") if isinstance(pair, dict) else None)
print("pairs=", pairs)

execd = load(exec_p)
if isinstance(execd, dict):
    print("contract_status=", execd.get("contract_status"))
    print("remote_pair_count=", execd.get("remote_pair_count"))
    print("execution_pair_count=", execd.get("execution_pair_count"))

if top_p.exists():
    print("--- TOP ENGINE COMPACT MARKERS ---")
    for line in top_p.read_text(errors="replace").splitlines():
        if any(k in line for k in ["top_rows=", "flow_ready_count=", "entry", "scanner=", "data_quality"]):
            print(line)

if f2k_p.exists():
    print("--- F2K COMPACT HEAD ---")
    for line in f2k_p.read_text(errors="replace").splitlines()[:80]:
        if any(k in line for k in ["enabled=", "writes_pairlist=", "before_count=", "after_count=", "drop_count=", "KEEP|", "DROP|"]):
            print(line)
PY
echo

echo "=== RESTART F2C LOOP BACK TO SAFE DEFAULT 100 ==="
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
tmux list-sessions 2>/dev/null | grep f2c_bybit_scanner || true
echo

echo "=== DECISION_HINT ==="
echo "If top150 flow_rows=150, flow_ready near 150, data_quality OK dominant, and pairlist_count > baseline 6 with F2K PASS, top150 is promising."
echo "If missing data, validator fail, or pairlist not improved, keep top100."
echo "No entry/gate behavior changed."

} | tee "$OUT"

echo
echo "COMPACT_FILE=$OUT"
