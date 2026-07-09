#!/usr/bin/env python3
"""Cross-sectional factor test: price-momentum vs OI-momentum vs CVD-flow + combos.
Long top-K / short bottom-K by factor, measure forward price-return spread (net 11bps).
Answers: do the OI/CVD data (that both bots relied on) have RELATIVE edge?
"""
import glob, os
import numpy as np
import pandas as pd

PERP = os.path.join(os.path.dirname(__file__), "_edge_data_d")
OID = os.path.join(os.path.dirname(__file__), "_edge_oi_d")
CVDD = os.path.join(os.path.dirname(__file__), "_edge_cvd_d")
L, H, K, RT = 30, 7, 10, 2 * 0.00055
rng = np.random.default_rng(7)


def load(dir_, col):
    d = {}
    for p in glob.glob(f"{dir_}/*.csv"):
        s = pd.read_csv(p)
        d[os.path.basename(p)[:-4]] = pd.Series(s[col].values, index=pd.to_datetime(s["ts"], unit="ms").dt.date)
    return pd.DataFrame(d).sort_index()


P = load(PERP, "close")
OI = load(OID, "oi").reindex(P.index)
CVDflow = load(CVDD, "flow").reindex(P.index)
coins = [c for c in P.columns if c in OI.columns and c in CVDflow.columns]
P, OI, CVDflow = P[coins], OI[coins], CVDflow[coins]
A, O, Cf = P.values, OI.values, CVDflow.values
n = len(P)


def xs_rank(v):
    """cross-sectional percentile rank ignoring nan."""
    out = np.full(len(v), np.nan)
    ok = ~np.isnan(v)
    if ok.sum() < 2:
        return out
    order = np.argsort(np.argsort(v[ok]))
    out[ok] = order / (ok.sum() - 1)
    return out


def factors(i):
    price_mom = A[i] / A[i - L] - 1
    oi_mom = O[i] / O[i - L] - 1
    win = Cf[i - L:i]
    cvd = np.nansum(win, axis=0) / (np.nansum(np.abs(win), axis=0) + 1e-9)   # directional accumulation [-1,1]
    return price_mom, oi_mom, cvd


def run(combo):
    rets = []
    for i in range(L, n - H, H):
        pm, om, cv = factors(i)
        fwd = A[i + H] / A[i] - 1
        valid = ~(np.isnan(pm) | np.isnan(fwd) | np.isnan(om) | np.isnan(cv))
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        score = np.zeros(len(idx))
        if "p" in combo: score += xs_rank(pm[idx])
        if "o" in combo: score += xs_rank(om[idx])
        if "c" in combo: score += xs_rank(cv[idx])
        if combo == "rand": score = rng.random(len(idx))
        order = idx[np.argsort(score)]
        longs, shorts = order[-K:], order[:K]
        rets.append((fwd[longs].mean() - fwd[shorts].mean()) / 2 - RT)
    r = np.array(rets)
    if len(r) == 0:
        return None
    wr = (r > 0).mean() * 100
    pf = r[r > 0].sum() / abs(r[r < 0].sum()) if (r < 0).any() else 99
    sh = r.mean() / r.std() * np.sqrt(365 / H) if r.std() else 0
    return r.mean() * 100, wr, pf, sh


print(f"Cross-sectional factor test | {len(coins)} coins | L={L} H={H} K={K} net {RT*1e4:.0f}bps\n")
print(f"{'factor':28s} {'ret/reb%':>8s} {'WR%':>5s} {'PF':>5s} {'Sharpe':>6s}")
print("-" * 56)
for combo, label in [("p", "price-momentum (baseline)"), ("o", "OI-momentum alone"),
                     ("c", "CVD-flow alone"), ("po", "price + OI"), ("pc", "price + CVD"),
                     ("poc", "price + OI + CVD"), ("rand", "random control")]:
    m = run(combo)
    if m:
        ret, wr, pf, sh = m
        flag = "  <==" if pf >= 1.5 and ret > 0 else ""
        print(f"{label:28s} {ret:8.3f} {wr:5.1f} {pf:5.2f} {sh:6.2f}{flag}")
