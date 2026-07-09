#!/usr/bin/env python3
"""Download daily OI (bybit) + daily taker-flow/CVD-delta (binance) for the universe.
CVD-delta per day = (2*takerBuyBase - volume) from binance fapi klines."""
import urllib.request, json, time, os, glob

PERP = os.path.join(os.path.dirname(__file__), "_edge_data_d")
OI_OUT = os.path.join(os.path.dirname(__file__), "_edge_oi_d")
CVD_OUT = os.path.join(os.path.dirname(__file__), "_edge_cvd_d")
os.makedirs(OI_OUT, exist_ok=True); os.makedirs(CVD_OUT, exist_ok=True)
DAYS = 760


def get(u, host=None):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=25).read())


def dl_oi(sym, start):
    out, end = [], int(time.time() * 1000)
    for _ in range(6):
        try:
            lst = get(f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={sym}&intervalTime=1d&limit=200&endTime={end}")["result"]["list"]
        except Exception:
            break
        if not lst:
            break
        out += [(int(r["timestamp"]), float(r["openInterest"])) for r in lst]
        o = min(int(r["timestamp"]) for r in lst)
        if o <= start:
            break
        end = o - 1; time.sleep(0.05)
    return sorted(set(out))


def dl_cvd(sym, start):
    # binance fapi daily klines: index 9 = taker buy base vol, 5 = volume
    out, end = [], int(time.time() * 1000)
    for _ in range(3):
        try:
            k = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit=1000&endTime={end}")
        except Exception:
            break
        if not k:
            break
        out += [(int(r[0]), 2 * float(r[9]) - float(r[5])) for r in k]   # net taker base flow
        o = min(int(r[0]) for r in k)
        if o <= start:
            break
        end = o - 1; time.sleep(0.05)
    return sorted(set(out))


def main():
    syms = [os.path.basename(p)[:-4] for p in glob.glob(f"{PERP}/*.csv")]
    start = int(time.time() * 1000) - DAYS * 86400 * 1000
    oi_ok = cvd_ok = 0
    for s in syms:
        op = os.path.join(OI_OUT, f"{s}.csv")
        if not (os.path.exists(op) and os.path.getsize(op) > 200):
            d = dl_oi(s, start)
            if len(d) >= 60:
                open(op, "w").write("ts,oi\n" + "\n".join(f"{t},{v}" for t, v in d)); oi_ok += 1
        else:
            oi_ok += 1
        cp = os.path.join(CVD_OUT, f"{s}.csv")
        if not (os.path.exists(cp) and os.path.getsize(cp) > 200):
            d = dl_cvd(s, start)
            if len(d) >= 60:
                open(cp, "w").write("ts,flow\n" + "\n".join(f"{t},{v}" for t, v in d)); cvd_ok += 1
        else:
            cvd_ok += 1
    print(f"OI daily: {oi_ok} pairs | CVD daily: {cvd_ok} pairs")


if __name__ == "__main__":
    main()
