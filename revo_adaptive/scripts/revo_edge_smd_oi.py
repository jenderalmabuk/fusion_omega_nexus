#!/usr/bin/env python3
"""SMD/order-block entry + OI confirmation — the original confluence thesis.
Downloads 1H open-interest history, aligns to price, then measures first-touch
edge of SMD entries FILTERED by OI confirmation (fresh positioning)."""
import glob, os, json, urllib.request, time
import numpy as np
import pandas as pd

PDATA = os.path.join(os.path.dirname(__file__), "_edge_data")     # 1H price
OIDIR = os.path.join(os.path.dirname(__file__), "_edge_oi")
os.makedirs(OIDIR, exist_ok=True)
DISP_ATR, ZONE_TTL, HORIZON = 1.0, 48, 48
THRESH = [1.5, 2.0, 3.0]
OI_LOOKBACK = 3        # bars to measure OI change at entry
rng = np.random.default_rng(7)


def dl_oi(sym):
    path = os.path.join(OIDIR, f"{sym}.csv")
    if os.path.exists(path) and os.path.getsize(path) > 500:
        return
    out, end = [], None
    for _ in range(34):                                   # ~6800h back
        u = f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={sym}&intervalTime=1h&limit=200"
        if end:
            u += f"&endTime={end}"
        try:
            lst = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20).read())["result"]["list"]
        except Exception:
            break
        if not lst:
            break
        out += [(int(r["timestamp"]), float(r["openInterest"])) for r in lst]
        end = min(int(r["timestamp"]) for r in lst) - 1
        time.sleep(0.06)
    out.sort()
    with open(path, "w") as fh:
        fh.write("ts,oi\n")
        for ts, oi in out:
            fh.write(f"{ts},{oi}\n")


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def smd_signals(df):
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    a = atr(df).values; body = np.abs(c - o); n = len(df); out = []
    for i in range(20, n - HORIZON - 1):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        if c[i] > o[i] and body[i] >= DISP_ATR * a[i] and c[i-1] < o[i-1]:
            zhi = h[i-1]
            for k in range(i+1, min(i+1+ZONE_TTL, n-1)):
                if l[k] <= zhi:
                    out.append((k, False)); break
        if c[i] < o[i] and body[i] >= DISP_ATR * a[i] and c[i-1] > o[i-1]:
            zlo = l[i-1]
            for k in range(i+1, min(i+1+ZONE_TTL, n-1)):
                if h[k] >= zlo:
                    out.append((k, True)); break
    return out


def first_touch(H, L, entry, short, thr):
    up, dn = entry*(1+thr/100), entry*(1-thr/100)
    for hi, lo in zip(H, L):
        if short:
            if lo <= dn: return "fav"
            if hi >= up: return "adv"
        else:
            if hi >= up: return "fav"
            if lo <= dn: return "adv"
    return "none"


buckets = {"SMD+OIconfirm": {t: [0,0,0] for t in THRESH},   # [fav,adv,none]
           "SMD+OIagainst": {t: [0,0,0] for t in THRESH},
           "SMD_all": {t: [0,0,0] for t in THRESH}}
n_conf = n_against = 0

for p in glob.glob(f"{PDATA}/*.csv"):
    base = os.path.basename(p)[:-4]
    sym = base + "USDT"
    dl_oi(sym)
    oipath = os.path.join(OIDIR, f"{sym}.csv")
    if not os.path.exists(oipath):
        continue
    oidf = pd.read_csv(oipath)
    oi_map = dict(zip(oidf["ts"].astype(int), oidf["oi"].astype(float)))
    df = pd.read_csv(p)
    ts = df["ts"].astype(int).values
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    # OI series aligned to price bars (nan if missing)
    oi_al = np.array([oi_map.get(int(t), np.nan) for t in ts])
    for k, short in smd_signals(df):
        if k - OI_LOOKBACK < 0 or np.isnan(oi_al[k]) or np.isnan(oi_al[k - OI_LOOKBACK]):
            continue
        oi_rising = oi_al[k] > oi_al[k - OI_LOOKBACK]      # fresh positioning = confirmation
        entry = C[k]; hs, ls = H[k+1:k+1+HORIZON], L[k+1:k+1+HORIZON]
        for t in THRESH:
            r = first_touch(hs, ls, entry, short, t)
            j = {"fav":0,"adv":1,"none":2}[r]
            buckets["SMD_all"][t][j] += 1
            buckets["SMD+OIconfirm" if oi_rising else "SMD+OIagainst"][t][j] += 1
        n_conf += int(oi_rising); n_against += int(not oi_rising)

print(f"SMD + OI confirmation (original thesis) | OI-confirm signals={n_conf}, OI-against={n_against}\n")
print(f"{'bucket':16s} {'thr':>4s} {'n':>5s} {'fav':>5s} {'adv':>5s} {'EDGE%':>6s}")
print("-"*50)
for name in ("SMD_all", "SMD+OIconfirm", "SMD+OIagainst"):
    for t in THRESH:
        fav, adv, none = buckets[name][t]
        dec = fav + adv
        edge = fav/dec*100 if dec else 0
        print(f"{name:16s} {t:4.1f} {dec+none:5d} {fav:5d} {adv:5d} {edge:6.1f}")
    print()
