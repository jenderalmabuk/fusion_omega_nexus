#!/usr/bin/env python3
"""Cross-sectional momentum on DAILY data, ~51 pairs (breadth).
Long top-K / short bottom-K by past return, hold H days, non-overlapping.
Honest stats + random baseline + reversal variant."""
import os, glob
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data_d")
LOOKBACKS = [30, 60, 90]
HOLDS = [7, 14, 30]
K = 10
rng = np.random.default_rng(7)

closes = {}
for p in glob.glob(f"{DATA}/*.csv"):
    s = pd.read_csv(p)
    closes[os.path.basename(p)[:-4]] = pd.Series(s["close"].values, index=s["ts"].values)
M = pd.DataFrame(closes).sort_index()
A = M.values
ts = M.index.values
n, npair = A.shape


def run(L, H, mode="mom"):
    sp = []
    for i in range(L, n - H, H):
        past = A[i] / A[i - L] - 1.0
        fwd = A[i + H] / A[i] - 1.0
        valid = ~(np.isnan(past) | np.isnan(fwd))
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        pv = past[idx]
        if mode == "rand":
            order = idx[rng.permutation(len(idx))]
        else:
            order = idx[np.argsort(pv)]
        longs, shorts = order[-K:], order[:K]
        s = fwd[longs].mean() - fwd[shorts].mean()
        sp.append((-s if mode == "rev" else s) * 100)
    return np.array(sp)


def stat(s):
    if len(s) == 0:
        return (0, 0, 0, 0, 0)
    wr = (s > 0).mean() * 100
    sh = s.mean() / s.std() if s.std() else 0
    pf = s[s > 0].sum() / abs(s[s < 0].sum()) if (s < 0).any() else 99
    return len(s), s.mean(), wr, sh, pf


print(f"Daily cross-sectional | {npair} pairs | K={K}/side | non-overlapping holds\n")
print(f"{'LB':>4s} {'H':>4s} {'n':>4s} {'meanSpread%':>11s} {'WR%':>6s} {'Sharpe/reb':>10s} {'PF':>6s}")
print("-" * 54)
for L in LOOKBACKS:
    for H in HOLDS:
        n_, m, wr, sh, pf = stat(run(L, H))
        flag = "  <==" if pf >= 1.5 and m > 0 and n_ >= 25 else ""
        print(f"{L:4d} {H:4d} {n_:4d} {m:11.3f} {wr:6.1f} {sh:10.3f} {pf:6.2f}{flag}")

print("\nMOMENTUM vs RANDOM vs REVERSAL (L=30,H=14):")
for mode in ("mom", "rand", "rev"):
    n_, m, wr, sh, pf = stat(run(30, 14, mode))
    print(f"  {mode:5s}: n={n_} meanSpread%={m:.3f} WR%={wr:.1f} Sharpe={sh:.3f} PF={pf:.2f}")
