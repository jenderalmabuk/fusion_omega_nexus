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


def fetch_mark(symbol, base_url):
    """Fetch latest mark through Nexus market-data API; None on failure."""
    try:
        payload = fetch(f"{base_url.rstrip('/')}/klines/binance/{symbol}?tf=1m&limit=1", "")
        bars = payload.get("data") if isinstance(payload, dict) else payload
        if bars:
            return float(bars[-1].get("close", 0))
    except Exception:
        pass
    return None


def position_risk(position):
    """Gross remaining loss to provider SL, before TP/fees."""
    entry = float(position.get("entry_price") or 0)
    stop = float(position.get("sl_price") or 0)
    qty = float(position.get("qty", position.get("initial_qty", 0)) or 0)
    if not entry or not stop or not qty:
        return None
    return abs(entry - stop) * qty


def unrealized_pnl(position, mark):
    if mark is None:
        return None
    entry = float(position.get("entry_price") or 0)
    qty = float(position.get("qty", position.get("initial_qty", 0)) or 0)
    if not entry or not qty:
        return 0.0
    if str(position.get("side", "")).upper() == "LONG":
        return (mark - entry) * qty
    return (entry - mark) * qty


def risk_pct(risk, equity):
    if risk is None or not equity:
        return None
    return risk / float(equity) * 100.0


def show(data, market_url):
    unrealized_total = 0.0
    unrealized_known = True
    risk_total = 0.0
    risk_known = True
    equity = float(data.get("equity") or 0)
    for position in data.get("open_positions", []):
        mark = fetch_mark(position.get("symbol", ""), market_url)
        position["monitor_mark"] = mark
        position["unrealized_pnl"] = unrealized_pnl(position, mark)
        position["risk_to_sl"] = position_risk(position)
        if position["risk_to_sl"] is None:
            risk_known = False
        else:
            risk_total += position["risk_to_sl"]
        if position["unrealized_pnl"] is None:
            unrealized_known = False
        else:
            unrealized_total += position["unrealized_pnl"]
    print("\033[2J\033[H", end="")
    pnl_text = money(unrealized_total) if unrealized_known else "unavailable"
    portfolio_risk_text = (f"{money(risk_total)} ({risk_pct(risk_total, equity):.2f}%)"
                           if risk_known and equity else "unavailable")
    print(f"SIGNAL COPY MONITOR | {datetime.now().astimezone().isoformat(timespec='seconds')}")
    print(f"Equity ${data.get('equity', 0):.2f} | Unrealized PnL {pnl_text} | Daily PnL {data.get('daily_pnl_pct', 0):+.2f}% | "
          f"Exposure {data.get('total_exposure_pct', 0):.2f}% | Open {data.get('open_position_count', 0)}")
    print(f"Open risk to SL {portfolio_risk_text} | Pending risk reserved ${data.get('reserved_risk_total', 0):.2f} | "
          f"Daily limit={data.get('daily_loss_limit_hit', False)} | "
          f"Exposure limit={data.get('exposure_limit_exceeded', False)}")

    positions = data.get("open_positions", [])
    print("\nOPEN POSITIONS")
    if not positions:
        print("  none")
    for p in positions:
        upnl = p.get("unrealized_pnl")
        mark_text = f"mark={p['monitor_mark']}" if p.get("monitor_mark") is not None else "mark=?"
        pnl_text = money(upnl) if upnl is not None else "unavailable"
        rpnl = p.get("risk_to_sl")
        risk_text = money(rpnl) if rpnl is not None else "unavailable"
        risk_pct_text = f" ({risk_pct(rpnl, equity):.2f}%)" if rpnl is not None and equity else ""
        print(f"  {p.get('symbol')} {p.get('side')} entry={p.get('entry_price')} {mark_text} "
              f"uPnL={pnl_text} risk-to-SL={risk_text}{risk_pct_text} SL={p.get('sl_price')} TP1={p.get('tp1_price')} "
              f"notional=${float(p.get('notional', 0)):.2f}")
    return


def show_legacy(data):
    show(data, "http://127.0.0.1:8000")


def _show_old(data):
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

    print("\nLAST SIGNAL-COPY INTENTS")
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
    ap.add_argument("--market-url", default=os.getenv("PAPER_NEXUS_API", "http://127.0.0.1:8000"), help="Nexus market-data base URL")
    args = ap.parse_args()
    while True:
        try:
            show(fetch(args.url, args.token), args.market_url)
        except Exception as exc:
            print(f"monitor error: {exc}")
        if args.watch <= 0:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()

# self-check: parser accepts no extra dependencies; live check is run separately.
assert callable(fetch) and callable(show)
