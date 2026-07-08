#!/usr/bin/env python3
"""Nexus bot report — compact terminal report like fusionnew clean_core/report.py."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "runtime" / "state"
CONFIG = ROOT / "bots" / "config.yaml"
UNIVERSE = ROOT / "bots" / "universe.txt"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def pnl(t: dict) -> float:
    return float(t.get("pnl") or t.get("pnl_usdt") or 0.0)


def age(ts) -> str:
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)) or str(ts).replace(".", "", 1).isdigit():
            sec = time.time() - float(ts)
        else:
            d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            sec = datetime.now(timezone.utc).timestamp() - d.timestamp()
        if sec < 3600:
            return f"{sec/60:.0f}m"
        if sec < 86400:
            return f"{sec/3600:.1f}h"
        return f"{sec/86400:.1f}d"
    except Exception:
        return "?"


def status_line(name: str) -> str:
    try:
        import subprocess
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Status}} {{.State.StartedAt}}", name],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return out
    except Exception:
        return "unknown"


def summarize(path: Path) -> dict:
    s = load_json(path)
    trades = s.get("trades", [])
    closed = [t for t in trades if t.get("status") == "CLOSED"] or s.get("closed", [])
    open_ = [t for t in trades if t.get("status") == "OPEN"]
    pending = [t for t in trades if t.get("status") == "PENDING"]
    wins = [t for t in closed if pnl(t) > 0]
    return {
        "path": path,
        "trades": trades,
        "open": open_,
        "pending": pending,
        "closed": closed,
        "wins": wins,
        "net": sum(pnl(t) for t in closed),
        "nearest": s.get("_symbol_nearest", {}),
        "seen": s.get("seen", []),
        "risk": s.get("risk", {}),
    }


def print_trade(t: dict) -> None:
    side = t.get("side") or t.get("imb_side") or "?"
    sym = t.get("symbol", "?")
    entry = float(t.get("entry") or 0)
    sl = float(t.get("sl") or 0)
    tp = float(t.get("tp") or 0)
    a = age(t.get("opened_at") or t.get("created_at") or t.get("ts"))
    print(f"  {CYAN}{sym:<14}{RESET} {side:<4} entry={entry:.6g} sl={sl:.6g} tp={tp:.6g} age={a}")


def main() -> None:
    pairs = UNIVERSE.read_text().split() if UNIVERSE.exists() else []
    files = sorted(STATE.glob("engine_state_*.json"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print(f"{BOLD}{CYAN}╔══════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║          NEXUS M30/H1 BOT REPORT            ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════╝{RESET}")
    print(f"{DIM}{now}{RESET}")
    print()

    print(f"{BOLD}🧩 SERVICES{RESET}")
    for c in ["nexus_m30_imbalance", "nexus_h1_imbalance", "nexus_fastapi", "nexus_timescaledb"]:
        st = status_line(c)
        ok = "running" in st or "healthy" in st
        print(f"  {'✅' if ok else '⚠️ '} {c:<22} {st}")
    print()

    print(f"{BOLD}🌐 UNIVERSE & CONFIG{RESET}")
    print(f"  Pairs: {len(pairs)}")
    print("  M30: both medium • stoch≤50 • min-turn≥500k • adversarial Gemini ON")
    print("  H1 : both medium • stoch≤50 • min-turn≥500k • adversarial Gemini ON")
    print()

    if not files:
        print(f"{YELLOW}No state files yet in {STATE}{RESET}")
        return

    print(f"{BOLD}📊 ENGINE STATE{RESET}")
    for path in files:
        r = summarize(path)
        name = path.stem.replace("engine_state_", "")
        closed = len(r["closed"])
        wr = (len(r["wins"]) / closed * 100) if closed else 0.0
        color = GREEN if r["net"] >= 0 else RED
        m_age = age(path.stat().st_mtime)
        print(f"  {BOLD}{name}{RESET}  updated {m_age} ago")
        print(
            f"    open={len(r['open'])} pending={len(r['pending'])} closed={closed} "
            f"wins={len(r['wins'])} WR={wr:.1f}% net={color}{r['net']:+.4f}{RESET}"
        )
        print(f"    tracked={len(r['nearest'])} seen={len(r['seen'])} risk={r['risk']}")
        if r["open"]:
            print(f"    {GREEN}OPEN{RESET}")
            for t in r["open"][-8:]:
                print_trade(t)
        if r["pending"]:
            print(f"    {YELLOW}PENDING{RESET}")
            for t in r["pending"][-8:]:
                print_trade(t)
        if r["closed"]:
            print("    RECENT CLOSED")
            for t in r["closed"][-5:]:
                print(f"      {t.get('symbol','?'):<14} {t.get('side','?'):<4} pnl={pnl(t):+.4f} reason={t.get('reason','?')}")
        print()

    print(f"{BOLD}🔎 NOTES{RESET}")
    print("  • State is persisted in runtime/state and survives container recreate.")
    print("  • Entry alerts use Telegram token/chat from .env.")
    print("  • Live logs: tmux window 5=H1, window 6=M30.")


if __name__ == "__main__":
    main()
