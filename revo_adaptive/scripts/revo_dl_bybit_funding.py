#!/usr/bin/env python3
"""Download 8h funding-rate history for the cached universe (matches _edge_data_d)."""
import urllib.request, json, time, os, glob

PRICE = os.path.join(os.path.dirname(__file__), "_edge_data_d")
OUT = os.path.join(os.path.dirname(__file__), "_edge_funding")
os.makedirs(OUT, exist_ok=True)
DAYS = 760
BASE = "https://api.bybit.com/v5/market/funding/history"


def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=25).read())


def main():
    syms = [os.path.basename(p)[:-4] for p in glob.glob(f"{PRICE}/*.csv")]   # already full symbol e.g. ICPUSDT
    start = int(time.time() * 1000) - DAYS * 86400 * 1000
    ok = 0
    for s in syms:
        path = os.path.join(OUT, f"{s}.csv")
        if os.path.exists(path) and os.path.getsize(path) > 300:
            ok += 1; continue
        out, end = [], int(time.time() * 1000)
        for _ in range(14):
            try:
                lst = get(f"{BASE}?category=linear&symbol={s}&limit=200&endTime={end}")["result"]["list"]
            except Exception:
                break
            if not lst:
                break
            out += [(int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in lst]
            oldest = min(int(r["fundingRateTimestamp"]) for r in lst)
            if oldest <= start:
                break
            end = oldest - 1
            time.sleep(0.05)
        out = sorted(set(out))
        out = [(t, r) for t, r in out if t >= start]
        if len(out) < 30:
            continue
        with open(path, "w") as fh:
            fh.write("ts,rate\n")
            for t, r in out:
                fh.write(f"{t},{r}\n")
        ok += 1
    print(f"cached funding for {ok} pairs -> {OUT}")


if __name__ == "__main__":
    main()
