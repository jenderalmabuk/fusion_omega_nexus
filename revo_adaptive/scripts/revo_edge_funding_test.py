#!/usr/bin/env python3
"""Test funding effects on cross-sectional momentum (daily, perp-only).
Variants (L=30,H=7,K=10, net 11bps), funding PnL explicit:
  A momentum baseline (price only)
  B momentum + funding PnL (long pays funding, short collects)
  C momentum tilted by funding (prefer short high-funding / long low-funding) + funding PnL
  D pure funding carry (rank by funding) + funding PnL
Leg totals: long = price_ret - sum_funding ; short = -price_ret + sum_funding (over hold).
"""
import glob, os
import numpy as np
import pandas as pd

PRICE = os.path.join(os.path.dirname(__file__), "_edge_data_d")
FUND = os.path.join(os.path.dirname(__file__), "_edge_funding")
L, H, K, RT = 30, 7, 10, 2 * 0.00055
rng = np.random.default_rng(7)

# price matrix
pc = {}
for p in glob.glob(f"{PRICE}/*.csv"):
    s = pd.read_csv(p); pc[os.path.basename(p)[:-4]] = pd.Series(s["close"].values, index=s["ts"].astype("int64").values)
M = pd.DataFrame(pc).sort_index()
days = pd.to_datetime(M.index, unit="ms").date

# funding daily-summed, aligned to price days
fmat = pd.DataFrame(index=range(len(M)), columns=M.columns, dtype=float)
day_to_row = {d: i for i, d in enumerate(days)}
for sym in M.columns:
    fp = os.path.join(FUND, f"{sym}.csv")
    if not os.path.exists(fp):
        continue
    fd = pd.read_csv(fp)
    fd["day"] = pd.to_datetime(fd["ts"], unit="ms").dt.date
    daily = fd.groupby("day")["rate"].sum()
    for d, v in daily.items():
        if d in day_to_row:
            fmat.iat[day_to_row[d], M.columns.get_loc(sym)] = v
F = fmat.fillna(0.0).values
A = M.values
n = len(M)


def fund_recent(i):
    return np.nansum(F[max(0, i - 7):i], axis=0)   # trailing ~7d funding (current regime)


def fund_over_hold(i):
    return np.nansum(F[i:i + H], axis=0)


def zs(x):
    m, s = np.nanmean(x), np.nanstd(x)
    return (x - m) / s if s > 0 else x * 0


def run(mode, W=0.5):
    rets = []
    for i in range(L, n - H, H):
        past = A[i] / A[i - L] - 1.0
        fwd = A[i + H] / A[i] - 1.0
        fr = fund_recent(i); fh = fund_over_hold(i)
        valid = ~(np.isnan(past) | np.isnan(fwd))
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        pv, frv = past[idx], fr[idx]
        if mode == "A" or mode == "B":
            order = idx[np.argsort(pv)]
        elif mode == "C":
            score = zs(pv) - W * zs(frv)           # high mom + low funding favored for long
            order = idx[np.argsort(score)]
        elif mode == "D":
            order = idx[np.argsort(-frv)]          # long lowest funding, short highest
        longs, shorts = order[-K:], order[:K]
        lp = fwd[longs]; sp = -fwd[shorts]
        if mode == "A":
            leg = 0.5 * lp.mean() + 0.5 * sp.mean()
        else:
            lf = fh[longs]; sf = fh[shorts]
            lp = lp - lf                            # long pays funding
            sp = sp + sf                            # short collects funding
            leg = 0.5 * lp.mean() + 0.5 * sp.mean()
        rets.append(leg - RT)
    return np.array(rets)


def stat(r):
    if len(r) == 0:
        return (0, 0, 0, 0)
    wr = (r > 0).mean() * 100
    pf = r[r > 0].sum() / abs(r[r < 0].sum()) if (r < 0).any() else 99
    sh = r.mean() / r.std() * np.sqrt(365 / H) if r.std() else 0
    return r.mean() * 100, wr, pf, sh


print(f"Funding tests | {M.shape[1]} pairs daily | L={L} H={H} K={K} net {RT*1e4:.0f}bps\n")
print(f"{'variant':38s} {'ret/reb%':>8s} {'WR%':>5s} {'PF':>5s} {'Sharpe':>6s}")
print("-" * 66)
for mode, label in [("A", "A momentum baseline (price only)"),
                    ("B", "B momentum + funding PnL"),
                    ("C", "C momentum tilted by funding (W=0.5)"),
                    ("D", "D pure funding carry")]:
    m, wr, pf, sh = stat(run(mode))
    print(f"{label:38s} {m:8.3f} {wr:5.1f} {pf:5.2f} {sh:6.2f}")
mC1, wr, pf, sh = stat(run("C", W=1.0))
print(f"{'C2 momentum tilted by funding (W=1.0)':38s} {mC1:8.3f} {wr:5.1f} {pf:5.2f} {sh:6.2f}")
