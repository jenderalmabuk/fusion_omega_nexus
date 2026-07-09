#!/usr/bin/env python3
"""Liquidity-threshold sweep for cross-sectional momentum.
Filters cached daily universe by current turnover24h; finds best edge/breadth tradeoff.
NOTE: flat 11bps cost; thinner universes have higher REAL slippage (results optimistic there)."""
import glob, os, json, urllib.request
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data_all")
L = 30
RT = 2 * 0.00055
rng = np.random.default_rng(7)

# current turnover map
req = urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear",
                             headers={"User-Agent": "edge/1.0"})
tk = json.loads(urllib.request.urlopen(req, timeout=25).read())
turn = {r["symbol"]: float(r.get("turnover24h", 0)) for r in tk["result"]["list"]}

closes = {}
for p in glob.glob(f"{DATA}/*.csv"):
    sym = os.path.basename(p)[:-4]
    s = pd.read_csv(p)
    closes[sym] = pd.Series(s["close"].values, index=s["ts"].values)
full = pd.DataFrame(closes).sort_index()
days = (int(full.index[-1]) - int(full.index[0])) / 86400000


def test(cols, H, K, rand=False):
    A = full[cols].values
    n = len(A)
    rets, ne, reb = [], 0, 0
    pl = ps = None
    for i in range(L, n - H, H):
        past = A[i] / A[i - L] - 1.0
        fwd = A[i + H] / A[i] - 1.0
        valid = ~(np.isnan(past) | np.isnan(fwd))
        idx = np.where(valid)[0]
        if len(idx) < 2 * K:
            continue
        order = idx[rng.permutation(len(idx))] if rand else idx[np.argsort(past[idx])]
        lo, sh = set(order[-K:].tolist()), set(order[:K].tolist())
        rets.append((fwd[list(lo)].mean() - fwd[list(sh)].mean()) / 2.0)
        reb += 1
        ne += (2 * K if pl is None else len(lo - pl) + len(sh - ps))
        pl, ps = lo, sh
    r = np.array(rets) - RT
    if len(r) == 0:
        return None
    wr = (r > 0).mean() * 100
    pf = r[r > 0].sum() / abs(r[r < 0].sum()) if (r < 0).any() else 99
    sh_ = r.mean() / r.std() * np.sqrt(365 / H) if r.std() else 0
    eq = np.cumprod(1 + r)
    cagr = (eq[-1] ** (365 / (H * len(r))) - 1) * 100 if eq[-1] > 0 else -100
    mdd = ((np.maximum.accumulate(eq) - eq) / np.maximum.accumulate(eq)).max() * 100
    return wr, pf, sh_, cagr, mdd, ne / days


def cols_threshold(th):
    return [c for c in full.columns if turn.get(c + "USDT", turn.get(c, 0)) >= th]


def cols_topn(nn):
    ranked = sorted(full.columns, key=lambda c: turn.get(c + "USDT", turn.get(c, 0)), reverse=True)
    return ranked[:nn]


universes = [("$10M", cols_threshold(10e6)), ("$20M", cols_threshold(20e6)),
             ("$50M", cols_threshold(50e6)), ("$100M", cols_threshold(100e6)),
             ("top30", cols_topn(30)), ("top50", cols_topn(50))]

for H in (7, 14):
    print(f"\n===== HOLD = {H} days | K=10 | L={L} | cost={RT*1e4:.0f}bps =====")
    print(f"{'universe':9s} {'npair':>5s} {'netPF':>6s} {'WR%':>5s} {'Shrp':>5s} {'CAGR%':>6s} {'maxDD%':>6s} {'ent/day':>7s}")
    print("-" * 60)
    for name, cols in universes:
        if len(cols) < 20:
            print(f"{name:9s} {len(cols):5d}  (too few pairs)")
            continue
        m = test(cols, H, 10)
        if not m:
            continue
        wr, pf, s_, cagr, mdd, epd = m
        print(f"{name:9s} {len(cols):5d} {pf:6.2f} {wr:5.1f} {s_:5.2f} {cagr:6.1f} {mdd:6.1f} {epd:7.2f}")
    # random control on $50M
    rc = test(cols_threshold(50e6), H, 10, rand=True)
    if rc:
        print(f"{'rand$50M':9s} {len(cols_threshold(50e6)):5d} {rc[1]:6.2f} {rc[0]:5.1f} {rc[2]:5.2f} {rc[3]:6.1f} {rc[4]:6.1f}")
