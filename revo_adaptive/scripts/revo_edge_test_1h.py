#!/usr/bin/env python3
"""First-touch edge test on 1H data for several candidate setups vs random baseline.

Edge = P(price touches +T% before -T%) after entry. ~50% = no edge.
All signals evaluated long+short (short mirrored). Exit logic NOT applied.
"""
import os, glob
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data")
THRESH = [2.0, 3.0]      # % first-touch thresholds (1H scale)
HORIZON = 48             # bars ahead (2 days)
rng = np.random.default_rng(7)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))


def load(path):
    df = pd.read_csv(path)
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["rsi"] = rsi(df["close"])
    df["hh20"] = df["high"].rolling(20).max()
    df["ll20"] = df["low"].rolling(20).min()
    df["hh12"] = df["high"].rolling(12).max()
    df["ll12"] = df["low"].rolling(12).min()
    return df


def signals(df):
    c, pc = df["close"], df["close"].shift(1)
    e20, e50, e200 = df["ema20"], df["ema50"], df["ema200"]
    up, dn = e50 > e200, e50 < e200
    sig = {}
    # 1. trend pullback reclaim
    sig["trend_pullback"] = (
        (up & (pc < e20.shift(1)) & (c > e20)),
        (dn & (pc > e20.shift(1)) & (c < e20)),
    )
    # 2. donchian breakout-retest (break then close back above broken level)
    brk_up = df["high"].shift(1) >= df["hh20"].shift(2)
    sig["breakout_retest"] = (
        (up & brk_up & (c > df["hh20"].shift(2)) & (df["low"] <= df["hh20"].shift(2))),
        (dn & (df["low"].shift(1) <= df["ll20"].shift(2)) & (c < df["ll20"].shift(2)) & (df["high"] >= df["ll20"].shift(2))),
    )
    # 3. momentum continuation (new 12-bar high in uptrend, rsi mid-high)
    sig["momentum_cont"] = (
        ((c > e20) & (e20 > e50) & up & df["rsi"].between(50, 72) & (c >= df["hh12"].shift(1))),
        ((c < e20) & (e20 < e50) & dn & df["rsi"].between(28, 50) & (c <= df["ll12"].shift(1))),
    )
    # 4. rsi mean-reversion (oversold reclaim / overbought fade)
    sig["rsi_meanrev"] = (
        ((df["rsi"].shift(1) < 30) & (df["rsi"] >= 30)),
        ((df["rsi"].shift(1) > 70) & (df["rsi"] <= 70)),
    )
    return sig


def first_touch(highs, lows, entry, short, thr):
    """Return 'fav' / 'adv' / 'none' for first touch of +/-thr% (favorable for the side)."""
    up_lvl = entry * (1 + thr/100)
    dn_lvl = entry * (1 - thr/100)
    for h, l in zip(highs, lows):
        if short:
            if l <= dn_lvl: return "fav"
            if h >= up_lvl: return "adv"
        else:
            if h >= up_lvl: return "fav"
            if l <= dn_lvl: return "adv"
    return "none"


dfs = {os.path.basename(p)[:-4]: load(p) for p in glob.glob(f"{DATA}/*.csv")}
results = {}
setup_names = list(next(iter(dfs.values())).pipe(signals).keys()) + ["baseline_random"]

for name in setup_names:
    for thr in THRESH:
        results[(name, thr)] = {"fav": 0, "adv": 0, "none": 0, "fwd": []}

for sym, df in dfs.items():
    sig = signals(df)
    H, L = df["high"].values, df["low"].values
    C = df["close"].values
    n = len(df)
    # build per-setup entry index lists
    entry_sets = {k: [(i, False) for i in np.where(v[0].fillna(False).values)[0]] +
                     [(i, True) for i in np.where(v[1].fillna(False).values)[0]] for k, v in sig.items()}
    # random baseline: same count as avg, random bars both sides
    rnd_idx = rng.choice(np.arange(200, n-HORIZON-1), size=min(300, n-260), replace=False)
    entry_sets["baseline_random"] = [(int(i), bool(rng.integers(0, 2))) for i in rnd_idx]
    for name, entries in entry_sets.items():
        for i, short in entries:
            if i < 200 or i + HORIZON >= n:
                continue
            entry = C[i]
            hs, ls = H[i+1:i+1+HORIZON], L[i+1:i+1+HORIZON]
            fwd = (entry - C[i+HORIZON]) / entry * 100 if short else (C[i+HORIZON] - entry) / entry * 100
            for thr in THRESH:
                r = results[(name, thr)]
                r[first_touch(hs, ls, entry, short, thr)] += 1
                if thr == THRESH[0]:
                    r["fwd"].append(fwd)

print(f"Data: {len(dfs)} pairs, 1H, horizon={HORIZON} bars (~{HORIZON}h)\n")
print(f"{'setup':18s} {'thr':>4s} {'n':>5s} {'fav':>5s} {'adv':>5s} {'edge%':>6s} {'fwdRet%':>8s}")
print("-" * 60)
for name in setup_names:
    for thr in THRESH:
        r = results[(name, thr)]
        dec = r["fav"] + r["adv"]
        edge = r["fav"] / dec * 100 if dec else 0
        fwd = np.mean(r["fwd"]) if r["fwd"] else 0
        n_tot = dec + r["none"]
        flag = "  <== EDGE" if (thr == THRESH[0] and edge >= 55 and n_tot >= 30) else ""
        print(f"{name:18s} {thr:4.1f} {n_tot:5d} {r['fav']:5d} {r['adv']:5d} {edge:6.1f} {fwd:8.2f}{flag}")
