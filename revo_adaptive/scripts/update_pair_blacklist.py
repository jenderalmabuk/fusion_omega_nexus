#!/usr/bin/env python3
"""Update Revo dynamic pair blacklist from Freqtrade paper DB.

Rules:
- loss streak >= REVO_BLACKLIST_LOSS_STREAK -> cooldown blacklist
- total losses >= REVO_BLACKLIST_PERM_LOSSES and net <= REVO_BLACKLIST_PERM_NET -> permanent
- losses >= REVO_BLACKLIST_TOTAL_LOSSES and net <= REVO_BLACKLIST_NET_LOSS -> longer cooldown

Stdlib only. Writes JSON atomically.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


def parse_dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


DB = Path(os.environ.get("REVO_TRADES_DB", "/freqtrade/user_data/tradesv3.paper.sqlite"))
OUT = Path(os.environ.get("REVO_PAIR_BLACKLIST_PATH", "/freqtrade/user_data/local/revo_pair_blacklist.json"))
LOSS_STREAK = env_int("REVO_BLACKLIST_LOSS_STREAK", 3)
TOTAL_LOSSES = env_int("REVO_BLACKLIST_TOTAL_LOSSES", 4)
NET_LOSS = env_float("REVO_BLACKLIST_NET_LOSS", -3.0)
COOLDOWN_HOURS = env_int("REVO_BLACKLIST_COOLDOWN_HOURS", 72)
LONG_COOLDOWN_HOURS = env_int("REVO_BLACKLIST_LONG_COOLDOWN_HOURS", 168)
PERM_LOSSES = env_int("REVO_BLACKLIST_PERM_LOSSES", 6)
PERM_NET = env_float("REVO_BLACKLIST_PERM_NET", -5.0)


def main() -> int:
    now = datetime.now(timezone.utc)
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    trades = list(
        con.execute(
            """
            select pair, close_date, close_profit_abs, exit_reason, is_open
            from trades
            where enter_tag='revo_adaptive_v1' and is_open=0
            order by close_date asc
            """
        )
    )

    by_pair: dict[str, list[sqlite3.Row]] = {}
    for row in trades:
        by_pair.setdefault(row["pair"], []).append(row)

    out: dict[str, dict] = {}
    for pair, rows in by_pair.items():
        pnls = [float(r["close_profit_abs"] or 0.0) for r in rows]
        losses = sum(p < 0 for p in pnls)
        wins = sum(p > 0 for p in pnls)
        net = sum(pnls)
        streak = 0
        for p in reversed(pnls):
            if p < 0:
                streak += 1
            else:
                break
        last_close = parse_dt(rows[-1]["close_date"])

        mode = None
        hours = COOLDOWN_HOURS
        reason = None
        if losses >= PERM_LOSSES and net <= PERM_NET:
            mode = "permanent"
            reason = f"losses={losses} net={net:.4f}"
        elif losses >= TOTAL_LOSSES and net <= NET_LOSS:
            mode = "cooldown"
            hours = LONG_COOLDOWN_HOURS
            reason = f"losses={losses} net={net:.4f}"
        elif streak >= LOSS_STREAK:
            mode = "cooldown"
            reason = f"loss_streak={streak}"

        if not mode:
            continue

        blocked_until = None if mode == "permanent" else (last_close + timedelta(hours=hours)).isoformat()
        if blocked_until and parse_dt(blocked_until) <= now:
            continue

        out[pair] = {
            "mode": mode,
            "reason": reason,
            "losses": losses,
            "wins": wins,
            "trades": len(rows),
            "loss_streak": streak,
            "net": round(net, 8),
            "last_close": last_close.isoformat(),
            "blocked_until": blocked_until,
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(OUT.suffix + ".tmp")
    tmp.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    tmp.replace(OUT)
    print(json.dumps({"pairs": len(out), "path": str(OUT), "pairs_list": sorted(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
