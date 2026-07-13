#!/usr/bin/env python3
"""Reconstruct discount depth (dist_ema55) + trend context for every trade in a
freqtrade backtest result zip, then bucket profit by depth. Answers: do losers
cluster in deep-discount (falling-knife) territory while winners sit in shallow
pullbacks? Uses existing feather data — no re-run needed.

Usage: analyze_discount_depth.py [result.zip]  (defaults to newest)
"""
import zipfile, json, glob, os, sys
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

z = sys.argv[1] if len(sys.argv) > 1 else sorted(
    glob.glob("revo_adaptive/user_data/backtest_results/*.zip"))[-1]
with zipfile.ZipFile(z) as zf:
    names = [n for n in zf.namelist()
             if n.endswith('.json') and not n.endswith('.meta.json')]
    data = json.load(zf.open(names[0]))
s = next(iter(data.get("strategy", {}).values()))
trades = s.get("trades", [])
print(f"baseline zip: {os.path.basename(z)} | {len(trades)} trades\n")

DATA = "revo_adaptive/user_data/data/bybit/futures"
cache = {}


def load(pair):
    fn = pair.replace("/", "_").replace(":", "_")
    p = f"{DATA}/{fn}-5m-futures.feather"
    if pair in cache:
        return cache[pair]
    if not os.path.exists(p):
        cache[pair] = None
        return None
    df = pd.read_feather(p)
    df["ema55"] = df["close"].ewm(span=55, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["dist"] = (df["close"] / df["ema55"] - 1) * 100
    df["uptrend"] = (df["ema50"] > df["ema200"]).astype(int)
    df = df.set_index("date")
    cache[pair] = df
    return df


rows = []
miss = 0
for t in trades:
    df = load(t["pair"])
    if df is None:
        miss += 1
        continue
    ts = pd.to_datetime(t["open_timestamp"], unit="ms", utc=True)
    sub = df[df.index <= ts]
    if len(sub) == 0:
        miss += 1
        continue
    r = sub.iloc[-1]
    rows.append({"pair": t["pair"], "profit_abs": t["profit_abs"],
                 "profit_ratio": t["profit_ratio"], "exit": t["exit_reason"],
                 "dist": r["dist"], "uptrend": int(r["uptrend"])})

d = pd.DataFrame(rows)
print(f"reconstructed {len(d)} trades ({miss} unmatched)\n")

bins = [-100, -12, -9, -7, -5, -3.5, 0, 100]
labels = ["<-12%", "-12..-9", "-9..-7", "-7..-5", "-5..-3.5", "-3.5..0", ">0"]
d["bucket"] = pd.cut(d["dist"], bins=bins, labels=labels)
print("=== PROFIT per KEDALAMAN DISKON (dist_ema55 saat entry) ===")
g = d.groupby("bucket", observed=True).agg(
    n=("profit_abs", "size"),
    win=("profit_abs", lambda x: (x > 0).sum()),
    net=("profit_abs", "sum"),
    mean=("profit_abs", "mean")).reset_index()
g["win%"] = (g["win"] / g["n"] * 100).round(1)
g["net"] = g["net"].round(2)
g["mean"] = g["mean"].round(3)
print(g.to_string(index=False))

print("\n=== PROFIT per KONTEKS TREN (ema50 vs ema200 saat entry) ===")
g2 = d.groupby("uptrend").agg(
    n=("profit_abs", "size"),
    win=("profit_abs", lambda x: (x > 0).sum()),
    net=("profit_abs", "sum"),
    mean=("profit_abs", "mean")).reset_index()
g2["win%"] = (g2["win"] / g2["n"] * 100).round(1)
g2["net"] = g2["net"].round(2)
g2["mean"] = g2["mean"].round(3)
g2["ctx"] = g2["uptrend"].map({0: "DOWNTREND(ema50<200)", 1: "UPTREND(ema50>200)"})
print(g2[["ctx", "n", "win", "win%", "net", "mean"]].to_string(index=False))

# cumulative effect of a floor: what if we drop trades deeper than threshold?
print("\n=== SIMULASI PLAFOND: buang entry lebih dalam dari X% ===")
for thr in [-12, -10, -9, -8, -7, -6]:
    kept = d[d["dist"] >= thr]
    dropped = d[d["dist"] < thr]
    if len(dropped) == 0:
        continue
    print(f"floor {thr:>4}%: buang {len(dropped):>3} trade "
          f"(net {dropped['profit_abs'].sum():>8.2f}, "
          f"win {(dropped['profit_abs']>0).sum()}/{len(dropped)}) | "
          f"SISA {len(kept):>3} trade net {kept['profit_abs'].sum():>8.2f} "
          f"win% {(kept['profit_abs']>0).mean()*100:.1f}")
