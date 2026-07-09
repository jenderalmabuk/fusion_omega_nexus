#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="/home/fusion_omega/revo_adaptive"
cd "$REPO_DIR" || exit 1

TS="$(date -u +%Y%m%d_%H%M%S)"
OUT="F2S_B_RECOVER_DEFAULT100_BASELINE_${TS}.txt"
RUNTIME="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"

{
echo "F2S_B_RECOVER_DEFAULT100_BASELINE"
echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "=== STOP OLD F2C LOOP ==="
tmux kill-session -t f2c_bybit_scanner 2>/dev/null || true
echo "old_loop_stopped_or_not_found=1"
echo

echo "=== START F2C TOP100 WITH F2K ON ==="
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
sleep 80

echo "=== TMUX STATUS ==="
tmux list-sessions 2>/dev/null | grep -E "f2c_bybit_scanner|^7:|^8:|^9:" || true
echo

echo "=== F2C VALIDATION ==="
python3 scripts/revo_validate_f2c_bybit_scanner_freshness.py \
  --runtime-dir "$RUNTIME" \
  --max-age-sec 420 || true
echo

echo "=== F2K VALIDATION ==="
python3 scripts/revo_validate_f2k_sticky_hygiene.py \
  --runtime-dir "$RUNTIME" \
  --expect-enabled || true
echo

echo "=== BTC CONTEXT AUDIT ==="
python3 - <<'PY'
import json
from pathlib import Path

p = Path("user_data/revo_alpha/runtime/bybit/btc_context_v135.json")
print("path=", p)
print("exists=", p.exists(), "size=", p.stat().st_size if p.exists() else 0)
if p.exists():
    try:
        d = json.loads(p.read_text())
        print("keys=", list(d.keys()))
        for k in sorted(d.keys()):
            if "mode" in k.lower() or "scanner" in k.lower() or "chop" in k.lower() or "policy" in k.lower():
                print(f"{k}={d.get(k)}")
        text = p.read_text()
        print("contains_DEFENSIVE_CHOP=", "DEFENSIVE_CHOP" in text)
        print("contains_F1I=", "F1I" in text)
    except Exception as e:
        print("READ_ERROR", e)
PY
echo

echo "=== F2K COMPACT HEAD ==="
grep -E "enabled=|writes_pairlist=|before_count=|after_count=|drop_count=|KEEP|DROP|F2K_STICKY_HYGIENE" \
  user_data/revo_alpha/runtime/bybit/F2K_STICKY_HYGIENE_COMPACT.txt 2>/dev/null | head -80 || true
echo

echo "=== TOP ENGINE CONFIRM ==="
grep -E "TOP100_FLOW_ENGINE|top=|top_rows=|flow_ready_count" \
  user_data/revo_alpha/runtime/bybit/F2C_LAST_CYCLE.out \
  user_data/revo_alpha/runtime/bybit/TOP100_FLOW_ENGINE_COMPACT.txt \
  2>/dev/null | tail -80 || true

} | tee "$OUT"

echo
echo "COMPACT_FILE=$OUT"
