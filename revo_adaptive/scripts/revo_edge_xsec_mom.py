#!/usr/bin/env python3
"""Cross-sectional momentum edge test on 12 pairs (1H).
Rank by past return (lookback L), long top-K / short bottom-K, hold H bars.
Measure long-short spread forward return. Compares vs random basket baseline."""
import os, glob
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data")
LOOKBACKS = [72, 168, 336]     # 3d, 7d, 14d
HOLDS = [24, 72, 168]          # 1d, 3d, 7d
K = 3                          # legs per side
rng = np.random.default_rng(7)

# aligned close matrix
closes = {}
for p in glob.glob(f"{DATA}/*.csv"):
    df = pd.read_csv(p).set_index("ts")["close"]
    closes[os.path.basename(p)[:-4]] = df
M = pd.DataFrame(closes).dropna()
pairs = list(M.columns)
arr = M.values
n, npair = arr.shape


def run(L, H, randomize=False):
    spreads = []
    step = H  # non-overlapping rebalances
    for i in range(L, n - H, step):
        past = arr[i] / arr[i - L] - 1.0
        fwd = arr[i + H] / arr[i] - 1.0
        if randomize:
            order = rng.permutation(npair)
        else:
            order = np.argsort(past)          # ascending: losers first
        longs = order[-K:]                    # top momentum
        shorts = order[:K]                    # bottom momentum
        spread = fwd[longs].mean() - fwd[shorts].mean()
        spreads.append(spread * 100)
    return np.array(spreads)


def stats(s):
    if len(s) == 0:
        return (0, 0, 0, 0, 0)
    wr = (s > 0).mean() * 100
    sharpe = s.mean() / s.std() if s.std() else 0
    pf = s[s > 0].sum() / abs(s[s < 0].sum()) if (s < 0).any() else 99
    return len(s), s.mean(), wr, sharpe, pf


print(f"Cross-sectional momentum | {npair} pairs 1H | K={K} per side | non-overlapping\n")
print(f"{'lookback':>8s} {'hold':>5s} {'n':>4s} {'meanSpread%':>11s} {'WR%':>6s} {'Sharpe':>7s} {'PF':>6s}")
print("-" * 56)
for L in LOOKBACKS:
    for H in HOLDS:
        n_, mean, wr, sh, pf = stats(run(L, H))
        flag = "  <==" if pf >= 1.5 and mean > 0 else ""
        print(f"{L:8d} {H:5d} {n_:4d} {mean:11.3f} {wr:6.1f} {sh:7.3f} {pf:6.2f}{flag}")

# baseline: random baskets at the best-ish config
print("\nBaseline (random baskets, L=168 H=72):")
b = run(168, 72, randomize=True)
n_, mean, wr, sh, pf = stats(b)
print(f"  n={n_} meanSpread%={mean:.3f} WR%={wr:.1f} Sharpe={sh:.3f} PF={pf:.2f}")

# also test REVERSAL (short top / long bottom) — alts often mean-revert short-term
print("\nReversal variant (long losers / short winners):")
print(f"{'lookback':>8s} {'hold':>5s} {'meanSpread%':>11s} {'PF':>6s}")
for L in [72, 168]:
    for H in [24, 72]:
        s = -run(L, H)
        n_, mean, wr, sh, pf = stats(s)
        print(f"{L:8d} {H:5d} {mean:11.3f} {pf:6.2f}")
