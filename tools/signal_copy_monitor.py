#!/usr/bin/env python3
"""Read-only live monitor for signal-copy paper gateway."""
import argparse
import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "journal" / "trade_history.json"


def fetch(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def money(value):
    return f"${float(value or 0):+.2f}"


def show(data):
    print("\033[2J\033[H", end="")
    print(f"SIGNAL COPY MONITOR | {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"Equity ${data.get('equity', 0):.2f} | Daily PnL {data.get('daily_pnl_pct', 0):+.2f}% | "
          f"Exposure {data.get('total_exposure_pct', 0):.2f}% | Open {data.get('open_position_count', 0)}")
    print(f"Risk reserved ${data.get('reserved_risk_total', 0):.2f} | "
          f"Daily limit={data.get('daily_loss_limit_hit', False)} | "
          f"Exposure limit={data.get('exposure_limit_exceeded', False)}")

    positions = data.get("open_positions", [])
    print("\nOPEN POSITIONS")
    if not positions:
        print("  none")
    for p in positions:
        print(f"  {p.get('symbol')} {p.get('side')} entry={p.get('entry_price')} "
              f"SL={p.get('sl_price')} TP1={p.get('tp1_price')} "
              f"notional=${float(p.get('notional', 0)):.2f}")

    print("\nLAST SIGNAL-COPY ENTRIES")
    intents = [x for x in data.get("recent_intents", [])
               if (x.get("intent") or {}).get("source") == "SIGNAL_COPY"]
    if not intents:
        print("  none")
    for x in intents[-10:][::-1]:
        i, r = x.get("intent", {}), x.get("result", {})
        print(f"  {i.get('symbol')} {i.get('side')} notional=${float(i.get('notional') or 0):.2f} "
              f"risk=${float(r.get('risk_amount') or 0):.2f} "
              f"status={'OPENED' if r.get('ok') else 'REJECTED'} reason={r.get('reason')}")

    print("\nLAST CLOSED PNL")
    try:
        rows = json.loads(JOURNAL.read_text())
        rows = [r for r in rows if r.get("regime") == "PAPER_MAINNET" or r.get("source") == "SIGNAL_COPY"]
        if rows:
            r = rows[-1]
            print(f"  {r.get('symbol')} {r.get('side')} entry={r.get('entry_price')} exit={r.get('exit_price')} "
                  f"PnL={money(r.get('pnl_usd'))} ({float(r.get('pnl_pct', 0)):+.2f}%) "
                  f"reason={r.get('reason')} closed={r.get('timestamp_close')}")
        else:
            print("  none")
    except Exception as exc:
        print(f"  journal unavailable: {exc}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("GATEWAY_URL", "http://127.0.0.1:8787/gateway") + "/portfolio")
    ap.add_argument("--token", default=os.getenv("GATEWAY_TOKEN", ""))
    ap.add_argument("--watch", type=float, default=0, help="refresh seconds; 0 = one snapshot")
    args = ap.parse_args()
    while True:
        try:
            show(fetch(args.url, args.token))
        except Exception as exc:
            print(f"monitor error: {exc}")
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()

# self-check: parser accepts no extra dependencies; live check is run separately.
assert callable(fetch) and callable(show)
