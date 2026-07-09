#!/usr/bin/env python3
"""Delta-neutral funding harvest backtest (long spot + short perp, same coin).
Daily net per active position = funding_collected + basis_pnl(spot_ret - perp_ret).
Enter when trailing-7d annualized funding >= threshold; charge taker fees on enter/exit.
"""
import glob, os
import numpy as np
import pandas as pd

PERP = os.path.join(os.path.dirname(__file__), "_edge_data_d")
SPOT = os.path.join(os.path.dirname(__file__), "_edge_spot")
FUND = os.path.join(os.path.dirname(__file__), "_edge_funding")
SPOT_TAKER, PERP_TAKER = 0.001, 0.00055
RT_COST = 2 * (SPOT_TAKER + PERP_TAKER)        # enter+exit both legs ~0.31%
THRESHOLDS_APY = [0.0, 0.10, 0.20, 0.30]       # annualized trailing funding to enter

# build common daily date index from perp
perp = {}
for p in glob.glob(f"{PERP}/*.csv"):
    s = pd.read_csv(p); perp[os.path.basename(p)[:-4]] = pd.Series(s["close"].values,
        index=pd.to_datetime(s["ts"], unit="ms").dt.date)
P = pd.DataFrame(perp).sort_index()
dates = list(P.index)

# spot aligned
spot = {}
for p in glob.glob(f"{SPOT}/*.csv"):
    s = pd.read_csv(p); spot[os.path.basename(p)[:-4]] = pd.Series(s["close"].values,
        index=pd.to_datetime(s["ts"], unit="ms").dt.date)
S = pd.DataFrame(spot).reindex(P.index)

# funding daily-summed aligned
fund = {}
for p in glob.glob(f"{FUND}/*.csv"):
    fd = pd.read_csv(p); fd["day"] = pd.to_datetime(fd["ts"], unit="ms").dt.date
    fund[os.path.basename(p)[:-4]] = fd.groupby("day")["rate"].sum()
Fdf = pd.DataFrame(fund).reindex(P.index).fillna(0.0)

# coins with all three
coins = [c for c in P.columns if c in S.columns and c in Fdf.columns]
S = S[coins]; Pp = P[coins]; Fd = Fdf[coins]
perp_ret = Pp.pct_change()
spot_ret = S.pct_change()
basis = spot_ret - perp_ret                     # long spot + short perp daily PnL
trail_fund_apy = Fd.rolling(7).mean() * 365      # annualized trailing funding


def run(thr):
    active = (trail_fund_apy >= thr) & Fd.notna() & basis.notna()
    daily_port = []
    n_active_hist = []
    prev = pd.Series(False, index=coins)
    for d in dates:
        if d not in active.index:
            continue
        act = active.loc[d].fillna(False)
        cur = act[act].index.tolist()
        if not cur:
            daily_port.append(0.0); n_active_hist.append(0); prev = act; continue
        # per-coin daily return: funding collected (short perp gets +funding) + basis pnl
        r = (Fd.loc[d, cur] + basis.loc[d, cur]).astype(float)
        # transition costs
        entered = [c for c in cur if not prev.get(c, False)]
        exited = [c for c in prev[prev].index if c not in cur]
        cost = (len(entered) + len(exited)) * (RT_COST / 2) / max(len(cur), 1)
        daily_port.append(r.mean() - cost)
        n_active_hist.append(len(cur))
        prev = act
    arr = np.array(daily_port)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return None
    apy = arr.mean() * 365 * 100
    sh = arr.mean() / arr.std() * np.sqrt(365) if arr.std() else 0
    eq = np.cumprod(1 + arr)
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100
    return apy, sh, mdd, np.mean(n_active_hist), (np.array(n_active_hist) > 0).mean() * 100


print(f"Delta-neutral funding harvest | {len(coins)} coins (spot+perp) | RT cost {RT_COST*100:.2f}%\n")
print(f"{'enter if APY>=':>14s} {'netAPY%':>8s} {'Sharpe':>7s} {'maxDD%':>7s} {'avg#coins':>9s} {'%days deployed':>14s}")
print("-" * 66)
for thr in THRESHOLDS_APY:
    m = run(thr)
    if m:
        apy, sh, mdd, navg, dep = m
        print(f"{thr*100:13.0f}% {apy:8.1f} {sh:7.2f} {mdd:7.1f} {navg:9.1f} {dep:14.0f}")
# gross funding (no basis, no cost) reference at thr=10%
act = (trail_fund_apy >= 0.10)
gross = (Fd[act].stack()).mean() * 365 * 100
print(f"\nRef: gross funding APY of coins above 10% threshold = {gross:.1f}% (before basis & fees)")
