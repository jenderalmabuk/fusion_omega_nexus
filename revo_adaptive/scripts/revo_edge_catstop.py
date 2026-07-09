#!/usr/bin/env python3
"""Catastrophe-stop test + monthly profit projection for $ capital.
Universe top-50 by turnover, L=30 H=7 K=10, net 11bps. Uses intra-period
high/low to model a WIDE stop that caps squeeze losses on a leg."""
import glob, os, json, urllib.request
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data_all")
L, H, K = 30, 7, 10
RT = 2 * 0.00055
TOPN = 50
CAP_USD = 500.0
STOPS = [None, 0.50, 0.40, 0.30]   # catastrophe stop levels (fraction); None = no stop

req = urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear",
                             headers={"User-Agent": "edge/1.0"})
turn = {r["symbol"]: float(r.get("turnover24h", 0))
        for r in json.loads(urllib.request.urlopen(req, timeout=25).read())["result"]["list"]}

C, Hi, Lo = {}, {}, {}
for p in glob.glob(f"{DATA}/*.csv"):
    s = pd.read_csv(p); sym = os.path.basename(p)[:-4]
    C[sym] = pd.Series(s["close"].values, index=s["ts"].values)
    Hi[sym] = pd.Series(s["high"].values, index=s["ts"].values)
    Lo[sym] = pd.Series(s["low"].values, index=s["ts"].values)
cols = sorted(C, key=lambda c: turn.get(c + "USDT", turn.get(c, 0)), reverse=True)[:TOPN]
MC = pd.DataFrame({c: C[c] for c in cols}).sort_index()
MH = pd.DataFrame({c: Hi[c] for c in cols}).reindex(MC.index)
ML = pd.DataFrame({c: Lo[c] for c in cols}).reindex(MC.index)
Ac, Ah, Al = MC.values, MH.values, ML.values
n = len(Ac)


def run(stop):
    rets = []
    for i in range(L, n - H, H):
        past = Ac[i] / Ac[i - L] - 1.0
        valid = ~np.isnan(past) & ~np.isnan(Ac[i + H]) & ~np.isnan(Ac[i])
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        order = idx[np.argsort(past[idx])]
        longs, shorts = order[-K:], order[:K]
        lc, sc = [], []
        for j in longs:
            entry = Ac[i, j]; r = Ac[i + H, j] / entry - 1
            if stop is not None:
                lowmin = np.nanmin(Al[i + 1:i + 1 + H, j])
                if lowmin <= entry * (1 - stop):
                    r = -stop
            lc.append(r)
        for j in shorts:
            entry = Ac[i, j]; r = 1 - Ac[i + H, j] / entry   # short profit
            if stop is not None:
                himax = np.nanmax(Ah[i + 1:i + 1 + H, j])
                if himax >= entry * (1 + stop):
                    r = -stop
            sc.append(r)
        rets.append(0.5 * np.mean(lc) + 0.5 * np.mean(sc) - RT)
    return np.array(rets)


def stats(r):
    wr = (r > 0).mean() * 100
    pf = r[r > 0].sum() / abs(r[r < 0].sum()) if (r < 0).any() else 99
    sh = r.mean() / r.std() * np.sqrt(365 / H) if r.std() else 0
    eq = np.cumprod(1 + r)
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100
    return r.mean(), wr, pf, sh, mdd


print(f"Catastrophe-stop test | top-{TOPN} | L={L} H={H} K={K} | {n} bars\n")
print(f"{'stop':>6s} {'ret/reb%':>8s} {'WR%':>5s} {'PF':>5s} {'Sharpe':>6s} {'maxDD%':>6s}")
print("-" * 44)
base = None
for s in STOPS:
    r = run(s)
    m, wr, pf, sh, mdd = stats(r)
    if s is None:
        base = m
    print(f"{('none' if s is None else f'{int(s*100)}%'):>6s} {m*100:8.3f} {wr:5.1f} {pf:5.2f} {sh:6.2f} {mdd:6.1f}")

# monthly projection (use no-stop base mean; ~4.35 rebalances/month)
rpm = 30.0 / H
m = base
monthly = (1 + m) ** rpm - 1
print(f"\n=== PROYEKSI PROFIT — modal ${CAP_USD:.0f} (config no-stop) ===")
print(f"  rebalance/bulan ~ {rpm:.1f}")
print(f"  return rata2/rebalance (net) : {m*100:.3f}%")
print(f"  return rata2/bulan (compound): {monthly*100:.2f}%  -> ~${CAP_USD*monthly:.1f}/bulan")
print(f"  CAGR ~ {((1+m)**(365/H)-1)*100:.0f}%/tahun")
# realism: monthly distribution
r = run(None)
month_rets = []
blk = int(round(rpm))
for i in range(0, len(r) - blk, blk):
    month_rets.append(np.prod(1 + r[i:i + blk]) - 1)
month_rets = np.array(month_rets)
pos = (month_rets > 0).mean() * 100
print(f"\n  Realita (variance tinggi): {len(month_rets)} 'bulan' historis")
print(f"    bulan positif: {pos:.0f}%  | terbaik {month_rets.max()*100:+.1f}% | terburuk {month_rets.min()*100:+.1f}%")
print(f"    median bulanan: {np.median(month_rets)*100:+.2f}% -> ~${CAP_USD*np.median(month_rets):+.0f}")
