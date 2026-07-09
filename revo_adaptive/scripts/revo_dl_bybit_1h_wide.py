#!/usr/bin/env python3
"""Download 1H OHLCV for top-N liquid bybit perps (~500d) for robust ICT edge test."""
import urllib.request, json, time, os

OUT = os.path.join(os.path.dirname(__file__), "_edge_data_1h")
os.makedirs(OUT, exist_ok=True)
TOPN, DAYS = 50, 500
BASE = "https://api.bybit.com/v5/market"


def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=25).read())


def main():
    rows = [r for r in get(f"{BASE}/tickers?category=linear")["result"]["list"] if r["symbol"].endswith("USDT")]
    rows.sort(key=lambda r: float(r.get("turnover24h", 0)), reverse=True)
    syms = [r["symbol"] for r in rows[:TOPN]]
    start = int(time.time()*1000) - DAYS*86400*1000
    ok = 0
    for s in syms:
        path = os.path.join(OUT, f"{s[:-4]}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            ok += 1; continue
        out, end = [], int(time.time()*1000)
        for _ in range(14):
            d = get(f"{BASE}/kline?category=linear&symbol={s}&interval=60&end={end}&limit=1000")
            lst = d.get("result", {}).get("list", [])
            if not lst:
                break
            out += lst
            oldest = min(int(r[0]) for r in lst)
            if oldest <= start:
                break
            end = oldest - 1
            time.sleep(0.06)
        out = [r for r in out if int(r[0]) >= start]
        out.sort(key=lambda r: int(r[0]))
        if len(out) < 500:
            continue
        with open(path, "w") as fh:
            fh.write("ts,open,high,low,close,volume\n")
            for r in out:
                fh.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
        ok += 1
    print(f"cached {ok} pairs 1H -> {OUT}")


if __name__ == "__main__":
    main()
