#!/usr/bin/env python3
"""Download bybit 1H OHLCV (linear perp) via REST v5, cache to CSV."""
import urllib.request, json, time, os
from datetime import datetime, timezone

OUT = os.path.join(os.path.dirname(__file__), "_edge_data")
os.makedirs(OUT, exist_ok=True)
PAIRS = ["BTC", "ETH", "SOL", "BNB", "XRP", "INJ", "SUI", "AAVE", "DOT", "WLD", "LINK", "AVAX"]
INTERVAL = "60"          # 1H
DAYS = 240
BASE = "https://api.bybit.com/v5/market/kline"


def fetch(symbol, start_ms, end_ms):
    out = []
    end = end_ms
    for _ in range(60):
        url = f"{BASE}?category=linear&symbol={symbol}&interval={INTERVAL}&end={end}&limit=1000"
        req = urllib.request.Request(url, headers={"User-Agent": "edge/1.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        rows = d.get("result", {}).get("list", [])
        if not rows:
            break
        out += rows
        oldest = min(int(r[0]) for r in rows)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.15)
    return [r for r in out if int(r[0]) >= start_ms]


def main():
    now = int(time.time() * 1000)
    start = now - DAYS * 86400 * 1000
    for p in PAIRS:
        sym = f"{p}USDT"
        path = os.path.join(OUT, f"{p}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            print(f"{p}: cached"); continue
        rows = fetch(sym, start, now)
        rows.sort(key=lambda r: int(r[0]))
        with open(path, "w") as fh:
            fh.write("ts,open,high,low,close,volume\n")
            for r in rows:
                fh.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
        print(f"{p}: {len(rows)} bars -> {path}")


if __name__ == "__main__":
    main()
