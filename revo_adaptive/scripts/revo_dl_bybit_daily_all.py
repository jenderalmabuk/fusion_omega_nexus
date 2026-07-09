#!/usr/bin/env python3
"""Download DAILY candles for ALL bybit USDT perps with turnover24h >= threshold."""
import urllib.request, json, time, os

OUT = os.path.join(os.path.dirname(__file__), "_edge_data_all")
os.makedirs(OUT, exist_ok=True)
THRESH = 4_000_000
DAYS = 760
BASE = "https://api.bybit.com/v5/market"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "edge/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def klines(sym, start_ms):
    out, end = [], int(time.time()*1000)
    for _ in range(3):
        d = get(f"{BASE}/kline?category=linear&symbol={sym}&interval=D&end={end}&limit=1000")
        rows = d.get("result", {}).get("list", [])
        if not rows:
            break
        out += rows
        oldest = min(int(r[0]) for r in rows)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.08)
    return [r for r in out if int(r[0]) >= start_ms]


def main():
    d = get(f"{BASE}/tickers?category=linear")
    syms = [r["symbol"] for r in d["result"]["list"]
            if r["symbol"].endswith("USDT") and float(r.get("turnover24h", 0)) >= THRESH]
    start = int(time.time()*1000) - DAYS*86400*1000
    ok = 0
    for s in syms:
        path = os.path.join(OUT, f"{s}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 500:
            ok += 1; continue
        try:
            rows = klines(s, start)
        except Exception:
            continue
        rows.sort(key=lambda r: int(r[0]))
        if len(rows) < 90:
            continue
        with open(path, "w") as fh:
            fh.write("ts,open,high,low,close,volume\n")
            for r in rows:
                fh.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
        ok += 1
    print(f"cached {ok}/{len(syms)} pairs -> {OUT}")


if __name__ == "__main__":
    main()
