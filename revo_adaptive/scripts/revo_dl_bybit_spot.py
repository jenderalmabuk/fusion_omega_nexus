#!/usr/bin/env python3
"""Download bybit SPOT daily klines for the cached perp universe (for basis calc)."""
import urllib.request, json, time, os, glob

PERP = os.path.join(os.path.dirname(__file__), "_edge_data_d")
OUT = os.path.join(os.path.dirname(__file__), "_edge_spot")
os.makedirs(OUT, exist_ok=True)
DAYS = 760
BASE = "https://api.bybit.com/v5/market/kline"


def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=25).read())


def main():
    syms = [os.path.basename(p)[:-4] for p in glob.glob(f"{PERP}/*.csv")]
    start = int(time.time() * 1000) - DAYS * 86400 * 1000
    ok = 0; missing = []
    for s in syms:
        path = os.path.join(OUT, f"{s}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 300:
            ok += 1; continue
        out, end = [], int(time.time() * 1000)
        for _ in range(3):
            try:
                d = get(f"{BASE}?category=spot&symbol={s}&interval=D&end={end}&limit=1000")
            except Exception:
                break
            lst = d.get("result", {}).get("list", [])
            if not lst:
                break
            out += lst
            oldest = min(int(r[0]) for r in lst)
            if oldest <= start:
                break
            end = oldest - 1
            time.sleep(0.05)
        out = [r for r in out if int(r[0]) >= start]
        if len(out) < 60:
            missing.append(s); continue
        out.sort(key=lambda r: int(r[0]))
        with open(path, "w") as fh:
            fh.write("ts,close\n")
            for r in out:
                fh.write(f"{r[0]},{r[4]}\n")
        ok += 1
    print(f"spot cached {ok} pairs; no-spot: {len(missing)} {missing[:15]}")


if __name__ == "__main__":
    main()
