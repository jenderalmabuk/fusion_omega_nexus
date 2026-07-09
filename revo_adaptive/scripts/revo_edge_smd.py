#!/usr/bin/env python3
"""Test edge of Supply/Demand (order-block) zone entries — the original thesis.

Order block (ICT/SMC):
- Bullish OB (demand): last bearish candle before a strong UP displacement.
  Entry LONG when price later retraces back INTO that zone.
- Bearish OB (supply): last bullish candle before a strong DOWN displacement.
  Entry SHORT when price retraces into zone.
Edge = first-touch +/-T% after entry (decoupled from exit). ~50% = no edge.
"""
import glob, os
import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(__file__), "_edge_data")   # 1H, 12 pairs, 240d
DISP_ATR = 1.0       # displacement body >= 1*ATR = "strong move"
ZONE_TTL = 48        # zone valid for N bars after creation
HORIZON = 48         # forward bars for first-touch
THRESH = [1.5, 2.0, 3.0]
rng = np.random.default_rng(7)


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def signals(df):
    """Return list of (entry_idx, side) from OB retrace entries."""
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    a = atr(df).values
    body = np.abs(c - o)
    out = []
    n = len(df)
    for i in range(20, n - HORIZON - 1):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        # bullish displacement at i, prev candle bearish => demand zone = prev candle range
        if c[i] > o[i] and body[i] >= DISP_ATR * a[i] and c[i - 1] < o[i - 1]:
            zlo, zhi = l[i - 1], h[i - 1]
            for k in range(i + 1, min(i + 1 + ZONE_TTL, n - 1)):
                if l[k] <= zhi:                      # price retraced into demand zone
                    out.append((k, False)); break    # LONG
        # bearish displacement, prev candle bullish => supply zone
        if c[i] < o[i] and body[i] >= DISP_ATR * a[i] and c[i - 1] > o[i - 1]:
            zlo, zhi = l[i - 1], h[i - 1]
            for k in range(i + 1, min(i + 1 + ZONE_TTL, n - 1)):
                if h[k] >= zlo:                      # price retraced into supply zone
                    out.append((k, True)); break     # SHORT
    return out


def first_touch(H, L, entry, short, thr):
    up, dn = entry * (1 + thr / 100), entry * (1 - thr / 100)
    for hi, lo in zip(H, L):
        if short:
            if lo <= dn: return "fav"
            if hi >= up: return "adv"
        else:
            if hi >= up: return "fav"
            if lo <= dn: return "adv"
    return "none"


res = {t: {"fav": 0, "adv": 0, "none": 0} for t in THRESH}
rnd = {t: {"fav": 0, "adv": 0, "none": 0} for t in THRESH}
total = 0
for p in glob.glob(f"{DATA}/*.csv"):
    df = pd.read_csv(p)
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    n = len(df)
    sig = signals(df)
    total += len(sig)
    for k, short in sig:
        entry = C[k]
        hs, ls = H[k + 1:k + 1 + HORIZON], L[k + 1:k + 1 + HORIZON]
        for t in THRESH:
            res[t][first_touch(hs, ls, entry, short, t)] += 1
    # random control: same count, random bars/sides
    for _ in range(len(sig)):
        k = int(rng.integers(20, n - HORIZON - 1)); short = bool(rng.integers(0, 2))
        entry = C[k]; hs, ls = H[k + 1:k + 1 + HORIZON], L[k + 1:k + 1 + HORIZON]
        for t in THRESH:
            rnd[t][first_touch(hs, ls, entry, short, t)] += 1

print(f"SMD / order-block entry edge | {total} signals | 1H 12 pairs | horizon {HORIZON}h\n")
print(f"{'thr':>5s} {'n':>5s} {'fav':>5s} {'adv':>5s} {'EDGE%':>6s}   {'random%':>8s}")
print("-" * 46)
for t in THRESH:
    r = res[t]; dec = r["fav"] + r["adv"]
    edge = r["fav"] / dec * 100 if dec else 0
    rr = rnd[t]; rdec = rr["fav"] + rr["adv"]
    redge = rr["fav"] / rdec * 100 if rdec else 0
    print(f"{t:5.1f} {dec + r['none']:5d} {r['fav']:5d} {r['adv']:5d} {edge:6.1f}   {redge:8.1f}")
