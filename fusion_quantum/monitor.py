#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "runtime/fusion_quantum/state.json"
JOURNAL = ROOT / "runtime/fusion_quantum/journal/trade_history.json"
GATEWAY = os.getenv("FQ_MONITOR_GATEWAY", "http://127.0.0.1:8790/gateway")
NEXUS = os.getenv("FQ_MONITOR_NEXUS", "http://127.0.0.1:8000")
TOKEN = os.getenv("FQ_GATEWAY_TOKEN") or os.getenv("GATEWAY_TOKEN", "nexus_gateway_test_2024")
REFRESH = max(2, int(os.getenv("FQ_MONITOR_REFRESH", "10")))


def get_json(url: str, auth: bool = False):
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def mark(symbol: str) -> float:
    try:
        data = get_json(f"{NEXUS}/klines/binance/{symbol}?tf=1m&limit=1")
        bars = data.get("data", []) if isinstance(data, dict) else data
        return float(bars[-1]["close"]) if bars else 0.0
    except Exception:
        return 0.0


def money(value: float) -> str:
    return f"${value:+,.2f}"


def age(iso: str) -> str:
    opened = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    seconds = max(0, int((datetime.now(timezone.utc) - opened).total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    return f"{days}d {hours:02}:{minutes:02}" if days else f"{hours:02}:{minutes:02}"


def snapshot() -> str:
    portfolio = get_json(f"{GATEWAY}/portfolio", auth=True)
    state = load_json(STATE, {"setups": {}})
    trades = load_json(JOURNAL, [])
    pending = sum(row.get("status") == "pending" for row in state.get("setups", {}).values())
    wins = [t for t in trades if float(t.get("pnl_usd", 0)) > 0]
    losses = [t for t in trades if float(t.get("pnl_usd", 0)) < 0]
    realized_profit = sum(float(t.get("pnl_usd", 0)) for t in wins)
    realized_loss = sum(float(t.get("pnl_usd", 0)) for t in losses)
    realized = realized_profit + realized_loss
    positions = portfolio.get("open_positions", [])
    rows = []
    unrealized_total = 0.0
    for pos in positions:
        entry = float(pos.get("entry_price", 0))
        current = mark(pos["symbol"])
        notional = float(pos.get("notional", 0))
        sign = 1 if pos.get("side") == "LONG" else -1
        pnl = sign * notional * (current - entry) / entry if entry and current else 0.0
        unrealized_total += pnl
        rows.append((pos["symbol"], pos.get("side", "?"), age(pos["opened_at"]), current, pnl))
    balance = float(portfolio.get("equity") or 0)
    lines = [
        "FUSION QUANTUM — PAPER MAINNET",
        f"Updated                 {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "=" * 78,
        f"Running positions       {len(positions)} / 20",
        f"Pending entry limits    {pending} / unlimited",
        f"Closed recorded         {len(trades)}",
        f"Closed win / loss       {len(wins)} / {len(losses)}",
        f"Realized PnL            {money(realized)}",
        f"  Profit realized       {money(realized_profit)}",
        f"  Loss realized         {money(realized_loss)}",
        f"Unrealized PnL          {money(unrealized_total)}",
        f"Balance                 ${balance:,.2f}",
        "-" * 78,
        f"{'PAIR':16} {'SIDE':6} {'RUN TIME':10} {'MARK':16} {'UNREAL PNL':>12}",
    ]
    if not rows:
        lines.append("No running positions")
    for symbol, side, runtime, current, pnl in rows:
        lines.append(f"{symbol:16} {side:6} {runtime:10} {current:<16.10g} {money(pnl):>12}")
    lines += ["-" * 78, "Ctrl-C stop dashboard | tmux detach: Ctrl-b d"]
    return "\n".join(lines)


def main() -> int:
    once = "--once" in sys.argv
    while True:
        try:
            text = snapshot()
        except Exception as exc:
            text = f"Fusion Quantum monitor error: {exc!r}"
        if once:
            print(text)
            return 0
        print("\033[2J\033[H" + text, flush=True)
        time.sleep(REFRESH)


if __name__ == "__main__":
    raise SystemExit(main())
