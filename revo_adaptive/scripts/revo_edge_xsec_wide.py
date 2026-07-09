#!/usr/bin/env python3
"""Cross-sectional momentum on the wide (turnover>=$4M) universe.
Tests several K, net-of-cost, entry frequency, with random control."""
import glob, os
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data_all")
L = 30
HOLDS = [7, 14]
KS = [10, 15, 20]
TAKER = 0.00055
RT = 2 * TAKER
rng = np.random.default_rng(7)

closes = {}
for p in glob.glob(f"{DATA}/*.csv"):
    s = pd.read_csv(p)
    closes[os.path.basename(p)[:-4]] = pd.Series(s["close"].values, index=s["ts"].values)
M = pd.DataFrame(closes).sort_index()
A = M.values
n = M.shape[0]
days = (int(M.index[-1]) - int(M.index[0])) / 86400000


def run(L, H, K, rand=False):
    rets, new_entries, reb = [], 0, 0
    pl = ps = None
    for i in range(L, n - H, H):
        past = A[i] / A[i - L] - 1.0
        fwd = A[i + H] / A[i] - 1.0
        valid = ~(np.isnan(past) | np.isnan(fwd))
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        order = idx[rng.permutation(len(idx))] if rand else idx[np.argsort(past[idx])]
        longs, shorts = set(order[-K:].tolist()), set(order[:K].tolist())
        rets.append((fwd[list(longs)].mean() - fwd[list(shorts)].mean()) / 2.0)
        reb += 1
        new_entries += (2 * K if pl is None else len(longs - pl) + len(shorts - ps))
        pl, ps = longs, shorts
    return np.array(rets), new_entries, reb


def m(r, H, cost):
    net = r - cost
    wr = (net > 0).mean() * 100
    pf = net[net > 0].sum() / abs(net[net < 0].sum()) if (net < 0).any() else 99
    sh = net.mean() / net.std() * np.sqrt(365 / H) if net.std() else 0
    eq = np.cumprod(1 + net)
    cagr = (eq[-1] ** (365 / (H * len(net))) - 1) * 100
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100
    return wr, pf, sh, cagr, mdd


print(f"WIDE universe: {M.shape[1]} pairs, {days:.0f} days | L={L} | RT cost={RT*1e4:.0f}bps\n")
print(f"{'H':>3s} {'K':>3s} {'reb':>4s} {'netPF':>6s} {'WR%':>5s} {'Shrp':>5s} {'CAGR%':>6s} {'maxDD%':>6s} {'entry/day':>9s} {'@reb':>5s}")
print("-" * 70)
for H in HOLDS:
    for K in KS:
        r, ne, reb = run(L, H, K)
        wr, pf, sh, cagr, mdd = m(r, H, RT)
        print(f"{H:3d} {K:3d} {reb:4d} {pf:6.2f} {wr:5.1f} {sh:5.2f} {cagr:6.1f} {mdd:6.1f} "
              f"{ne/days:9.2f} {ne/reb:5.0f}")
# random control at H=14 K=15
rr, _, _ = run(L, 14, 15, rand=True)
wr, pf, sh, cagr, mdd = m(rr, 14, RT)
print(f"\nrandom control (H=14,K=15): netPF={pf:.2f} WR={wr:.1f}% CAGR={cagr:.1f}%")
