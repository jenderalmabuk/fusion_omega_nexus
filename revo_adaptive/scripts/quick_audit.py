#!/usr/bin/env python3
"""Quick audit script for Revo Alpha bot performance."""
import sqlite3
import json
from datetime import datetime, timedelta

DB = "/freqtrade/user_data/tradesv3_revo_v13914f2_bybit_dynamic_watch_promote.dryrun.sqlite"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Overall stats
cur.execute("""
SELECT COUNT(*) as cnt,
    SUM(CASE WHEN close_profit > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN close_profit <= 0 THEN 1 ELSE 0 END) as losses,
    AVG(CASE WHEN close_profit > 0 THEN close_profit ELSE NULL END)*100 as avg_win,
    AVG(CASE WHEN close_profit <= 0 THEN close_profit ELSE NULL END)*100 as avg_loss,
    SUM(close_profit)*100 as total_pct,
    MAX(close_profit)*100 as best_trade,
    MIN(close_profit)*100 as worst_trade
FROM trades WHERE close_date IS NOT NULL
""")
r = cur.fetchone()
wr = r['wins']/r['cnt']*100 if r['cnt'] else 0
pf = abs(r['avg_win']*r['wins']/(r['avg_loss']*r['losses'])) if r['losses'] and r['avg_loss'] else 0

print("=" * 60)
print("REVO ALPHA BOT - PERFORMANCE AUDIT")
print("=" * 60)
print(f"Total Closed Trades: {r['cnt']}")
print(f"Wins: {r['wins']} | Losses: {r['losses']}")
print(f"Win Rate: {wr:.1f}%")
print(f"Avg Win: {r['avg_win']:+.3f}% | Avg Loss: {r['avg_loss']:+.3f}%")
print(f"Profit Factor: {pf:.2f}")
print(f"Total PnL: {r['total_pct']:+.2f}%")
print(f"Best: {r['best_trade']:+.2f}% | Worst: {r['worst_trade']:+.2f}%")

# Last 48h stats
print("\n--- LAST 48H ---")
cur.execute("""
SELECT COUNT(*) as cnt,
    SUM(CASE WHEN close_profit > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN close_profit <= 0 THEN 1 ELSE 0 END) as losses,
    AVG(CASE WHEN close_profit > 0 THEN close_profit ELSE NULL END)*100 as avg_win,
    AVG(CASE WHEN close_profit <= 0 THEN close_profit ELSE NULL END)*100 as avg_loss,
    SUM(close_profit)*100 as total_pct
FROM trades WHERE close_date IS NOT NULL AND close_date > datetime('now', '-48 hours')
""")
r2 = cur.fetchone()
if r2['cnt']:
    wr2 = r2['wins']/r2['cnt']*100
    pf2 = abs(r2['avg_win']*r2['wins']/(r2['avg_loss']*r2['losses'])) if r2['losses'] and r2['avg_loss'] else 99
    print(f"Trades: {r2['cnt']} | WR: {wr2:.1f}%")
    print(f"Avg Win: {r2['avg_win']:+.3f}% | Avg Loss: {r2['avg_loss']:+.3f}%")
    print(f"PF: {pf2:.2f} | PnL: {r2['total_pct']:+.2f}%")

# Exit reason breakdown
print("\n--- EXIT REASON BREAKDOWN ---")
cur.execute("""
SELECT exit_reason, COUNT(*) as cnt,
    SUM(CASE WHEN close_profit > 0 THEN 1 ELSE 0 END) as wins,
    AVG(close_profit)*100 as avg_pct,
    SUM(close_profit)*100 as total_pct
FROM trades WHERE close_date IS NOT NULL
GROUP BY exit_reason ORDER BY cnt DESC
""")
for r in cur.fetchall():
    wr_e = r['wins']/r['cnt']*100 if r['cnt'] else 0
    print(f"  {r['exit_reason']:25s} n={r['cnt']:3d} WR={wr_e:5.1f}% avg={r['avg_pct']:+.2f}% sum={r['total_pct']:+.2f}%")

# Direction breakdown
print("\n--- DIRECTION BREAKDOWN ---")
cur.execute("""
SELECT is_short, COUNT(*) as cnt,
    SUM(CASE WHEN close_profit > 0 THEN 1 ELSE 0 END) as wins,
    AVG(close_profit)*100 as avg_pct,
    SUM(close_profit)*100 as total_pct
FROM trades WHERE close_date IS NOT NULL
GROUP BY is_short
""")
for r in cur.fetchall():
    d = "SHORT" if r['is_short'] else "LONG"
    wr_d = r['wins']/r['cnt']*100 if r['cnt'] else 0
    print(f"  {d:5s} n={r['cnt']:3d} WR={wr_d:5.1f}% avg={r['avg_pct']:+.2f}% sum={r['total_pct']:+.2f}%")

# Big losers analysis (> -1.5%)
print("\n--- BIG LOSERS (> -1.5%) ---")
cur.execute("""
SELECT pair, is_short, close_profit*100 as pct, exit_reason, close_date
FROM trades WHERE close_date IS NOT NULL AND close_profit < -0.015
ORDER BY close_profit ASC LIMIT 15
""")
for r in cur.fetchall():
    d = "SHORT" if r['is_short'] else "LONG"
    print(f"  {r['pair']:22s} {d:5s} {r['pct']:+.2f}% {r['exit_reason']:20s} {r['close_date'][:16]}")

# Currently open trades
print("\n--- OPEN TRADES ---")
cur.execute("""
SELECT pair, is_short, open_rate, open_date, enter_tag
FROM trades WHERE is_open = 1
""")
for r in cur.fetchall():
    d = "SHORT" if r['is_short'] else "LONG"
    print(f"  {r['pair']:22s} {d:5s} @{r['open_rate']:.6f} {r['open_date'][:16]} {r['enter_tag']}")

conn.close()
