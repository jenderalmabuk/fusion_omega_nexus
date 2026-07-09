#!/usr/bin/env python3
"""RevoSignalStrategy PnL monitor — query freqtrade SQLite DB, print summary."""
import sqlite3, os, sys
from datetime import datetime, timezone

DB_PATH = os.path.expanduser(
    os.environ.get("REVO_SIGNAL_DB",
    "/home/fusion_omega/revo_adaptive/user_data/tradesv3.signal.bybit.paper.sqlite")
)

def get_stats():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()

    # Closed trades
    cur.execute("""
        SELECT COUNT(*),
               COALESCE(SUM(close_profit_abs), 0),
               COALESCE(AVG(close_profit), 0) * 100,
               COALESCE(SUM(CASE WHEN close_profit > 0 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN close_profit < 0 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN close_profit > 0 THEN close_profit_abs ELSE 0 END), 0),
               COALESCE(SUM(CASE WHEN close_profit < 0 THEN ABS(close_profit_abs) ELSE 0 END), 0)
        FROM trades WHERE is_open = 0
    """)
    closed = cur.fetchone()

    # Open trades — sum realized + close_profit_abs (freqtrade stores realized for open)
    cur.execute("""
        SELECT COUNT(*),
               COALESCE(SUM(close_profit_abs), 0),
               COALESCE(SUM(realized_profit), 0)
        FROM trades WHERE is_open = 1
    """)
    open_row = cur.fetchone()
    open_count = open_row[0]
    open_pnl = open_row[1]  # unrealized PnL from last check

    # Recent closed (last 24h)
    cur.execute("""
        SELECT COUNT(*), COALESCE(SUM(close_profit_abs), 0)
        FROM trades WHERE is_open = 0 AND close_date > datetime('now', '-1 day')
    """)
    day = cur.fetchone()

    conn.close()
    return closed, (open_count, open_pnl), day


def format_pnl(usdt):
    sign = "+" if usdt >= 0 else ""
    return f"{sign}{usdt:.2f} USDT"


def main():
    closed, (open_count, open_pnl), day = get_stats()
    total_trades, total_pnl, avg_pct, wins, losses, win_pnl, loss_pnl = closed
    day_trades, day_pnl = day

    pf = (win_pnl / loss_pnl) if loss_pnl > 0 else float('inf')
    wr = (wins / total_trades * 100) if total_trades > 0 else 0

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"RevoSignal PnL — {now}")
    print(f"  Closed: {total_trades:>4d} trades  |  PnL: {format_pnl(total_pnl)}")
    print(f"  PF: {pf:.2f}  |  WR: {wr:.1f}%  |  Wins: {wins}  Losses: {losses}")
    print(f"  Avg: {avg_pct:+.2f}%  |  Win PnL: {format_pnl(win_pnl)}  |  Loss PnL: {format_pnl(loss_pnl)}")
    print(f"  Open:  {open_count:>4d} trades  |  Unreal: {format_pnl(open_pnl)}")
    print(f"  24h:   {day_trades:>4d} trades  |  PnL: {format_pnl(day_pnl)}")

    return 0 if total_pnl >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
