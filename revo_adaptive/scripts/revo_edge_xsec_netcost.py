#!/usr/bin/env python3
"""Net-of-cost backtest for cross-sectional momentum long-short.
Strategy return/rebalance = spread/2 (capital split 50/50 long/short).
Costs: full-turnover round-trip taker fee + funding-drag sensitivity.
Reports net PF, WR, annualized Sharpe, compounded equity, maxDD, breakeven cost."""
import os, glob
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data_d")
K = 10
TAKER = 0.00055                 # bybit taker per side
RT_COST = 2 * TAKER             # round-trip per position (full turnover, conservative)
FUNDING_SCEN = [0.0, 0.0005, 0.0010, 0.0015]   # extra drag per rebalance (capital %)
CONFIGS = [(30, 7), (30, 14), (60, 14), (90, 14)]

closes = {}
for p in glob.glob(f"{DATA}/*.csv"):
    s = pd.read_csv(p)
    closes[os.path.basename(p)[:-4]] = pd.Series(s["close"].values, index=s["ts"].values)
M = pd.DataFrame(closes).sort_index()
A, n, npair = M.values, *M.shape[:1], M.shape[1]
n = M.shape[0]


def strat_returns(L, H):
    """Per-rebalance GROSS strategy return (fraction) = spread/2."""
    out = []
    for i in range(L, n - H, H):
        past = A[i] / A[i - L] - 1.0
        fwd = A[i + H] / A[i] - 1.0
        valid = ~(np.isnan(past) | np.isnan(fwd))
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        order = idx[np.argsort(past[idx])]
        longs, shorts = order[-K:], order[:K]
        out.append((fwd[longs].mean() - fwd[shorts].mean()) / 2.0)
    return np.array(out)


def metrics(r, H):
    if len(r) == 0:
        return None
    wr = (r > 0).mean() * 100
    pf = r[r > 0].sum() / abs(r[r < 0].sum()) if (r < 0).any() else 99
    sharpe = (r.mean() / r.std() * np.sqrt(365 / H)) if r.std() else 0
    eq = np.cumprod(1 + r)
    total = (eq[-1] - 1) * 100
    cagr = ((eq[-1]) ** (365 / (H * len(r))) - 1) * 100
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100
    return wr, pf, sharpe, total, cagr, mdd


print(f"Net-of-cost backtest | {npair} pairs daily | K={K}/side | RT taker={RT_COST*100:.3f}%/reb\n")
for (L, H) in CONFIGS:
    g = strat_returns(L, H)
    rpy = 365 / H
    print(f"=== L={L} H={H} | {len(g)} rebalances (~{rpy:.0f}/yr) | gross ret/reb={g.mean()*100:.2f}% ===")
    for fund in FUNDING_SCEN:
        net = g - RT_COST - fund
        m = metrics(net, H)
        if not m:
            continue
        wr, pf, sh, tot, cagr, mdd = m
        cost_bps = (RT_COST + fund) * 1e4
        print(f"  cost={cost_bps:5.1f}bps/reb -> netPF={pf:4.2f} WR={wr:4.1f}% Sharpe(ann)={sh:4.2f} "
              f"CAGR={cagr:6.1f}% maxDD={mdd:4.1f}%")
    # breakeven round-trip cost where mean net = 0
    be = g.mean() * 1e4
    print(f"  breakeven total cost/reb = {be:.1f} bps (gross edge per rebalance)\n")
