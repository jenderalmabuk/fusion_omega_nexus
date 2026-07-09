#!/usr/bin/env python3
"""Asymmetric payoff test: fixed SL=1R, TP at k*R. Even with ~50% direction,
positive-skew trend-following can yield PF>1 if winners run. Reuses 1H setups."""
import os, glob
import numpy as np
import pandas as pd
from revo_edge_test_1h import load, signals  # reuse indicators + signals

DATA = os.path.join(os.path.dirname(__file__), "_edge_data")
SL_PCT = 2.0          # 1R = 2%
TP_MULTS = [1.5, 2.0, 3.0]
HORIZON = 120         # 5 days, room for trend targets
rng = np.random.default_rng(7)


def simulate(highs, lows, closeN, entry, short, sl_pct, k):
    sl = entry * (1 - sl_pct/100) if not short else entry * (1 + sl_pct/100)
    tp = entry * (1 + k*sl_pct/100) if not short else entry * (1 - k*sl_pct/100)
    for h, l in zip(highs, lows):
        if short:
            if h >= sl: return -1.0            # stop first
            if l <= tp: return float(k)        # target first
        else:
            if l <= sl: return -1.0
            if h >= tp: return float(k)
    # neither hit: close at horizon, in R units
    r = (entry - closeN)/entry if short else (closeN - entry)/entry
    return r / (sl_pct/100)


dfs = {os.path.basename(p)[:-4]: load(p) for p in glob.glob(f"{DATA}/*.csv")}
names = list(next(iter(dfs.values())).pipe(signals).keys()) + ["baseline_random"]
agg = {(n, k): [] for n in names for k in TP_MULTS}

for sym, df in dfs.items():
    sig = signals(df)
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    n = len(df)
    esets = {kk: [(i, False) for i in np.where(v[0].fillna(False).values)[0]] +
                 [(i, True) for i in np.where(v[1].fillna(False).values)[0]] for kk, v in sig.items()}
    ridx = rng.choice(np.arange(200, n-HORIZON-1), size=min(300, n-260), replace=False)
    esets["baseline_random"] = [(int(i), bool(rng.integers(0, 2))) for i in ridx]
    for name, entries in esets.items():
        for i, short in entries:
            if i < 200 or i + HORIZON >= n:
                continue
            hs, ls = H[i+1:i+1+HORIZON], L[i+1:i+1+HORIZON]
            for k in TP_MULTS:
                agg[(name, k)].append(simulate(hs, ls, C[i+HORIZON], C[i], short, SL_PCT, k))


def stats(rs):
    rs = np.array(rs)
    wins = rs[rs > 0]; losses = rs[rs < 0]
    wr = len(wins)/len(rs)*100 if len(rs) else 0
    pf = wins.sum()/abs(losses.sum()) if losses.sum() != 0 else 99
    return len(rs), wr, rs.mean(), pf


print(f"Asymmetric test | SL=1R={SL_PCT}% | horizon={HORIZON}h | 12 pairs 1H\n")
print(f"{'setup':18s} {'TP':>4s} {'n':>5s} {'WR%':>6s} {'expR':>7s} {'PF':>6s}")
print("-"*52)
for name in names:
    for k in TP_MULTS:
        n_, wr, exp, pf = stats(agg[(name, k)])
        flag = "  <==" if (pf >= 1.75 and n_ >= 30) else ""
        print(f"{name:18s} {k:4.1f} {n_:5d} {wr:6.1f} {exp:7.3f} {pf:6.2f}{flag}")
