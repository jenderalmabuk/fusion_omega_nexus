#!/usr/bin/env bash
set -uo pipefail

REPO_DIR="/home/fusion_omega/fusion_omega_nexus"
cd "$REPO_DIR" || exit 1

INTERVAL="${1:-60}"
LOG_FILE="revo_adaptive/user_data/revo_adaptive.log"
DB_FILE="revo_adaptive/user_data/tradesv3.revo_adaptive.paper.sqlite"
RUNTIME_DIR="revo_adaptive/user_data/revo_alpha/runtime/bybit"

while true; do
  clear
  echo "============================================================"
  echo "REVO BYBIT PAPER + F2C MONITOR"
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "interval_sec=$INTERVAL"
  echo "============================================================"

  echo
  echo "=== DOCKER STATUS ==="
  docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" \
    | grep -E "revo_adaptive_signal_bybit_paper|revo_binance_flow_collector|revo_freqtrade_e3b_binance_dynamic_paper" || true

  echo
  echo "=== TMUX CORE SESSIONS ==="
  tmux list-sessions 2>/dev/null | grep -E "f2c_bybit_scanner|^7:|^8:|^9:" || true

  echo
  echo "=== F2C FRESHNESS ==="
  python3 /home/fusion_omega/fusion_omega_nexus/revo_adaptive/scripts/revo_validate_f2c_bybit_scanner_freshness.py \
    --runtime-dir "$RUNTIME_DIR" \
    --max-age-sec 420 2>&1 | tail -80

  echo
  echo "=== BYBIT DB QUICK CHECK ==="
  python3 - <<'PY'
import sqlite3
from pathlib import Path

db = Path("revo_adaptive/user_data/tradesv3.revo_adaptive.paper.sqlite")
print("db_exists=", db.exists(), "size=", db.stat().st_size if db.exists() else 0)

if db.exists():
    con = sqlite3.connect(str(db))
    cur = con.cursor()

    for table in ["trades", "orders", "pairlocks"]:
        try:
            print(table, "count=", cur.execute(f"select count(*) from {table}").fetchone()[0])
        except Exception as e:
            print(table, "error=", e)

    try:
        cols = [r[1] for r in cur.execute("pragma table_info(trades)").fetchall()]
        safe_cols = [c for c in [
            "id", "exchange", "pair", "is_open", "open_date", "close_date",
            "open_rate", "close_rate", "stake_amount", "amount",
            "leverage", "is_short", "enter_tag", "exit_reason",
            "close_profit", "realized_profit"
        ] if c in cols]

        if safe_cols:
            q = "select " + ",".join(safe_cols) + " from trades order by id desc limit 8"
            rows = cur.execute(q).fetchall()
            print("recent_trade_columns=", safe_cols)
            if rows:
                for r in rows:
                    print(r)
            else:
                print("NO_TRADES")
    except Exception as e:
        print("recent_trades_error=", e)

    con.close()
PY

  echo
  echo "=== BYBIT IMPORTANT LOG TAIL ==="
  if [ -f "$LOG_FILE" ]; then
    grep -iE "error|exception|traceback|failed|invalid|rejected|precision|minimum|min_notional|rate|timeout|429|too many|pairlist|whitelist|heartbeat|entry|enter|buy|sell|long|short|deny|allow|wallet|order|opened|closed" \
      "$LOG_FILE" | tail -120 || true
  else
    echo "MISSING_LOG_FILE $LOG_FILE"
  fi

  echo
  echo "=== RAW DOCKER LOG LAST 40 ==="
  docker logs --tail=40 revo_adaptive_signal_bybit_paper 2>&1 || true

  echo
  echo "============================================================"
  echo "CTRL-C to stop monitor only. Bot/scanner tetap jalan."
  echo "============================================================"

  sleep "$INTERVAL"
done
